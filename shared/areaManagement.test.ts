import assert from 'node:assert/strict';
import test from 'node:test';
import { countUnitsByPopulationBand, POPULATION_BANDS, populationBandInk } from './population.ts';
import type { Pole, TerritoryUnit } from './types.ts';
import {
  areaWorkbookSheets,
  buildPoleBandRows,
  buildUnitDetailRows,
  effectiveUnitPopulation,
  filterPoleRows,
  sortPoleRows,
  summarizePoleRows,
} from './areaManagement.ts';

const pole = (id: string, extras: Partial<Pole> = {}): Pole => ({
  id, name: extras.name || id, longitude: extras.longitude ?? 0, latitude: extras.latitude ?? 0,
  area: extras.area || 'SUL', uf: extras.uf || 'RS', source: 'v5',
});

const unit = (partial: Partial<TerritoryUnit> & Pick<TerritoryUnit, 'id' | 'type' | 'municipalityCode' | 'poleId'>): TerritoryUnit => ({
  population: 0, stores: 0, latitude: 0, longitude: 0, distanceKm: 0, ...partial,
});

test('conta unidades nas faixas do mapa de calor', () => {
  const bands = countUnitsByPopulationBand([10_000, 10_001, 50_000, 50_001, 100_000, 500_001, 1_000_001]);
  assert.deepEqual(bands.map(band => ({ label: band.label, count: band.count })), [
    { label: 'Até 30 mil', count: 2 },
    { label: '30 a 100 mil', count: 3 },
    { label: '100 a 500 mil', count: 0 },
    { label: '500 mil a 1 milhão', count: 1 },
    { label: 'Acima de 1 milhão', count: 1 },
  ]);
  assert.equal(bands.length, POPULATION_BANDS.length);
});

test('escolhe tinta contrastante nas faixas claras e escuras', () => {
  assert.equal(populationBandInk('#fee8c8'), '#1a1208');
  assert.equal(populationBandInk('#b30000'), '#fff8f0');
});

test('usa população IBGE no município e a da unidade no distrito', () => {
  const mun = unit({ id: 'MUN-3550308', type: 'MUNICIPIO', municipalityCode: '3550308', poleId: 'p', population: 99 });
  const dist = unit({ id: 'DIST-1', type: 'DISTRITO', municipalityCode: '3550308', poleId: 'p', population: 12000 });
  assert.equal(effectiveUnitPopulation(mun, { '3550308': 12_000_000 }), 12_000_000);
  assert.equal(effectiveUnitPopulation(dist, { '3550308': 12_000_000 }), 12_000);
});

test('deduplica município e mantém distrito na linha do polo', () => {
  const p = pole('p1', { name: 'Porto Alegre' });
  const rows = buildPoleBandRows([p], [
    unit({ id: 'MUN-a', type: 'MUNICIPIO', municipalityCode: '4314902', poleId: 'p1', population: 8_000, stores: 1 }),
    unit({ id: 'MUN-a2', type: 'MUNICIPIO', municipalityCode: '4314902', poleId: 'p1', population: 9_000, stores: 2 }),
    unit({ id: 'DIST-x', type: 'DISTRITO', municipalityCode: '4314902', districtCode: '431490205', poleId: 'p1', population: 80_000, stores: 3 }),
  ]);
  assert.equal(rows[0].units, 2);
  assert.equal(rows[0].municipalities, 1);
  assert.equal(rows[0].districts, 1);
  assert.equal(rows[0].stores, 6);
  assert.equal(rows[0].bandCounts[0], 1);
  assert.equal(rows[0].bandCounts[1], 1);
});

test('filtra e ordena polos por gerência e unidades', () => {
  const rows = buildPoleBandRows([
    pole('a', { name: 'Curitiba', area: 'SUL', uf: 'PR' }),
    pole('b', { name: 'Recife', area: 'NORDESTE 1', uf: 'PE' }),
  ], [
    unit({ id: 'm1', type: 'MUNICIPIO', municipalityCode: '4106902', poleId: 'a', population: 5_000 }),
    unit({ id: 'm2', type: 'MUNICIPIO', municipalityCode: '2611606', poleId: 'b', population: 4_000 }),
    unit({ id: 'm3', type: 'MUNICIPIO', municipalityCode: '2607901', poleId: 'b', population: 6_000 }),
  ]);
  const nordeste = filterPoleRows(rows, '', 'NORDESTE 1');
  assert.equal(nordeste.length, 1);
  assert.equal(nordeste[0].poleName, 'Recife');
  const byQuery = filterPoleRows(rows, 'curi', null);
  assert.equal(byQuery[0].poleName, 'Curitiba');
  const sorted = sortPoleRows(rows, 'units', 'desc');
  assert.equal(sorted[0].poleName, 'Recife');
  assert.equal(sorted[0].units, 2);
});

test('detalha município e distrito com faixa do heatmap', () => {
  const p = pole('p1', { name: 'São Paulo', latitude: -23.55, longitude: -46.63 });
  const details = buildUnitDetailRows(p, [
    unit({ id: 'MUN-3550308', type: 'MUNICIPIO', municipalityCode: '3550308', municipalityName: 'São Paulo', poleId: 'p1', population: 200_000, latitude: -23.55, longitude: -46.63 }),
    unit({ id: 'DIST-1', type: 'DISTRITO', municipalityCode: '3550308', districtCode: '355030805', municipalityName: 'São Paulo', poleId: 'p1', population: 1_200_000, latitude: -23.6, longitude: -46.7 }),
  ], { '3550308': 11_000_000 });
  assert.equal(details[0].population, 11_000_000);
  assert.equal(details[0].bandIndex, 4);
  const district = details.find(row => row.type === 'DISTRITO');
  assert.equal(district?.name, 'Distrito 355030805');
  assert.equal(district?.parentMunicipality, 'São Paulo');
  assert.equal(district?.bandIndex, 4);
});

test('resume totais visíveis e monta abas da planilha', () => {
  const rows = buildPoleBandRows([pole('a', { name: 'A' })], [
    unit({ id: 'm1', type: 'MUNICIPIO', municipalityCode: '4314902', poleId: 'a', population: 8_000 }),
  ]);
  const summary = summarizePoleRows(rows);
  assert.equal(summary.poles, 1);
  assert.equal(summary.units, 1);
  assert.equal(summary.bandCounts[0], 1);
  const sheets = areaWorkbookSheets(rows, buildUnitDetailRows(pole('a', { name: 'A' }), [
    unit({ id: 'm1', type: 'MUNICIPIO', municipalityCode: '4314902', municipalityName: 'Porto Alegre', poleId: 'a', population: 8_000 }),
  ]));
  assert.equal(sheets[0].name, 'Polos');
  assert.equal(sheets[1].name, 'Unidades');
  assert.ok(sheets[0].headers.includes('Até 30 mil'));
  assert.equal(sheets[0].rows[0][0], 'A');
  assert.equal(sheets[1].rows[0][3], 'Porto Alegre');
});
