import { describe, expect, it } from 'vitest';
import { calculatePoleMetrics, geometryCenter, haversineKm, normalizeMunicipalityCode } from './geo.ts';

describe('geography helpers',()=>{
  it('normalizes IBGE codes',()=>{ expect(normalizeMunicipalityCode('355030')).toBe('3550300'); expect(normalizeMunicipalityCode(3550308)).toBe('3550308'); });
  it('calculates distances',()=>expect(haversineKm(-23.55,-46.63,-22.91,-43.17)).toBeGreaterThan(350));
  it('calculates geometry centers without Turf',()=>expect(geometryCenter({type:'Polygon',coordinates:[[[0,0],[4,0],[4,2],[0,2],[0,0]]]})).toEqual([2,1]));
  it('aggregates a portfolio',()=>{
    const pole={id:'p',name:'P',latitude:0,longitude:0,area:'A',source:'v3' as const};
    const unit={id:'u',type:'MUNICIPIO' as const,municipalityCode:'1',poleId:'p',population:10,stores:2,latitude:0,longitude:1,distanceKm:0};
    expect(calculatePoleMetrics(pole,[unit])).toMatchObject({municipalities:1,units:1,stores:2,population:10});
  });
});
