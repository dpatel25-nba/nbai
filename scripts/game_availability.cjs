'use strict';
function forGame(snapshot,data,game,now=Date.now()){
 const held=reason=>({holdReason:reason,outIds:[],players:[],validUntil:snapshot?.validUntil||null});
 if(snapshot?.schemaVersion!==1||!Number.isFinite(Date.parse(snapshot.validUntil))||Date.parse(snapshot.validUntil)<=now||
   !Number.isFinite(Date.parse(snapshot.generatedAt))||Date.parse(snapshot.generatedAt)>now)
  return held('Current availability needs a refresh (maximum observation age: six hours).');
 const matches=snapshot.events.filter(e=>e.home===game.home&&e.away===game.away&&e.season===data.season&&Math.abs(Date.parse(e.startsAt)-Date.parse(game.tip))<=15*60000);
 if(matches.length!==1)return held('No current availability observation matched to this game.');
 const e=matches[0];if(e.holdReason)return held(e.holdReason);
 const actual=Object.values(data.players).filter(p=>[game.home,game.away].includes(p.team));
 const key=p=>`${p.id}:${p.team}`,expected=actual.map(key).sort(),observed=e.players.map(key).sort();
 if(JSON.stringify(expected)!==JSON.stringify(observed))return held('Official and injury-provider rosters differ; current membership needs review.');
 const out=e.players.filter(p=>p.out);
 if([game.home,game.away].some(t=>e.players.filter(p=>p.team===t&&!p.out).length<5))return held('Fewer than five available players on a team.');
 return {holdReason:null,outIds:out.map(p=>String(p.id)),players:e.players,validUntil:snapshot.validUntil,
   observedAt:snapshot.injuryObservedAt,source:snapshot.source,sourceUrl:snapshot.sourceUrl,
   excluded:out.map(p=>({id:p.id,name:p.name,team:p.team,status:p.status})),
   uncertain:e.players.filter(p=>!p.out&&p.status!=='Not listed').map(p=>({id:p.id,name:p.name,team:p.team,status:p.status}))};
}
module.exports={forGame};
