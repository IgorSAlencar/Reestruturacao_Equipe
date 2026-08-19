export const POPULATION_BANDS = [
  { max: 30_000, label: 'Até 30 mil', color: '#fee8c8' },
  { max: 100_000, label: '30 a 100 mil', color: '#fdbb84' },
  { max: 500_000, label: '100 a 500 mil', color: '#fc8d59' },
  { max: 1_000_000, label: '500 mil a 1 milhão', color: '#e34a33' },
  { max: Number.POSITIVE_INFINITY, label: 'Acima de 1 milhão', color: '#b30000' },
] as const;

/** Mesma escala do mapa de calor, do painel e do card Municípios. */
export const MUNICIPALITY_SIZE_BANDS = POPULATION_BANDS;

export function populationBandIndex(population:number){
  return POPULATION_BANDS.findIndex(band=>population<=band.max);
}

export function populationColor(population:number){
  return POPULATION_BANDS[populationBandIndex(population)]?.color||'#152338';
}

/** Texto legível sobre a cor da faixa do mapa de calor. */
export function populationBandInk(color:string){
  const hex=color.replace('#','');
  if(hex.length<6)return '#1a1208';
  const r=parseInt(hex.slice(0,2),16);
  const g=parseInt(hex.slice(2,4),16);
  const b=parseInt(hex.slice(4,6),16);
  const luminance=(0.2126*r+0.7152*g+0.0722*b)/255;
  return luminance>0.55?'#1a1208':'#fff8f0';
}

export function countUnitsByPopulationBand(populations:number[]){
  const bands=POPULATION_BANDS.map(band=>({label:band.label,color:band.color,max:band.max,count:0}));
  for(const population of populations){
    const value=Number.isFinite(population)?Math.max(0,population):0;
    const index=bands.findIndex(band=>value<=band.max);
    if(index>=0)bands[index].count+=1;
  }
  return bands;
}

export function countMunicipalitiesBySize(populations:number[]){
  return countUnitsByPopulationBand(populations);
}
