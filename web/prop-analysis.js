(function(root){
  const normalize=s=>String(s).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
  function compare(player,stat,unit,line){
    const p=player?.stats?.[stat]?.[unit];
    if(!p||!Number.isFinite(line)||line<0)return {label:'Pass',reason:'A valid line and supported season projection are required.'};
    // Use both simulated spread and a separate 10% production stress. This is a research screen, not a calibrated probability.
    const low=Math.min(p.p10,p.mean*.9),high=Math.max(p.p90,p.mean*1.1);
    if(line<low)return {label:'Over lean',reason:'The line is below the scenario range and the 10% lower-production stress. Price, fees and model validation still need checking.'};
    if(line>high)return {label:'Under lean',reason:'The line is above the scenario range and the 10% higher-production stress. Price, fees and model validation still need checking.'};
    return {label:'Pass',reason:'The line is inside the scenario or production-stress range; no clear directional signal.'};
  }
  function fresh(row,now=Date.now()){
    const age=now-Date.parse(row.observedAt),close=Date.parse(row.closeTime);
    return Number.isFinite(age)&&age>=-60000&&age<=120000&&Number.isFinite(close)&&close>now;
  }
  function marketCall(player,row,live,now=Date.now()){
    if(!live||!fresh(row,now))return {label:'Pass',reason:'Quotes are stale, closed or unavailable.'};
    if(normalize(player?.name)!==normalize(row.player))return {label:'Pass',reason:'Player identity does not match.'};
    if(row.scope!=='season'||!['average','total'].includes(row.unit)||row.operator!=='>'||!Number.isFinite(row.threshold))return {label:'Pass',reason:'This contract needs a matching game forecast or reviewed settlement definition. A season average is not a game forecast.'};
    const result=compare(player,row.stat,row.unit,row.threshold);
    const ask=result.label==='Over lean'?row.yesAsk:row.noAsk;
    if(!Number.isFinite(ask)||ask<=0||ask>=1)return {label:'Pass',reason:'No usable buy quote for the suggested side.'};
    return result;
  }
  const api={normalize,compare,fresh,marketCall};if(typeof module!=='undefined')module.exports=api;else root.NBAI_PROPS=api;
})(typeof window!=='undefined'?window:globalThis);
