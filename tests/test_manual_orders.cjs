const {test}=require('node:test'),assert=require('node:assert/strict');
const E=require('../web/manual-order-engine.js'),{cost}=require('../web/lib/watchlist-quotes.cjs');
function fixture(){
 const now=Date.parse('2026-10-20T14:00:00Z'),stamp=new Date(now).toISOString(),validUntil='2026-10-20T19:55:00Z';
 const pick={id:'BOSTON-15:no',ticker:'BOSTON-15',side:'no',game_id:'g1',starts_at:'2026-10-20T19:00:00Z',label:'Player under 14.5 points',kind:'prop',rules_sha256:'rules1',forecast:{probability:.67,samplingLower:.65,targetAsk:.58,scenarios:2000},hold:null};
 const game={id:'g1',home:'DET',away:'BOS',starts_at:pick.starts_at,singles:[pick],combos:[]};
 const quote={id:pick.id,status:'Observed',receivedAt:stamp,ask:.44,asks:[[.44,10],[.45,25]],contractTitle:'Player scores 15+',rulesHash:'rules1',priceRanges:[{start:'0.0000',end:'1.0000',step:'0.0100'}]};
 const response={gameId:'g1',status:'Observation only',quotes:[quote]};
 return {now,budget:10,discount:2,watch:{inputSignature:'sig1',generatedAt:stamp,validUntil,expiresAt:'2026-10-21T01:40:00Z',runs:2000,games:[game]},
  models:{inputSignature:'sig1',generatedAt:stamp,availabilityValidUntil:validUntil,events:[{gameId:'g1',startsAt:game.starts_at,home:game.home,away:game.away,gameProjection:{}}]},
  availability:{validUntil},reference:{inputSignature:'sig1',quotes:[structuredClone(response)]},live:{g1:response}};
}
const row=f=>E.build(f)[0];
test('Fee arithmetic and budget sizing match a single-price public-depth scenario',()=>{
 for(const price of [1,20,42,50,53,98,99])for(const n of [1,18,20,47,100]){
  const got=E.estimate(price,n),expected=cost([[price/100,100]],n);
  assert.equal(got.total,expected.total);assert.equal(got.fee,expected.fee);
 }
 assert.equal(E.estimate(50,20).fee,.35);
 for(const p of [20,42,53,99]){
  const s=E.size(p,10);assert.ok(s.total<=10);assert.ok(s.quantity===100||E.estimate(p,s.quantity+1).total>10);
 }
 assert.throws(()=>E.estimate(0,2));assert.throws(()=>E.estimate(42,1.5));assert.equal(E.size(42,-10),null);
 assert.equal(E.size(99,1).total,1);assert.equal(E.size(42,.99),null);assert.equal(E.size(42,NaN),null);
});
test('A patient limit stays anchored as live quotes change and respects the model cap',()=>{
 const f=fixture(),r=row(f);assert.equal(r.limitCents,42);assert.equal(r.ceilingCents,58);assert.equal(r.cost.quantity,22);
 assert.equal(r.status,'Waiting for limit');assert.equal(r.actualOrders,0);
 f.live.g1.quotes[0].ask=.39;f.live.g1.quotes[0].asks=[[.39,5]];
 const next=row(f);assert.equal(next.limitCents,42);assert.equal(next.depthAtLimit,5);assert.equal(next.status,'At limit · limited depth');
 f.reference.quotes[0].quotes[0].ask=.8;assert.equal(row(f).limitCents,58);
 f.reference.quotes[0].quotes[0].priceRanges=[{start:'0.0000',end:'1.0000',step:'0.0500'}];
 f.live.g1.quotes[0].priceRanges=f.reference.quotes[0].quotes[0].priceRanges;assert.equal(row(f).limitCents,55);
});
test('Missing or invalid model context clears prices, sizes and copy action',()=>{
 const changes=[
  f=>{f.now=Date.parse(f.watch.validUntil);},
  f=>{f.models.inputSignature='changed';},
  f=>{f.availability.validUntil='2026-10-20T20:00:00Z';},
  f=>{f.models.events[0].reviewRequired=true;},
  f=>{f.models.events[0].startsAt='2026-10-20T20:00:00Z';},
  f=>{f.watch.games[0].singles[0].side='yes';},
  f=>{f.watch.games[0].singles[0].forecast.targetAsk=.99;},
  f=>{f.watch.games[0].singles[0].forecast.probability=NaN;},
  f=>{f.watch.games[0].singles[0].hold='Injury status unresolved';},
  f=>{f.now=Date.parse(f.watch.games[0].starts_at)-300000;}
 ];
 for(const change of changes){const f=fixture();change(f);const r=row(f);assert.ok(r.hold);assert.equal(r.copyable,false);assert.equal(r.limitCents,null);assert.equal(r.cost,null);}
});
test('Stale or changed quotes and invalid reference data cannot be copied',()=>{
 const changes=[
  f=>{f.live={};},
  f=>{f.live.g1.status='Pregame context unavailable or changed';},
  f=>{f.live.g1.quotes[0].receivedAt=new Date(f.now-30001).toISOString();},
  f=>{f.live.g1.quotes[0].receivedAt=new Date(f.now+2000).toISOString();},
  f=>{f.live.g1.quotes[0].rulesHash='changed';},
  f=>{f.live.g1.quotes[0].priceRanges=[];},
  f=>{f.live.g1.quotes[0].asks=[[.5,NaN]];},
  f=>{f.reference.inputSignature='changed';},
  f=>{f.reference.quotes[0].quotes[0].ask=0;},
  f=>{f.reference.quotes[0].quotes[0].rulesHash='changed';},
  f=>{f.reference.quotes[0].quotes[0].priceRanges=[];},
  f=>{f.budget=0;},
  f=>{f.discount=NaN;}
 ];
 for(const change of changes){const f=fixture();change(f);const r=row(f);assert.ok(r.hold);assert.equal(r.copyable,false);assert.throws(()=>E.worksheet(r,f.now));}
});
test('Empty depth never implies a fill; worksheet includes exact NO contract and cutoff',()=>{
 const f=fixture();f.live.g1.quotes[0].ask=null;f.live.g1.quotes[0].asks=[];
 const r=row(f);assert.equal(r.reached,false);assert.equal(r.depthAtLimit,0);assert.equal(r.status,'Waiting for limit');
 assert.equal(r.expiresAt,'2026-10-20T18:55:00.000Z');
 const text=E.worksheet(r,f.now);assert.match(text,/Side: BUY NO/);assert.match(text,/Ticker: BOSTON-15/);assert.match(text,/not an order/);
 assert.throws(()=>E.worksheet(r,f.now+30001));
 f.watch.validUntil=f.models.availabilityValidUntil=f.availability.validUntil='2026-10-20T15:00:00Z';
 assert.equal(row(f).expiresAt,'2026-10-20T15:00:00.000Z');
});
test('Shortlist selects at most one per game and excludes weak or held candidates',()=>{
 const base=row(fixture()),other={...base,id:'other',selectionScore:.1};
 const picks=E.shortlist([base,other,{...base,id:'weak',gameId:'g2',selectionScore:.04},{...base,id:'held',gameId:'g3',copyable:false}]);
 assert.equal(picks.length,1);assert.equal(picks[0].id,base.id);
});
test('Late UTC games remain on the Eastern slate, not the following day',()=>{
 assert.equal(E.dateET('2026-10-21T01:30:00Z'),'2026-10-20');
 const f=fixture();f.now=Date.parse('2026-10-21T14:00:00Z');assert.deepEqual(E.build(f),[]);
});
