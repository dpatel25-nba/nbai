(()=>{
  'use strict';const $=id=>document.getElementById(id),A=NBAI_PROPS;
  const el=(tag,value,cls)=>{const n=document.createElement(tag);if(value!=null)n.textContent=value;if(cls)n.className=cls;return n;};
  const names={PTS:'Points',AST:'Assists',REB:'Rebounds',STL:'Steals',BLK:'Blocks',FG_PCT:'Field goal %',FT_PCT:'Free throw %',FG3_PCT:'Three-point %'},fmt=n=>Number.isFinite(n)?n.toFixed(1):'—',cents=n=>Number.isFinite(n)?(n*100).toFixed(1)+'¢':'Unavailable';
  let data=null,selected=null,quotes=null,live=false;
  function search(){
    if(!data)return;const q=A.normalize($('search').value),rows=data.players.filter(p=>A.normalize(p.name).includes(q));
    if(!q)rows.sort((a,b)=>(b.stats.PTS.average?.mean??-1)-(a.stats.PTS.average?.mean??-1));
    $('playerResults').replaceChildren();
    for(const p of rows.slice(0,12)){const b=el('button',p.name+' · '+p.team);b.type='button';b.setAttribute('aria-pressed',String(selected?.id===p.id));b.onclick=()=>{selected=p;$('manualLine').value='';search();render();};$('playerResults').append(b);}
    $('searchCount').textContent=rows.length?`${rows.length} matching players${rows.length>12?' · Keep typing to narrow the list':''}`:'No matching player. Try a surname.';
  }
  function manual(){
    const value=$('manualLine').value;
    if(!selected||value===''){$('manualCall').textContent='Enter a season line to compare.';return;}
    const call=A.compare(selected,$('manualStat').value,'average',Number(value));
    $('manualCall').textContent=`${call.label} · Manual ${$('manualStat').value.endsWith('_PCT')?'shooting percentage':'per-game average'} line. ${call.reason}`;
  }
  function render(){
    if(!selected)return;$('playerPanel').hidden=false;$('playerName').textContent=selected.name;$('playerTeam').textContent=selected.team+' / '+data.season;
    $('playerAssumptions').textContent=`Scenario: ${fmt(selected.games.mean)} games played · ${fmt(selected.minutes?.mean)} minutes per game · Historical injury/illness scenarios; rotations adjust. Projection inputs saved ${new Date(data.inputGeneratedAt).toLocaleDateString()}. ${selected.reviewRequired||""}`;
    $('projections').replaceChildren();
    for(const stat of Object.keys(names)){
      const model=selected.stats[stat],p=model?.average,percent=stat.endsWith('_PCT'),card=el('article',null,'card');
      card.append(el('h3',names[stat]),el('p',p?fmt(p.mean)+(percent?'%':''):'—','value'),el('p',percent?'Season shooting percentage':'Per game','stat-unit'),el('p',p?`Scenario range ${fmt(p.p10)}–${fmt(p.p90)}${percent?'%':''}`:'No projected minutes or attempts; unavailable.','note'));
      if(percent)card.append(el('p',`${fmt(model?.attemptsPerGame?.mean)} attempts per game · Descriptive projection`,'note'));
      $('projections').append(card);
    }
    manual();markets();
  }
  function markets(){
    const age=Date.now()-Date.parse(quotes?.generatedAt),fresh=live&&age>=-60000&&age<=120000;
    $('quoteStatus').textContent=quotes?`${fresh?'Live Kalshi quotes · refreshes every 60 seconds':live?'Stale quotes · not current':'Live refresh unavailable · saved quotes only'} · Observed ${new Date(quotes.generatedAt).toLocaleString()}`:'Live quotes and saved snapshot unavailable. No prices are being estimated.';
    if(!selected)return;
    const all=quotes?.markets||[],matching=all.filter(r=>A.normalize(r.player)===A.normalize(selected.name)),period=$('period').value;
    const rows=matching.filter(r=>r.scope===period&&(!$('marketStat').value||r.stat===$('marketStat').value));
    $('marketCoverage').textContent=quotes?`${quotes.coverage.map(c=>c.series).join(', ')} checked · ${all.length} open contracts in this saved response. Coverage is limited to these supported points/assists/rebounds series.`:'';
    $('playerMarkets').replaceChildren();
    if(!rows.length){
      const box=el('article',null,'empty-props');box.append(el('h3',`No ${period==='season'?'season':'single-game'} ${$('marketStat').value?names[$('marketStat').value].toLowerCase()+' ':''}props found for ${selected.name}.`),el('p',period==='season'?'No matching season over/under is listed in the supported Kalshi response. Try the manual season-line comparison above. A league-leader market is a different contract.':'There may be no open line for this player, or the feed may be unavailable. We do not generate substitute market prices.'));
      if(period==='season'&&matching.some(r=>r.scope==='game')){const b=el('button','View available game props','button');b.onclick=()=>{$('period').value='game';markets();};box.append(b);}
      if(!quotes){box.replaceChildren(el('h3','Market availability unknown.'),el('p','The live feed and saved quotes could not load. Try refreshing shortly.'));}
      else if(!fresh){box.querySelector('h3').textContent=`No matching ${period} props in the last saved response for ${selected.name}.`;}
      $('playerMarkets').append(box);
    }
    for(const row of rows){
      const card=el('article',null,'card'),call=A.marketCall(selected,row,fresh),prices=el('div',null,'prop-price');
      card.append(el('p',period==='game'?'Single-game contract':'Season contract','eyebrow'),el('h3',row.title));
      prices.append(el('span','YES '+cents(row.yesAsk)),el('span','NO '+cents(row.noAsk)));card.append(prices);
      if(row.threshold!==null)card.append(el('p',`YES: more than ${row.threshold} · NO: ${row.threshold} or fewer. See full settlement rules.`, 'note'));
      card.append(el('p',call.label+' · '+call.reason,'prop-call'));
      const details=el('details');details.append(el('summary','Contract rules & quote source'),el('p',row.rules),el('p',row.supplementaryRules),el('p',`Observed ${new Date(row.observedAt).toLocaleString()} · Close ${row.closeTime?new Date(row.closeTime).toLocaleString():'unknown'}`),el('p','Receipt '+row.receiptSha256,'receipt'));
      const link=el('a','View contract on Kalshi ↗','market-link');link.href=row.url;link.target='_blank';link.rel='noopener noreferrer';card.append(details,link);$('playerMarkets').append(card);
    }
  }
  const refresh=NBAI_QUOTES.watch('player','player-markets.json',(d,ok)=>{quotes=d;live=ok;markets();});
  $('refreshQuotes').onclick=refresh;setInterval(markets,15000);
  $('search').oninput=search;
  $('manualStat').onchange=()=>{const percent=$('manualStat').value.endsWith('_PCT');$('manualLine').value='';$('lineUnit').textContent='Over / under line · '+(percent?'percentage (0–100)':'per game');if(percent)$('manualLine').max=100;else $('manualLine').removeAttribute('max');manual();};$('manualLine').oninput=manual;
  $('period').onchange=markets;$('marketStat').onchange=markets;
  fetch('player-projections.json').then(r=>{if(!r.ok)throw Error();return r.json();}).then(d=>{
    if(d.schemaVersion!==1||!d.players?.length)throw Error();data=d;
    selected=d.players.find(p=>p.id===203999)||d.players[0];
    $('projectionStatus').textContent=`${d.season} · ${d.players.length} players · ${d.runs} full-league scenarios · Built ${new Date(d.generatedAt).toLocaleDateString()}`;
    $('method').textContent=d.assumptions;$('provenance').textContent=`Input snapshot: ${d.inputGeneratedAt}. Projected line = the average simulated outcome across ${d.runs} seasons, not a bookmaker line or a calibrated fair-price estimate.`;
    search();render();
  }).catch(()=>{$('projectionStatus').textContent='Player projections unavailable. No projections or picks are being invented.';});
})();
