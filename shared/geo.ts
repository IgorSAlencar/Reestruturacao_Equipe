import type { Pole, PoleMetrics, TerritoryUnit } from './types.ts';
import type { Feature, Geometry, Polygon } from 'geojson';

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
  // Um município pode existir em mais de uma unidade (legado do cache por polo).
  // Métricas e carteira devem contar cada CD_MUN uma vez.
  const byCode=new Map<string,TerritoryUnit>();
  const districts:TerritoryUnit[]=[];
  for(const unit of owned){
    if(unit.type==='DISTRITO'){districts.push(unit);continue;}
    const code=normalizeMunicipalityCode(unit.municipalityCode);
    const current=byCode.get(code);
    if(!current)byCode.set(code,{...unit,municipalityCode:code});
    else{
      current.stores=(current.stores||0)+(unit.stores||0);
      current.population=Math.max(current.population||0,unit.population||0);
    }
  }
  const municipalities=[...byCode.values()];
  const representative=[...municipalities,...districts];
  const distances=representative.map(u=>haversineKm(pole.latitude,pole.longitude,u.latitude,u.longitude));
  return {
    municipalities:municipalities.length,
    units:representative.length,
    districts:districts.length,
    stores:representative.reduce((n,u)=>n+(u.stores||0),0),
    population:representative.reduce((n,u)=>n+(u.population||0),0),
    minKm:distances.length?Math.min(...distances):0,
    meanKm:distances.length?distances.reduce((a,b)=>a+b,0)/distances.length:0,
    maxKm:distances.length?Math.max(...distances):0,
  };
}

/** Preferência: já no polo alvo > mais lojas > id estável. */
export function pickPreferredUnit(units:TerritoryUnit[],preferredPoleId?:string|null){
  return [...units].sort((a,b)=>{
    const aPreferred=preferredPoleId&&a.poleId===preferredPoleId?1:0;
    const bPreferred=preferredPoleId&&b.poleId===preferredPoleId?1:0;
    if(aPreferred!==bPreferred)return bPreferred-aPreferred;
    if((b.stores||0)!==(a.stores||0))return (b.stores||0)-(a.stores||0);
    return String(a.id).localeCompare(String(b.id));
  })[0];
}

/** Uma unidade por município (DISTRITO permanece separado). */
export function uniqueUnitsByMunicipality(units:TerritoryUnit[],preferredPoleId?:string|null){
  const districts=units.filter(u=>u.type==='DISTRITO');
  const byCode=new Map<string,TerritoryUnit[]>();
  for(const unit of units){
    if(unit.type==='DISTRITO')continue;
    const code=normalizeMunicipalityCode(unit.municipalityCode);
    if(!code)continue;
    byCode.set(code,[...(byCode.get(code)||[]),unit]);
  }
  return [...[...byCode.values()].map(group=>pickPreferredUnit(group,preferredPoleId)).filter(Boolean) as TerritoryUnit[],...districts];
}

/** Após atribuir: funde municípios duplicados no mesmo polo. */
export function mergeDuplicateMunicipalities(units:TerritoryUnit[],poleId:string){
  const kept:TerritoryUnit[]=[];
  const groups=new Map<string,TerritoryUnit[]>();
  for(const unit of units){
    if(unit.type==='DISTRITO'||unit.poleId!==poleId){kept.push(unit);continue;}
    const code=normalizeMunicipalityCode(unit.municipalityCode);
    groups.set(code,[...(groups.get(code)||[]),unit]);
  }
  for(const [,group] of groups){
    if(group.length===1){kept.push(group[0]);continue;}
    const primary=pickPreferredUnit(group,poleId)!;
    kept.push({
      ...primary,
      municipalityCode:normalizeMunicipalityCode(primary.municipalityCode),
      stores:group.reduce((n,u)=>n+(u.stores||0),0),
      population:Math.max(...group.map(u=>u.population||0)),
      poleId,
    });
  }
  return kept;
}

export type MunicipalityPlace={code:string;name?:string;latitude:number;longitude:number};

/**
 * Municípios dentro do raio, crescendo de forma contígua a partir do centro (polo).
 * Usa o município mais próximo do centro como semente e só inclui vizinhos
 * alcançáveis por saltos curtos entre sedes — evita “ilhas” soltas no círculo.
 */
export function contiguousWithinRadius(
  centerLat:number,
  centerLon:number,
  radiusKm:number,
  places:MunicipalityPlace[],
):MunicipalityPlace[]{
  if(radiusKm<=0||!places.length)return [];
  const within=places
    .map(place=>{
      const code=normalizeMunicipalityCode(place.code);
      return{
        code,
        name:place.name,
        latitude:place.latitude,
        longitude:place.longitude,
        dist:haversineKm(centerLat,centerLon,place.latitude,place.longitude),
      };
    })
    .filter(place=>place.code&&place.code!=='0000000'&&place.dist<=radiusKm)
    .sort((a,b)=>a.dist-b.dist||a.code.localeCompare(b.code));
  if(!within.length)return [];

  const nn:number[]=[];
  for(let i=0;i<within.length;i++){
    let best=Infinity;
    for(let j=0;j<within.length;j++){
      if(i===j)continue;
      const d=haversineKm(within[i].latitude,within[i].longitude,within[j].latitude,within[j].longitude);
      if(d<best)best=d;
    }
    if(Number.isFinite(best))nn.push(best);
  }
  nn.sort((a,b)=>a-b);
  const p75=nn.length?nn[Math.min(nn.length-1,Math.floor(nn.length*0.75))]!:25;
  // Elo entre vizinhos: cobre sedes de municípios limítrofes sem pular para ilhas distantes.
  const linkKm=Math.min(Math.max(20,p75*2.5),Math.max(35,radiusKm*0.45));

  const selected=new Set<string>([within[0].code]);
  const queue=[within[0].code];
  const byCode=new Map(within.map(place=>[place.code,place]));
  while(queue.length){
    const current=byCode.get(queue.shift()!);
    if(!current)continue;
    for(const candidate of within){
      if(selected.has(candidate.code))continue;
      if(haversineKm(current.latitude,current.longitude,candidate.latitude,candidate.longitude)<=linkKm){
        selected.add(candidate.code);
        queue.push(candidate.code);
      }
    }
  }
  return within
    .filter(place=>selected.has(place.code))
    .map(({code,name,latitude,longitude})=>({code,name,latitude,longitude}));
}

/** Polígono aproximado de um círculo geodésico (raio em km). */
export function circlePolygon(longitude:number,latitude:number,radiusKm:number,steps=64):Feature<Polygon>{
  const R=6371.0088;
  const coords:[number,number][]=[];
  const φ1=latitude*Math.PI/180;
  const λ1=longitude*Math.PI/180;
  const δ=Math.max(0,radiusKm)/R;
  for(let i=0;i<=steps;i++){
    const θ=(i/steps)*2*Math.PI;
    const φ2=Math.asin(Math.sin(φ1)*Math.cos(δ)+Math.cos(φ1)*Math.sin(δ)*Math.cos(θ));
    const λ2=λ1+Math.atan2(Math.sin(θ)*Math.sin(δ)*Math.cos(φ1),Math.cos(δ)-Math.sin(φ1)*Math.sin(φ2));
    coords.push([λ2*180/Math.PI,φ2*180/Math.PI]);
  }
  return{type:'Feature',properties:{radiusKm},geometry:{type:'Polygon',coordinates:[coords]}};
}
