import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { Pole } from './types.ts';
import { compareAreaCounts, matchPoleMovements } from './scenarioComparison.ts';

const pole=(id:string,area:string,longitude:number):Pole=>({
  id,name:id,area,longitude,latitude:0,source:'current',
});

describe('scenario comparison',()=>{
  it('calculates impacts for the union of areas',()=>{
    assert.deepEqual(compareAreaCounts({Norte:3,Sul:2},{Norte:4,Leste:1}),[
      {area:'Leste',current:0,proposed:1,delta:1},
      {area:'Norte',current:3,proposed:4,delta:1},
      {area:'Sul',current:2,proposed:0,delta:-2},
    ]);
  });

  it('matches each pole once and prioritizes its area',()=>{
    const current=[pole('C1','A',0),pole('C2','B',10)];
    const proposed=[pole('P1','A',9),pole('P2','B',1),pole('P3','B',11)];
    const matches=matchPoleMovements(current,proposed);
    assert.equal(matches.length,2);
    assert.equal(matches.find(item=>item.current.id==='C1')?.proposed.id,'P1');
    assert.equal(matches.find(item=>item.current.id==='C2')?.proposed.id,'P3');
    assert.equal(new Set(matches.map(item=>item.proposed.id)).size,matches.length);
  });
});
