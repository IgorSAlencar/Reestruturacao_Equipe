import fs from 'node:fs';
import { randomUUID } from 'node:crypto';
import express from 'express';
import cors from 'cors';
import { z } from 'zod';
import { API_HOST, API_PORT, DATA_DIR, DISTRICTS_FILE, MAPBOX_ACCESS_TOKEN, MAPBOX_STYLE, MUNICIPALITIES_FILE } from './config.js';
import { createDraft, deleteDraft, getDraft, listDrafts, updateDraft } from './db.js';
import { listScenarios, loadCurrent, loadScenario } from './scenarios.js';
import { loadExcludedMunicipalities, loadPopulation, loadRegionalOffices, refreshCurrentCache } from './currentCache.js';
import { getMunicipalityCenters } from './municipalityCenters.js';
import { closeSqlPool, testSqlConnection } from './sqlServer.js';

const app=express();
app.use(cors());
app.use(express.json({limit:'50mb'}));
app.get('/api/health',(_,res)=>res.json({ok:true,time:new Date().toISOString()}));
app.get('/api/config',(_,res)=>res.json({mapboxToken:MAPBOX_ACCESS_TOKEN,mapboxStyle:MAPBOX_STYLE}));
app.get('/api/scenarios',async(_,res)=>{const current=loadCurrent();res.json([...(current?[current.summary]:[]),...await listScenarios()]);});
app.get('/api/scenarios/current',(_,res)=>{const data=loadCurrent();data?res.json(data):res.status(404).json({message:'Cache atual ainda não foi gerado.'});});
app.get('/api/scenarios/:id',async(req,res)=>{const data=await loadScenario(req.params.id);data?res.json(data):res.status(404).json({message:'Cenário não encontrado.'});});
app.get('/api/geometry/municipalities',(_,res)=>fs.existsSync(MUNICIPALITIES_FILE)?res.type('application/geo+json').sendFile(MUNICIPALITIES_FILE):res.status(404).json({message:'Malha municipal não encontrada.'}));
app.get('/api/geometry/districts',(_,res)=>fs.existsSync(DISTRICTS_FILE)?res.type('application/geo+json').sendFile(DISTRICTS_FILE):res.status(404).json({message:'Malha distrital não encontrada. Gere com scripts/generate_district_mesh.py.'}));
app.get('/api/geometry/municipality-centers',(_,res)=>{
  try{res.json(getMunicipalityCenters());}
  catch(error){res.status(500).json({message:error.message||'Falha ao carregar centros municipais.'});}
});
app.get('/api/population',async(_,res)=>res.json(await loadPopulation()));
app.get('/api/regional-offices',async(_,res)=>res.json(await loadRegionalOffices()));
app.get('/api/excluded-municipalities',async(_,res)=>res.json(await loadExcludedMunicipalities()));
app.get('/api/drafts',(_,res)=>res.json(listDrafts()));
app.get('/api/drafts/:id',(req,res)=>{const d=getDraft(req.params.id);d?res.json(d):res.status(404).json({message:'Rascunho não encontrado.'});});
app.post('/api/drafts',(req,res)=>{const b=z.object({name:z.string().min(1),baseScenarioId:z.string(),data:z.any()}).parse(req.body);res.status(201).json(createDraft(randomUUID(),b.name,b.baseScenarioId,b.data));});
app.put('/api/drafts/:id',(req,res)=>{const b=z.object({name:z.string().min(1),revision:z.number().int(),data:z.any()}).parse(req.body),result=updateDraft(req.params.id,b.name,b.revision,b.data);if(result==='conflict')return res.status(409).json({message:'Este rascunho foi alterado em outra aba.'});return result?res.json(result):res.status(404).json({message:'Rascunho não encontrado.'});});
app.delete('/api/drafts/:id',(req,res)=>deleteDraft(req.params.id)?res.sendStatus(204):res.status(404).json({message:'Rascunho não encontrado.'}));
app.get('/api/drafts/:id/export',(req,res)=>{const d=getDraft(req.params.id);if(!d)return res.status(404).json({message:'Rascunho não encontrado.'});res.attachment(`${d.id}.json`).json(d);});
app.get('/api/drafts/:id/geojson',(req,res)=>{
  const d=getDraft(req.params.id);if(!d)return res.status(404).json({message:'Rascunho não encontrado.'});
  let base=d.data.territories;
  if((!base?.features?.length)&&fs.existsSync(MUNICIPALITIES_FILE))base=JSON.parse(fs.readFileSync(MUNICIPALITIES_FILE,'utf8'));
  const munCode=(value)=>String(value||'').replace(/\D/g,'').padStart(7,'0').slice(-7);
  const distCode=(value)=>String(value||'').replace(/\D/g,'');
  const byId=new Map(d.data.units.map(u=>[u.id,u]));
  const byMunCode=new Map();
  d.data.units.forEach(u=>{
    if(u.type==='DISTRITO')return;
    const code=munCode(u.municipalityCode);
    byMunCode.set(code,[...(byMunCode.get(code)||[]),u]);
  });
  const used=new Set();
  const features=[];
  for(const f of base?.features||[]){
    const props=f.properties||{};
    const direct=byId.get(String(props.DEMAND_ID||props._unitId||''));
    const featureDist=distCode(props.CD_DIST);
    // Feature distrital já presente no GeoJSON do rascunho
    if(direct?.type==='DISTRITO'||(featureDist&&!direct)){
      const unit=direct?.type==='DISTRITO'
        ?direct
        :d.data.units.find(u=>u.type==='DISTRITO'&&distCode(u.districtCode)===featureDist);
      if(!unit)continue;
      used.add(unit.id);
      features.push({...f,properties:{
        ...(props),
        DEMAND_ID:unit.id,
        GERENCIA_ID:unit.poleId,
        DISTANCIA_KM:unit.distanceKm,
        QTD_LOJAS:unit.stores,
        POPULACAO_UNIDADE:unit.population,
        TIPO_UNIDADE:'DISTRITO',
        CD_DIST:distCode(unit.districtCode)||featureDist,
        CD_MUN:munCode(unit.municipalityCode||props.CD_MUN),
        NM_DIST:props.NM_DIST||unit.municipalityName,
        NM_MUN:props.NM_MUN||unit.municipalityName,
      }});
      continue;
    }
    const code=munCode(props.COD_IBGE||props.CD_MUN||props.id||'');
    const units=direct&&direct.type!=='DISTRITO'?[direct]:(byMunCode.get(code)||[]);
    if(!units.length)continue;
    for(const u of units){
      used.add(u.id);
      features.push({...f,properties:{
        ...(props),
        DEMAND_ID:u.id,
        GERENCIA_ID:u.poleId,
        DISTANCIA_KM:u.distanceKm,
        QTD_LOJAS:u.stores,
        POPULACAO_UNIDADE:u.population,
        TIPO_UNIDADE:'MUNICIPIO',
        CD_MUN:munCode(u.municipalityCode),
        NM_MUN:u.municipalityName||props.NM_MUN,
      }});
    }
  }
  // Municípios novos no Builder ainda sem geometria no GeoJSON base
  if(fs.existsSync(MUNICIPALITIES_FILE)){
    const mesh=base?.features?.length?base:JSON.parse(fs.readFileSync(MUNICIPALITIES_FILE,'utf8'));
    const meshByCode=new Map();
    for(const f of mesh.features||[]){
      const code=munCode(f.properties?.COD_IBGE||f.properties?.CD_MUN||f.properties?.id||'');
      if(code&&code!=='0000000')meshByCode.set(code,f);
    }
    for(const u of d.data.units){
      if(used.has(u.id)||!u.poleId||u.type==='DISTRITO')continue;
      const code=munCode(u.municipalityCode);
      const f=meshByCode.get(code);
      if(!f)continue;
      used.add(u.id);
      features.push({...f,properties:{
        ...(f.properties||{}),
        DEMAND_ID:u.id,
        GERENCIA_ID:u.poleId,
        DISTANCIA_KM:u.distanceKm,
        QTD_LOJAS:u.stores,
        POPULACAO_UNIDADE:u.population,
        TIPO_UNIDADE:'MUNICIPIO',
        CD_MUN:code,
        NM_MUN:u.municipalityName||f.properties?.NM_MUN,
      }});
    }
  }
  // Distritos atribuídos: geometria da malha distrital metropolitana
  if(fs.existsSync(DISTRICTS_FILE)){
    const districtMesh=JSON.parse(fs.readFileSync(DISTRICTS_FILE,'utf8'));
    const meshByDist=new Map();
    for(const f of districtMesh.features||[]){
      const code=distCode(f.properties?.CD_DIST);
      if(code)meshByDist.set(code,f);
    }
    for(const u of d.data.units){
      if(u.type!=='DISTRITO'||used.has(u.id)||!u.poleId)continue;
      const code=distCode(u.districtCode);
      const f=meshByDist.get(code);
      if(!f)continue;
      used.add(u.id);
      features.push({...f,properties:{
        ...(f.properties||{}),
        DEMAND_ID:u.id,
        GERENCIA_ID:u.poleId,
        DISTANCIA_KM:u.distanceKm,
        QTD_LOJAS:u.stores,
        POPULACAO_UNIDADE:u.population||f.properties?.POP_2022||0,
        TIPO_UNIDADE:'DISTRITO',
        CD_DIST:code,
        CD_MUN:munCode(u.municipalityCode||f.properties?.CD_MUN),
        NM_DIST:f.properties?.NM_DIST||u.municipalityName,
        NM_MUN:f.properties?.NM_MUN||u.municipalityName,
      }});
    }
  }
  res.attachment(`${d.id}.geojson`).type('application/geo+json').json({type:'FeatureCollection',features});
});
let refreshing=false,lastRefreshError=null;
app.get('/api/current-cache/status',(_,res)=>res.json({available:!!loadCurrent(),refreshing,lastError:lastRefreshError,dataDir:DATA_DIR}));
app.get('/api/sql/health',async(_,res)=>res.json({ok:true,...await testSqlConnection()}));
app.post('/api/current-cache/refresh',(_,res)=>{if(refreshing)return res.status(409).json({message:'Atualização já está em andamento.'});refreshing=true;lastRefreshError=null;refreshCurrentCache().catch(error=>{lastRefreshError=error.message;console.error('Falha ao atualizar lojas:',error);}).finally(()=>{refreshing=false;});res.status(202).json({message:'Atualização iniciada.'});});
app.use((error,_,res,__)=>res.status(error.status||500).json({message:error.message}));
const server=app.listen(API_PORT,API_HOST,()=>console.log(`API: http://${API_HOST}:${API_PORT}`));
const shutdown=async()=>{server.close();await closeSqlPool();};
process.once('SIGINT',shutdown);process.once('SIGTERM',shutdown);
