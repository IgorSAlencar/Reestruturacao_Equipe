import type { Pole, PoleMetrics, TerritoryUnit } from './types.ts';
import type { Geometry } from 'geojson';

export const normalizeMunicipalityCode = (value: unknown) => {
  const digits = String(value ?? '').replace(/\D/g, '');
  return digits.length === 6 ? `${digits}0` : digits.slice(0, 7);
};

export function geometryCenter(geometry:Geometry):[number,number] {
  const points:[number,number][]=[];
  const visit=(value:unknown):void=>{
    if(!Array.isArray(value))return;
    if(value.length>=2&&typeof value[0]==='number'&&typeof value[1]==='number'){
      points.push([value[0],value[1]]);return;
    }
    value.forEach(visit);
  };
  if(geometry.type==='GeometryCollection')geometry.geometries.forEach(g=>visit('coordinates' in g?g.coordinates:[]));
  else visit(geometry.coordinates);
  if(!points.length)return [0,0];
  let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  for(const [x,y] of points){minX=Math.min(minX,x);minY=Math.min(minY,y);maxX=Math.max(maxX,x);maxY=Math.max(maxY,y);}
  return [(minX+maxX)/2,(minY+maxY)/2];
}

export function haversineKm(aLat:number,aLon:number,bLat:number,bLon:number) {
  const rad = Math.PI / 180, dLat=(bLat-aLat)*rad, dLon=(bLon-aLon)*rad;
  const x=Math.sin(dLat/2)**2+Math.cos(aLat*rad)*Math.cos(bLat*rad)*Math.sin(dLon/2)**2;
  return 6371.0088*2*Math.atan2(Math.sqrt(x),Math.sqrt(1-x));
}

export function calculatePoleMetrics(pole:Pole, units:TerritoryUnit[]):PoleMetrics {
  const owned=units.filter(u=>u.poleId===pole.id);
  const distances=owned.map(u=>haversineKm(pole.latitude,pole.longitude,u.latitude,u.longitude));
  return {
    municipalities:new Set(owned.map(u=>u.municipalityCode)).size, units:owned.length,
    stores:owned.reduce((n,u)=>n+u.stores,0), population:owned.reduce((n,u)=>n+u.population,0),
    minKm:distances.length?Math.min(...distances):0, meanKm:distances.length?distances.reduce((a,b)=>a+b,0)/distances.length:0,
    maxKm:distances.length?Math.max(...distances):0
  };
}
