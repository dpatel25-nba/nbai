(function(root){
 const C=typeof module!=='undefined'?require('./consensus-analysis.js'):root.NBAI_CONSENSUS;
 const day=t=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(t));
 const money=n=>Number.isFinite(n)?(n>0?'+':'')+n:'—';
 const probability=n=>!Number.isFinite(n)||Math.abs(n)<100?null:n>0?100/(n+100):-n/(-n+100);
 function games(market,models){
  const result=new Map();
  for(const s of models?.schedule||[])result.set(s.id,{id:'schedule:'+s.id,gameId:s.id,home_team:s.homeName,away_team:s.awayName,home:s.home,away:s.away,commence_time:s.startsAt,competition:'Regular season'});
  for(const e of market?.events||[]){
   const model=models?.events?.find(m=>m.eventId===e.id);
   const matches=(models?.schedule||[]).filter(s=>s.homeName===e.home_team&&s.awayName===e.away_team&&Math.abs(Date.parse(s.startsAt)-Date.parse(e.commence_time))<=900000);
   const scheduled=matches.length===1?matches[0]:null,key=model?.gameId||scheduled?.id||e.id;
   result.set(key,{...e,home:model?.home||scheduled?.home,away:model?.away||scheduled?.away,gameId:model?.gameId||scheduled?.id,model:model||null});
  }
  return [...result.values()].sort((a,b)=>Date.parse(a.commence_time)-Date.parse(b.commence_time)||a.id.localeCompare(b.id));
 }
 function projection(event){
  const m=event?.model;if(!m?.gameProjection||m.reviewRequired||Date.parse(m.startsAt)!==Date.parse(event.commence_time))return null;
  const p=m.gameProjection;if(![p.homeScore?.mean,p.awayScore?.mean,p.homeMargin?.mean,p.total?.mean,p.homeWinShare].every(Number.isFinite))return null;
  return {...p,winner:p.homeMargin.mean>0?event.home_team:p.homeMargin.mean<0?event.away_team:null,margin:Math.abs(p.homeMargin.mean)};
 }
 function gameLines(market,event,now=Date.now()){
  const matched=(market?.gameQuotes||[]).filter(q=>q.eventId===event.id&&q.scope==='game'&&q.homeTeam===event.home_team&&q.awayTeam===event.away_team&&Date.parse(q.startsAt)===Date.parse(event.commence_time));
  return matched.filter(q=>matched.filter(o=>o.provider===q.provider&&o.market===q.market).length===1).map(q=>({...q,fresh:[q.observedAt,q.sourceUpdatedAt].every(t=>Number.isFinite(Date.parse(t))&&now-Date.parse(t)>=-60000&&now-Date.parse(t)<=C.MAX_AGE)&&Date.parse(event.commence_time)>now}));
 }
 function teamComparison(quote,event){
  const p=projection(event);if(!p)return null;
  if(quote.market==='spreads'){const home=quote.outcomes.find(o=>o.name===event.home_team);return Number.isFinite(home?.point)?{model:p.homeMargin.mean,market:-home.point,difference:p.homeMargin.mean+home.point}:null;}
  if(quote.market==='totals'){const over=quote.outcomes.find(o=>o.name==='Over');return Number.isFinite(over?.point)?{model:p.total.mean,market:over.point,difference:p.total.mean-over.point}:null;}
  if(quote.market==='h2h'){const a=probability(quote.outcomes.find(o=>o.name===event.home_team)?.price),b=probability(quote.outcomes.find(o=>o.name===event.away_team)?.price);return a!=null&&b!=null?{model:p.homeWinShare*100,market:a/(a+b)*100,difference:(p.homeWinShare-a/(a+b))*100}:null;}
  return null;
 }
 function props(market,event,now=Date.now()){
  const rows=[];for(const player of event.model?.players||[])for(const stat of ['PTS','AST','REB','STL','BLK']){
   const call=C.compare(market,player,event,stat,event.model,now);if(!call.sourceCount)continue;
   rows.push({player,stat,...call});
  }
  // Show sourced lines even when no matched model exists, e.g. preseason.
  const unmodeled=(market?.quotes||[]).filter(q=>q.eventId===event.id&&!rows.some(r=>r.stat===q.stat&&C.matchesPlayer(q,r.player)));
  for(const q of unmodeled){const player={id:q.playerId,name:q.canonicalPlayer||q.player};if(rows.some(r=>r.player.name===player.name&&r.stat===q.stat))continue;const call=C.compare(market,player,event,q.stat,event.model,now);if(call.sourceCount)rows.push({player,stat:q.stat,...call});}
  return rows;
 }
 function teamWatchlist(market,event,now=Date.now()){
  const p=projection(event);if(!p||Date.parse(event.commence_time)<=now)return [];
  const choices=new Map();for(const q of gameLines(market,event,now).filter(q=>q.fresh&&q.market!=='h2h')){
   const comparison=teamComparison(q,event);if(!comparison||Math.abs(comparison.difference)<.01)continue;
   const homeSide=comparison.difference>0,range=q.market==='spreads'?p.homeMargin:p.total;
   const outcome=q.outcomes.find(o=>o.name===(q.market==='spreads'?(homeSide?event.home_team:event.away_team):(homeSide?'Over':'Under')));
   if(!outcome)continue;const row={quote:q,outcome,comparison,rank:Math.abs(comparison.difference)/Math.max(1,(range.p90-range.p10)/2)};
   if(!choices.has(q.market)||choices.get(q.market).rank<row.rank)choices.set(q.market,row);
  }return [...choices.values()].sort((a,b)=>b.rank-a.rank);
 }
 function watchlist(rows,event,now=Date.now()){
  if(Date.parse(event.commence_time)<=now||event.model?.reviewRequired)return [];
  return rows.filter(r=>r.gap!=null&&r.fresh&&Math.abs(r.gap)>.01).map(r=>({...r,direction:r.gap>0?'Over':'Under',rank:Math.abs(r.gap)/Math.max(1,(r.model.p90-r.model.p10)/2)})).sort((a,b)=>b.rank-a.rank).slice(0,3);
 }
 const api={day,money,probability,games,projection,gameLines,teamComparison,props,watchlist,teamWatchlist};if(typeof module!=='undefined')module.exports=api;else root.NBAI_DAY=api;
})(typeof window!=='undefined'?window:globalThis);
