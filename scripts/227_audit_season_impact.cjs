/* Full-league repeated-seed diagnostics; this is a model behavior audit, not an accuracy backtest.
 * node scripts/227_audit_season_impact.cjs [seeds=30] [before-payload.js]
 */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
global.window=global;
require('../web/season-data.js');require('../web/season-possession.js');require('../web/season-engine.js');
const D=NBAI_SEASON_DATA,E=NBAI_SEASON,n=Number(process.argv[2]||30);
assert.ok(Number.isInteger(n)&&n>=2);
const h=Object.values(D.players).find(p=>p.n==='Tyrese Haliburton');
const original=E.assignments(D),moved={...original,[h.id]:'NYK'};
const definitions=[{name:'fixed_original',data:D,membership:original},{name:'fixed_transfer',data:D,membership:moved}];
if(process.argv[3]) {
 const context={window:{}};vm.runInNewContext(fs.readFileSync(process.argv[3],'utf8'),context);
 const old=context.window.NBAI_SEASON_DATA;
 definitions.unshift({name:'before_original',data:old,membership:E.assignments(old)});
}
const changed=[];
if(definitions[0].name==='before_original')for(const p of Object.values(D.players)){
 const old=definitions[0].data.players[p.id];
 if(old.bpm!==p.bpm)changed.push({id:p.id,name:p.n,team:p.team,before:old.bpm,after:p.bpm,source:p.impactSeason});
}
const caches=new Map(definitions.map(d=>[d.name,new Map()])),runs=[];
for(let i=0;i<n;i++) {
 const seed=202627+i,run={seed};
 for(const def of definitions){
  const season=new E.Season(def.data,{seed,membership:def.membership});
  // Matchup calibration is seed-independent and fixed for this roster/payload.
  season.calibrated=caches.get(def.name);
  while(season.index<season.schedule.length)season.next();
  assert.equal(season.results.length,1230);
  assert.ok(season.ranked().every(t=>t.w+t.l===82));
  run[def.name]={standings:season.ranked(),delta:{IND:season.delta.IND,NYK:season.delta.NYK}};
 }
 runs.push(run);
}
const stats=xs=>{const mean=xs.reduce((a,b)=>a+b,0)/xs.length;return {mean,min:Math.min(...xs),max:Math.max(...xs),standardError:Math.sqrt(xs.reduce((s,x)=>s+(x-mean)**2,0)/(xs.length-1)/xs.length)};};
const team=(r,name,t)=>r[name].standings.find(x=>x.team===t);
const report={seeds:n,gamesSimulated:n*definitions.length*1230,teams:30,changedPlayers:changed,impactPolicy:D.impactPolicy,teamsAffected:{},league:[]};
for(const t of Object.keys(D.teams)){
 const entry={team:t};
 for(const def of definitions)entry[def.name]=stats(runs.map(r=>team(r,def.name,t).w));
 entry.transferWinDifference=stats(runs.map(r=>team(r,'fixed_transfer',t).w-team(r,'fixed_original',t).w));
 report.league.push(entry);
}
for(const t of ['IND','NYK'])report.teamsAffected[t]={...report.league.find(r=>r.team===t),
 targetStrengthChange:runs[0].fixed_transfer.delta[t]-runs[0].fixed_original.delta[t],
 transferPointDifferentialChange:stats(runs.map(r=>{const a=team(r,'fixed_transfer',t),b=team(r,'fixed_original',t);return ((a.pf-a.pa)-(b.pf-b.pa))/82;})),
 seedsWithMoreWinsAfterTransfer:runs.filter(r=>team(r,'fixed_transfer',t).w>team(r,'fixed_original',t).w).length};
console.log(JSON.stringify(report,null,2));
