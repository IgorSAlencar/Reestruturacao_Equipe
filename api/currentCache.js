import fs from 'node:fs/promises';
import path from 'node:path';
import { DATA_DIR, MUNICIPALITIES_FILE, ROOT } from './config.js';
import { getSqlPool, sql } from './sqlServer.js';

const UF_BY_PREFIX = {
  11: 'RO', 12: 'AC', 13: 'AM', 14: 'RR', 15: 'PA', 16: 'AP', 17: 'TO',
  21: 'MA', 22: 'PI', 23: 'CE', 24: 'RN', 25: 'PB', 26: 'PE', 27: 'AL',
  28: 'SE', 29: 'BA', 31: 'MG', 32: 'ES', 33: 'RJ', 35: 'SP', 41: 'PR',
  42: 'SC', 43: 'RS', 50: 'MS', 51: 'MT', 52: 'GO', 53: 'DF',
};

const digits = (value) => String(value ?? '').replace(/\D/g, '');
const text = (value) => String(value ?? '').trim();
const number = (value) => {
  const parsed = Number(String(value ?? '').replace(',', '.'));
  return Number.isFinite(parsed) ? parsed : null;
};
const validCoordinates = (latitude, longitude) => latitude >= -35.5 && latitude <= 6.5 && longitude >= -75.5 && longitude <= -32;
const populationCacheFile = path.join(DATA_DIR, 'population.json');

function normalizeMunicipalityCode(value, validCodes, sixDigitCodes) {
  const code = digits(value);
  if (code.length >= 7 && validCodes.has(code.slice(0, 7))) return code.slice(0, 7);
  if (code.length === 6) return sixDigitCodes.get(code) || null;
  return null;
}

async function readQuery(filename) {
  return (await fs.readFile(path.join(ROOT, 'sql', filename), 'utf8')).replace(/^\uFEFF/, '');
}

async function query(connection, filename, configure) {
  const request = connection.request();
  configure?.(request);
  const statement = (await readQuery(filename)).replaceAll(':periodo', '@periodo');
  return (await request.query(statement)).recordset;
}

function populationPayload(rows, stale = false) {
  const values = {};
  let censusYear = null;
  for (const row of rows) {
    const code = digits(row.COD_UN_REG).padStart(7, '0').slice(0, 7);
    const value = number(row.POPULACAO);
    const year = number(row.DATA_CENSO);
    if (code && value !== null) values[code] = Math.max(0, Math.round(value));
    if (year !== null) censusYear = Math.max(censusYear || 0, year);
  }
  return {
    source: 'IBGE.dbo.IBGE_POP',
    censusYear,
    count: Object.keys(values).length,
    values,
    cachedAt: new Date().toISOString(),
    stale,
  };
}

async function writePopulationCache(payload) {
  await fs.mkdir(DATA_DIR, { recursive: true });
  const temporaryFile = `${populationCacheFile}.tmp`;
  await fs.writeFile(temporaryFile, JSON.stringify(payload), 'utf8');
  await fs.rename(temporaryFile, populationCacheFile);
}

export async function loadPopulation() {
  try {
    const connection = await getSqlPool();
    const payload = populationPayload(await query(connection, 'POPULACAO.sql'));
    await writePopulationCache(payload);
    return payload;
  } catch (error) {
    try {
      const cached = JSON.parse(await fs.readFile(populationCacheFile, 'utf8'));
      return { ...cached, stale: true };
    } catch {
      throw error;
    }
  }
}

