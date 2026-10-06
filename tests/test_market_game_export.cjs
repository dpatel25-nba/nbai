const fs=require('node:fs'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {summary}=require('../scripts/player_projection_stats.cjs');
global.window=global;require('../web/season-data.js');
const models=JSON.parse(fs.readFileSync('web/player-game-projections.json')),quotes=JSON.parse(fs.readFileSync('web/player-consensus.json'));
const samplePath='data/features/player_props/consensus/game-projection-samples.json';
const saved=fs.existsSync(samplePath)?JSON.parse(fs.readFileSync(samplePath)):null;
const fingerprint=require('../scripts/market_projection_inputs.cjs').inputs(process.cwd(),quotes);
assert.equal(models.inputSignature,fingerprint.inputSignature);
assert.equal(models.marketInventoryHash,fingerprint.marketInventoryHash);
assert.equal(models.scope,'game');assert.equal(models.runs,200);assert.equal(models.events.length+models.rejected.length,quotes.events.length);
assert.equal(models.coverage.teams,new Set(models.events.flatMap(e=>[e.home,e.away])).size);
assert.equal(models.coverage.rosteredPlayers,new Set(models.events.flatMap(e=>e.players.map(p=>p.id))).size);
for(const event of quotes.events){const matching=require('../scripts/match_market_schedule.cjs')(NBAI_SEASON_DATA,event);assert.equal(models.events.some(e=>e.eventId===event.id),!!matching);}
for(const [file,sha] of Object.entries(models.sourceHashes))assert.equal(crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'),sha);
for(const e of models.events){
 assert.deepEqual(e.players.map(p=>p.id).sort((a,b)=>a-b),Object.values(NBAI_SEASON_DATA.players).filter(p=>[e.home,e.away].includes(p.team)).map(p=>p.id).sort((a,b)=>a-b));
 const event=quotes.events.find(x=>x.id===e.eventId),game=NBAI_SEASON_DATA.schedule.find(g=>g.id===e.gameId);
 assert.equal(Date.parse(e.officialStartsAt),Date.parse(game.tip));assert.ok(Math.abs(Date.parse(e.startsAt)-Date.parse(game.tip))<=15*60000);assert.equal((Date.parse(e.startsAt)-Date.parse(game.tip))/60000,e.scheduleOffsetMinutes);assert.equal(Date.parse(e.startsAt),Date.parse(event.commence_time));
 assert.equal(NBAI_SEASON_DATA.teams[e.home].name,event.home_team);assert.equal(NBAI_SEASON_DATA.teams[e.away].name,event.away_team);
 assert.ok(e.gameProjection);assert.ok(e.gameProjection.homeWinShare>=0&&e.gameProjection.homeWinShare<=1);
 assert.ok(Math.abs(e.gameProjection.homeScore.mean-e.gameProjection.awayScore.mean-e.gameProjection.homeMargin.mean)<1e-8);
 if(saved){const scores=saved.find(x=>x.eventId===e.eventId).scores;assert.equal(scores.length,models.runs);assert.deepEqual(e.gameProjection.homeMargin,summary(scores.map(g=>g.H-g.A)));}
 for(const p of e.players){
  for(const stat of ['PTS','AST','REB','STL','BLK','FG_PCT','FT_PCT','FG3_PCT']){const x=p.stats[stat];if(x){assert.ok([x.mean,x.p10,x.p90].every(Number.isFinite));assert.ok(x.p10<=x.p90);assert.ok(x.mean>=0);if(stat.endsWith('_PCT'))assert.ok(x.mean<=100);}}
  if(!saved)continue;
  const samples=saved.find(x=>x.eventId===e.eventId).players.find(x=>x.player.id===p.id).samples;
  assert.equal(p.playedScenarios,samples.length);assert.ok(samples.length<=200);
  for(const stat of ['PTS','AST','REB','STL','BLK'])assert.deepEqual(p.stats[stat],summary(samples.map(x=>x[stat])));
  for(const [stat,[made,attempts]] of Object.entries({FG_PCT:['FGM','FGA'],FT_PCT:['FTM','FTA'],FG3_PCT:['FG3M','FG3A']}))assert.deepEqual(p.stats[stat],summary(samples.filter(x=>x[attempts]>0).map(x=>100*x[made]/x[attempts])));
 }
}
console.log(JSON.stringify({passed:true,matchedGames:models.events.length,players:models.events.reduce((n,e)=>n+e.players.length,0),runs:models.runs,sourceHashesVerified:true}));
