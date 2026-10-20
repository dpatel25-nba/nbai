(function(root){
 'use strict';
 const STATS={PTS:'points',REB:'rebounds',AST:'assists',FG3M:'threes',STL:'steals',BLK:'blocks',PRA:'points + rebounds + assists',PR:'points + rebounds',PA:'points + assists',RA:'rebounds + assists',FTM:'free throws made',STOCKS:'steals + blocks',DD:'double double',TD:'triple double'};
 const SUMS={PRA:['PTS','REB','AST'],PR:['PTS','REB'],PA:['PTS','AST'],RA:['REB','AST'],STOCKS:['STL','BLK']},FRESH_MS=75000;
 const norm=s=>String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
 const day=t=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(t));
 const mean=a=>a.reduce((n,v)=>n+v,0)/a.length;
 function estimate(hits){const n=hits.length,p=mean(hits),z=1.96;return {probability:p,lower:(p+z*z/(2*n)-z*Math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n),n};}
 function fee(price){return Math.ceil((.07*price*(1-price)-1e-12)*100)/100;}
 function hold(game,bundle,now){
  if(Date.parse(game.startsAt)<=now)return 'Game has started — pregame opinion closed';
  if(!game.model)return 'Matching game forecast unavailable';
  if(game.model.reviewRequired)return game.model.reviewRequired;
  const until=Date.parse(bundle.validUntil);
  if(!Number.isFinite(until)||now>=until)return 'Availability expired — forecast refresh needed';
  if(game.model.scores?.length!==bundle.runs)return 'Aligned game scenarios unavailable';
  return null;
 }
 function playerHits(player,stat,threshold,runs){
  const v=player?.values;
  if(!v||!Number.isFinite(threshold)||!STATS[stat])return null;
  let fields=SUMS[stat]||[stat];
  if(stat==='DD'||stat==='TD')fields=['PTS','REB','AST','STL','BLK'];
  if(!fields.every(f=>Array.isArray(v[f])&&v[f].length===runs&&v[f].every(Number.isFinite)))return null;
  const values=Array.from({length:runs},(_,i)=>
   stat==='DD'||stat==='TD'?Number(fields.filter(f=>v[f][i]>=10).length>=(stat==='DD'?2:3)):
   fields.reduce((s,f)=>s+v[f][i],0));
  return {hits:values.map(n=>Number(n>threshold)),average:mean(values)};
 }
 function side(row,which,now){
  const price=row.market[which+'Ask'],size=row.market[which+'Size'],age=now-Date.parse(row.market.observedAt);
  const fresh=Number.isFinite(age)&&age>=-1000&&age<=FRESH_MS;
  const priced=row.market.quoteValid===true&&Number.isFinite(price)&&price>0&&price<1;
  const liquid=priced&&Number.isFinite(size)&&size>=1;
  const hits=row.hits?(which==='yes'?row.hits:row.hits.map(x=>1-x)):null,forecast=hits?estimate(hits):null;
  const feeEstimate=priced?fee(price):null;
  return {...row,side:which,price:priced?price:null,size,fresh,liquid,hits,forecast,fee:feeEstimate,
   edge:forecast&&priced?forecast.probability-price-feeEstimate:null,
   rank:forecast&&priced?forecast.lower-price-feeEstimate:null,
   eligible:!row.hold&&fresh&&liquid&&!!forecast};
 }
 function build(bundle,quotes,now=Date.now()){
  const games=new Map();
  for(const s of bundle?.schedule||[])games.set(s.id,{id:s.id,home:s.home,away:s.away,homeName:s.homeName,awayName:s.awayName,startsAt:s.startsAt,model:null,props:[],winners:[]});
  for(const m of bundle?.games||[])games.set(m.gameId,{id:m.gameId,home:m.home,away:m.away,homeName:m.homeName,awayName:m.awayName,startsAt:m.startsAt,model:m,props:[],winners:[]});
  const index=new Map();
  for(const g of games.values()){
   if(!Number.isFinite(Date.parse(g.startsAt)))continue;
   const key=day(g.startsAt)+'|'+[g.home,g.away].sort().join('|');
   const entries=index.get(key)||[];entries.push(g);index.set(key,entries);g.hold=hold(g,bundle,now);
  }
  const seen=new Map();
  for(const m of quotes?.markets||[])seen.set(m.ticker,(seen.get(m.ticker)||0)+1);
  let unmatched=0;
  for(const market of quotes?.markets||[]){
   const id=market.identity;if(!id||!Array.isArray(id.teams)||seen.get(market.ticker)!==1){unmatched++;continue;}
   const matches=index.get(id.date+'|'+[...id.teams].sort().join('|'));
   if(matches?.length!==1){unmatched++;continue;}
   const game=matches[0];let hits=null,player=null,average=null,reason=game.hold;
   if(market.kind==='winner'){
    if(![game.home,game.away].includes(id.yesTeam))continue;
    if(!reason)hits=game.model.scores.map(s=>Number(id.yesTeam===game.home?s[0]>s[1]:s[1]>s[0]));
   }else{
    const people=(game.model?.players||[]).filter(p=>norm(p.name)===norm(market.player)&&[game.home,game.away].includes(p.team));
    player=people.length===1?people[0]:null;
    if(!reason){
     if(!player)reason='Player identity not matched to this game';
     else if(player.projectionStatus!=='available'||!['Not listed','Active','Available','Probable'].includes(player.injuryStatus))reason='Participation uncertain or projection unavailable';
     else{
      const calculated=playerHits(player,market.stat,market.threshold,bundle.runs);
      if(calculated){hits=calculated.hits;average=calculated.average;}else reason='Aligned player scenarios unavailable';
     }
    }
   }
   const row={market,game,player,average,hits,hold:reason||null};
   row.yes=side(row,'yes',now);row.no=side(row,'no',now);
   row.best=[row.yes,row.no].filter(s=>s.eligible).sort((a,b)=>b.rank-a.rank)[0]||null;
   game[market.kind==='winner'?'winners':'props'].push(row);
  }
  return {games:[...games.values()].sort((a,b)=>Date.parse(a.startsAt)-Date.parse(b.startsAt)),unmatched};
 }
 function favorites(games,limit=3){
  const choices=games.flatMap(g=>g.props).map(r=>r.best).filter(r=>r?.eligible&&r.rank>0).sort((a,b)=>b.rank-a.rank||a.market.ticker.localeCompare(b.market.ticker));
  const result=[],seen=new Set();
  for(const r of choices){if(seen.has(r.player.id))continue;result.push(r);seen.add(r.player.id);if(result.length>=limit)break;}
  return result;
 }
 function winnerSides(game){
  return [game.away,game.home].map(team=>{
   const all=game.winners.map(r=>r.market.identity.yesTeam===team?r.yes:r.no);
   const usable=all.filter(r=>r.fresh&&r.liquid).sort((a,b)=>a.price-b.price||a.market.ticker.localeCompare(b.market.ticker));
   const choice=usable[0]||all.filter(r=>r.price!==null).sort((a,b)=>a.price-b.price)[0]||null;
   return {team,name:team===game.home?game.homeName:game.awayName,choice};
  });
 }
 function combo(game){
  const winner=winnerSides(game).filter(r=>r.choice?.eligible).sort((a,b)=>b.choice.forecast.probability-a.choice.forecast.probability)[0];
  if(!winner)return null;
  const props=favorites([game],Infinity),away=props.find(r=>r.player.team===game.away),home=props.find(r=>r.player.team===game.home);
  if(!away||!home)return null;
  const legs=[winner.choice,away,home],hits=legs[0].hits.map((_,i)=>Number(legs.every(l=>l.hits[i]===1)));
  return {legs,winner,joint:estimate(hits),marginalProduct:legs.reduce((v,l)=>v*l.forecast.probability,1),priced:false};
 }
 function label(row){
  const m=row.market;
  if(m.kind==='winner'){
   const team=row.side==='yes'?m.identity.yesTeam:row.game.home===m.identity.yesTeam?row.game.away:row.game.home;
   return (team===row.game.home?row.game.homeName:row.game.awayName)+' to win';
  }
  if(m.stat==='DD'||m.stat==='TD')return `${m.player} · ${row.side==='yes'?'':'No '}${STATS[m.stat]}`;
  return `${m.player} · ${row.side==='yes'?Math.floor(m.threshold)+1+'+':'Under '+m.threshold} ${STATS[m.stat]}`;
 }
 const api={STATS,FRESH_MS,day,estimate,fee,hold,playerHits,side,build,favorites,winnerSides,combo,label};
 if(typeof module!=='undefined')module.exports=api;else root.NBAI_KALSHI_DAY=api;
})(typeof window!=='undefined'?window:globalThis);
