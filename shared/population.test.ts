import assert from 'node:assert/strict';
import test from 'node:test';
import { populationBandIndex, populationColor, POPULATION_BANDS, countMunicipalitiesBySize, countUnitsByPopulationBand } from './population.ts';

test('classifica os limites da população municipal',()=>{
  assert.equal(populationBandIndex(10_000),0);
  assert.equal(populationBandIndex(10_001),1);
  assert.equal(populationBandIndex(50_000),1);
  assert.equal(populationBandIndex(1_000_001),5);
  assert.equal(populationColor(100_000),POPULATION_BANDS[2].color);
});

test('conta unidades nas faixas do mapa de calor nos limites',()=>{
  const bands=countUnitsByPopulationBand([0,10_000,10_001,50_000]);
  assert.equal(bands[0].count,2);
  assert.equal(bands[1].count,2);
});

test('conta municípios nas faixas do card do polo',()=>{
  const bands=countMunicipalitiesBySize([12_000,30_000,30_001,99_999,100_000,250_000,500_000,500_001,1_000_000,1_000_001]);
  assert.deepEqual(bands.map(band=>({label:band.label,count:band.count})),[
    {label:'Até 30 mil',count:2},
    {label:'30 a 100 mil',count:3},
    {label:'100 a 500 mil',count:2},
    {label:'500 mil a 1 milhão',count:2},
    {label:'Acima de 1 milhão',count:1},
  ]);
});
