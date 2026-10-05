/* Read-only season research. Quotes, projections and reviewed picks stay distinct. */
(()=>{
  'use strict';
  const $=id=>document.getElementById(id),P=NBAI_PICKS;
  const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;};
  const cents=n=>Number.isFinite(n)&&n>0&&n<1?`${(n*100).toFixed(1).replace(/\.0$/,'')}¢`:'Unavailable';
  let rows=[],generatedAt=null,live=false,winProjections=null,category='Team wins',visible=9;
  const selection=new Map();
  const season='2026-27';
  function isFresh(row){const age=Date.now()-Date.parse(row.observedAt);return live&&age>=-60000&&age<=120000&&Date.parse(row.closeTime)>Date.now();}
  function freshness(){
    const time=Date.parse(generatedAt),age=Date.now()-time;
    const label=live&&age>=-60000&&age<=120000?'Live Kalshi prices · Updates every minute':'Saved prices · Live freshness not confirmed';
    $('snapshot').textContent=Number.isFinite(time)?`${label} · Observed ${new Date(time).toLocaleString()}`:'Market prices unavailable. Team projections remain available.';
    // Update badges without rebuilding selects or moving keyboard focus.
    for(const badge of document.querySelectorAll('[data-quote-ticker]')){const row=rows.find(r=>r.ticker===badge.dataset.quoteTicker);badge.textContent=row&&isFresh(row)?'Live quote':'Saved, stale or closed quote';}
  }
  function contract(row,target){
    target.replaceChildren();
    if(!row){target.append(node('p','No matching contract in the available feed.','note'));return;}
    const identity=P.identify(row,season),badge=node('p',isFresh(row)?'Live quote':'Saved, stale or closed quote','quote-badge');badge.dataset.quoteTicker=row.ticker;
    const quotes=node('div',null,'quotes');
    quotes.append(node('span',`YES ${cents(row.yesAsk)}`),node('span',`NO ${cents(row.noAsk)}`));
    target.append(badge,quotes,node('p',identity.threshold==null?'YES: wins the championship.':'YES: '+identity.threshold+'+ wins · NO: '+(identity.threshold-1)+' or fewer.','note'));
    const details=node('details');details.append(node('summary','Settlement rules & source'),node('p',row.title),node('p',row.rules));
    if(row.supplementaryRules)details.append(node('p',row.supplementaryRules));
    if(row.earlyCloseCondition)details.append(node('p',row.earlyCloseCondition));
    details.append(node('p',`Observed ${new Date(row.observedAt).toLocaleString()}. Buy quotes exclude fees; depth and fills are not guaranteed.`),node('p',row.ticker,'receipt'),node('p',`Receipt SHA-256 ${row.receiptSha256}`,'receipt'));
    const a=node('a','View exact contract ↗','market-link');a.href=row.url;a.target='_blank';a.rel='noopener noreferrer';target.append(details,a);
  }
  function render(){
    const query=$('search').value.trim().toLowerCase();
    const groups=P.group(rows,category,season).filter(g=>`${g.team} ${g.name}`.toLowerCase().includes(query));
    const projection=g=>winProjections?.teams[g.team];
    groups.sort($('sort').value==='wins'?(a,b)=>((projection(b)?.reviewRequired?-1:projection(b)?.injuries.mean)??-1)-((projection(a)?.reviewRequired?-1:projection(a)?.injuries.mean)??-1)||a.name.localeCompare(b.name):(a,b)=>a.name.localeCompare(b.name));
    $('markets').replaceChildren();
    for(const g of groups.slice(0,visible)){
      const card=node('article',null,'card team-card'),p=projection(g);
      const head=node('div',null,'team-card-head');head.append(node('span',g.team,'team-code'),node('h3',g.name));card.append(head);
      if(category==='Team wins'){
        if(p?.reviewRequired){card.append(node('p','Projection under review','status-pill'),node('p',p.reviewRequired,'note'));}
        else if(p){
          const stat=node('div',null,'team-projection');stat.append(node('strong',p.injuries.mean.toFixed(1)),node('span','projected wins'));
          card.append(stat,node('p',`Scenario range: ${p.injuries.p10}–${p.injuries.p90} wins`,'note'));
          const d=node('details');d.append(node('summary','Injury assumptions'),node('p',`Healthy comparison: ${p.healthy.mean.toFixed(1)} wins. Historical absence scenarios, not current medical reports. Range covers 10th–90th percentile simulation outcomes, not calibrated confidence.`));card.append(d);
        }else card.append(node('p','Team projection unavailable','status-pill'));
        if(g.markets.length){
          const label=node('label','Market win threshold'),select=node('select');select.setAttribute('aria-label',g.name+' win threshold');
          for(const row of g.markets){const o=node('option',P.identify(row,season).threshold+'+ wins');o.value=row.ticker;select.append(o);}
          const chosen=g.markets.find(r=>r.ticker===selection.get(g.team))||P.nearest(g.markets,p,season);select.value=chosen.ticker;
          const content=node('div',null,'contract');contract(chosen,content);
          select.onchange=()=>{selection.set(g.team,select.value);contract(g.markets.find(r=>r.ticker===select.value),content);};
          label.append(select);card.append(label,content);
        }else contract(null,card.appendChild(node('div',null,'contract')));
      }else{
        card.append(node('p','Market prices only · No championship forecast','note'));
        contract(g.markets[0],card.appendChild(node('div',null,'contract')));
      }
      $('markets').append(card);
    }
    $('more').hidden=groups.length<=visible;
    if(!groups.length)$('markets').append(node('p','No teams match. Try a city, team name, or abbreviation.'));
    $('count').textContent=`${groups.length} teams · Showing ${Math.min(visible,groups.length)} · ${groups.reduce((n,g)=>n+g.markets.length,0)} matching contracts · ${category==='Team wins'?'Injury-scenario projections':'Championship prices'}`;
  }
  for(const b of document.querySelectorAll('[data-category]'))b.onclick=()=>{category=b.dataset.category;visible=9;for(const x of document.querySelectorAll('[data-category]'))x.setAttribute('aria-pressed',String(x===b));$('sort').disabled=category==='Championship';if(category==='Championship')$('sort').value='name';render();};
  $('search').oninput=()=>{visible=9;render();};$('sort').onchange=()=>{visible=9;render();};$('more').onclick=()=>{visible+=9;render();};
  const refresh=NBAI_QUOTES.watch('season','season-betting.json',(data,ok)=>{rows=data?.markets||[];generatedAt=data?.generatedAt;live=ok;freshness();render();});
  $('refreshQuotes').onclick=refresh;setInterval(freshness,15000);
  fetch('season-win-projections.json').then(r=>{if(!r.ok)throw Error();return r.json();}).then(d=>{
    if(d.schemaVersion!==1||!d.availabilityVersion||d.season!==season||!d.teams)throw Error();winProjections=d;
    $('teamCount').textContent=Object.keys(d.teams).length;$('runCount').textContent=d.runs;$('holdCount').textContent=Object.values(d.teams).filter(t=>t.reviewRequired).length;
    $('projectionStatus').textContent=`Projections built ${new Date(d.generatedAt).toLocaleDateString()} · ${d.season} · Historical injury scenarios; current medical reports not included.`;render();
  }).catch(()=>{$('projectionStatus').textContent='Team projections unavailable. Market prices are not model forecasts.';render();});
  fetch('season-shortlist.json').then(r=>{if(!r.ok)throw Error();return r.json();}).then(d=>{
    if(d.schemaVersion!==1||!Array.isArray(d.items))throw Error();
    // An old research case is not a current recommendation. Keep evidence accessible.
    $('shortlistStatus').textContent=d.items.length?'Research cases are available for review. No trade-ready recommendations are published.':'No published picks yet. Previous healthy-only leans were withdrawn; injury and rotation checks are still in progress.';
    if(d.items.length){const a=node('a','View research cases and their evidence');a.href='season-shortlist.json';$('shortlistCards').append(a);}
  }).catch(()=>{$('shortlistStatus').textContent='Pick review status unavailable. No recommendations are being inferred.';});
  render();
})();
