// Fixed prospective numerical study. No observed outcomes or market fitting.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
global.window=global;require('../web/season-data.js');require('../web/season-possession.js');require('../web/season-engine.js');
const root=path.resolve(__dirname,'..'),out=path.join(root,'data/features/game_stability/20261019-v1');
fs.mkdirSync(out,{recursive:true});
const sources=['web/season-data.js','web/season-engine.js','web/season-possession.js','data/features/player_props/consensus/game-projection-samples.json','web/player-consensus.json'];
const hashes={};for(const f of sources){const raw=fs.readFileSync(path.join(root,f));hashes[f]=crypto.createHash('sha256').update(raw).digest('hex');const dest=path.join(out,hashes[f]+path.extname(f));if(!fs.existsSync(dest))fs.writeFileSync(dest,raw);}
const entries=JSON.parse(fs.readFileSync(path.join(root,sources[3]))).filter(e=>e.scores.length),data=NBAI_SEASON_DATA;
const mean=x=>x.reduce((a,b)=>a+b,0)/x.length;
const sd=x=>Math.sqrt(mean(x.map(v=>(v-mean(x))**2)));
const spec={batches:10,sizes:[200,2000],seedPrefix:'stability-v1',calibrationGames:512,calibrationPasses:4,classification:'Monte Carlo stability, not predictive accuracy',sourceHashes:hashes};
fs.writeFileSync(path.join(out,'spec.json'),JSON.stringify(spec,null,2));
const results=[];
for(const e of entries){
 const s=new NBAI_SEASON.Season(data,{injuries:false,calibrationGames:512,calibrationPasses:4});s.health.set(e.game.id,new Set(e.availability.outIds));const state=s.matchup(e.game),batches=[];
 for(let b=0;b<spec.batches;b++){
  const scores=[],points=Object.fromEntries(e.players.filter(p=>p.samples.length===200).map(p=>[p.player.id,[]]));
  for(let i=0;i<2000;i++){
   const g=NBAI_SIM.playGame(state.H,state.A,id=>data.players[id],data.constants,data.league,state.pace,state.scale,NBAI_SEASON.hash(`${spec.seedPrefix}:${e.game.id}:${b}:${i}`),false,data.calibration);scores.push(g.score);
   for(const id of Object.keys(points)){const box=g.box[data.players[id].team===e.game.home?'H':'A'][id];points[id].push(box?.MIN>0?box.PTS:null);}
  }
  for(const n of spec.sizes){const a=scores.slice(0,n);batches.push({batch:b,n,margin:mean(a.map(x=>x.H-x.A)),total:mean(a.map(x=>x.H+x.A)),win:mean(a.map(x=>Number(x.H>x.A))),points:Object.fromEntries(Object.entries(points).map(([id,x])=>[id,mean(x.slice(0,n).filter(v=>v!==null))]))});}
 }
 const sizes=spec.sizes.map(n=>{const x=batches.filter(x=>x.n===n);return {n,margin:{mean:mean(x.map(x=>x.margin)),sd:sd(x.map(x=>x.margin)),min:Math.min(...x.map(x=>x.margin)),max:Math.max(...x.map(x=>x.margin))},totalSD:sd(x.map(x=>x.total)),winShareSD:sd(x.map(x=>x.win)),homeFavoriteBatches:x.filter(x=>x.margin>0).length,players:Object.fromEntries(Object.keys(x[0].points).map(id=>[id,{name:data.players[id].n,pointsMean:mean(x.map(x=>x.points[id])),pointsSD:sd(x.map(x=>x.points[id]))}]))};});
 results.push({gameId:e.game.id,home:e.game.home,away:e.game.away,targetMargin:state.scale.mu.H-state.scale.mu.A,sizes,batches});console.log(JSON.stringify({game:e.game.id,sizes:sizes.map(({players,...x})=>x)}));
}
fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({generatedAt:new Date().toISOString(),...spec,results},null,2));
