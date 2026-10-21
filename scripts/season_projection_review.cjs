// Review the same Out-excluded rotation used by the matchup simulation.
module.exports=function(data,engine,team,excluded=new Set()){
 const rotation=engine.rotation(data,team,engine.assignments(data),excluded),ids=new Set(rotation.map(p=>p.id));
 const strongest=Math.max(...rotation.map(p=>data.players[p.id].bpm));
 const omitted=Object.values(data.players).filter(p=>p.team===team&&!excluded.has(String(p.id))&&!ids.has(p.id)&&p.MPG>=20&&p.bpm>strongest);
 return omitted.length?'Held for rotation review: '+omitted.map(p=>p.n).join(', ')+' has a higher impact rating than the active rotation but receives no scenario minutes. Absences can misleadingly improve this team.':null;
};
