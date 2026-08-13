import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { DATA_DIR, ROOT, V3_DIR, V4_DIR, V5_DIR } from './config.js';

export const MAX_INLINE_TERRITORIES_BYTES=64*1024*1024;

const value=(r,...keys)=>keys.map(k=>r[k]).find(v=>v!==undefined&&v!==null&&v!=='');
const num=v=>Number.isFinite(Number(v))?Number(v):0;
const text=(v,fallback='')=>String(v??fallback).trim();
const normalizeMunicipalityCode=value=>{const digits=String(value??'').replace(/\D/g,'');return digits.length===6?`${digits}0`:digits.slice(0,7);};
const geometryCenter=geometry=>{
  const points=[];const visit=input=>{if(!Array.isArray(input))return;if(input.length>=2&&typeof input[0]==='number'&&typeof input[1]==='number'){points.push([input[0],input[1]]);return;}input.forEach(visit);};
  if(geometry.type==='GeometryCollection')geometry.geometries.forEach(g=>visit(g.coordinates||[]));else visit(geometry.coordinates);
  if(!points.length)return[0,0];let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  for(const [x,y] of points){minX=Math.min(minX,x);minY=Math.min(minY,y);maxX=Math.max(maxX,x);maxY=Math.max(maxY,y);}return[(minX+maxX)/2,(minY+maxY)/2];
};
function findWorkbook(folder){return fs.readdirSync(folder).find(n=>/^resultado_.*\.xlsx$/i.test(n));}
function folders(root){if(!fs.existsSync(root))return[];return fs.readdirSync(root,{withFileTypes:true}).filter(x=>x.isDirectory()).map(x=>path.join(root,x.name)).filter(f=>findWorkbook(f)&&fs.existsSync(path.join(f,'carteiras_unidades.geojson')));}
const workbookCache=new Map();
function readWorkbook(file,includeUnits=false){const mtime=fs.statSync(file).mtimeMs,key=`${file}:${includeUnits?'full':'summary'}`,cached=workbookCache.get(key);if(cached?.mtime===mtime)return cached.data;const args=[path.join(ROOT,'utils','read_scenario_workbook.py'),file];if(includeUnits)args.push('--include-units');const output=execFileSync(process.env.PYTHON_BIN||'python',args,{cwd:ROOT,encoding:'utf8',maxBuffer:64*1024*1024});const data=JSON.parse(output);workbookCache.set(key,{mtime,data});return data;}
const sheet=(book,name)=>book[name]||[];
export const shouldInlineTerritories=bytes=>bytes<=MAX_INLINE_TERRITORIES_BYTES;
export const unitsFromRows=rows=>rows.map((r,i)=>({
  id:text(value(r,'DEMAND_ID'),`UNIDADE-${i}`),
  type:text(value(r,'TIPO_UNIDADE'),'MUNICIPIO').toUpperCase()==='DISTRITO'?'DISTRITO':'MUNICIPIO',
  municipalityCode:normalizeMunicipalityCode(value(r,'COD_IBGE','CD_MUN')),
  districtCode:text(value(r,'CD_DIST'))||undefined,
  municipalityName:text(value(r,'NM_MUN')),
  uf:text(value(r,'UF')),
  poleId:text(value(r,'GERENCIA_ID'))||null,
  population:num(value(r,'POPULACAO_UNIDADE')),
  stores:num(value(r,'QTD_LOJAS')),
  latitude:num(value(r,'LATITUDE')),
  longitude:num(value(r,'LONGITUDE')),
  distanceKm:num(value(r,'DISTANCIA_KM')),
}));
function detectKind(folder,book){const version=text(sheet(book,'cenario')[0]?.MODELO_VERSAO).toUpperCase(),name=folder.toLowerCase();if(version.includes('V5')||name.includes('v5'))return'v5';if(version.includes('V4')||name.includes('v4'))return'v4';return'v3';}
async function summaryFrom(folder){const wb=findWorkbook(folder),book=readWorkbook(path.join(folder,wb)),managers=sheet(book,'gerencias_propostas'),kind=detectKind(folder,book),areas={};managers.forEach(r=>{const a=text(value(r,'DESC_GERENCIA_AREA_PROPOSTA'),'SEM ÁREA');areas[a]=(areas[a]||0)+1;});const scenario=sheet(book,'cenario')[0]||{};return{id:path.basename(folder),name:path.basename(folder),kind,version:text(value(scenario,'MODELO_VERSAO'),kind.toUpperCase()),createdAt:text(value(scenario,'DATA_EXECUCAO')),path:folder,poleCount:managers.length,areaCounts:areas,warnings:[]};}
export async function listScenarios(){const scenarios=await Promise.all([...folders(V5_DIR),...folders(V4_DIR),...folders(V3_DIR)].map(summaryFrom));return scenarios.sort((a,b)=>(b.createdAt||'').localeCompare(a.createdAt||''));}
export async function loadScenario(id){
  const summary=(await listScenarios()).find(s=>s.id===id);if(!summary?.path)return null;const book=readWorkbook(path.join(summary.path,findWorkbook(summary.path)),true),managers=sheet(book,'gerencias_propostas');
  const poles=managers.map(r=>({id:text(value(r,'GERENCIA_ID')),name:text(value(r,'NM_MUN_POLO','NM_DIST_POLO','GERENCIA_ID')),longitude:num(value(r,'LONGITUDE')),latitude:num(value(r,'LATITUDE')),area:text(value(r,'DESC_GERENCIA_AREA_PROPOSTA'),'SEM ÁREA'),regional:text(value(r,'GER_REGIONAL')),uf:text(value(r,'UF_POLO')),municipalityCode:normalizeMunicipalityCode(value(r,'COD_IBGE_POLO')),municipalityName:text(value(r,'NM_MUN_POLO')),source:summary.kind})).filter(p=>p.id&&p.latitude&&p.longitude);
  const geoFile=path.join(summary.path,'carteiras_unidades.geojson');
  const inlineTerritories=shouldInlineTerritories(fs.statSync(geoFile).size);
  const geo=inlineTerritories?JSON.parse(fs.readFileSync(geoFile,'utf8')):{type:'FeatureCollection',features:[]};
  const workbookUnits=unitsFromRows(sheet(book,'unidades_atendidas'));
  const units=workbookUnits.length?workbookUnits:geo.features.map((f,i)=>{const p=f.properties||{},c=geometryCenter(f.geometry);return{id:text(value(p,'DEMAND_ID'),`UNIDADE-${i}`),type:text(value(p,'TIPO_UNIDADE'),'MUNICIPIO').toUpperCase()==='DISTRITO'?'DISTRITO':'MUNICIPIO',municipalityCode:normalizeMunicipalityCode(value(p,'COD_IBGE','CD_MUN')),districtCode:text(value(p,'CD_DIST'))||undefined,municipalityName:text(value(p,'NM_MUN')),uf:text(value(p,'UF')),poleId:text(value(p,'GERENCIA_ID'))||null,population:num(value(p,'POPULACAO_UNIDADE')),stores:num(value(p,'QTD_LOJAS')),longitude:c[0],latitude:c[1],distanceKm:num(value(p,'DISTANCIA_KM'))};});
  if(!units.length)throw new Error(`Cenário ${id} sem unidades na aba unidades_atendidas.`);
  return{summary,poles,units,territories:geo};
}
export function loadCurrent(){const file=path.join(DATA_DIR,'current.json');return fs.existsSync(file)?JSON.parse(fs.readFileSync(file,'utf8')):null;}
