const {test}=require('node:test'),assert=require('node:assert/strict');
global.window=global;require('../web/season-data.js');require('../web/season-possession.js');require('../web/season-engine.js');
test('Actual first half, second half and overtime reconcile to final score',()=>{
 const d=NBAI_SEASON_DATA,s=new NBAI_SEASON.Season(d,{injuries:false}),g=d.schedule[0],state=s.matchup(g);let overtime=0;
 for(let i=0;i<300;i++){
  const result=NBAI_SIM.playGame(state.H,state.A,id=>d.players[id],d.constants,d.league,state.pace,state.scale,i,false,d.calibration);overtime+=Number(result.ot>0);
  for(const side of ['H','A']){assert.equal(Object.values(result.periods).reduce((v,x)=>v+x[side],0),result.score[side]);assert.ok(Object.values(result.periods).every(x=>Number.isInteger(x[side])&&x[side]>=0));if(!result.ot)assert.equal(result.periods.overtime[side],0);}
 }assert.ok(overtime>0);
});
