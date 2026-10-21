(function(root){
 'use strict';
 const E=typeof module!=='undefined'?require('./kalshi-day-engine.js'):root.NBAI_KALSHI_DAY;
 const safeSource=s=>s&&typeof s.title==='string'&&/^https:\/\//.test(s.url||'');
 function reviewFor(data,game){
  const matches=(data?.games||[]).filter(r=>r.gameId===game.id&&
   Number.isFinite(Date.parse(r.startsAt))&&Date.parse(r.startsAt)===Date.parse(game.startsAt));
  return matches.length===1?matches[0]:null;
 }
 function reviewed(p){
  return p?.status==='reviewed'&&['yes','no'].includes(p.side)&&
   typeof p.thesis==='string'&&p.thesis.length>20&&typeof p.risk==='string'&&p.risk.length>20&&
   Number.isInteger(p.playerId)&&Number.isFinite(p.threshold)&&
   p.sources?.length>0&&p.sources.every(safeSource)&&
   Number.isFinite(p.maxPrice)&&p.maxPrice>0&&p.maxPrice<1;
 }
 function assess(p,game,review,now=Date.now()){
  const matches=game.props.filter(r=>r.market.ticker===p.ticker&&r.player?.id===p.playerId&&
   r.player?.team===p.team&&r.market.stat===p.stat&&r.market.threshold===p.threshold);
  const row=matches.length===1?matches[0][p.side]:null;
  let state='ready',status='Within our price limit';
  if(p.editorialDecision==='withdraw'){state='withdrawn';status='Withdrawn after review';}
  else if(now>=Date.parse(E.tip(game))){state='closed';status='Pregame closed';}
  else if(!Number.isFinite(Date.parse(review.reviewedAt))||Date.parse(review.reviewedAt)>now+1000||
          !Number.isFinite(Date.parse(review.validUntil))||now>=Date.parse(review.validUntil)){
   state='review';status='Research update needed';
  }else if(p.editorialDecision==='watch'){state='watch';status='Research watch — not a current pick';}
  else if(!row){state='wait';status='Exact contract unavailable';}
  else if(row.hold){state='review';status='Availability or forecast review needed';}
  else if(!row.fresh||!row.liquid){state='wait';status='Waiting for a current offer';}
  else if(row.price>p.maxPrice+1e-9){state='price';status='Wait for a lower price';}
  else if(!(row.edge>0)||!(row.rank>0)){state='review';status='Insufficient model margin at this price';}
  return {pick:p,game,review,row,state,status};
 }
 function picks(games,data,now=Date.now()){
  const out=[];
  for(const game of games){
   const review=reviewFor(data,game);if(!review)continue;
   const seen=new Set();
   for(const p of review.picks||[]){
    if(!reviewed(p)||seen.has(p.playerId))continue;
    seen.add(p.playerId);out.push(assess(p,game,review,now));
   }
  }
  return out.sort((a,b)=>(a.state==='ready'?0:a.state==='closed'?2:1)-(b.state==='ready'?0:b.state==='closed'?2:1)||
   (a.pick.priority??99)-(b.pick.priority??99));
 }
 function favorites(games,data,now=Date.now(),limit=3){
  const seen=new Set();
  return picks(games,data,now).filter(p=>p.pick.editorialDecision!=='withdraw').filter(p=>{
   if(seen.has(p.pick.playerId))return false;
   seen.add(p.pick.playerId);return true;
  }).slice(0,limit);
 }
 function combo(game,data,now=Date.now()){
  const review=reviewFor(data,game),c=review?.combo;
  if(!c||c.status!=='reviewed'||!c.script||!c.risk||!c.sources?.length||!c.sources.every(safeSource)||
     ![game.home,game.away].includes(c.winnerTeam)||c.propTickers?.length!==2)return null;
  const all=picks([game],data,now),props=c.propTickers.map(t=>all.find(p=>p.pick.ticker===t));
  if(props.some(p=>!p||p.pick.editorialDecision==='withdraw')||new Set(props.map(p=>p.pick.team)).size!==2)return null;
  const winner=E.winnerSides(game).find(w=>w.team===c.winnerTeam);
  const ready=props.every(p=>p.state==='ready')&&winner?.choice?.eligible;
  return {game,review,...c,props,winner,ready:!!ready,closed:now>=Date.parse(E.tip(game)),priced:false};
 }
 const api={reviewFor,reviewed,assess,picks,favorites,combo};
 if(typeof module!=='undefined')module.exports=api;else root.NBAI_RESEARCH_PICKS=api;
})(typeof window!=='undefined'?window:globalThis);
