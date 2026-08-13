import fs from 'node:fs';
import path from 'node:path';
import { DATA_DIR, MUNICIPALITIES_FILE } from './config.js';

const CACHE_FILE=path.join(DATA_DIR,'municipality-centers.json');
let memory=null;

const normalizeCode=(value)=>{
  const digits=String(value??'').replace(/\D/g,'');
  return digits.length===6?`${digits}0`:digits.slice(0,7);
};

const geometryCenter=(geometry)=>{
  const points=[];
  const visit=(value)=>{
    if(!Array.isArray(value))return;
    if(value.length>=2&&typeof value[0]==='number'&&typeof value[1]==='number'){
      points.push([value[0],value[1]]);
      return;
    }
    value.forEach(visit);
  };
  if(!geometry)return[0,0];
  if(geometry.type==='GeometryCollection')(geometry.geometries||[]).forEach(g=>visit(g.coordinates||[]));
  else visit(geometry.coordinates);
  if(!points.length)return[0,0];
  let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  for(const[x,y] of points){minX=Math.min(minX,x);minY=Math.min(minY,y);maxX=Math.max(maxX,x);maxY=Math.max(maxY,y);}
  return[(minX+maxX)/2,(minY+maxY)/2];
};

const writeCache=(payload)=>{
  fs.mkdirSync(DATA_DIR,{recursive:true});
  fs.writeFileSync(CACHE_FILE,JSON.stringify(payload),'utf8');
  memory=payload;
  return payload;
};

/** Grava centros a partir do banco (preferencial) após refresh do cache atual. */
export function writeMunicipalityCentersFromSql(places,meta={}){
  const normalized=(places||[])
    .map(place=>({
      code:normalizeCode(place.code),
      name:String(place.name||place.code||''),
      latitude:Number(place.latitude),
      longitude:Number(place.longitude),
    }))
    .filter(place=>place.code&&place.code!=='0000000'&&Number.isFinite(place.latitude)&&Number.isFinite(place.longitude));
  return writeCache({
    count:normalized.length,
    source:'sql',
    cachedAt:new Date().toISOString(),
    ...meta,
    places:normalized,
  });
}

const centersFromMesh=()=>{
  if(!fs.existsSync(MUNICIPALITIES_FILE))return{count:0,source:'missing',places:[],cachedAt:new Date().toISOString()};
  const geo=JSON.parse(fs.readFileSync(MUNICIPALITIES_FILE,'utf8'));
  const places=[];
  for(const feature of geo.features||[]){
    const code=normalizeCode(feature.properties?.CD_MUN||feature.properties?.COD_IBGE||feature.properties?.id);
    if(!code||code==='0000000'||!feature.geometry)continue;
    const[longitude,latitude]=geometryCenter(feature.geometry);
    places.push({
      code,
      name:String(feature.properties?.NM_MUN||feature.properties?.name||feature.properties?.description||code),
      latitude,
      longitude,
    });
  }
  return writeCache({count:places.length,source:'mesh',cachedAt:new Date().toISOString(),places});
};

export function getMunicipalityCenters(){
  if(memory)return memory;
  if(fs.existsSync(CACHE_FILE)){
    memory=JSON.parse(fs.readFileSync(CACHE_FILE,'utf8'));
    return memory;
  }
  return centersFromMesh();
}
