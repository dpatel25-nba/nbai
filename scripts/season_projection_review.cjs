// A stronger player omitted from the healthy rotation can create misleading injury benefits.
module.exports=function(data,engine,team){
 const rotation=engine.rotation(data,team,engine.assignments(data)),ids=new Set(rotation.map(p=>p.id));
 const strongest=Math.max(...rotation.map(p=>data.players[p.id].bpm));
 const omitted=Object.values(data.players).filter(p=>p.team===team&&!ids.has(p.id)&&p.MPG>=20&&p.bpm>strongest);
 return omitted.length?'Held for rotation review: '+omitted.map(p=>p.n).join(', ')+' has a higher impact rating than the active rotation but receives no healthy-scenario minutes. Absences can misleadingly improve this team.':null;
};
