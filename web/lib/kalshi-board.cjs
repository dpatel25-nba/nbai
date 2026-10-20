'use strict';
const crypto=require('node:crypto'),winnerIdentity=require('./kalshi-game-identity.cjs');
const BASE='https://external-api.kalshi.com/trade-api/v2';
const SERIES={
 KXNBAGAME:{kind:'winner'},KXNBAPTS:{stat:'PTS',words:'points'},KXNBAREB:{stat:'REB',words:'rebounds'},
 KXNBAAST:{stat:'AST',words:'assists'},KXNBA3PT:{stat:'FG3M',words:'made three-pointers'},
 KXNBASTL:{stat:'STL',words:'steals'},KXNBABLK:{stat:'BLK',words:'blocks'},
 KXNBAPRA:{stat:'PRA',words:'combined points, rebounds, and assists'},
 KXNBAPR:{stat:'PR',words:'combined points and rebounds'},KXNBAPA:{stat:'PA',words:'combined points and assists'},
 KXNBARA:{stat:'RA',words:'combined rebounds and assists'},KXNBAFTM:{stat:'FTM',words:'made free throws'},
 KXNBASTOCK:{stat:'STOCKS',words:'combined steals and blocks'},KXNBASTOCKS:{stat:'STOCKS',words:'combined steals and blocks'},
 KXNBA2D:{stat:'DD',words:'a double double'},KXNBA3D:{stat:'TD',words:'a triple double'}
};
const cities=Object.fromEntries([
 ['Atlanta','ATL'],['Boston','BOS'],['Brooklyn','BKN'],['Charlotte','CHA'],['Chicago','CHI'],['Cleveland','CLE'],
 ['Dallas','DAL'],['Denver','DEN'],['Detroit','DET'],['Golden State','GSW'],['Houston','HOU'],['Indiana','IND'],
 ['Los Angeles C','LAC'],['Los Angeles L','LAL'],['Memphis','MEM'],['Miami','MIA'],['Milwaukee','MIL'],
 ['Minnesota','MIN'],['New Orleans','NOP'],['New York','NYK'],['Oklahoma City','OKC'],['Orlando','ORL'],
 ['Philadelphia','PHI'],['Phoenix','PHX'],['Portland','POR'],['Sacramento','SAC'],['San Antonio','SAS'],
 ['Toronto','TOR'],['Utah','UTA'],['Washington','WAS']
]);
const number=v=>typeof v==='string'&&/^\d+(\.\d+)?$/.test(v)?Number(v):null;
function normalize(m,series,observedAt){
 const spec=SERIES[series];
 if(!spec||!m.ticker?.startsWith(series+'-')||m.event_ticker!==m.ticker.split('-').slice(0,2).join('-')||
    !['active','open'].includes(m.status)||m.market_type!=='binary'||m.notional_value_dollars!=='1.0000'||m.mve_selected_legs?.length)return null;
 let identity,player=null,threshold=null;
 if(spec.kind==='winner')identity=winnerIdentity(m);
 else{
  const r=/^If (.+?) records (.+?) in the (.+?) (?:vs|at) (.+?) Pro Basketball game originally scheduled for ([A-Z][a-z]{2} \d{1,2}, \d{4}), then the market resolves to Yes\.$/.exec(m.rules_primary||'');
  if(!r||!cities[r[3]]||!cities[r[4]]||r[3]===r[4])return null;
  player=r[1];
  if(!m.title?.startsWith(player+': ')||m.strike_type!=='greater'||!Number.isFinite(m.floor_strike))return null;
  if(['DD','TD'].includes(spec.stat)){
   if(r[2]!==spec.words||m.floor_strike!==.5)return null;
   threshold=.5;
  }else{
   const amount=/^(\d+)\+ (.+)$/.exec(r[2]);
   if(!amount||amount[2]!==spec.words||Number(amount[1])<1||m.floor_strike!==Number(amount[1])-.5||
      !m.title.startsWith(player+': '+amount[1]+'+ '))return null;
   threshold=m.floor_strike;
  }
  const date=new Date(r[5]+' 12:00:00 GMT');if(!Number.isFinite(date.getTime()))return null;
  identity={teams:[cities[r[3]],cities[r[4]]].sort(),date:date.toISOString().slice(0,10)};
 }
 if(!identity)return null;
 const raw={yesBid:number(m.yes_bid_dollars),yesAsk:number(m.yes_ask_dollars),noBid:number(m.no_bid_dollars),noAsk:number(m.no_ask_dollars)};
 const pricesValid=Object.values(raw).every(v=>v!==null&&v>=0&&v<=1)&&raw.yesBid<=raw.yesAsk&&raw.noBid<=raw.noAsk&&
  Math.abs(raw.yesAsk+raw.noBid-1)<.00011&&Math.abs(raw.noAsk+raw.yesBid-1)<.00011&&raw.yesBid+raw.noBid<1;
 const yesSize=number(m.yes_ask_size_fp),noSize=number(m.yes_bid_size_fp);
 return {ticker:m.ticker,event:m.event_ticker,series,kind:spec.kind||'prop',stat:spec.stat||null,player,threshold,identity,
  title:m.title,yesLabel:m.yes_sub_title||m.title,observedAt,sourceUpdatedAt:m.updated_time||null,
  yesAsk:pricesValid&&raw.yesAsk>0&&raw.yesAsk<1?raw.yesAsk:null,noAsk:pricesValid&&raw.noAsk>0&&raw.noAsk<1?raw.noAsk:null,
  yesSize:yesSize??0,noSize:noSize??0,quoteValid:pricesValid,
  rules:m.rules_primary,rulesHash:crypto.createHash('sha256').update((m.rules_primary||'')+'\n'+(m.rules_secondary||'')).digest('hex'),
  url:'https://kalshi.com/markets/'+series.toLowerCase()+'?op_market_ticker='+encodeURIComponent(m.ticker)};
}
async function collect(fetcher=fetch){
 const markets=[],coverage=[],signal=AbortSignal.timeout(26000);
 for(const series of Object.keys(SERIES)){
  let cursor='',seen=new Set(),rows=[],rawCount=0,pages=0;
  try{
   do{
    const url=BASE+'/markets?'+new URLSearchParams({series_ticker:series,status:'open',limit:'1000',...(cursor?{cursor}:{})});
    let response=await fetcher(url,{method:'GET',signal,redirect:'error',headers:{Accept:'application/json'}});
    if(response.status===429){await new Promise(r=>setTimeout(r,1200));response=await fetcher(url,{method:'GET',signal,redirect:'error'});}
    if(!response.ok)throw Error('Source unavailable');
    const text=await response.text();if(text.length>8000000)throw Error('Inventory too large');
    const data=JSON.parse(text),at=new Date().toISOString();
    if(!Array.isArray(data.markets))throw Error('Invalid inventory');
    rawCount+=data.markets.length;pages++;
    rows.push(...data.markets.map(m=>normalize(m,series,at)).filter(Boolean));
    cursor=data.cursor||'';
    if(cursor&&(typeof cursor!=='string'||seen.has(cursor)||pages>=5))throw Error('Incomplete inventory');
    seen.add(cursor);
    await new Promise(r=>setTimeout(r,200));
   }while(cursor);
   markets.push(...rows);coverage.push({series,complete:true,rawCount,accepted:rows.length,unsupported:rawCount-rows.length,pages});
  }catch(_){coverage.push({series,complete:false,rawCount,accepted:0,error:'Series unavailable or pagination incomplete'});}
 }
 if(!coverage.some(c=>c.complete))throw Error('Quotes unavailable');
 return {schemaVersion:1,generatedAt:new Date().toISOString(),markets,coverage,
  scope:'Open full-game NBA player thresholds, double/triple doubles and game winners. Protected, head-to-head, specials and period contracts are not ranked.'};
}
module.exports={SERIES,normalize,collect};
