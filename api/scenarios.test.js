import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { MAX_INLINE_TERRITORIES_BYTES, shouldInlineTerritories, unitsFromRows } from './scenarios.js';

describe('scenario loading',()=>{
  it('does not inline oversized territory GeoJSON',()=>{
    assert.equal(shouldInlineTerritories(MAX_INLINE_TERRITORIES_BYTES),true);
    assert.equal(shouldInlineTerritories(MAX_INLINE_TERRITORIES_BYTES+1),false);
  });

  it('builds lightweight units from workbook rows',()=>{
    const [unit]=unitsFromRows([{
      DEMAND_ID:'DIST-1',GERENCIA_ID:'V5-G001',TIPO_UNIDADE:'DISTRITO',
      COD_IBGE:3550308,CD_DIST:355030801,NM_MUN:'São Paulo',UF:'SP',
      POPULACAO_UNIDADE:123456,QTD_LOJAS:42,LATITUDE:-23.55,
      LONGITUDE:-46.63,DISTANCIA_KM:12.5,
    }]);
    assert.deepEqual(unit,{
      id:'DIST-1',type:'DISTRITO',municipalityCode:'3550308',
      districtCode:'355030801',municipalityName:'São Paulo',uf:'SP',
      poleId:'V5-G001',population:123456,stores:42,latitude:-23.55,
      longitude:-46.63,distanceKm:12.5,
    });
  });
});
