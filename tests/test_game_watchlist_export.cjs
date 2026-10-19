const {test}=require('node:test'),assert=require('node:assert/strict');
const {build}=require('../scripts/export_game_watchlist.cjs');
test('Joint combo share uses aligned outcomes, not marginal multiplication',()=>{
 const models={runs:4,events:[{gameId:'g',eventId:'e',startsAt:'2099-01-01'}]},samples=[{game:{id:'g',home:'H',away:'A'},providerStartsAt:'2099-01-01',scores:[{H:10,A:0},{H:10,A:0},{H:0,A:10},{H:0,A:10}],players:[],availability:{players:[]}}];
 const singles=[{id:'H:yes',ticker:'H',side:'yes',kind:'winner',yesTeam:'H',game_id:'g'},{id:'A:yes',ticker:'A',side:'yes',kind:'winner',yesTeam:'A',game_id:'g'}];
 const targets={games:[{id:'g',home:'H',away:'A',starts_at:'2099-01-01'}],singles,combos:[{game_id:'g',legs:[{market_ticker:'H',side:'yes'},{market_ticker:'A',side:'yes'}]}]};
 let r=build(models,samples,targets);assert.equal(r.games[0].combos[0].forecast.probability,0);assert.equal(r.games[0].combos[0].forecast.marginalProduct,.25);
 models.runs=5;r=build(models,samples,targets);assert.equal(r.games[0].singles[0].forecast,null);
 models.runs=4;samples[0].reviewRequired='hold';r=build(models,samples,targets);assert.equal(r.games[0].combos[0].forecast,null);
});
