const {test}=require('node:test'),assert=require('node:assert/strict');
const {forGame}=require('../scripts/game_availability.cjs');
const D=require('../web/day-board.js'),C=require('../web/consensus-analysis.js');
const T=require('../web/daily-trading-engine.js');
const now=Date.parse('2026-10-19T15:00:00Z');
const game={home:'BOS',away:'DET',tip:'2026-10-20T19:00:00Z'};
const players=Array.from({length:12},(_,id)=>({id,name:'Player '+id,team:id<6?'BOS':'DET',out:id===0,status:id===0?'Out':'Not listed'}));
const data={season:'2026-27',players:Object.fromEntries(players.map(p=>[p.id,p]))};
const snapshot={schemaVersion:1,generatedAt:'2026-10-19T14:00:00Z',validUntil:'2026-10-19T20:00:00Z',events:[{...game,startsAt:game.tip,season:data.season,players}]};
test('Current injury excludes only identified Out players on matching rosters',()=>{
 const a=forGame(snapshot,data,game,now);assert.equal(a.holdReason,null);assert.deepEqual(a.outIds,['0']);
 for(const change of [{validUntil:'2026-10-19T14:00:00Z'},{generatedAt:'2026-10-20T14:00:00Z'},{events:[]},
  {events:[{...snapshot.events[0],players:players.slice(1)}]},
  {events:[{...snapshot.events[0],startsAt:'2026-10-21T19:00:00Z'}]},
  {events:[{...snapshot.events[0],holdReason:'Unknown injury'}]}])assert.ok(forGame({...snapshot,...change},data,game,now).holdReason);
});
test('Availability expiry suppresses game forecasts, props and paper entries',()=>{
 const model={startsAt:game.tip,availability:{validUntil:'2026-10-19T14:00:00Z'},gameProjection:{homeWinShare:.7}};
 const event={id:'g',home:'BOS',away:'DET',commence_time:game.tip,model};
 assert.match(C.forecastHold(model,now),/expired/);assert.equal(D.projection(event,now),null);
 assert.deepEqual(D.featuredProps([],event,now),[]);
 const session=T.create('2026-10-20',[event],{budget:10,maxBet:5,stopLoss:5},now);
 T.scan(session,[event],[],{now,feedOK:true,projectionAt:new Date(now).toISOString()});
 assert.equal(session.orders.length,0);assert.match(session.reasons.g,/expired/);
});
