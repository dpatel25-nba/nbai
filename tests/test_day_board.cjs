const {test}=require('node:test'),assert=require('node:assert/strict'),D=require('../web/day-board.js');
const e={id:'g',home_team:'Home',away_team:'Away',commence_time:'2099-01-01T00:00:00Z',model:{startsAt:'2099-01-01T00:00:00Z',gameProjection:{homeScore:{mean:112},awayScore:{mean:106},homeMargin:{mean:6},total:{mean:218},homeWinShare:.7}}};
test('Winner uses home margin and spread comparisons preserve the bookmaker sign',()=>{
 assert.equal(D.projection(e).winner,'Home');assert.equal(D.projection(e).margin,6);
 const c=D.teamComparison({market:'spreads',outcomes:[{name:'Home',point:-3.5}]},e);assert.equal(c.market,3.5);assert.equal(c.difference,2.5);
 assert.equal(D.projection({...e,model:{...e.model,reviewRequired:'hold'}}),null);
});
test('Moneyline compares simulation share to a normalized two-way reference',()=>{
 const c=D.teamComparison({market:'h2h',outcomes:[{name:'Home',price:-150},{name:'Away',price:130}]},e);assert.equal(c.model,70);assert.ok(Math.abs(c.market-57.9831932773)<1e-6);assert.equal(D.probability(10),null);
});
test('The date board uses Eastern days and merges projections with the official schedule',()=>{
 assert.equal(D.day('2026-10-21T01:30:00Z'),'2026-10-20');
 const rows=D.games({events:[{...e,id:'g'}]},{schedule:[{id:'s',startsAt:e.commence_time}],events:[{...e.model,eventId:'g',gameId:'s'}]});assert.equal(rows.length,1);assert.equal(rows[0].id,'g');
});
test('Wrong-matchup, duplicate and stale game lines never appear fresh',()=>{
 const q={eventId:'g',scope:'game',homeTeam:'Home',awayTeam:'Away',startsAt:e.commence_time,provider:'fanduel',market:'h2h',observedAt:'2026-10-05T00:00:00Z',sourceUpdatedAt:'2026-10-05T00:00:00Z'};
 assert.equal(D.gameLines({gameQuotes:[q,q]},e).length,0);assert.equal(D.gameLines({gameQuotes:[{...q,homeTeam:'Wrong'}]},e).length,0);assert.equal(D.gameLines({gameQuotes:[q]},e,Date.parse('2026-10-06')).at(0).fresh,false);
});
test('Shortlist excludes stale, held, started and unavailable comparisons',()=>{
 const r={gap:2,fresh:true,model:{p90:25,p10:10}};assert.equal(D.watchlist([r],e).length,1);assert.equal(D.watchlist([{...r,fresh:false}],e).length,0);assert.equal(D.watchlist([r],{...e,model:{reviewRequired:'hold'}}).length,0);assert.equal(D.watchlist([r],{...e,commence_time:'2020-01-01'}).length,0);
});
test('Unmodeled market games replace matching schedule placeholders without inventing projections',()=>{
 const rows=D.games({events:[e]},{schedule:[{id:'s',homeName:'Home',awayName:'Away',startsAt:e.commence_time}],events:[]});
 assert.equal(rows.length,1);assert.equal(rows[0].id,'g');assert.equal(D.projection(rows[0]),null);
});
test('Team watchlist preserves spread direction and withholds stale or held forecasts',()=>{
 const now=Date.parse('2026-10-05T00:00:00Z'),stamp=new Date(now).toISOString();
 const event={...e,model:{...e.model,gameProjection:{...e.model.gameProjection,homeMargin:{mean:6,p10:-10,p90:20},total:{mean:218,p10:200,p90:240}}}};
 const q={eventId:'g',scope:'game',homeTeam:'Home',awayTeam:'Away',startsAt:e.commence_time,provider:'fanduel',market:'spreads',observedAt:stamp,sourceUpdatedAt:stamp,outcomes:[{name:'Home',point:-8.5,price:-110},{name:'Away',point:8.5,price:-110}]};
 const market={gameQuotes:[q]};assert.equal(D.teamWatchlist(market,event,now)[0].outcome.name,'Away');
 assert.equal(D.teamWatchlist(market,event,now+3600000).length,0);
 assert.equal(D.teamWatchlist(market,{...event,model:{...event.model,reviewRequired:true}},now).length,0);
});
test('Roster browsing includes unquoted players and refuses unrelated or unresolved quotes',()=>{
 const event={...e,model:{players:[{id:1,name:'No Line',team:'Home'}]}};
 const q={eventId:'g',homeTeam:'Home',awayTeam:'Away',startsAt:e.commence_time,playerId:2,player:'Quoted Player',identityStatus:'matched'};
 assert.deepEqual(D.roster({quotes:[q,{...q,playerId:3,homeTeam:'Wrong'},{...q,playerId:4,identityStatus:'unresolved'}]},event).map(p=>p.name).sort(),['No Line','Quoted Player']);
});
function candidate(id,stat,provider='fanduel',options={}){
 return {player:{id,name:'Player '+id},stat,model:{mean:25,p10:10,p90:35},snapshotGap:3,sourceCount:2,median:22,fresh:true,books:{[provider]:{provider,providerName:provider,line:22.5,americanOdds:{over:-110,under:-110}}},...options};
}
test('Featured props cap at three, label saved comparisons and exclude held/unstable forecasts',()=>{
 const rows=[candidate(1,'PTS'),candidate(2,'PTS'),candidate(3,'PTS'),candidate(4,'PTS')];
 assert.equal(D.featuredProps(rows,e).length,3);
 assert.equal(D.featuredProps([candidate(1,'PTS','fanduel',{fresh:false})],e)[0].fresh,false);
 assert.equal(D.featuredProps(rows,{...e,model:{reviewRequired:true}}).length,0);
 assert.equal(D.featuredProps([candidate(1,'PTS','fanduel',{participation:'limited_participation'}),candidate(2,'PTS','fanduel',{snapshotGap:null})],e).length,0);
});
test('Parlay prefers three actual same-book legs, falls back to two and never invents a price',()=>{
 const rows=[candidate(1,'PTS'),candidate(1,'AST'),candidate(2,'PTS'),candidate(3,'REB')];
 const p=D.parlay(rows,e);assert.equal(p.legs.length,3);assert.equal(new Set(p.legs.map(l=>l.player.id)).size,3);assert.equal(p.provider,'fanduel');assert.equal(p.legs[0].quote.line,22.5);assert.equal(p.combinedOdds,undefined);
 assert.equal(D.parlay(rows.slice(0,2),e).legs.length,2);assert.equal(D.parlay(rows.slice(0,1),e),null);
 assert.equal(D.parlay([candidate(1,'PTS'),candidate(2,'PTS','draftkings')],e),null);
 assert.equal(D.parlay([candidate(1,'PTS','prizepicks'),candidate(2,'PTS','prizepicks')],e),null);
 assert.equal(D.parlay(rows.map(r=>({...r,fresh:false})),e).fresh,false);
 assert.equal(D.parlay(rows,{...e,commence_time:'2020-01-01'}),null);
 assert.equal(D.parlay(rows,{...e,model:{reviewRequired:true}}),null);
 assert.equal(D.parlay([rows[0],rows[0]],e),null);
 const wrong=candidate(9,'AST');wrong.books.fanduel.line=30;assert.equal(D.parlay([rows[0],wrong],e),null);
 const missing=candidate(9,'AST');missing.books.fanduel.americanOdds={};assert.equal(D.parlay([rows[0],missing],e),null);
});
