import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { calculatePoleMetrics, contiguousWithinRadius, geometryCenter, haversineKm, normalizeMunicipalityCode } from './geo.ts';

describe('geography helpers',()=>{
  it('normalizes IBGE codes',()=>{ assert.equal(normalizeMunicipalityCode('355030'),'3550300'); assert.equal(normalizeMunicipalityCode(3550308),'3550308'); });
  it('calculates distances',()=>assert.ok(haversineKm(-23.55,-46.63,-22.91,-43.17)>350));
  it('calculates geometry centers without Turf',()=>assert.deepEqual(geometryCenter({type:'Polygon',coordinates:[[[0,0],[4,0],[4,2],[0,2],[0,0]]]}),[2,1]));
  it('aggregates a portfolio',()=>{
    const pole={id:'p',name:'P',latitude:0,longitude:0,area:'A',source:'v3' as const};
    const unit={id:'u',type:'MUNICIPIO' as const,municipalityCode:'1',poleId:'p',population:10,stores:2,latitude:0,longitude:1,distanceKm:0};
    const result=calculatePoleMetrics(pole,[unit]);
    assert.equal(result.municipalities,1);assert.equal(result.units,1);assert.equal(result.districts,0);assert.equal(result.stores,2);assert.equal(result.population,10);
  });
  it('grows contiguous municipalities from the center and skips distant islands',()=>{
    // ~11 km between neighbors on this latitude band; island is inside radius but far from the cluster.
    const places=[
      {code:'1000001',latitude:0,longitude:0},
      {code:'1000002',latitude:0,longitude:0.1},
      {code:'1000003',latitude:0,longitude:0.2},
      {code:'1000004',latitude:0,longitude:1.5}, // ~167 km — inside 200 km circle, not contiguous
    ];
    const selected=contiguousWithinRadius(0,0,200,places).map(place=>place.code).sort();
    assert.deepEqual(selected,['1000001','1000002','1000003']);
  });
  it('always includes the municipality hosting the pole',()=>{
    const places=[
      {code:'2000001',latitude:0.05,longitude:0.05},
      {code:'2000002',latitude:0.05,longitude:0.15},
    ];
    const selected=contiguousWithinRadius(0.05,0.05,50,places);
    assert.equal(selected[0]?.code,'2000001');
    assert.equal(selected.length,2);
  });
});
