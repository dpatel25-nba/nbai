(function(root){
  const normalize=s=>String(s??'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
  const STATS={PTS:'Points',AST:'Assists',REB:'Rebounds',STL:'Steals',BLK:'Blocks',FG_PCT:'FG%',FT_PCT:'FT%',FG3_PCT:'3P%'};
  const MAX_AGE=15*60*1000;
  function validTime(value,now){const t=Date.parse(value);return Number.isFinite(t)&&t<=now+60000;}
  function sources(data,player,event,stat,now=Date.now()){
    const result={};
    for(const provider of ['fanduel','prizepicks']){
      const rows=(data?.quotes||[]).filter(q=>q.provider===provider&&q.eventId===event?.id&&normalize(q.player)===normalize(player?.name)&&q.stat===stat&&q.scope==='game'&&q.unit==='game'&&q.mainLine===true&&Number.isFinite(q.line)&&q.line>=0&&q.homeTeam===event.home_team&&q.awayTeam===event.away_team&&Date.parse(q.startsAt)===Date.parse(event.commence_time)&&validTime(q.observedAt,now)&&validTime(q.sourceUpdatedAt,now));
      result[provider]=rows.length===1?rows[0]:null;
    }
    return result;
  }
  function compare(data,player,event,stat,forecast,now=Date.now()){
    const books=sources(data,player,event,stat,now),available=Object.values(books).filter(Boolean);
    const median=available.length===2?(books.fanduel.line+books.prizepicks.line)/2:null;
    const fresh=available.length===2&&available.every(q=>now-Date.parse(q.observedAt)<=MAX_AGE&&now-Date.parse(q.sourceUpdatedAt)<=MAX_AGE)&&Date.parse(event?.commence_time)>now;
    const match=forecast?.eventId===event?.id&&Date.parse(forecast?.startsAt)===Date.parse(event?.commence_time);
    const p=match?forecast.players?.find(p=>p.id===player?.id&&normalize(p.name)===normalize(player.name)):null;
    const raw=p?.stats?.[stat],model=raw&&[raw.mean,raw.p10,raw.p90].every(Number.isFinite)?raw:null;
    const held=forecast?.reviewRequired||player?.reviewRequired;
    let reason='Research comparison only; no validated betting signal.';
    if(available.length<2)reason=available.length?'One source only; consensus unavailable.':'No matching lines in this snapshot.';
    else if(!fresh)reason='Saved, stale or started-game lines; refresh required for a current comparison.';
    else if(held)reason=held;
    else if(!model)reason='Matching game projection unavailable; season averages are not substituted.';
    const snapshotGap=median!==null&&model&&!held&&Date.parse(event?.commence_time)>now?model.mean-median:null;
    const gap=fresh?snapshotGap:null;
    return {books,median,sourceCount:available.length,fresh,model:held?null:model,gap,snapshotGap,reason,held:held||null};
  }
  const api={normalize,STATS,MAX_AGE,sources,compare};if(typeof module!=='undefined')module.exports=api;else root.NBAI_CONSENSUS=api;
})(typeof window!=='undefined'?window:globalThis);
