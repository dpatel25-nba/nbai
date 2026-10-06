// Percentages are season makes / attempts, never an average of game percentages.
const counting = ['PTS','AST','REB','STL','BLK'];
const shooting = {FG_PCT:['FGM','FGA'],FT_PCT:['FTM','FTA'],FG3_PCT:['FG3M','FG3A']};
const sampleFields = ['MIN',...counting,...Object.values(shooting).flat()];
function summary(values) {
  if (!values.length) return null;
  if (values.some(x=>!Number.isFinite(x))) throw Error('Non-finite projection sample');
  const x=[...values].sort((a,b)=>a-b);
  return {mean:x.reduce((s,n)=>s+n,0)/x.length,p10:x[Math.floor((x.length-1)*.1)],p90:x[Math.ceil((x.length-1)*.9)]};
}
function playerStats(samples) {
  const played=samples.filter(x=>x.gp>0),stats={};
  for (const stat of counting) stats[stat]={unit:'per_game',average:summary(played.map(x=>x[stat]/x.gp))};
  for (const [stat,[made,attempted]] of Object.entries(shooting)) {
    const attemptedSeasons=played.filter(x=>x[attempted]>0);
    stats[stat]={unit:'percent',average:summary(attemptedSeasons.map(x=>100*x[made]/x[attempted])),
      attemptsPerGame:summary(played.map(x=>x[attempted]/x.gp)),sampleSeasons:attemptedSeasons.length};
  }
  return stats;
}
module.exports={summary,playerStats,sampleFields};
