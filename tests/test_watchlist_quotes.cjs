const {test}=require('node:test'),assert=require('node:assert/strict');
const {book,cost,collect}=require('../web/lib/watchlist-quotes.cjs'),targets=require('../web/lib/game-watchlist-targets.json');
test('Asks come from opposing bids, preserving fractional depth',()=>{
 const b=book({orderbook_fp:{yes_dollars:[['0.4000','10.25']],no_dollars:[['0.5000','3.25'],['0.4900','7.00']]}},'yes');
 assert.deepEqual(b.asks,[[.5,3.25],[.51,7]]);assert.equal(cost(b.asks,20),null);assert.ok(cost(b.asks,5).unitCost>.5);
 assert.throws(()=>book({orderbook_fp:{yes_dollars:[['0.5000','1']],no_dollars:[['0.5000','1']]}},'no'),/Crossed/);
});
test('General-fee cent rounding changes effective unit cost with quantity',()=>{
 assert.deepEqual(cost([[.5,100]],1),{quantity:1,principal:.5,fee:.02,total:.52,unitCost:.52,worstPrice:.5});
 assert.equal(cost([[.5,100]],20).fee,.35);assert.equal(cost([[.5,100]],20).unitCost,.5175);
 assert.equal(cost([[.5,4],[.6,30]],20).worstPrice,.6);
});
test('Fixed targets reject inactive/changed contracts and non-pregame context',async()=>{
 const game=targets.games[0],p=targets.singles[0];let calls=0;
 const context=state=>({events:[{date:game.starts_at,status:{type:{state}},competitions:[{competitors:[{homeAway:'home',team:{abbreviation:game.home}},{homeAway:'away',team:{abbreviation:game.away}}]}]}]});
 const fetcher=async url=>{calls++;return {ok:true,text:async()=>JSON.stringify(url.includes('scoreboard')?context('pre'):url.includes('orderbook')?{orderbook_fp:{yes_dollars:[],no_dollars:[]}}:{market:{ticker:p.ticker,status:'closed'}})};};
 const r=await collect(game.id,20,fetcher,Date.parse(game.starts_at)-3600000);assert.ok(r.quotes.every(x=>x.status.includes('unavailable')));assert.equal(r.actualOrders,0);assert.equal(calls,9);
 calls=0;const stopped=await collect(game.id,20,async()=>{calls++;return {ok:true,text:async()=>JSON.stringify(context('in'))};},Date.parse(game.starts_at)-3600000);assert.deepEqual(stopped.quotes,[]);assert.equal(calls,1);
 const ended=await collect(game.id,20,fetcher,Date.parse(game.starts_at));assert.deepEqual(ended.quotes,[]);assert.equal(calls,1);
});
test('API rejects arbitrary tickers, duplicate parameters and all writes before fetching',async()=>{
 const handler=require('../web/api/watchlist.js');
 for(const [method,url,status] of [['POST','/api/watchlist',405],['GET','/api/watchlist?game=x',400],['GET','/api/watchlist?game='+targets.games[0].id+'&ticker=evil',400],['GET','/api/watchlist?game='+targets.games[0].id+'&quantity=20&quantity=100',400]]){
  const res={setHeader(){},end(){}};await handler({method,url},res);assert.equal(res.statusCode,status);
 }
});
