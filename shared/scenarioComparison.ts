import type { Pole } from './types.ts';
import { haversineKm } from './geo.ts';

export interface PoleMovement{
  current:Pole;
  proposed:Pole;
  distanceKm:number;
}

export interface AreaImpact{
  area:string;
  current:number;
  proposed:number;
  delta:number;
}

const matchClosest=(current:Pole[],proposed:Pole[])=>{
  const pairs=current.flatMap(source=>proposed.map(target=>({
    current:source,
    proposed:target,
    distanceKm:haversineKm(source.latitude,source.longitude,target.latitude,target.longitude),
  }))).sort((a,b)=>a.distanceKm-b.distanceKm||a.current.id.localeCompare(b.current.id)||a.proposed.id.localeCompare(b.proposed.id));
  const usedCurrent=new Set<string>(),usedProposed=new Set<string>();
  const matches:PoleMovement[]=[];
  for(const pair of pairs){
    if(usedCurrent.has(pair.current.id)||usedProposed.has(pair.proposed.id))continue;
    usedCurrent.add(pair.current.id);
    usedProposed.add(pair.proposed.id);
    matches.push(pair);
  }
  return matches;
};

export function matchPoleMovements(current:Pole[],proposed:Pole[]){
  const areas=[...new Set([...current,...proposed].map(pole=>pole.area))];
  const matches:PoleMovement[]=[];
  const usedCurrent=new Set<string>(),usedProposed=new Set<string>();
  for(const area of areas){
    const areaMatches=matchClosest(
      current.filter(pole=>pole.area===area),
      proposed.filter(pole=>pole.area===area),
    );
    for(const match of areaMatches){
      matches.push(match);
      usedCurrent.add(match.current.id);
      usedProposed.add(match.proposed.id);
    }
  }
  matches.push(...matchClosest(
    current.filter(pole=>!usedCurrent.has(pole.id)),
    proposed.filter(pole=>!usedProposed.has(pole.id)),
  ));
  return matches;
}

export function compareAreaCounts(current:Record<string,number>,proposed:Record<string,number>){
  return [...new Set([...Object.keys(current),...Object.keys(proposed)])]
    .sort((a,b)=>a.localeCompare(b,'pt-BR'))
    .map(area=>({area,current:current[area]||0,proposed:proposed[area]||0,delta:(proposed[area]||0)-(current[area]||0)}));
}

export function countPolesByArea(poles:{area:string}[]){
  const counts:Record<string,number>={};
  for(const pole of poles){
    const area=String(pole.area||'').trim()||'SEM ÁREA';
    counts[area]=(counts[area]||0)+1;
  }
  return counts;
}

/** Reusa o nome já existente (acentos/caixa) ou normaliza uma gerência nova. */
export function resolveAreaName(raw:string,known:string[]=[]){
  const trimmed=raw.trim().replace(/\s+/g,' ');
  if(!trimmed)return '';
  const match=known.find(area=>area.localeCompare(trimmed,'pt-BR',{sensitivity:'base'})===0);
  return match||trimmed.toUpperCase();
}
