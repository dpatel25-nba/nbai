const {test}=require('node:test'),assert=require('node:assert/strict'),E=require('../web/app-picks-engine.js');
const now=Date.parse('2026-10-20T13:00:00Z'),stamp=new Date(now).toISOString();
function fixture(){
 const event={id:'g',home_team:'Home',away_team:'Away',commence_time:'2026-10-20T19:00:00Z'};
 const players=[1,2,3].map(id=>({id,name:'Player '+id,team:id===1?'H':'A',stats:{PTS:{mean:25,p10:10,p90:40}},projectionStatus:'available'}));
 event.model={eventId:'g',startsAt:event.commence_time,players,availability:{validUntil:'2026-10-20T18:00:00Z'}};
 const quotes=players.flatMap(p=>['prizepicks','underdog'].map(provider=>({provider,player:p.name,playerId:p.id,identityStatus:'matched',eventId:'g',homeTeam:'Home',awayTeam:'Away',startsAt:event.commence_time,stat:'PTS',scope:'game',unit:'game',mainLine:true,line:provider==='prizepicks'?22.5:27.5,observedAt:stamp,sourceUpdatedAt:stamp})));
 return {market:{quotes},event};
}
test('App-specific directions use the selected line, including with only one provider',()=>{
 const {market,event}=fixture();
 assert.equal(E.candidates(market,[event],'prizepicks',now)[0].direction,'up');
 assert.equal(E.candidates(market,[event],'underdog',now)[0].direction,'down');
 market.quotes=market.quotes.filter(q=>q.provider==='prizepicks');
 assert.equal(E.candidates(market,[event],'prizepicks',now).length,3);
 assert.equal(E.candidates(market,[event],'underdog',now).length,0);
});
test('Freshness belongs to the selected app, not another book',()=>{
 const {market,event}=fixture();
 market.quotes.filter(q=>q.provider==='underdog').forEach(q=>q.sourceUpdatedAt='2026-10-20T00:00:00Z');
 assert.ok(E.candidates(market,[event],'prizepicks',now).every(r=>r.fresh));
 assert.ok(E.candidates(market,[event],'underdog',now).every(r=>!r.fresh));
});
test('Ambiguous, wrong-game, future and non-main-line quotes cannot produce a pick',()=>{
 for(const mutation of [q=>({...q,homeTeam:'Wrong'}),q=>({...q,sourceUpdatedAt:'2027-01-01'}),q=>({...q,mainLine:false}),q=>({...q,identityStatus:'unresolved'})]){
  const {market,event}=fixture();market.quotes=market.quotes.map(mutation);
  assert.equal(E.candidates(market,[event],'prizepicks',now).length,0);
 }
 const {market,event}=fixture();market.quotes.push({...market.quotes[0]});
 assert.equal(E.candidates(market,[event],'prizepicks',now).length,2);
});
test('Started games, forecast holds, expired availability and unstable players are withheld',()=>{
 for(const mutate of [
  e=>e.commence_time=stamp,e=>e.model.reviewRequired='hold',
  e=>e.model.availability.validUntil=stamp,
  e=>e.model.players.forEach(p=>p.projectionStatus='limited_participation'),
  e=>e.model.players.forEach(p=>p.projectionStatus='reported_out'),
  e=>e.model.players.forEach(p=>p.team=null)
 ]){
  const {market,event}=fixture();mutate(event);
  assert.equal(E.candidates(market,[event],'prizepicks',now).length,0);
 }
});
test('Drafts keep one app, different players and at least two teams; no invented payouts',()=>{
 const {market,event}=fixture(),r=E.candidates(market,[event],'prizepicks',now),u=E.candidates(market,[event],'underdog',now);
 const drafts=E.drafts(r);assert.deepEqual(drafts.map(d=>d.legs.length),[2,3]);
 for(const d of drafts){assert.ok(E.validCombo(d.legs));assert.equal(d.payout,undefined);assert.equal(d.jointProbability,undefined);}
 assert.equal(E.validCombo([r[0],r[0]]),false);
 assert.equal(E.validCombo([r[0],u[1]]),false);
 assert.equal(E.validCombo([r[1],r[2]]),false);
 assert.deepEqual(E.drafts([r[0]]),[]);
});
test('Calculator distinguishes returned stake from profit and withholds flex break-even',()=>{
 assert.deepEqual(E.payout(10,3,'all'),{total:30,profit:20,breakEven:1/3});
 assert.deepEqual(E.payout(10,.4,'flex'),{total:4,profit:-6,breakEven:null});
 assert.equal(E.payout(10,6,'flex').breakEven,null);
 for(const [s,m] of [[NaN,3],[10,NaN],[0,3],[-1,3],[10,0],[10,Infinity],[1e308,1e308]])assert.equal(E.payout(s,m,'all'),null);
});
