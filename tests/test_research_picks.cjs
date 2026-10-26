const {test}=require('node:test'),assert=require('node:assert/strict');
const E=require('../web/kalshi-day-engine.js'),R=require('../web/research-picks-engine.js');
const now=Date.parse('2026-10-20T18:50:00Z');
function fixture(){
 const start='2026-10-20T19:00:00Z',stamp=new Date(now).toISOString(),n=200;
 const players=[{id:1,name:'Player A',team:'BOS'},{id:2,name:'Player B',team:'BOS'},{id:3,name:'Player C',team:'DET'}].map(p=>({...p,projectionStatus:'available',injuryStatus:'Not listed',values:{PTS:Array.from({length:n},(_,i)=>i<150?30:10)}}));
 const game={gameId:'0022600001',home:'DET',away:'BOS',homeName:'Detroit Pistons',awayName:'Boston Celtics',startsAt:start,players,scores:Array.from({length:n},(_,i)=>i<140?[110,100]:[100,110])};
 const model={runs:n,validUntil:'2026-10-20T20:00:00Z',games:[game],schedule:[]};
 const quotes={markets:players.map(p=>({ticker:'p'+p.id,kind:'prop',player:p.name,stat:'PTS',threshold:24.5,identity:{date:'2026-10-20',teams:['BOS','DET']},title:p.name+': 25+ points',yesAsk:.49,noAsk:.53,yesSize:5,noSize:5,quoteValid:true,observedAt:stamp})).concat([{ticker:'winner',kind:'winner',title:'Detroit wins',identity:{date:'2026-10-20',teams:['BOS','DET'],yesTeam:'DET'},yesAsk:.4,noAsk:.62,yesSize:5,noSize:5,quoteValid:true,observedAt:stamp}])};
 const sources=[{title:'Test source',url:'https://example.com/research'}];
 const picks=players.map(p=>({status:'reviewed',ticker:'p'+p.id,playerId:p.id,team:p.team,stat:'PTS',threshold:24.5,side:'yes',maxPrice:.51,priority:p.id,thesis:'Sustained role with supporting archived evidence.',risk:'Minutes and role can change before the opening game.',sources}));
 const data={games:[{gameId:game.gameId,startsAt:start,reviewedAt:'2026-10-20T18:00:00Z',validUntil:start,picks,combo:{status:'reviewed',winnerTeam:'DET',propTickers:['p2','p3'],script:'A shared hypothetical game script.',risk:'A coherent story does not guarantee a joint outcome.',sources}}]};
 const g=E.build(model,quotes,now).games[0];return {model,quotes,data,g};
}
test('Only reviewed, exactly matched players can qualify, never model-only filler',()=>{
 const {g,data}=fixture();const r=R.picks([g],data,now);
 assert.equal(r.length,3);assert.equal(new Set(r.map(r=>r.pick.playerId)).size,3);
 assert.equal(R.picks([g],null,now).length,0);
 data.games[0].picks[0].status='draft';assert.equal(R.picks([g],data,now).length,2);
 data.games[0].picks[1].sources=[{title:'bad',url:'javascript:alert(1)'}];assert.equal(R.picks([g],data,now).length,1);
 const p=data.games[0].picks[2];p.playerId=999;assert.equal(R.picks([g],data,now)[0].state,'wait');
});
test('Price ceiling and current model support both required; no automatic reversal of side',()=>{
 const {g,data}=fixture(),p=data.games[0].picks[0],review=data.games[0],row=g.props.find(r=>r.market.ticker===p.ticker).yes;
 Object.assign(row,{price:.49,edge:.08,fresh:true,liquid:true,hold:null});
 assert.equal(R.assess(p,g,review,now).state,'ready');
 row.price=.52;assert.equal(R.assess(p,g,review,now).state,'price');
 row.price=.49;row.edge=-.001;assert.equal(R.assess(p,g,review,now).state,'review');
 assert.equal(R.assess(p,g,review,now).pick.side,'yes');
});
test('Stale, thin, held, expired and future-dated reviews cannot be actionable',()=>{
 for(const change of [
  ({row})=>row.fresh=false,({row})=>row.liquid=false,({row})=>row.hold='Questionable',
  ({review})=>review.validUntil=new Date(now).toISOString(),
  ({review})=>review.reviewedAt='2026-10-21T00:00:00Z',({review})=>review.validUntil='bad'
 ]){
  const {g,data}=fixture(),p=data.games[0].picks[0],review=data.games[0],row=g.props.find(r=>r.market.ticker===p.ticker).yes;
  Object.assign(row,{price:.49,edge:.08,fresh:true,liquid:true,hold:null});change({row,review});
  assert.notEqual(R.assess(p,g,review,now).state,'ready');
 }
});
test('At tip, all reviewed ideas close without removing the research record',()=>{
 const {g,data}=fixture(),closed=R.picks([g],data,Date.parse(g.startsAt));
 assert.equal(closed.length,3);assert.ok(closed.every(p=>p.state==='closed'));
 assert.equal(R.combo(g,data,Date.parse(g.startsAt)).closed,true);
});
test('Combo is a reviewed script with one player per team; no invented joint price or probability',()=>{
 const {g,data}=fixture(),c=R.combo(g,data,now);
 assert.equal(c.props.length,2);assert.equal(new Set(c.props.map(p=>p.pick.team)).size,2);
 assert.equal(c.priced,false);assert.equal(c.price,undefined);assert.equal(c.probability,undefined);
 data.games[0].combo.propTickers=[data.games[0].picks[0].ticker,data.games[0].picks[1].ticker];
 assert.equal(R.combo(g,data,now),null);
});
test('Wrong game or duplicate research records cannot attach to the slate',()=>{
 const {g,data}=fixture();data.games[0].startsAt='2026-10-21T19:00:00Z';assert.equal(R.picks([g],data,now).length,0);
 data.games[0].startsAt=g.startsAt;data.games.push(structuredClone(data.games[0]));assert.equal(R.reviewFor(data,g),null);
});
test('Official tip closes opinions before a delayed provider start without changing research identity',()=>{
 const {model,quotes,data}=fixture();
 model.games[0].officialStartsAt='2026-10-20T18:55:00Z';
 const before=Date.parse('2026-10-20T18:54:00Z'),at=Date.parse('2026-10-20T18:55:00Z');
 const g=E.build(model,quotes,before).games[0];
 assert.equal(E.tip(g),'2026-10-20T18:55:00Z');
 assert.equal(R.reviewFor(data,g),data.games[0]);
 assert.equal(R.picks([g],data,at).length,3);
 assert.ok(R.picks([g],data,at).every(p=>p.state==='closed'));
 assert.equal(R.combo(g,data,at).closed,true);
 assert.match(E.build(model,quotes,at).games[0].hold,/started/);
 model.games[0].officialStartsAt='bad';assert.equal(E.tip(E.build(model,quotes,before).games[0]),model.games[0].startsAt);
 model.games[0].officialStartsAt='2026-10-20T19:10:00Z';assert.equal(E.tip(E.build(model,quotes,before).games[0]),model.games[0].startsAt);
});
test('Editorial watches cannot become recommendations merely through price movement',()=>{
 const {g,data}=fixture(),p=data.games[0].picks[0];p.editorialDecision='watch';p.auditReason='Role evidence is insufficient.';
 const row=g.props.find(r=>r.market.ticker===p.ticker).yes;Object.assign(row,{price:.01,edge:.9,fresh:true,liquid:true,hold:null});
 assert.equal(R.assess(p,g,data.games[0],now).state,'watch');
 assert.equal(R.combo(g,{games:[{...data.games[0],combo:{...data.games[0].combo,propTickers:['p1','p3']}}]},now).ready,false);
});
test('Withdrawals retain their audit record but cannot appear as favorites or combo legs',()=>{
 const {g,data}=fixture();data.games[0].picks[1].editorialDecision='withdraw';
 assert.equal(R.picks([g],data,now).find(p=>p.pick.ticker==='p2').state,'withdrawn');
 assert.equal(R.favorites([g],data,now).length,2);
 assert.equal(R.combo(g,data,now),null);
 assert.equal(data.games[0].picks.length,3);
});

