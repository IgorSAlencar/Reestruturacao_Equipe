import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import type { Feature, FeatureCollection, Geometry } from 'geojson';
import { DATA_DIR, ROOT, V3_DIR, V4_DIR } from './config.ts';
import type { Pole, ScenarioData, ScenarioKind, ScenarioSummary, TerritoryUnit } from '../shared/types.ts';
import { geometryCenter, normalizeMunicipalityCode } from '../shared/geo.ts';

type RecordRow=Record<string,unknown>;
const value=(r:RecordRow,...keys:string[])=>keys.map(k=>r[k]).find(v=>v!==undefined&&v!==null&&v!=='');
const num=(v:unknown)=>Number.isFinite(Number(v))?Number(v):0;
const text=(v:unknown,fallback='')=>String(v??fallback).trim();

function findWorkbook(folder:string){ return fs.readdirSync(folder).find(n=>/^resultado_.*\.xlsx$/i.test(n)); }
function folders(root:string){
  if(!fs.existsSync(root))return [];
  return fs.readdirSync(root,{withFileTypes:true}).filter(x=>x.isDirectory()).map(x=>path.join(root,x.name)).filter(f=>findWorkbook(f)&&fs.existsSync(path.join(f,'carteiras_unidades.geojson')));
}
type WorkbookData=Record<string,RecordRow[]>;
const workbookCache=new Map<string,{mtime:number;data:WorkbookData}>();
function readWorkbook(file:string):WorkbookData {
  const mtime=fs.statSync(file).mtimeMs,cached=workbookCache.get(file);if(cached?.mtime===mtime)return cached.data;
  const output=execFileSync(process.env.PYTHON_BIN||'python',[path.join(ROOT,'utils','read_scenario_workbook.py'),file],{cwd:ROOT,encoding:'utf8',maxBuffer:20*1024*1024});
  const data=JSON.parse(output) as WorkbookData;workbookCache.set(file,{mtime,data});return data;
}
function sheet(book:WorkbookData,name:string):RecordRow[]{return book[name]||[];}
function detectKind(folder:string,book:WorkbookData):ScenarioKind {
  const rows=sheet(book,'cenario'); const version=text(rows[0]?.MODELO_VERSAO).toUpperCase();
  return version.includes('V4')||folder.toLowerCase().includes('v4')?'v4':'v3';
}
async function summaryFrom(folder:string):Promise<ScenarioSummary> {
  const wb=findWorkbook(folder)!; const book=readWorkbook(path.join(folder,wb)); const managers=sheet(book,'gerencias_propostas'); const kind=detectKind(folder,book);
  const areas:Record<string,number>={}; managers.forEach(r=>{const a=text(value(r,'DESC_GERENCIA_AREA_PROPOSTA'),'SEM ÁREA');areas[a]=(areas[a]||0)+1;});
  const scenario=sheet(book,'cenario')[0]||{}; return {id:path.basename(folder),name:path.basename(folder),kind,version:text(value(scenario,'MODELO_VERSAO'),kind.toUpperCase()),createdAt:text(value(scenario,'DATA_EXECUCAO')),path:folder,poleCount:managers.length,areaCounts:areas,warnings:[]};
}
export async function listScenarios(){ const scenarios=await Promise.all([...folders(V4_DIR),...folders(V3_DIR)].map(summaryFrom));return scenarios.sort((a,b)=>(b.createdAt||'').localeCompare(a.createdAt||'')); }

export async function loadScenario(id:string):Promise<ScenarioData|null> {
  const summary=(await listScenarios()).find(s=>s.id===id); if(!summary?.path)return null;
  const workbook=findWorkbook(summary.path)!; const book=readWorkbook(path.join(summary.path,workbook));
  const managers=sheet(book,'gerencias_propostas');
  const poles:Pole[]=managers.map(r=>({
    id:text(value(r,'GERENCIA_ID')),name:text(value(r,'NM_MUN_POLO','NM_DIST_POLO', 'GERENCIA_ID')),
    longitude:num(value(r,'LONGITUDE')),latitude:num(value(r,'LATITUDE')),area:text(value(r,'DESC_GERENCIA_AREA_PROPOSTA'),'SEM ÁREA'),
    regional:text(value(r,'GER_REGIONAL')),uf:text(value(r,'UF_POLO')),municipalityCode:normalizeMunicipalityCode(value(r,'COD_IBGE_POLO')),
    municipalityName:text(value(r,'NM_MUN_POLO')),source:summary.kind
  })).filter(p=>p.id&&p.latitude&&p.longitude);
  const geo=JSON.parse(fs.readFileSync(path.join(summary.path,'carteiras_unidades.geojson'),'utf8')) as FeatureCollection<Geometry>;
  const units:TerritoryUnit[]=geo.features.map((f:Feature<Geometry>,i)=>{
    const p=(f.properties||{}) as RecordRow; const c=geometryCenter(f.geometry);
    return {id:text(value(p,'DEMAND_ID'),`UNIDADE-${i}`),type:text(value(p,'TIPO_UNIDADE'),'MUNICIPIO').toUpperCase()==='DISTRITO'?'DISTRITO':'MUNICIPIO',
      municipalityCode:normalizeMunicipalityCode(value(p,'COD_IBGE','CD_MUN')),districtCode:text(value(p,'CD_DIST'))||undefined,
      municipalityName:text(value(p,'NM_MUN')),uf:text(value(p,'UF')),poleId:text(value(p,'GERENCIA_ID'))||null,
      population:num(value(p,'POPULACAO_UNIDADE')),stores:num(value(p,'QTD_LOJAS')),longitude:c[0],latitude:c[1],distanceKm:num(value(p,'DISTANCIA_KM'))};
  });
  return {summary,poles,units,territories:geo};
}

export function loadCurrent():ScenarioData|null {
  const file=path.join(DATA_DIR,'current.json'); return fs.existsSync(file)?JSON.parse(fs.readFileSync(file,'utf8')):null;
}
