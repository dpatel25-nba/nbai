'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
global.window=global;
require('../web/season-engine.js');
const E=NBAI_SEASON;
function data(weights){return {players:Object.fromEntries(weights.map((MPG,i)=>[i+1,{id:i+1,n:`Player ${i+1}`,team:'A',MPG}]))};}
function check(d,team='A',excluded=new Set()){
 const selected=Object.values(d.players).filter(p=>p.team===team&&!excluded.has(String(p.id)))
  .sort((a,b)=>b.MPG-a.MPG||a.id-b.id).slice(0,12);
 const before=JSON.stringify(d),rotation=E.rotation(d,team,E.assignments(d),excluded);
 assert.equal(JSON.stringify(d),before);
 assert.deepEqual(rotation.map(p=>p.id),selected.map(p=>p.id));
 assert(rotation.every(p=>Number.isFinite(p.min)&&p.min>0&&p.min<=48+1e-9&&!excluded.has(String(p.id))));
 assert(Math.abs(rotation.reduce((s,p)=>s+p.min,0)-240)<1e-8);
 // Independent scalar bisection, rather than the production active-set method.
 const weights=selected.map(p=>Math.max(1,Math.min(48,p.MPG)));
 let lo=0,hi=240;
 for(let i=0;i<150;i++){
  const mid=(lo+hi)/2,total=weights.reduce((s,w)=>s+Math.min(48,mid*w),0);
  if(total<240)lo=mid;else hi=mid;
 }
 for(let i=0;i<rotation.length;i++)assert(Math.abs(rotation[i].min-Math.min(48,((lo+hi)/2)*weights[i]))<1e-8);
 return rotation;
}

test('Every selected player receives minutes even when raw weights exceed240',()=>{
 const r=check(data(Array(12).fill(30)));
 assert.equal(r.length,12);assert(r.every(p=>Math.abs(p.min-20)<1e-10));
});

test('Five-player emergency rotation caps each at48 and still totals240',()=>{
 assert(check(data([100,40,10,1,0])).every(p=>Math.abs(p.min-48)<1e-10));
});

test('Clamped weights redistribute both overfull and underfull budgets',()=>{
 for(const weights of [[100,48,20,10,2,1],[1,2,3,4,5,6],[0,0,0,0,0,0],[48,1,1,1,1,1]])check(data(weights));
});

test('Exclusions and fixed top12 support remain unchanged',()=>{
 const d=data(Array.from({length:16},(_,i)=>40-i));
 const excluded=new Set(['1','3','16']);
 const r=check(d,'A',excluded);assert.equal(r.length,12);
 assert.deepEqual([...excluded],['1','3','16']);
 assert(!r.some(p=>p.id===15));
});

test('Finite identities and MPG required; insufficient eligible players fail closed',()=>{
 for(const value of [NaN,Infinity,-Infinity,-1,'30',undefined]){
  const d=data([30,30,30,30,30,30]);d.players[1].MPG=value;
  assert.throws(()=>E.rotation(d,'A',E.assignments(d)),/finite nonnegative/);
 }
 for(const value of [0,-1,1.1,Number.MAX_SAFE_INTEGER+1]){
  const d=data([30,30,30,30,30]);d.players[1].id=value;
  assert.throws(()=>E.rotation(d,'A',E.assignments(d)),/unique player IDs/);
 }
 const duplicate=data([30,30,30,30,30]);duplicate.players.extra={...duplicate.players[1]};
 assert.throws(()=>E.rotation(duplicate,'A',E.assignments(duplicate)),/unique player IDs/);
 const d=data([30,30,30,30,30]);assert.throws(()=>E.rotation(d,'A',E.assignments(d),new Set(['1'])),/at least five/);
});

test('Tie ordering and result are deterministic under source insertion reorder',()=>{
 const d=data(Array(14).fill(25));
 const reordered={players:Object.fromEntries(Object.values(d.players).reverse().map(p=>['player-'+p.id,p]))};
 assert.deepEqual(check(d),check(reordered));
 assert.deepEqual(check(d),check(d));
});

test('Two hundred deterministic populations agree with independent capped solution',()=>{
 let state=12345;const random=()=>{state=(Math.imul(state,1664525)+1013904223)>>>0;return state/2**32;};
 for(let i=0;i<200;i++)check(data(Array.from({length:5+Math.floor(random()*20)},()=>random()*70)));
});

test('All30 current teams and every reported-Out event retain support, budget and caps',()=>{
 require('../web/season-data.js');
 const actual=NBAI_SEASON_DATA,availability=require('../web/game-availability.json');
 assert.equal(Object.keys(actual.teams).length,30);
 for(const team of Object.keys(actual.teams))check(actual,team);
 for(const event of availability.events){
  const excluded=new Set(event.players.filter(p=>p.out).map(p=>String(p.id)));
  for(const team of [event.home,event.away])check(actual,team,excluded);
 }
});
