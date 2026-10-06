const {test}=require('node:test'),assert=require('node:assert/strict');
const {resolvePlayer,build}=require('../scripts/build_player_line_coverage.cjs');
const C=require('../web/consensus-analysis.js'),{inputs}=require('../scripts/market_projection_inputs.cjs');
const teams={MEM:{name:'Memphis Grizzlies'},NYK:{name:'New York Knicks'}},event={id:'g',home_team:teams.MEM.name,away_team:teams.NYK.name,commence_time:'2099-01-01T00:00:00Z'},players=[{id:1,n:'Jaren Jackson Jr.',team:'MEM'},{id:2,n:'Jalen Brunson',team:'NYK'}];
test('Identity matching accepts unique suffix variants only on the event roster',()=>{
 const r=resolvePlayer({player:'Jaren Jackson'},event,players,teams);assert.equal(r.playerId,1);assert.equal(r.method,'unique_suffix_variant');
 assert.equal(resolvePlayer({player:'Jaren Jackson'},event,[...players,{id:3,n:'Jaren Jackson',team:'MEM'}],teams).playerId,3);
 assert.equal(resolvePlayer({player:'Jaren Jackson Jr'},event,[...players,{id:3,n:'Jaren Jackson Jr.',team:'NYK'}],teams).status,'unresolved');
 assert.equal(resolvePlayer({player:'Jaren Jackson Jr.'},event,[{...players[0],team:'LAL'}],teams).status,'unresolved');
});
test('Resolved identities never fall back to a different player with the same name',()=>{
 assert.ok(C.matchesPlayer({player:'Jaren Jackson',playerId:1},{id:1,name:'Jaren Jackson Jr.'}));
 assert.equal(C.matchesPlayer({player:'Jaren Jackson Jr.',playerId:2},{id:1,name:'Jaren Jackson Jr.'}),false);
 assert.equal(C.matchesPlayer({player:'Jaren Jackson Jr.',identityStatus:'unresolved'},{id:1,name:'Jaren Jackson Jr.'}),false);
});
test('Coverage separates absent quotes, unmatched identities, missing models and review holds',()=>{
 const market={events:[event,{...event,id:'empty'}],quotes:[{eventId:'g',player:'Jaren Jackson',provider:'fanduel',stat:'PTS'},{eventId:'g',player:'Unknown',provider:'prizepicks',stat:'PTS'}]};
 const {quotes,report}=build(market,{events:[{eventId:'g',reviewRequired:'Rotation review'}],rejected:[{eventId:'empty',reason:'Preseason unsupported'}]}, {players:Object.fromEntries(players.map(p=>[p.id,p])),teams});
 assert.equal(quotes[0].player,'Jaren Jackson');assert.equal(quotes[0].playerId,1);assert.equal(report.summary.unmatchedQuotes,1);assert.equal(report.events[0].projectionStatus,'under_review');assert.equal(report.events[1].quoteCount,0);assert.equal(report.events[1].projectionStatus,'unavailable');
});
test('Changing a quote does not rerun simulations; changing the matchup does',()=>{
 const market={events:[event],quotes:[{line:25.5}]},before=inputs(process.cwd(),market);
 assert.equal(inputs(process.cwd(),{...market,quotes:[{line:30.5}]}).inputSignature,before.inputSignature);
 assert.notEqual(inputs(process.cwd(),{...market,events:[{...event,commence_time:'2099-01-02T00:00:00Z'}]}).inputSignature,before.inputSignature);
 assert.notEqual(inputs(process.cwd(),{...market,events:[{...event,sport_key:'basketball_nba_preseason'}]}).inputSignature,before.inputSignature);
});