export async function refreshCurrentCache() {
  const connection = await getSqlPool();
  const period = Number(process.env.PERIODO_LOJAS || 202607);
  const [municipalities, population, stores, currentPoles, hierarchy, geometry] = await Promise.all([
    query(connection, 'COORDENADAS_MUNICIPIOS.sql'),
    query(connection, 'POPULACAO.sql'),
    query(connection, 'LOJAS.sql', (request) => request.input('periodo', sql.Int, period)),
    query(connection, 'POLOS_ATUAIS.sql'),
    query(connection, 'HIERARQUIA_ATUAL.sql'),
    fs.readFile(MUNICIPALITIES_FILE, 'utf8').then(JSON.parse),
  ]);

  const names = new Map();
  for (const feature of geometry.features || []) {
    const code = digits(feature.properties?.id || feature.properties?.CD_MUN || feature.properties?.COD_IBGE).slice(0, 7);
    if (code) names.set(code, text(feature.properties?.name || feature.properties?.NM_MUN || feature.properties?.description));
  }

  const coordinates = new Map();
  for (const row of municipalities) {
    const code = digits(row.CODIGO_IBGE).padStart(7, '0').slice(0, 7);
    const latitude = number(row.LATITUDE);
    const longitude = number(row.LONGITUDE);
    if (code && latitude !== null && longitude !== null && validCoordinates(latitude, longitude)) {
      coordinates.set(code, { latitude, longitude });
    }
  }
  const validCodes = new Set(coordinates.keys());
  const sixDigitCodes = new Map([...validCodes].map((code) => [code.slice(0, 6), code]));

  const populations = new Map();
  for (const row of population) {
    const code = digits(row.COD_UN_REG).padStart(7, '0').slice(0, 7);
    const value = number(row.POPULACAO);
    if (code && value !== null) populations.set(code, value);
  }

  const hierarchyByPole = new Map();
  for (const row of hierarchy) {
    const poleId = text(row.CHAVE_SUPERVISAO);
    if (!poleId || hierarchyByPole.has(poleId)) continue;
    hierarchyByPole.set(poleId, {
      area: text(row.DESC_GERENCIA_AREA).toUpperCase() || 'SEM ÁREA',
      name: text(row.DESC_SUPERVISAO) || poleId,
    });
  }

  const poles = [];
  for (const row of currentPoles) {
    const id = text(row.CHAVE_SUPERVISAO);
    const latitude = number(row.LAT);
    const longitude = number(row.LON);
    if (!id || latitude === null || longitude === null || !validCoordinates(latitude, longitude)) continue;
    const details = hierarchyByPole.get(id) || { area: 'SEM ÁREA', name: id };
    if (!poles.some((pole) => pole.id === id)) poles.push({ id, name: details.name, longitude, latitude, area: details.area, source: 'current' });
  }

  const poleIds = new Set(poles.map((pole) => pole.id));
  const groupedStores = new Map();
  const seenStores = new Set();
  for (const row of stores) {
    const storeId = text(row.CHAVE_LOJA);
    const poleId = text(row.CHAVE_SUPERVISAO);
    const code = normalizeMunicipalityCode(row.CD_MUNIC, validCodes, sixDigitCodes);
    if (!storeId || seenStores.has(storeId) || !code || !poleId || !poleIds.has(poleId)) continue;
    seenStores.add(storeId);
    const key = `${poleId}:${code}`;
    groupedStores.set(key, (groupedStores.get(key) || 0) + 1);
  }

  const units = [...groupedStores].map(([key, storeCount]) => {
    const separator = key.indexOf(':');
    const poleId = key.slice(0, separator);
    const code = key.slice(separator + 1);
    const location = coordinates.get(code);
    return {
      id: `MUN-${code}-${poleId}`,
      type: 'MUNICIPIO',
      municipalityCode: code,
      municipalityName: names.get(code) || code,
      uf: UF_BY_PREFIX[code.slice(0, 2)] || '',
      poleId,
      population: populations.get(code) || 0,
      stores: storeCount,
      latitude: location.latitude,
      longitude: location.longitude,
      distanceKm: 0,
    };
  });

  const areaCounts = {};
  for (const pole of poles) areaCounts[pole.area] = (areaCounts[pole.area] || 0) + 1;
  const refreshedAt = new Date().toISOString();
  const payload = {
    summary: {
      id: 'current', name: 'Atual — lojas ativas', kind: 'current', version: `PERIODO_${period}`,
      createdAt: refreshedAt, poleCount: poles.length, areaCounts, warnings: [],
    },
    poles,
    units,
    territories: { type: 'FeatureCollection', features: [] },
    refreshedAt,
  };

  await fs.mkdir(DATA_DIR, { recursive: true });
  await writePopulationCache(populationPayload(population));
  const temporaryFile = path.join(DATA_DIR, 'current.json.tmp');
  await fs.writeFile(temporaryFile, JSON.stringify(payload), 'utf8');
  await fs.rename(temporaryFile, path.join(DATA_DIR, 'current.json'));
  return { polos: poles.length, unidades: units.length, lojas: seenStores.size };
}
