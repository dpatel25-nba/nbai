/* Run with Node: node tests/test_season_engine.cjs */
const assert = require('node:assert/strict');
global.window = global;
require('../web/season-data.js');
require('../web/season-possession.js');
require('../web/season-engine.js');
const D = NBAI_SEASON_DATA, E = NBAI_SEASON;
const near = (a,b) => assert.ok(Math.abs(a-b)<1e-6,`${a} != ${b}`);
const schedule = E.makeSchedule(D,true), counts = {}, occupied = new Set();
assert.equal(schedule.length,1230);
assert.equal(schedule.filter(g=>g.provisional).length,30);
for (const game of schedule) {
  assert.notEqual(game.home,game.away);
  for (const t of [game.home,game.away]) {
    counts[t]=(counts[t]||0)+1;
    assert.ok(!occupied.has(game.date+t),'team scheduled twice on the same day');occupied.add(game.date+t);
  }
}
assert.equal(Object.keys(counts).length,30);
assert.deepEqual([...new Set(Object.values(counts))],[82]);
assert.equal(E.makeSchedule(D,false).length,1200);
assert.deepEqual(schedule.filter(g=>!g.provisional),D.schedule);
const membership = E.assignments(D);
const crowded = {players:{}};
for(let i=0;i<12;i++) crowded.players[i]={id:i,n:String(i),MPG:36-i};
const crowdedRotation=E.rotation(crowded,'TEST',Object.fromEntries(Object.keys(crowded.players).map(id=>[id,'TEST'])));
near(crowdedRotation[0].min,36); // Adding depth must not scale every starter down.
for (const t of Object.keys(D.teams)) {
  const rotation=E.rotation(D,t,membership);
  near(rotation.reduce((s,p)=>s+p.min,0),240);
  assert.ok(rotation.every(p=>p.min>0&&p.min<=48));
  assert.equal(new Set(rotation.map(p=>p.id)).size,rotation.length);
}
const first = new E.Season(D,{seed:19});
const second = new E.Season(D,{seed:19});
for(let i=0;i<20;i++) assert.deepEqual(first.next(),second.next(),'seed must replay exactly');
const jokic=Object.values(D.players).find(p=>p.n==='Nikola Jokić'||p.n==='Nikola Jokic');
assert.ok(jokic,'expected a current player for transfer test');
const moved={...membership,[jokic.id]:'WAS'};
const scenario=new E.Season(D,{seed:19,membership:moved});
assert.ok(!scenario.rosters.DEN.some(p=>p.id===jokic.id));
assert.ok(scenario.rosters.WAS.some(p=>p.id===jokic.id));
assert.ok(scenario.delta.DEN<first.delta.DEN,'removing star must change source strength');
assert.ok(scenario.delta.WAS>first.delta.WAS,'adding star must change destination strength');
const removed=new E.Season(D,{membership:{...membership,[jokic.id]:null}});
assert.ok(!Object.values(removed.rosters).flat().some(p=>p.id===jokic.id));
const tooSmall={...membership};
Object.values(D.players).filter(p=>p.team==='DEN').slice(4).forEach(p=>{tooSmall[p.id]=null;});
assert.throws(()=>new E.Season(D,{membership:tooSmall}),/at least five/);
const bench=Object.values(D.players).filter(p=>p.team==='DEN').slice(0,5);
const five={...membership};Object.values(D.players).filter(p=>p.team==='DEN').forEach(p=>{five[p.id]=null;});bench.forEach(p=>{five[p.id]='DEN';});
assert.ok(E.rotation(D,'DEN',five).every(p=>p.min===48));
// Full league/full season: validate accounting, including every overtime.
const full=new E.Season(D,{seed:202627});let otGames=0;const start=Date.now();
while(full.index<full.schedule.length) {
  const g=full.next();if(g.ot)otGames++;
  assert.notEqual(g.score.H,g.score.A);
  for(const side of ['H','A']) {
    const boxes=Object.values(g.box[side]);
    near(boxes.reduce((s,b)=>s+b.MIN,0),240+25*g.ot);
    near(boxes.reduce((s,b)=>s+b.PTS,0),g.score[side]);
    for(const b of boxes) {
      assert.ok(Object.values(b).every(v=>Number.isFinite(v)&&v>=0));
      assert.ok(b.MIN<=48+5*g.ot+1e-6);
      assert.ok(b.FGM<=b.FGA && b.FG3M<=b.FG3A && b.FTM<=b.FTA && b.FG3M<=b.FGM);
      assert.equal(b.PTS,2*b.FGM+b.FG3M+b.FTM);
      assert.equal(b.REB,b.OREB+b.DREB);
    }
  }
}
assert.ok(otGames>0,'full season should exercise overtime');
const rows=full.ranked();assert.ok(rows.every(t=>t.w+t.l===82));
assert.equal(rows.reduce((s,t)=>s+t.w,0),1230);assert.equal(rows.reduce((s,t)=>s+t.l,0),1230);
const seasonPoints=Object.values(full.players).reduce((s,p)=>s+p.PTS,0);
assert.equal(seasonPoints,rows.reduce((s,t)=>s+t.pf,0));
for (const p of Object.values(full.players)) {
  const gameBoxes=full.results.flatMap(g=>[g.box.H[p.id],g.box.A[p.id]]).filter(Boolean);
  assert.equal(p.gp,gameBoxes.filter(b=>b.MIN>0).length);
  near(p.MIN,gameBoxes.reduce((s,b)=>s+b.MIN,0));
  assert.equal(p.PTS,gameBoxes.reduce((s,b)=>s+b.PTS,0));
}
assert.equal(full.next(),null);
const exported=full.export();assert.equal(exported.completed,1230);assert.equal(exported.games.length,1230);
console.log(JSON.stringify({passed:true,games:1230,teams:30,players:Object.keys(D.players).length,otGames,seconds:(Date.now()-start)/1000,meanPoints:seasonPoints/2460}));
