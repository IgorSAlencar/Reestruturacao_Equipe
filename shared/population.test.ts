import assert from 'node:assert/strict';
import test from 'node:test';
import { populationBandIndex, populationColor, POPULATION_BANDS } from './population.ts';

test('classifica os limites da população municipal',()=>{
  assert.equal(populationBandIndex(10_000),0);
  assert.equal(populationBandIndex(10_001),1);
  assert.equal(populationBandIndex(50_000),1);
  assert.equal(populationBandIndex(1_000_001),5);
  assert.equal(populationColor(100_000),POPULATION_BANDS[2].color);
});
