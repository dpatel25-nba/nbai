(function(root){
  const normalize=s=>String(s??'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
  const matchesPlayer=(quote,player)=>quote?.identityStatus==='unresolved'?false:quote?.playerId!=null?Number.isInteger(quote.playerId)&&quote.playerId===player?.id:normalize(quote?.player)===normalize(player?.name);
  const STATS={PTS:'Points',AST:'Assists',REB:'Rebounds',STL:'Steals',BLK:'Blocks',FG_PCT:'FG%',FT_PCT:'FT%',FG3_PCT:'3P%'};
  const PROVIDERS={fanduel:'FanDuel',prizepicks:'PrizePicks',draftkings:'DraftKings',betmgm:'BetMGM',fanatics:'Fanatics',betrivers:'BetRivers',williamhill_us:'Caesars',underdog:'Underdog',espnbet:'theScore Bet',hardrockbet:'Hard Rock Bet'};
  const MAX_AGE=15*60*1000;
  function validTime(value,now){const t=Date.parse(value);return Number.isFinite(t)&&t<=now+60000;}
  function sources(data,player,event,stat,now=Date.now()){
    const result={};
    for(const provider of Object.keys(PROVIDERS)){
      const rows=(data?.quotes||[]).filter(q=>q.provider===provider&&q.eventId===event?.id&&matchesPlayer(q,player)&&q.stat===stat&&q.scope==='game'&&q.unit==='game'&&q.mainLine===true&&Number.isFinite(q.line)&&q.line>=0&&q.homeTeam===event.home_team&&q.awayTeam===event.away_team&&Date.parse(q.startsAt)===Date.parse(event.commence_time)&&validTime(q.observedAt,now)&&validTime(q.sourceUpdatedAt,now));
      result[provider]=rows.length===1?rows[0]:null;
    }
    return result;
  }
  function eventsFor(player,data,forecasts,now=Date.now()){
    const choices=new Map();
    for(const e of forecasts?.events||[]){
      if(e.players?.some(p=>p.id===player?.id&&normalize(p.name)===normalize(player.name))&&Date.parse(e.startsAt)>now)
        choices.set(e.eventId,{id:e.eventId,home_team:e.homeName||e.home,away_team:e.awayName||e.away,commence_time:e.startsAt});
    }
    for(const e of data?.events||[]){
      if(Date.parse(e.commence_time)>now&&(choices.has(e.id)||(data.quotes||[]).some(q=>q.eventId===e.id&&matchesPlayer(q,player))))choices.set(e.id,e);
    }
    return [...choices.values()].sort((a,b)=>Date.parse(a.commence_time)-Date.parse(b.commence_time)||a.id.localeCompare(b.id));
  }
  function compare(data,player,event,stat,forecast,now=Date.now()){
    const books=sources(data,player,event,stat,now),available=Object.values(books).filter(Boolean);
    const values=available.map(q=>q.line).sort((a,b)=>a-b),middle=Math.floor(values.length/2);
    const median=values.length>=2?(values.length%2?values[middle]:(values[middle-1]+values[middle])/2):null;
    const fresh=available.length>=2&&available.every(q=>now-Date.parse(q.observedAt)<=MAX_AGE&&now-Date.parse(q.sourceUpdatedAt)<=MAX_AGE)&&Date.parse(event?.commence_time)>now;
    const match=forecast?.eventId===event?.id&&Date.parse(forecast?.startsAt)===Date.parse(event?.commence_time);
    const p=match?forecast.players?.find(p=>p.id===player?.id&&normalize(p.name)===normalize(player.name)):null;
    const raw=p?.stats?.[stat],model=raw&&[raw.mean,raw.p10,raw.p90].every(Number.isFinite)?raw:null;
    const held=forecast?.reviewRequired||player?.reviewRequired;
    let reason='Research comparison only; no validated betting signal.';
    if(available.length<2)reason=available.length?'One source only; consensus unavailable.':'No matching lines in this snapshot.';
    else if(!fresh)reason='Saved, stale or started-game lines; refresh required for a current comparison.';
    else if(held)reason=held;
    else if(!model)reason=p?.projectionStatus==='no_projected_minutes'?'No projected rotation minutes in these scenarios.':'Matching game projection unavailable; season averages are not substituted.';
    else if(p?.projectionStatus==='limited_participation')reason='Too few simulated appearances for a stable comparison.';
    const snapshotGap=median!==null&&model&&!held&&p?.projectionStatus!=='limited_participation'&&Date.parse(event?.commence_time)>now?model.mean-median:null;
    const gap=fresh?snapshotGap:null;
    return {books,median,sourceCount:available.length,fresh,model:held?null:model,gap,snapshotGap,reason,held:held||null,participation:p?.projectionStatus||null,playedScenarios:p?.playedScenarios??null};
  }
  const api={normalize,matchesPlayer,STATS,PROVIDERS,MAX_AGE,sources,eventsFor,compare};if(typeof module!=='undefined')module.exports=api;else root.NBAI_CONSENSUS=api;
})(typeof window!=='undefined'?window:globalThis);
