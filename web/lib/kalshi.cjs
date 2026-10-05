'use strict';
const crypto=require('node:crypto');
const BASE='https://external-api.kalshi.com/trade-api/v2/markets';
const GROUPS={player:['KXNBAPTS','KXNBAAST','KXNBAREB'],season:['KXNBAWINS','KXNBA']};
const STATS={KXNBAPTS:'PTS',KXNBAAST:'AST',KXNBAREB:'REB'};
function quote(m,key){const v=m[key+'_dollars'];if(typeof v!=='string'||!/^\d+(\.\d+)?$/.test(v))return null;const n=Number(v);return n<0||n>1||key.endsWith('ask')&&n===0?null:n;}
function normalize(m,series,receipt){
  if(!m.ticker?.startsWith(series+'-')||!['active','open'].includes(m.status)||m.market_type!=='binary'||!m.rules_primary)return null;
  const stat=STATS[series]||null,rules=m.rules_primary;
  let player=null,scope='unknown',unit=null,threshold=null;
  if(stat){
    const match=/^(.+?):\s*\d+(?:\.\d+)?\+/.exec(m.title||'');player=match?.[1]||null;
    if(/\bgame originally scheduled for\b/i.test(rules))scope='game';
    // Do not infer season totals, averages or operator semantics from a series name.
    if(/2026[-–]27 regular season/i.test(rules)&&!/\bgame originally scheduled for\b/i.test(rules))scope='season';
    unit=scope==='game'?'game':scope==='season'?(/\baverage|per game\b/i.test(rules)?'average':/\btotal\b/i.test(rules)?'total':null):null;
    if(m.strike_type==='greater'&&typeof m.floor_strike==='number'&&Number.isFinite(m.floor_strike))threshold=m.floor_strike;
  }else if(!new RegExp('^'+series+'-27').test(m.ticker)||!/2026.?27|2027/.test(rules))return null;
  const bid=quote(m,'yes_bid'),ask=quote(m,'yes_ask');
  return {ticker:m.ticker,event:m.event_ticker,title:m.title,outcome:m.yes_sub_title||'',player,stat,scope,unit,threshold,operator:threshold===null?null:'>',
    category:stat?'Player '+({PTS:'points',AST:'assists',REB:'rebounds'}[stat]):series==='KXNBAWINS'?'Team wins':'Championship',
    rules,supplementaryRules:m.rules_secondary||'',earlyCloseCondition:m.early_close_condition||'',
    yesBid:bid,yesAsk:ask,noBid:quote(m,'no_bid'),noAsk:quote(m,'no_ask'),
    marketProbability:bid>0&&ask!==null&&bid<=ask&&ask<1&&Number(m.yes_bid_size_fp)>0?(bid+ask)/2:null,
    observedAt:receipt.observedAt,sourceUpdatedAt:m.updated_time||null,sourceUrl:receipt.url,receiptSha256:receipt.sha256,
    closeTime:m.close_time||null,url:'https://kalshi.com/markets/'+series.toLowerCase()+'?op_market_ticker='+encodeURIComponent(m.ticker)};
}
async function collect(group,fetcher=fetch){
  if(!GROUPS[group])throw Error('Unsupported market group');
  const markets=[],coverage=[],receipts=[],signal=AbortSignal.timeout(20000);
  // Bounded, sequential requests: complete each inventory or fail without replacing the last snapshot.
  for(const series of GROUPS[group]){
    let cursor='',seen=new Set(),count=0;
    for(let page=0;page<5;page++){
      const u=new URL(BASE);u.search=new URLSearchParams({series_ticker:series,status:'open',limit:'1000',...(cursor?{cursor}:{})});
      const r=await fetcher(u.toString(),{signal,redirect:'error',headers:{Accept:'application/json'}});
      if(!r.ok)throw Error('Market source unavailable ('+r.status+')');
      const raw=await r.text();if(raw.length>8000000)throw Error('Inventory too large');
      const d=JSON.parse(raw);if(!Array.isArray(d.markets))throw Error('Invalid inventory');
      const receipt={url:u.toString(),observedAt:new Date().toISOString(),sha256:crypto.createHash('sha256').update(raw).digest('hex')};receipts.push(receipt);
      for(const m of d.markets){const row=normalize(m,series,receipt);if(row)markets.push(row);}count+=d.markets.length;
      cursor=d.cursor||'';
      if(!cursor){coverage.push({series,complete:true,pages:page+1,rawContracts:count});break;}
      if(typeof cursor!=='string'||seen.has(cursor)||page===4)throw Error('Incomplete pagination');seen.add(cursor);
    }
  }
  return {schemaVersion:1,group,generatedAt:new Date().toISOString(),coverage,receipts,markets};
}
module.exports={collect,normalize,quote,GROUPS};
