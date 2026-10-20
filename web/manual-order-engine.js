(function(root){
 'use strict';
 const finite=x=>typeof x==='number'&&Number.isFinite(x),time=x=>Date.parse(x);
 const fresh=(stamp,now,age=30000)=>Number.isFinite(time(stamp))&&now-time(stamp)>=-1000&&now-time(stamp)<=age;
 function dateET(stamp){return new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(stamp));}
 function estimate(cents,quantity){
  if(!Number.isInteger(cents)||cents<1||cents>99||!Number.isInteger(quantity)||quantity<1||quantity>100)throw Error('Invalid worksheet size');
  const c=BigInt(cents),q=BigInt(quantity),fee=Number((7n*q*c*(100n-c)+9999n)/10000n),principal=cents*quantity;
  return {quantity,principal:principal/100,fee:fee/100,total:(principal+fee)/100,unitCost:(principal+fee)/100/quantity,maxPayout:quantity};
 }
 function size(cents,budget){
  if(!finite(budget)||budget<1||budget>1000)return null;
  let best=null;for(let n=1;n<=100;n++){const c=estimate(cents,n);if(Math.round(c.total*100)<=Math.floor(budget*100+1e-7))best=c;else break;}return best;
 }
 function allowedTick(cents,ranges){
  if(!Array.isArray(ranges)||!ranges.length)return false;
  const unit=cents*100;
  return ranges.some(r=>{const [start,end,step]=['start','end','step'].map(k=>Math.round(Number(r[k])*10000));return Number.isFinite(start)&&Number.isFinite(end)&&step>0&&unit>=start&&unit<=end&&(unit-start)%step===0;});
 }
 function modelHold(watch,models,availability,game,pick,now){
  if(!watch||!models||!availability||watch.inputSignature!==models.inputSignature||watch.generatedAt!==models.generatedAt||watch.validUntil!==models.availabilityValidUntil||watch.validUntil!==availability.validUntil)return 'Forecast bundle is updating';
  if(!fresh(watch.generatedAt,now,86400000)||!(now<time(watch.validUntil)))return 'Availability / forecast expired';
  if(!(now<time(watch.expiresAt)))return 'Watchlist expired';
  const event=models.events?.find(e=>e.gameId===game.id);
  if(!event||event.startsAt!==game.starts_at||event.home!==game.home||event.away!==game.away||event.reviewRequired||event.availability?.holdReason||!event.gameProjection)return 'Game forecast on hold';
  if(pick.game_id!==game.id||pick.starts_at!==game.starts_at||!['yes','no'].includes(pick.side)||pick.id!==pick.ticker+':'+pick.side)return 'Contract identity mismatch';
  const f=pick.forecast;
  if(pick.hold||!f||![f.probability,f.samplingLower,f.targetAsk].every(finite)||f.probability<0||f.probability>1||f.samplingLower<0||f.samplingLower>f.probability||f.scenarios!==watch.runs||watch.runs<2000)return pick.hold||'Forecast unavailable';
  const ceiling=Math.max(0,Math.floor((f.samplingLower-.07)*100+1e-9));
  if(Math.abs(f.targetAsk-ceiling/100)>1e-9)return 'Forecast price mismatch';
  if(now>=time(game.starts_at)-300000)return 'Pregame cutoff reached';
  return null;
 }
 function build({watch,models,availability,reference,live={},budget=10,discount=2,now=Date.now()}){
  const rows=[];
  for(const game of watch?.games||[]){
   if(dateET(game.starts_at)!==dateET(now))continue;
   const response=live[game.id],refGame=reference?.quotes?.find(g=>g.gameId===game.id);
   for(const pick of game.singles||[]){
    const f=pick.forecast,anchor=refGame?.quotes?.find(q=>q.id===pick.id),quote=response?.quotes?.find(q=>q.id===pick.id);
    let hold=modelHold(watch,models,availability,game,pick,now);
    const cutoff=Math.min(time(game.starts_at)-300000,time(watch.validUntil),time(watch.expiresAt));
    const row={id:pick.id,gameId:game.id,matchup:game.away+' at '+game.home,startsAt:game.starts_at,
     label:pick.label,kind:pick.kind,ticker:pick.ticker,side:pick.side,contractTitle:anchor?.contractTitle||pick.ticker,
     expiresAt:Number.isFinite(cutoff)?new Date(cutoff).toISOString():null,hold,probability:f?.probability,samplingLower:f?.samplingLower,
     referenceAsk:anchor?.ask,referenceAt:anchor?.receivedAt,marketAsk:quote?.ask,quoteAt:quote?.receivedAt,
     limitCents:null,ceilingCents:null,cost:null,copyable:false,actualOrders:0};
    if(!hold&&(reference?.inputSignature!==watch.inputSignature||refGame?.status!=='Observation only'||anchor?.status!=='Observed'||anchor.rulesHash!==pick.rules_sha256||!finite(anchor.ask)||!(anchor.ask>0&&anchor.ask<1)||!fresh(anchor.receivedAt,now,21600000)))hold='Reference quote needs a refresh';
    if(!hold&&(!Number.isInteger(discount)||discount<0||discount>5))hold='Invalid entry discount';
    if(!hold){
     row.ceilingCents=Math.round(f.targetAsk*100);
     let limit=Math.min(row.ceilingCents,Math.floor(anchor.ask*100+1e-9)-discount);
     while(limit>0&&!allowedTick(limit,anchor.priceRanges))limit--;
     if(limit<1)hold='No positive limit on this contract’s tick grid';
     else{row.limitCents=limit;row.cost=size(limit,budget);if(!row.cost)hold='Budget cannot cover a contract and estimated fee';}
    }
    const quoteFresh=response?.status==='Observation only'&&quote?.status==='Observed'&&fresh(quote?.receivedAt,now)&&quote.rulesHash===pick.rules_sha256;
    row.quoteFresh=quoteFresh;
    if(!hold&&!quoteFresh)hold=response?.status==='Pregame context unavailable or changed'?'Game timing or status changed':'Current quote unavailable / stale';
    if(!hold&&!allowedTick(row.limitCents,quote.priceRanges))hold='Contract tick grid changed';
    if(!hold){
     const asks=quote.asks;
     if(!Array.isArray(asks)||asks.some(x=>!Array.isArray(x)||x.length!==2||!x.every(finite)||x[0]<=0||x[0]>=1||x[1]<0))hold='Depth unavailable';
     else{
      row.depthAtLimit=asks.filter(([p])=>p<=row.limitCents/100+1e-9).reduce((s,[,q])=>s+q,0);
      row.reached=finite(quote.ask)&&quote.ask<=row.limitCents/100;
      row.status=row.reached?(row.depthAtLimit>=row.cost.quantity?'At limit · review quote':'At limit · limited depth'):'Waiting for limit';
      row.copyable=true;
      row.selectionScore=f.samplingLower-estimate(Math.max(1,Math.min(99,Math.ceil(anchor.ask*100))),20).unitCost;
     }
    }
    row.hold=hold;if(hold){row.status='On hold';row.copyable=false;}
    // Never expose expired model prices as current recommendations.
    if(modelHold(watch,models,availability,game,pick,now)){row.limitCents=null;row.ceilingCents=null;row.cost=null;row.probability=null;row.samplingLower=null;}
    rows.push(row);
   }
  }
  return rows;
 }
 function shortlist(rows){
  const chosen=[];for(const id of new Set(rows.map(r=>r.gameId))){const candidates=rows.filter(r=>r.gameId===id&&r.copyable&&finite(r.selectionScore)&&r.selectionScore>=.05);candidates.sort((a,b)=>b.selectionScore-a.selectionScore||a.id.localeCompare(b.id));if(candidates[0])chosen.push(candidates[0]);}return chosen;
 }
 function worksheet(row,now=Date.now()){
  if(!row.copyable||!row.cost||!row.quoteFresh||!fresh(row.quoteAt,now)||!(now<time(row.expiresAt)))throw Error('Refresh before copying this worksheet');
  return ['NBAI — MANUAL RESEARCH WORKSHEET (not an order)',row.matchup+' · '+row.label,'Contract: '+row.contractTitle,'Ticker: '+row.ticker,
   'Side: BUY '+row.side.toUpperCase(),'Limit: '+row.limitCents+' cents per contract or lower','Quantity: '+row.cost.quantity,
   'Principal at limit: $'+row.cost.principal.toFixed(2),'Estimated fee scenario: $'+row.cost.fee.toFixed(2),'Total scenario: $'+row.cost.total.toFixed(2),
   'Review / expire by: '+row.expiresAt,'Reference ask: '+(row.referenceAsk*100).toFixed(1)+' cents at '+row.referenceAt,
   'Maximum price is not a predicted daily low. Verify availability, exact contract, expiration support and actual fees in Kalshi.',
   'No order submitted. Fills and profitability unverified.'].join('\n');
 }
 const api={dateET,fresh,estimate,size,allowedTick,modelHold,build,shortlist,worksheet};
 if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.NBAI_MANUAL_ORDERS=api;
})(typeof window==='undefined'?globalThis:window);
