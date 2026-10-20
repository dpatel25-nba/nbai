'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const FIELDS=['PTS','REB','AST','STL','BLK','FG3M','FTM'];
const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
function build(models,samples){
 if(!Number.isInteger(models.runs)||models.runs<100||!models.inputSignature)throw Error('Invalid model');
 return {schemaVersion:1,generatedAt:models.generatedAt,inputSignature:models.inputSignature,runs:models.runs,
  validUntil:models.availabilityValidUntil,calibration:models.calibration,schedule:models.schedule,
  games:models.events.map(m=>{
   const s=samples.find(e=>e.eventId===m.eventId&&e.game.id===m.gameId&&e.game.home===m.home&&e.game.away===m.away&&e.providerStartsAt===m.startsAt);
   if(!s)throw Error('Sample/game mismatch');
   const scores=s.scores.length===models.runs?s.scores.map(x=>[x.H,x.A]):[];
   if(scores.length&&(!scores.every(x=>x.every(Number.isFinite)&&x[0]!==x[1])||
      Math.abs(scores.reduce((n,x)=>n+x[0],0)/models.runs-m.gameProjection.homeScore.mean)>1e-7||
      Math.abs(scores.reduce((n,x)=>n+x[1],0)/models.runs-m.gameProjection.awayScore.mean)>1e-7))throw Error('Sample/forecast mismatch');
   const players=m.players.map(p=>{
    const raw=s.players.find(x=>x.player.id===p.id&&x.player.n===p.name&&x.player.team===p.team);
    if(!raw)throw Error('Player mismatch');
    const aligned=raw.samples.length===models.runs&&scores.length===models.runs;
    const values=aligned?Object.fromEntries(FIELDS.map(f=>[f,raw.samples.map(x=>x[f])])):null;
    if(values){
     if(Object.values(values).some(a=>a.some(v=>!Number.isInteger(v)||v<0)))throw Error('Invalid stat samples');
     for(const stat of ['PTS','REB','AST','STL','BLK'])if(Math.abs(values[stat].reduce((a,b)=>a+b,0)/models.runs-p.stats[stat].mean)>1e-7)throw Error('Player sample/forecast mismatch');
    }
    return {...p,values};
   });
   return {...m,players,scores};
  })};
}
function run(root){
 const modelFile=path.join(root,'web/player-game-projections.json'),raw=fs.readFileSync(modelFile,'utf8'),models=JSON.parse(raw);
 const samples=JSON.parse(fs.readFileSync(path.join(root,'data/features/player_props/consensus/game-projection-samples.json')));
 const out={...build(models,samples),sourceModelHash:hash(raw)};
 if(fs.readFileSync(modelFile,'utf8')!==raw)throw Error('Forecast changed during export; retry');
 const dest=path.join(root,'web/kalshi-board-model.json');fs.writeFileSync(dest+'.tmp',JSON.stringify(out));fs.renameSync(dest+'.tmp',dest);
 return {games:out.games.length,alignedPlayers:out.games.reduce((n,g)=>n+g.players.filter(p=>p.values).length,0),bytes:fs.statSync(dest).size};
}
if(require.main===module)console.log(JSON.stringify(run(path.resolve(__dirname,'..'))));
module.exports={build,run};
