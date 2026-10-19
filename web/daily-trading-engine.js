/* Local-only paper decisions. No real order submission or claimed fills. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NBAI_DAILY_TRADING=api;})(globalThis,()=>{
 'use strict';
 const MAX_AGE=120000,FEE_CENTS=2,EDGE=.05;
 const unitCost=ask=>Math.ceil(ask*100-1e-9)+FEE_CENTS;
 const day=t=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(t));
 const price=p=>typeof p==='number'&&Number.isFinite(p)&&p>=0&&p<=1;
 const fresh=(time,now)=>Number.isFinite(Date.parse(time))&&now-Date.parse(time)>=0&&now-Date.parse(time)<=MAX_AGE;
 function settings(c){
  const result={};for(const key of ['budget','stopLoss','maxBet']){const v=c[key];if(typeof v!=='number'||!Number.isFinite(v)||v<1||v>10000||Math.round(v*100)/100!==v)throw Error('Enter whole cents between $1 and $10,000.');result[key]=v;}
  if(c.stopLoss>c.budget||c.maxBet>c.budget)throw Error('Stop loss and maximum bet must fit within the daily budget.');return result;
 }
 function create(date,games,config,now=Date.now()){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(date)||!games.length||games.some(g=>day(g.commence_time)!==date))throw Error('Choose games from one daily slate.');
  return {version:1,mode:'paper',liveTrading:false,id:new Date(now).toISOString(),date,gameIds:[...new Set(games.map(g=>g.id))],config:settings(config),orders:[],activity:[],reasons:{},halt:null};
 }
 function matches(q,g){const i=q.gameIdentity;return !!i&&i.date===day(g.commence_time)&&i.teams.length===2&&[g.home,g.away].every(t=>i.teams.includes(t))&&i.teams.includes(i.yesTeam);}
 function validQuote(q,now){return fresh(q.observedAt,now)&&Date.parse(q.closeTime)>now&&price(q.yesBid)&&price(q.yesAsk)&&price(q.noBid)&&price(q.noAsk)&&q.yesBid<=q.yesAsk&&q.noBid<=q.noAsk;}
 function note(s,g,text,now){if(s.reasons[g.id]===text)return;s.reasons[g.id]=text;s.activity.push({at:new Date(now).toISOString(),gameId:g.id,game:g.away_team+' @ '+g.home_team,message:text});if(s.activity.length>150)s.activity.shift();}
 function valuation(s,quotes,now=Date.now()){
  let spent=0,marked=0,unpriced=0;const values={};
  for(const o of s.orders){spent+=o.costCents;const q=quotes.find(q=>q.ticker===o.ticker),bid=q?.[o.side+'Bid'];
   if(!q||!fresh(q.observedAt,now)||Date.parse(q.closeTime)<=now||!price(bid)){unpriced++;values[o.ticker]=null;continue;}
   const value=bid*100*o.quantity;marked+=value;values[o.ticker]={valueCents:value,netCents:value-o.costCents};
  }
  return {spentCents:spent,remainingCents:Math.round(s.config.budget*100)-spent,markedCents:marked,unpriced,netCents:unpriced?null:marked-spent,values};
 }
 function scan(s,games,quotes,{now=Date.now(),feedOK=false,projectionAt=null}={}){
  if(s.mode!=='paper'||s.liveTrading!==false)throw Error('Paper sessions only.');
  const before=valuation(s,quotes,now);
  if(before.netCents!==null&&before.netCents<=-Math.round(s.config.stopLoss*100))s.halt='Stop loss reached on the displayed bid estimate. New paper entries are paused; existing positions remain open.';
  if(day(now)>s.date)s.halt='This slate has ended. Existing paper positions remain in the record.';
  let remaining=before.remainingCents;
  for(const id of s.gameIds){const g=games.find(g=>g.id===id);if(!g)continue;
   const current=valuation(s,quotes,now);if(current.netCents!==null&&current.netCents<=-Math.round(s.config.stopLoss*100))s.halt='Stop loss reached on the displayed bid estimate. New entries paused; positions remain open.';
   let reason=s.halt;
   if(s.orders.some(o=>o.gameId===id))continue;
   if(!reason&&!feedOK)reason='Waiting for a successful live quote refresh.';
   if(!reason&&before.unpriced)reason='Waiting for fresh bid prices on existing paper positions.';
   if(!reason&&Date.parse(g.commence_time)<=now)reason='Tip time passed; in-game execution is not connected.';
   const p=g.model?.gameProjection?.homeWinShare;
   if(!reason&&(g.competition==='Preseason'||g.model?.reviewRequired||!price(p)||Date.parse(g.model?.startsAt)!==Date.parse(g.commence_time)))reason='No eligible pregame forecast for this matchup.';
   if(!reason&&g.model?.availability&&(!Number.isFinite(Date.parse(g.model.availability.validUntil))||Date.parse(g.model.availability.validUntil)<=now))reason='Availability observation expired; forecast refresh required.';
   const age=now-Date.parse(projectionAt);
   if(!reason&&(!Number.isFinite(age)||age<0||age>48*3600000))reason='Forecast snapshot needs a refresh (maximum age: 48 hours).';
   const mapped=quotes.filter(q=>matches(q,g));
   if(!reason&&!mapped.length)reason='No rule-matched Kalshi game-winner market returned.';
   const candidates=[];
   if(!reason)for(const q of mapped.filter(q=>validQuote(q,now))){
    const yes=q.gameIdentity.yesTeam===g.home?p:1-p;
    for(const side of ['yes','no']){const ask=q[side+'Ask'],probability=side==='yes'?yes:1-yes;
     if(ask>0&&ask<1)candidates.push({q,side,ask,probability,edge:probability-ask-FEE_CENTS/100});}
   }
   candidates.sort((a,b)=>b.edge-a.edge||a.q.ticker.localeCompare(b.q.ticker)||a.side.localeCompare(b.side));const c=candidates[0];
   if(!reason&&!c)reason='No fresh, complete two-sided quote available.';
   if(!reason&&c.edge<EDGE)reason='Pass: model difference is below the 5¢ research threshold after the fee assumption.';
   if(!reason){const unit=unitCost(c.ask),quantity=Math.floor(Math.min(remaining,Math.round(s.config.maxBet*100))/unit);
    if(quantity<1)reason='Daily budget cannot cover another whole contract.';
    else {const costCents=quantity*unit;s.orders.push({gameId:g.id,game:g.away_team+' @ '+g.home_team,ticker:c.q.ticker,title:c.q.title,side:c.side,quantity,entryAsk:c.ask,costCents,feeReserveCents:quantity*FEE_CENTS,modelProbability:c.probability,projectionAt,quoteAt:c.q.observedAt,sourceUrl:c.q.sourceUrl,receiptSha256:c.q.receiptSha256,createdAt:new Date(now).toISOString(),status:'paper_open',execution:'hypothetical_at_displayed_ask_not_a_verified_fill'});remaining-=costCents;reason='Paper entry recorded; displayed ask assumption, no exchange order or verified fill.';}
   }
   note(s,g,reason,now);
  }
  // Include the immediate spread/fee loss of newly opened paper positions.
  const after=valuation(s,quotes,now);if(after.netCents!==null&&after.netCents<=-Math.round(s.config.stopLoss*100))s.halt='Stop loss reached on the displayed bid estimate. New entries paused; positions remain open.';
  return s;
 }
 function restore(raw){
  if(raw?.version!==1||raw.mode!=='paper'||raw.liveTrading!==false||!/^\d{4}-\d{2}-\d{2}$/.test(raw.date)||!Array.isArray(raw.gameIds)||!Array.isArray(raw.orders)||!Array.isArray(raw.activity)||!raw.reasons||typeof raw.reasons!=='object')throw Error('Invalid saved paper session.');
  settings(raw.config);const seen=new Set();let spent=0;
  for(const o of raw.orders){if(!raw.gameIds.includes(o.gameId)||seen.has(o.gameId)||!['yes','no'].includes(o.side)||!Number.isInteger(o.quantity)||o.quantity<1||!price(o.entryAsk)||o.entryAsk===0||o.entryAsk===1||o.costCents!==o.quantity*unitCost(o.entryAsk)||o.feeReserveCents!==o.quantity*FEE_CENTS||o.costCents>Math.round(raw.config.maxBet*100))throw Error('Invalid paper order record.');seen.add(o.gameId);spent+=o.costCents;}
  if(spent>Math.round(raw.config.budget*100))throw Error('Invalid paper budget.');return raw;
 }
 return {day,settings,create,matches,validQuote,valuation,scan,restore,MAX_AGE,FEE_CENTS};
});
