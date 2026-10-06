// Matchup-specific scenarios for every rostered player in listed events, independent of quote coverage.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..');global.window=global;
require('../web/season-data.js');require('../web/season-possession.js');require('../web/season-engine.js');
const {summary}=require('./player_projection_stats.cjs'),review=require('./season_projection_review.cjs');
const d=NBAI_SEASON_DATA,market=JSON.parse(fs.readFileSync(path.join(root,'web/player-consensus.json')));
const matchEvent=require('./match_market_schedule.cjs');
const fingerprint=require('./market_projection_inputs.cjs').inputs(root,market);
const destination=path.join(root,'web/player-game-projections.json');
let previous;try{previous=JSON.parse(fs.readFileSync(destination));}catch(_){}
if(!process.argv.includes('--force')&&previous?.inputSignature===fingerprint.inputSignature){
 console.log(JSON.stringify({reused:true,events:previous.events.length,inputSignature:fingerprint.inputSignature}));process.exit(0);
}
const hash=s=>{let h=2166136261;for(let i=0;i<s.length;i++)h=Math.imul(h^s.charCodeAt(i),16777619);return h>>>0;};
const games=[],rejected=[];
for(const event of market.events){
  const match=matchEvent(d,event);
  if(!match){rejected.push({eventId:event.id,reason:event.sport_key==='basketball_nba_preseason'?'Preseason projections are not supported by the regular-season model':'Unique same-team schedule match within 15 minutes not verified'});continue;}
  const {home,away,game,offsetMinutes}=match;
  const players=Object.values(d.players).filter(p=>[home,away].includes(p.team)).map(player=>({player,samples:[]}));
  games.push({eventId:event.id,providerStartsAt:event.commence_time,offsetMinutes,game,players,scores:[],reviewRequired:review(d,NBAI_SEASON,home)||review(d,NBAI_SEASON,away)});
}
let cache;const runs=200,seedPrefix='market-matchups-v1:';
for(let i=0;i<runs&&games.length;i++){
  const season=new NBAI_SEASON.Season(d,{seed:seedPrefix+i});if(cache)season.calibrated=cache;
  for(const entry of games){
    const state=season.matchup(entry.game),g=NBAI_SIM.playGame(state.H,state.A,id=>d.players[id],d.constants,d.league,state.pace,state.scale,hash(seedPrefix+i+':'+entry.game.id),false,d.calibration);
    entry.scores.push(g.score);
    for(const p of entry.players){const box=g.box[p.player.team===entry.game.home?'H':'A'][p.player.id];if(box?.MIN>0)p.samples.push(box);}
  }
  cache=season.calibrated;
}
const shooting={FG_PCT:['FGM','FGA'],FT_PCT:['FTM','FTA'],FG3_PCT:['FG3M','FG3A']};
const output={schemaVersion:1,generatedAt:new Date().toISOString(),scope:'game',runs,seedPrefix,inputGeneratedAt:d.generated,
  coverage:{events:games.length,teams:new Set(games.flatMap(e=>[e.game.home,e.game.away])).size,rosteredPlayers:new Set(games.flatMap(e=>e.players.map(p=>p.player.id))).size,playersWithProjections:new Set(games.flatMap(e=>e.players.filter(p=>p.samples.length).map(p=>p.player.id))).size},
  schedule:d.schedule.filter(g=>!g.provisional).map(g=>({id:g.id,home:g.home,away:g.away,homeName:d.teams[g.home].name,awayName:d.teams[g.away].name,startsAt:new Date(g.tip).toISOString()})),
  unassignedPlayers:Object.values(d.players).filter(p=>!p.team).map(p=>({id:p.id,name:p.n,reason:'Roster assignment unresolved; no team matchup can be selected.'})),
  ...fingerprint,
  assumptions:'Matchup-specific possession scenarios conditional on playing. Historical injury spells affect rotations; current medical status, confirmed starters and minutes restrictions are not connected. Not calibrated betting probabilities. Ranges are individual-game scenario outcomes.',
  events:games.map(e=>({eventId:e.eventId,gameId:e.game.id,home:e.game.home,away:e.game.away,homeName:d.teams[e.game.home].name,awayName:d.teams[e.game.away].name,startsAt:e.providerStartsAt,officialStartsAt:new Date(e.game.tip).toISOString(),scheduleOffsetMinutes:e.offsetMinutes,scheduleMatchMethod:'Unique home/away match within 15 minutes',reviewRequired:e.reviewRequired,
    gameProjection:{homeScore:summary(e.scores.map(g=>g.H)),awayScore:summary(e.scores.map(g=>g.A)),homeMargin:summary(e.scores.map(g=>g.H-g.A)),total:summary(e.scores.map(g=>g.H+g.A)),homeWinShare:e.scores.filter(g=>g.H>g.A).length/runs},
    players:e.players.map(p=>({id:p.player.id,name:p.player.n,team:p.player.team,playedScenarios:p.samples.length,projectionStatus:p.samples.length===0?'no_projected_minutes':p.samples.length<20?'limited_participation':'available',minutes:summary(p.samples.map(b=>b.MIN)),
      stats:Object.fromEntries([...['PTS','AST','REB','STL','BLK'].map(s=>[s,summary(p.samples.map(b=>b[s]))]),...Object.entries(shooting).map(([s,[m,a]])=>[s,summary(p.samples.filter(b=>b[a]>0).map(b=>100*b[m]/b[a]))])])}))})),rejected};
const dir=path.join(root,'data/features/player_props/consensus');fs.mkdirSync(dir,{recursive:true});
fs.writeFileSync(path.join(dir,'game-projection-samples.json'),JSON.stringify(games));
fs.writeFileSync(destination+'.tmp',JSON.stringify(output,null,2)+'\n');fs.renameSync(destination+'.tmp',destination);
console.log(JSON.stringify({events:games.length,players:games.reduce((n,g)=>n+g.players.length,0),simulatedGames:games.length*runs,rejected}));
