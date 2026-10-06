const {test}=require('node:test'),assert=require('node:assert/strict');
const C=require('../web/consensus-analysis.js');
const now=Date.parse('2026-10-05T23:00:00Z'),event={id:'game1',home_team:'New York Knicks',away_team:'Philadelphia 76ers',commence_time:'2026-10-20T23:00:00Z'},player={id:1,name:'Jalen Brunson'};
const q={eventId:event.id,homeTeam:event.home_team,awayTeam:event.away_team,startsAt:event.commence_time,player:player.name,scope:'game',unit:'game',stat:'REB',mainLine:true,observedAt:'2026-10-05T22:58:00Z',sourceUpdatedAt:'2026-10-05T22:57:00Z'};
const data={quotes:[{...q,provider:'fanduel',line:2.5},{...q,provider:'prizepicks',line:3}]};
const forecast={eventId:event.id,startsAt:event.commence_time,players:[{id:1,name:player.name,stats:{REB:{mean:3.1,p10:1,p90:6}}}]};
test('Two-source median and difference preserve the playable source lines',()=>{
 const r=C.compare(data,player,event,'REB',forecast,now);assert.equal(r.median,2.75);assert.ok(Math.abs(r.gap-.35)<1e-8);assert.equal(r.books.fanduel.line,2.5);assert.equal(r.books.prizepicks.line,3);
});
test('A single source or ambiguous duplicate never becomes consensus',()=>{
 for(const quotes of [data.quotes.slice(0,1),[...data.quotes,data.quotes[0]]]){const r=C.compare({quotes},player,event,'REB',forecast,now);assert.equal(r.median,null);assert.equal(r.gap,null);}
});
test('No stale or future quote, started game or mismatched scope can produce a current gap',()=>{
 for(const patch of [{sourceUpdatedAt:'2020-01-01T00:00:00Z'},{observedAt:'2020-01-01T00:00:00Z'},{observedAt:'2030-01-01T00:00:00Z'},{scope:'season'},{unit:'average'},{stat:'AST'},{mainLine:false},{player:'Bruce Brown'},{homeTeam:'Boston Celtics'},{startsAt:'2026-10-21T23:00:00Z'},{line:NaN}]){
  const r=C.compare({quotes:data.quotes.map(x=>({...x,...patch}))},player,event,'REB',forecast,now);assert.equal(r.gap,null,JSON.stringify(patch));
 }
 assert.equal(C.compare(data,player,event,'REB',forecast,Date.parse('2026-10-21')).gap,null);
 assert.equal(C.compare(data,player,event,'REB',forecast,Date.parse('2026-10-21')).snapshotGap,null);
 const old=C.compare(data,player,event,'REB',forecast,now+20*60*1000);assert.equal(old.gap,null);assert.ok(Math.abs(old.snapshotGap-.35)<1e-8);
});
test('Wrong event/player, missing forecast and rotation holds never borrow a season average',()=>{
 for(const model of [null,{...forecast,eventId:'other'},{...forecast,startsAt:'2026-10-21T23:00:00Z'},{...forecast,players:[{...forecast.players[0],id:2}]},{...forecast,reviewRequired:'Rotation hold'}])assert.equal(C.compare(data,{...player,stats:{REB:{average:{mean:10}}}},event,'REB',model,now).gap,null);
 assert.equal(C.compare(data,{...player,reviewRequired:'Rotation hold'},event,'REB',forecast,now).gap,null);
});

test('Matchups remain available for players without bookmaker lines',()=>{
 const models={events:[{...forecast,home:'NYK',away:'PHI'}]};
 assert.equal(C.eventsFor(player,{events:[event],quotes:[]},models,now).length,1);
 assert.equal(C.eventsFor(player,null,models,now)[0].id,event.id);
 const result=C.compare({quotes:[]},player,event,'REB',forecast,now);assert.equal(result.model.mean,3.1);assert.equal(result.median,null);
});
test('Four-source median uses each provider once and marks limited appearances',()=>{
 const d={quotes:[...data.quotes,{...q,provider:'draftkings',line:2.5},{...q,provider:'betmgm',line:2.5}]};
 assert.equal(C.compare(d,player,event,'REB',forecast,now).median,2.5);
 const f={...forecast,players:[{...forecast.players[0],projectionStatus:'limited_participation',playedScenarios:3}]};
 const r=C.compare(d,player,event,'REB',f,now);assert.equal(r.gap,null);assert.equal(r.snapshotGap,null);assert.equal(r.playedScenarios,3);
});
test('Schedule reconciliation allows a ten-minute tip offset but not ambiguous or different games',()=>{
 const match=require('../scripts/match_market_schedule.cjs');
 const d={teams:{NYK:{name:event.home_team},PHI:{name:event.away_team}},schedule:[{id:'nba',home:'NYK',away:'PHI',tip:'2026-10-20T22:50:00Z'}]};
 assert.equal(match(d,event).offsetMinutes,10);
 assert.equal(match({...d,schedule:[...d.schedule,...d.schedule]},event),null);
 assert.equal(match(d,{...event,home_team:event.away_team,away_team:event.home_team}),null);
 assert.equal(match(d,{...event,commence_time:'2026-10-21T23:00:00Z'}),null);
});
