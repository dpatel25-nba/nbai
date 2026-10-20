const {test}=require('node:test'),assert=require('node:assert/strict');
const E=require('../web/kalshi-day-engine.js'),K=require('../web/lib/kalshi-board.cjs'),Export=require('../scripts/export_kalshi_board_model.cjs');
const NOW=Date.parse('2026-10-20T14:00:00Z'),stamp=new Date(NOW).toISOString(),start='2026-10-20T19:00:00Z';
function fixture(){
 const n=200,values=()=>Object.fromEntries(['PTS','REB','AST','STL','BLK','FG3M','FTM'].map(f=>[f,Array.from({length:n},(_,i)=>f==='PTS'?(i<150?30:10):f==='REB'?12:f==='AST'?6:1)]));
 const players=[1,2,3].map(id=>({id,name:'Player '+id,team:id===1?'BOS':'DET',projectionStatus:'available',injuryStatus:'Not listed',values:values()}));
 const game={gameId:'g',eventId:'event',home:'DET',away:'BOS',homeName:'Detroit Pistons',awayName:'Boston Celtics',startsAt:start,reviewRequired:null,players,scores:Array.from({length:n},(_,i)=>i<150?[110,100]:[90,100])};
 const model={schemaVersion:1,runs:n,validUntil:'2026-10-20T18:00:00Z',games:[game],schedule:[]};
 const quotes={markets:players.map(p=>({ticker:'p'+p.id,kind:'prop',stat:'PTS',player:p.name,threshold:24.5,identity:{date:'2026-10-20',teams:['BOS','DET']},title:p.name+': 25+ points',yesAsk:.3,noAsk:.75,yesSize:5,noSize:10,quoteValid:true,observedAt:stamp})).concat([{ticker:'winner',kind:'winner',identity:{date:'2026-10-20',teams:['BOS','DET'],yesTeam:'DET'},title:'Detroit wins',yesAsk:.4,noAsk:.65,yesSize:10,noSize:10,quoteValid:true,observedAt:stamp}])};
 return {model,quotes};
}
function raw(series='KXNBAPTS'){
 return {ticker:series+'-26OCT20BOSDET-BOSPLAYER1-25',event_ticker:series+'-26OCT20BOSDET',status:'active',market_type:'binary',notional_value_dollars:'1.0000',title:'Player One: 25+ points',rules_primary:'If Player One records 25+ points in the Boston vs Detroit Pro Basketball game originally scheduled for Oct 20, 2026, then the market resolves to Yes.',rules_secondary:'Participation rules',strike_type:'greater',floor_strike:24.5,yes_ask_dollars:'.30',yes_bid_dollars:'.25',no_ask_dollars:'.75',no_bid_dollars:'.70',yes_ask_size_fp:'12.00',yes_bid_size_fp:'9.00'};
}
test('Normalizer binds the player, full-game rule, threshold, teams and $1 contract',()=>{
 const m=raw();for(const k of ['yes_ask_dollars','yes_bid_dollars','no_ask_dollars','no_bid_dollars'])m[k]='0'+m[k];
 const q=K.normalize(m,'KXNBAPTS',stamp);assert.equal(q.threshold,24.5);assert.equal(q.noSize,9);assert.equal(q.noAsk,.75);assert.equal(q.yesSize,12);
 for(const patch of [{floor_strike:25},{title:'Someone Else: 25+ points'},{notional_value_dollars:'2.0000'},{status:'closed'},{strike_type:'less'},{event_ticker:'WRONG'},{rules_primary:m.rules_primary.replace('points','assists')},{rules_primary:m.rules_primary.replace('Pro Basketball game','Pro Basketball first-half game')}])assert.equal(K.normalize({...m,...patch},'KXNBAPTS',stamp),null);
 const crossed=K.normalize({...m,yes_bid_dollars:'0.90'},'KXNBAPTS',stamp);assert.equal(crossed.quoteValid,false);assert.equal(crossed.noAsk,null);
});
test('Favorites use current side-specific price, fee and distinct players, not a stat gap',()=>{
 const {model,quotes}=fixture(),board=E.build(model,quotes,NOW);
 assert.equal(E.favorites(board.games).length,3);
 assert.ok(E.favorites(board.games).every(r=>r.side==='yes'&&r.price===.3));
 quotes.markets[0].yesAsk=.99;quotes.markets[0].noAsk=.10;
 const r=E.build(model,quotes,NOW).games[0].props[0];
 assert.equal(r.best.side,'no');assert.equal(r.best.forecast.probability,.25);assert.match(E.label(r.best),/Under 24.5/);
 quotes.markets.push({...quotes.markets[1],ticker:'alternate',threshold:19.5});
 assert.equal(E.favorites(E.build(model,quotes,NOW).games).length,3);
});
test('Stale, future, empty-size, expired, held, started and questionable candidates are excluded',()=>{
 for(const mutate of [
  x=>x.quotes.markets.forEach(q=>q.observedAt='2026-10-20T12:00:00Z'),
  x=>x.quotes.markets.forEach(q=>q.observedAt='2026-10-21T14:00:00Z'),
  x=>x.quotes.markets.forEach(q=>{q.yesSize=0;q.noSize=.5;}),
  x=>x.model.validUntil=stamp,x=>x.model.games[0].reviewRequired='hold',
  x=>x.model.games[0].startsAt=stamp,x=>x.model.games[0].players.forEach(p=>p.injuryStatus='Questionable'),
  x=>x.model.games[0].players.forEach(p=>p.values.PTS.pop())
 ]){
  const f=fixture();mutate(f);assert.equal(E.favorites(E.build(f.model,f.quotes,NOW).games).length,0);
 }
});
test('Wrong games, dates, duplicate identities and duplicated tickers are never ranked',()=>{
 for(const mutate of [
  x=>x.quotes.markets.forEach(q=>q.identity.teams=['NYK','PHI']),
  x=>x.quotes.markets.forEach(q=>q.identity.date='2026-10-21'),
  x=>x.quotes.markets.push(...x.quotes.markets.map(q=>({...q}))),
  x=>x.model.games.push({...x.model.games[0],gameId:'ambiguous'})
 ]){
  const f=fixture();mutate(f);assert.equal(E.favorites(E.build(f.model,f.quotes,NOW).games).length,0);
 }
});
test('Unmodeled player lines remain visible without an invented forecast',()=>{
 const f=fixture();f.quotes.markets[0].player='Unmatched';
 const row=E.build(f.model,f.quotes,NOW).games[0].props[0];
 assert.equal(row.hits,null);assert.equal(row.best,null);assert.equal(row.yes.price,.3);
});
test('Combined stats use same-scenario sums, with explicit double/triple-double semantics',()=>{
 const p=fixture().model.games[0].players[0];
 assert.equal(E.playerHits(p,'PRA',44.5,200).hits.reduce((a,b)=>a+b,0),150);
 assert.equal(E.playerHits(p,'DD',.5,200).hits.reduce((a,b)=>a+b,0),200);
 assert.equal(E.playerHits(p,'TD',.5,200).hits.reduce((a,b)=>a+b,0),0);
 assert.equal(E.playerHits(p,'FG3M',.5,200).average,1);
});
test('Winner routes preserve NO-on-opponent identity; combos retain correlation',()=>{
 const f=fixture(),game=E.build(f.model,f.quotes,NOW).games[0],s=E.winnerSides(game);
 assert.equal(s.find(x=>x.team==='BOS').choice.side,'no');
 assert.equal(E.label(s.find(x=>x.team==='BOS').choice),'Boston Celtics to win');
 const c=E.combo(game);assert.equal(c.legs.length,3);assert.equal(c.joint.probability,.75);
 assert.equal(c.marginalProduct,.75**3);assert.equal(c.priced,false);assert.equal(c.price,undefined);
 assert.equal(new Set(c.legs.slice(1).map(l=>l.player.team)).size,2);
});
test('Prices that do not leave a conservative margin yield no favorites',()=>{
 const f=fixture();f.quotes.markets.forEach(q=>{q.yesAsk=.9;q.noAsk=.9;});
 assert.equal(E.favorites(E.build(f.model,f.quotes,NOW).games).length,0);
 assert.equal(E.fee(.5),.02);assert.equal(E.fee(.01),.01);
});
test('Exporter refuses mixed scenario/model bundles',()=>{
 const f=fixture(),g=f.model.games[0];
 const models={...f.model,inputSignature:'sig',events:[{...g,gameProjection:{homeScore:{mean:105},awayScore:{mean:100}},players:[]}]};
 const samples=[{eventId:'event',providerStartsAt:start,game:{id:'g',home:'DET',away:'BOS'},scores:g.scores.map(([H,A])=>({H,A})),players:[]}];
 assert.equal(Export.build(models,samples).games[0].scores.length,200);
 models.events[0].gameProjection.homeScore.mean=106;
 assert.throws(()=>Export.build(models,samples),/mismatch/);
 samples[0].game.away='PHI';assert.throws(()=>Export.build(models,samples),/mismatch/);
});
test('Public API rejects writes and caller-supplied query targets',async()=>{
 const handler=require('../web/api/kalshi-board.js');
 for(const [method,url,status] of [['POST','/api/kalshi-board',405],['GET','/api/kalshi-board?url=https://evil.test',400]]){
  const res={setHeader(){},end(body){this.body=JSON.parse(body);}};await handler({method,url},res);assert.equal(res.statusCode,status);
 }
});
test('Incomplete inventory pages do not leak partial series into the current scan',async()=>{
 const q=await K.collect(async url=>({ok:true,status:200,text:async()=>JSON.stringify(url.includes('series_ticker=KXNBAPTS')?{markets:[],cursor:'loop'}:{markets:[],cursor:''})}));
 assert.equal(q.coverage.find(c=>c.series==='KXNBAPTS').complete,false);
 assert.equal(q.markets.length,0);assert.equal(q.coverage.length,Object.keys(K.SERIES).length);
});
