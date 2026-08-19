import { calculatePoleMetrics, haversineKm, normalizeMunicipalityCode, uniqueUnitsByMunicipality } from './geo.ts';
import { countUnitsByPopulationBand, populationBandIndex, POPULATION_BANDS } from './population.ts';
import type { Pole, TerritoryUnit } from './types.ts';

export type PopulationLookup = Record<string, number>;
export type SortDir = 'asc' | 'desc';
export type PoleSortKey = 'name' | 'area' | 'uf' | 'units' | 'municipalities' | 'districts' | 'stores' | 'population' | `band:${number}`;
export type UnitSortKey = 'name' | 'type' | 'parent' | 'uf' | 'ibge' | 'population' | 'stores' | 'distanceKm';

export interface PoleBandRow {
  poleId: string;
  poleName: string;
  area: string;
  uf: string;
  units: number;
  municipalities: number;
  districts: number;
  stores: number;
  population: number;
  bandCounts: number[];
}

export interface UnitDetailRow {
  id: string;
  poleId: string;
  poleName: string;
  area: string;
  type: TerritoryUnit['type'];
  name: string;
  parentMunicipality: string;
  uf: string;
  ibge: string;
  population: number;
  bandIndex: number;
  stores: number;
  distanceKm: number;
}

export interface SheetModel {
  name: string;
  headers: string[];
  rows: Array<Array<string | number>>;
}

export function effectiveUnitPopulation(unit: TerritoryUnit, ibgePopulation: PopulationLookup = {}) {
  if (unit.type === 'DISTRITO') return finitePop(unit.population);
  const code = normalizeMunicipalityCode(unit.municipalityCode);
  const ibge = ibgePopulation[code];
  if (Number.isFinite(ibge)) return finitePop(ibge);
  return finitePop(unit.population);
}

export function buildPoleBandRows(poles: Pole[], units: TerritoryUnit[], ibgePopulation: PopulationLookup = {}): PoleBandRow[] {
  return poles.map(pole => {
    const portfolio = uniqueUnitsByMunicipality(units.filter(unit => unit.poleId === pole.id), pole.id);
    const metrics = calculatePoleMetrics(pole, units);
    const populations = portfolio.map(unit => effectiveUnitPopulation(unit, ibgePopulation));
    const bands = countUnitsByPopulationBand(populations);
    return {
      poleId: pole.id,
      poleName: pole.name,
      area: pole.area || 'SEM ÁREA',
      uf: pole.uf || '',
      units: metrics.units,
      municipalities: metrics.municipalities,
      districts: metrics.districts,
      stores: metrics.stores,
      population: populations.reduce((total, value) => total + value, 0),
      bandCounts: bands.map(band => band.count),
    };
  });
}

export function buildUnitDetailRows(pole: Pole, units: TerritoryUnit[], ibgePopulation: PopulationLookup = {}): UnitDetailRow[] {
  return uniqueUnitsByMunicipality(units.filter(unit => unit.poleId === pole.id), pole.id).map(unit => {
    const population = effectiveUnitPopulation(unit, ibgePopulation);
    const ibge = normalizeMunicipalityCode(unit.municipalityCode);
    const isDistrict = unit.type === 'DISTRITO';
    return {
      id: unit.id,
      poleId: pole.id,
      poleName: pole.name,
      area: pole.area || 'SEM ÁREA',
      type: unit.type,
      name: isDistrict ? districtLabel(unit) : (unit.municipalityName || unit.id),
      parentMunicipality: isDistrict ? (unit.municipalityName || '') : '',
      uf: unit.uf || pole.uf || '',
      ibge: isDistrict ? (unit.districtCode || unit.id) : ibge,
      population,
      bandIndex: Math.max(0, populationBandIndex(population)),
      stores: unit.stores || 0,
      distanceKm: haversineKm(pole.latitude, pole.longitude, unit.latitude, unit.longitude),
    };
  });
}

export function buildUnitDetailRowsForPoles(poles: Pole[], units: TerritoryUnit[], ibgePopulation: PopulationLookup = {}, poleIds?: Set<string>) {
  return poles
    .filter(pole => !poleIds || poleIds.has(pole.id))
    .flatMap(pole => buildUnitDetailRows(pole, units, ibgePopulation));
}

export function filterPoleRows(rows: PoleBandRow[], query: string, area: string | null) {
  const q = query.trim().toLowerCase();
  return rows.filter(row => {
    if (area && row.area !== area) return false;
    if (!q) return true;
    return row.poleName.toLowerCase().includes(q)
      || row.area.toLowerCase().includes(q)
      || row.uf.toLowerCase().includes(q)
      || row.poleId.toLowerCase().includes(q);
  });
}

