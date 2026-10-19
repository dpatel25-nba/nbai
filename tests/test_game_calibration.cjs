// Independent seeds catch the large, fixed level error caused by an 8-game warm-up.
const {test}=require('node:test'),assert=require('node:assert/strict');
global.window=global;require('../web/season-data.js');require('../web/season-possession.js');require('../web/season-engine.js');
const d=NBAI_SEASON_DATA;
test('Export calibration stays near team targets on independent simulation seeds',()=>{
 const precise=new NBAI_SEASON.Season(d,{injuries:false,calibrationGames:512,calibrationPasses:4});
 const fast=new NBAI_SEASON.Season(d,{injuries:false});
 for(const [home,away] of [['DET','BOS'],['NYK','PHI'],['SAS','OKC']]){
  const game=d.schedule.find(g=>g.home===home&&g.away===away);assert.ok(game);
  // Share a cache deliberately: precise mode must not reuse fast calibration.
  const cheap=fast.matchup(game);precise.calibrated=fast.calibrated;const state=precise.matchup(game);
  assert.notStrictEqual(state,cheap);
  let sum=0,sumSquares=0;const n=1600;
  for(let i=0;i<n;i++){
   const {score}=NBAI_SIM.playGame(state.H,state.A,id=>d.players[id],d.constants,d.league,state.pace,state.scale,NBAI_SEASON.hash('calibration-regression:'+game.id+':'+i),false,d.calibration);
   const margin=score.H-score.A;sum+=margin;sumSquares+=margin*margin;
  }
  const mean=sum/n,se=Math.sqrt((sumSquares/n-mean*mean)/n),target=state.scale.mu.H-state.scale.mu.A;
  assert.ok(Math.abs(mean-target)<1.5+3*se,`${home}/${away} mean ${mean} is far from target ${target}`);
 }
});
test('Invalid calibration budgets fail explicitly',()=>{
 for(const options of [{calibrationGames:0},{calibrationGames:Infinity},{calibrationPasses:1},{calibrationPasses:2.5}])assert.throws(()=>new NBAI_SEASON.Season(d,options),/calibration budget/);
});
