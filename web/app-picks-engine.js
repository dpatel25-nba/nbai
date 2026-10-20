(function(root){
 'use strict';
 const node=typeof module!=='undefined',C=node?require('./consensus-analysis.js'):root.NBAI_CONSENSUS,D=node?require('./day-board.js'):root.NBAI_DAY;
 const APPS={
  prizepicks:{name:'PrizePicks',up:'More',down:'Less',url:'https://app.prizepicks.com/',format:'Player Picks',rules:'https://www.prizepicks.com/help-center/payouts'},
  underdog:{name:'Underdog',up:'Higher',down:'Lower',url:'https://underdogfantasy.com/',format:'Classic Pick’em',rules:'https://help.underdogsports.com/en/articles/11010091-correlated-picks-how-stacking-players-affects-your-payout-multiplier'}
 };
 function candidates(market,events,app,now=Date.now()){
  if(!APPS[app])return [];
  const result=[];
  for(const event of events){
   if(Date.parse(event.commence_time)<=now||C.forecastHold(event.model,now))continue;
   for(const r of D.props(market,event,now)){
    const quote=r.books[app];
    if(!quote||!r.model||r.held||['limited_participation','reported_out','no_projected_minutes'].includes(r.participation)||!r.player.team)continue;
    const gap=r.model.mean-quote.line;
    if(Math.abs(gap)<.01)continue;
    const direction=gap>0?'up':'down',fresh=[quote.observedAt,quote.sourceUpdatedAt].every(t=>now-Date.parse(t)<=C.MAX_AGE);
    const key=JSON.stringify([app,event.id,r.player.id,r.stat,quote.line,direction]);
    result.push({...r,key,event,quote,gap,direction,fresh,rank:Math.abs(gap)/Math.max(1,(r.model.p90-r.model.p10)/2)});
   }
  }
  return result.sort((a,b)=>Number(b.fresh)-Number(a.fresh)||b.rank-a.rank||a.key.localeCompare(b.key));
 }
 function validCombo(legs){
  return legs.length>=2&&legs.length<=6&&new Set(legs.map(l=>l.quote.provider)).size===1&&
   new Set(legs.map(l=>l.player.id)).size===legs.length&&legs.every(l=>l.player.team)&&new Set(legs.map(l=>l.player.team)).size>=2;
 }
 // A transparent shortlist heuristic, not a joint-probability or payout optimizer.
 function drafts(rows){
  const pool=rows.slice(0,24),best=new Map();
  function consider(legs){
   if(!validCombo(legs))return;
   const score=legs.reduce((s,l)=>s+l.rank,0)/legs.length,fresh=legs.every(l=>l.fresh),prior=best.get(legs.length);
   if(!prior||Number(fresh)>Number(prior.fresh)||(fresh===prior.fresh&&score>prior.score))best.set(legs.length,{legs,score,fresh});
  }
  for(let i=0;i<pool.length;i++)for(let j=i+1;j<pool.length;j++){
   consider([pool[i],pool[j]]);
   for(let k=j+1;k<pool.length;k++)consider([pool[i],pool[j],pool[k]]);
  }
  return [...best.values()];
 }
 function payout(stake,multiplier,format){
  if(!Number.isFinite(stake)||stake<=0||!Number.isFinite(multiplier)||multiplier<=0||!['all','flex'].includes(format))return null;
  const total=stake*multiplier;
  if(!Number.isFinite(total))return null;
  return {total,profit:total-stake,breakEven:format==='all'&&multiplier>1?1/multiplier:null};
 }
 const api={APPS,candidates,validCombo,drafts,payout};
 if(node)module.exports=api;else root.NBAI_APP_PICKS=api;
})(typeof window!=='undefined'?window:globalThis);