export function sortPoleRows(rows: PoleBandRow[], key: PoleSortKey, dir: SortDir) {
  const mul = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const compared = comparePoleValue(a, b, key) * mul;
    if (compared) return compared;
    return a.poleName.localeCompare(b.poleName, 'pt-BR') || a.poleId.localeCompare(b.poleId);
  });
}

export function filterUnitRows(rows: UnitDetailRow[], query: string) {
  const q = query.trim().toLowerCase();
  if (!q) return rows;
  return rows.filter(row =>
    row.name.toLowerCase().includes(q)
    || row.parentMunicipality.toLowerCase().includes(q)
    || row.uf.toLowerCase().includes(q)
    || row.ibge.toLowerCase().includes(q)
    || row.type.toLowerCase().includes(q)
    || row.id.toLowerCase().includes(q),
  );
}

export function sortUnitRows(rows: UnitDetailRow[], key: UnitSortKey, dir: SortDir) {
  const mul = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const compared = compareUnitValue(a, b, key) * mul;
    if (compared) return compared;
    return a.name.localeCompare(b.name, 'pt-BR') || a.id.localeCompare(b.id);
  });
}

export function summarizePoleRows(rows: PoleBandRow[]) {
  const bandCounts = POPULATION_BANDS.map(() => 0);
  let units = 0, municipalities = 0, districts = 0, stores = 0, population = 0;
  for (const row of rows) {
    units += row.units;
    municipalities += row.municipalities;
    districts += row.districts;
    stores += row.stores;
    population += row.population;
    row.bandCounts.forEach((count, index) => { bandCounts[index] += count; });
  }
  return { poles: rows.length, units, municipalities, districts, stores, population, bandCounts };
}

export function areaWorkbookSheets(poleRows: PoleBandRow[], unitRows: UnitDetailRow[]): SheetModel[] {
  const bandHeaders = POPULATION_BANDS.map(band => band.label);
  return [
    {
      name: 'Polos',
      headers: ['Polo', 'Gerência de área', 'UF', 'Unidades', 'Municípios', 'Distritos', 'Lojas', 'População', ...bandHeaders],
      rows: poleRows.map(row => [
        row.poleName, row.area, row.uf, row.units, row.municipalities, row.districts, row.stores, row.population, ...row.bandCounts,
      ]),
    },
    {
      name: 'Unidades',
      headers: ['Polo', 'Gerência de área', 'Tipo', 'Nome', 'Município', 'UF', 'IBGE', 'População', 'Faixa', 'Lojas', 'Distância (km)'],
      rows: unitRows.map(row => [
        row.poleName,
        row.area,
        row.type === 'DISTRITO' ? 'Distrito' : 'Município',
        row.name,
        row.parentMunicipality || (row.type === 'MUNICIPIO' ? row.name : ''),
        row.uf,
        row.ibge,
        row.population,
        POPULATION_BANDS[row.bandIndex]?.label || '',
        row.stores,
        Math.round(row.distanceKm * 10) / 10,
      ]),
    },
  ];
}

function finitePop(value: unknown) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(0, n) : 0;
}

function districtLabel(unit: TerritoryUnit) {
  if (unit.districtCode) return `Distrito ${unit.districtCode}`;
  return unit.municipalityName ? `${unit.municipalityName} (distrito)` : unit.id;
}

function comparePoleValue(a: PoleBandRow, b: PoleBandRow, key: PoleSortKey) {
  if (key.startsWith('band:')) {
    const index = Number(key.slice(5));
    return (a.bandCounts[index] || 0) - (b.bandCounts[index] || 0);
  }
  switch (key) {
    case 'name': return a.poleName.localeCompare(b.poleName, 'pt-BR');
    case 'area': return a.area.localeCompare(b.area, 'pt-BR');
    case 'uf': return a.uf.localeCompare(b.uf, 'pt-BR');
    case 'units': return a.units - b.units;
    case 'municipalities': return a.municipalities - b.municipalities;
    case 'districts': return a.districts - b.districts;
    case 'stores': return a.stores - b.stores;
    case 'population': return a.population - b.population;
    default: return 0;
  }
}

function compareUnitValue(a: UnitDetailRow, b: UnitDetailRow, key: UnitSortKey) {
  switch (key) {
    case 'name': return a.name.localeCompare(b.name, 'pt-BR');
    case 'type': return a.type.localeCompare(b.type, 'pt-BR');
    case 'parent': return a.parentMunicipality.localeCompare(b.parentMunicipality, 'pt-BR');
    case 'uf': return a.uf.localeCompare(b.uf, 'pt-BR');
    case 'ibge': return a.ibge.localeCompare(b.ibge, 'pt-BR');
    case 'population': return a.population - b.population;
    case 'stores': return a.stores - b.stores;
    case 'distanceKm': return a.distanceKm - b.distanceKm;
    default: return 0;
  }
}