test('Positive average edge alone cannot promote a pick within simulation sampling noise',()=>{
 const {g,data}=fixture(),p=data.games[0].picks[0],row=g.props.find(r=>r.market.ticker===p.ticker).yes;
 Object.assign(row,{price:.49,edge:.005,rank:-.01,fresh:true,liquid:true,hold:null});
 assert.equal(R.assess(p,g,data.games[0],now).state,'review');
 row.rank=.001;assert.equal(R.assess(p,g,data.games[0],now).state,'ready');
 row.rank=undefined;assert.equal(R.assess(p,g,data.games[0],now).state,'review');
});

test('A diagnosed rotation defect blocks model certification even when simulated edge is positive',()=>{
 const {g,data}=fixture();data.games[0].modelEvidenceStatus='rotation_review_required';
 const p=R.picks([g],data,now)[0];
 assert.equal(p.state,'review');assert.match(p.status,/rotation model under review/);
 assert.equal(R.combo(g,data,now).ready,false);
});
test('Automated historical screens never become model-certified recommendations',()=>{
 const {g,data}=fixture();data.games[0].reviewType='automated_statistics';
 const result=R.picks([g],data,now);
 assert.ok(result.every(p=>p.state!=='ready'));
 assert.match(result[0].status,/Automated research lean/);
 assert.equal(R.combo(g,data,now).ready,false);
});
test('Additional scripts retain exact reviewed legs, tip closure and unverified pricing',()=>{
 const {g,data}=fixture();
 data.games[0].additionalCombos=[{...data.games[0].combo,title:'Alternative',propTickers:['p1','p3']},
  {...data.games[0].combo,propTickers:['missing','p3']}];
 const c=R.combos(g,data,now);assert.equal(c.length,2);assert.ok(c.every(x=>x.priced===false));
 assert.ok(R.combos(g,data,Date.parse(g.startsAt)).every(x=>x.closed));
 data.games[0].picks[0].editorialDecision='withdraw';assert.equal(R.combos(g,data,now).length,1);
});
