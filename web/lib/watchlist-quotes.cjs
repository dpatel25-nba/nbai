'use strict';
const crypto=require('node:crypto');
const targets=require('./game-watchlist-targets.json');
const BASE='https://external-api.kalshi.com/trade-api/v2/markets/';
function book(payload,side){
 if(!['yes','no'].includes(side))throw Error('Invalid side');
 const parse=s=>{const a=payload.orderbook_fp?.[s+'_dollars'];if(!Array.isArray(a))throw Error('Missing depth');const seen=new Set();return a.map(([p,q])=>{if(!/^\d+\.\d{1,4}$/.test(p)||!/^\d+(\.\d{1,2})?$/.test(q))throw Error('Invalid precision');p=Number(p);q=Number(q);if(!(p>0&&p<1&&q>=0)||seen.has(p))throw Error('Invalid depth');seen.add(p);return [p,q];}).filter(x=>x[1]>0);};
 const yes=parse('yes'),no=parse('no');
 if(yes.length&&no.length&&Math.max(...yes.map(x=>x[0]))+Math.max(...no.map(x=>x[0]))>=1)throw Error('Crossed book');
 const asks=(side==='yes'?no:yes).map(([p,q])=>[Math.round((1-p)*10000)/10000,q]).sort((a,b)=>a[0]-b[0]);
 return {asks,ask:asks[0]?.[0]??null,askSize:asks[0]?.[1]??0};
}
function cost(asks,quantity){
 if(!Number.isInteger(quantity)||quantity<1||quantity>100)throw Error('Invalid quantity');
 let remaining=BigInt(quantity*100),principal=0n,fees=0n,accumulator=0n,worst=null;
 for(const [price,size] of asks){
  const p=BigInt(Math.round(price*10000)),take=remaining<BigInt(Math.round(size*100))?remaining:BigInt(Math.round(size*100));if(!take)continue;
  const paid=p*take,fee=(7n*take*p*(10000n-p)+999999n)/1000000n;
  const rounding=((paid+fee+9999n)/10000n)*10000n-paid-fee;accumulator+=rounding;
  const rebate=(accumulator/10000n<(fee+rounding)/10000n?accumulator/10000n:(fee+rounding)/10000n)*10000n;
  accumulator-=rebate;principal+=paid;fees+=fee+rounding-rebate;remaining-=take;worst=price;if(!remaining)break;
 }
 if(remaining)return null;
 return {quantity,principal:Number(principal)/1e6,fee:Number(fees)/1e6,total:Number(principal+fees)/1e6,unitCost:Number(principal+fees)/1e6/quantity,worstPrice:worst};
}
async function collect(gameId,quantity,fetcher=fetch,now=Date.now()){
 const game=targets.games.find(x=>x.id===gameId);if(!game)throw Error('Unknown game');
 if(now>=Date.parse(game.starts_at)||now>=Date.parse(targets.expires_at))return {gameId,quantity,generatedAt:new Date(now).toISOString(),status:'Pregame monitoring ended',quotes:[]};
 const signal=AbortSignal.timeout(18000);
 async function get(url){const r=await fetcher(url,{method:'GET',redirect:'error',signal,headers:{Accept:'application/json'}});if(!r.ok)throw Error('Source unavailable');const raw=await r.text();if(raw.length>2000000)throw Error('Response too large');return {payload:JSON.parse(raw),receivedAt:new Date().toISOString(),sha256:crypto.createHash('sha256').update(raw).digest('hex')};}
 const gameDate=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(game.starts_at)).replaceAll('-','');
 const context=await get('https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates='+gameDate);
 const aliases={NY:'NYK',SA:'SAS',GS:'GSW',NO:'NOP',WSH:'WAS'};
 const events=(context.payload.events||[]).filter(e=>{const teams=Object.fromEntries((e.competitions?.[0]?.competitors||[]).map(c=>[c.homeAway,aliases[c.team.abbreviation]||c.team.abbreviation]));return teams.home===game.home&&teams.away===game.away;});
 if(events.length!==1||events[0].status?.type?.state!=='pre'||Date.parse(events[0].date)!==Date.parse(game.starts_at))return {gameId,quantity,generatedAt:new Date().toISOString(),status:'Pregame context unavailable or changed',quotes:[]};
 const quotes=await Promise.all(targets.singles.filter(x=>x.game_id===gameId).map(async p=>{
  try{
   const [meta,depth]=await Promise.all([get(BASE+p.ticker),get(BASE+p.ticker+'/orderbook?depth=100')]);
   const m=meta.payload.market,hash=crypto.createHash('sha256').update((m?.rules_primary||'')+'\n'+(m?.rules_secondary||'')).digest('hex');
   if(m?.ticker!==p.ticker||m.status!=='active'||m.notional_value_dollars!=='1.0000'||hash!==p.rules_sha256)throw Error('Contract changed or inactive');
   const b=book(depth.payload,p.side);return {id:p.id,status:'Observed',receivedAt:depth.receivedAt,receiptSha256:depth.sha256,contractTitle:m.title,yesLabel:m.yes_sub_title,noLabel:m.no_sub_title,rulesHash:hash,priceRanges:m.price_ranges,priceLevelStructure:m.price_level_structure,...b,cost:cost(b.asks,quantity),tradeEligible:false};
  }catch(_){return {id:p.id,status:'Quote unavailable or contract changed',tradeEligible:false};}
 }));
 return {gameId,quantity,generatedAt:new Date().toISOString(),status:'Observation only',feeScenario:'General fee coefficient 0.07; fill rounding and per-order accumulation. Contract applicability unverified.',quotes,actualOrders:0};
}
module.exports={book,cost,collect};
