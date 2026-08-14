export const POPULATION_BANDS = [
  { max: 10_000, label: 'Até 10 mil', color: '#fff7ec' },
  { max: 50_000, label: '10 a 50 mil', color: '#fee8c8' },
  { max: 100_000, label: '50 a 100 mil', color: '#fdbb84' },
  { max: 500_000, label: '100 a 500 mil', color: '#fc8d59' },
  { max: 1_000_000, label: '500 mil a 1 milhão', color: '#e34a33' },
  { max: Number.POSITIVE_INFINITY, label: 'Acima de 1 milhão', color: '#b30000' },
] as const;

/** Faixas usadas no card Municípios do polo selecionado. */
export const MUNICIPALITY_SIZE_BANDS = [
  { max: 30_000, label: 'Até 30 mil', color: '#fee8c8' },
  { max: 100_000, label: '30 a 100 mil', color: '#fdbb84' },
  { max: 500_000, label: '100 a 500 mil', color: '#fc8d59' },
  { max: 1_000_000, label: '500 mil a 1 milhão', color: '#e34a33' },
  { max: Number.POSITIVE_INFINITY, label: 'Acima de 1 milhão', color: '#b30000' },
] as const;

export function populationBandIndex(population:number){
  return POPULATION_BANDS.findIndex(band=>population<=band.max);
}

export function populationColor(population:number){
  return POPULATION_BANDS[populationBandIndex(population)]?.color||'#152338';
}

export function countMunicipalitiesBySize(populations:number[]){
  const bands=MUNICIPALITY_SIZE_BANDS.map(band=>({label:band.label,color:band.color,max:band.max,count:0}));
  for(const population of populations){
    const value=Number.isFinite(population)?Math.max(0,population):0;
    const index=bands.findIndex(band=>value<=band.max);
    if(index>=0)bands[index].count+=1;
  }
  return bands;
}
