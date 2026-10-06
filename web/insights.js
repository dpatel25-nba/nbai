(()=>{
  'use strict';const $=id=>document.getElementById(id),A=NBAI_PROPS;
  const el=(tag,value,cls)=>{const n=document.createElement(tag);if(value!=null)n.textContent=value;if(cls)n.className=cls;return n;};
  const names={PTS:'Points',AST:'Assists',REB:'Rebounds',STL:'Steals',BLK:'Blocks',FG_PCT:'Field goal %',FT_PCT:'Free throw %',FG3_PCT:'Three-point %'},fmt=n=>Number.isFinite(n)?n.toFixed(1):'—',cents=n=>Number.isFinite(n)?(n*100).toFixed(1)+'¢':'Unavailable';
  let data=null,selected=null,quotes=null,live=false,consensus=null,gameForecasts=null,view='game',eventId=null,reloadFailed=false,visiblePlayers=12;
  const C=NBAI_CONSENSUS;
  const hasLines=p=>(consensus?.quotes||[]).some(q=>C.normalize(q.player)===C.normalize(p.name));
  function search(){
    if(!data)return;const q=A.normalize($('search').value),rows=data.players.filter(p=>A.normalize(p.name).includes(q)&&(!$('researchTeam').value||(p.team||'unassigned')===$('researchTeam').value));
    if(!q)rows.sort((a,b)=>Number(hasLines(b))-Number(hasLines(a))||(b.stats.PTS.average?.mean??-1)-(a.stats.PTS.average?.mean??-1));
    $('playerResults').replaceChildren();
    for(const p of rows.slice(0,visiblePlayers)){const b=el('button',p.name+' · '+(p.team||'Roster pending')+(hasLines(p)?' · Lines available':''));b.type='button';b.setAttribute('aria-pressed',String(selected?.id===p.id));b.onclick=()=>{selected=p;eventId=null;$('manualLine').value='';search();render();};$('playerResults').append(b);}
    $('morePlayers').hidden=rows.length<=visiblePlayers;
    $('searchCount').textContent=rows.length?`${rows.length} matching players · Showing ${Math.min(visiblePlayers,rows.length)}${rows.length>visiblePlayers?' · Search, choose a team, or show more':''}`:'No matching player. Try a surname.';
  }
  function manual(){
    const value=$('manualLine').value;
    if(!selected||value===''){$('manualCall').textContent='Enter a season line to compare.';return;}
    const call=A.compare(selected,$('manualStat').value,'average',Number(value));
    $('manualCall').textContent=`${call.label} · Manual ${$('manualStat').value.endsWith('_PCT')?'shooting percentage':'per-game average'} line. ${call.reason}`;
  }
  function render(){
    if(!selected)return;$('playerPanel').hidden=false;$('playerName').textContent=selected.name;$('playerTeam').textContent=(selected.team||'Roster pending')+' / '+data.season;
    $('playerAssumptions').textContent=`Scenario: ${fmt(selected.games.mean)} games played · ${fmt(selected.minutes?.mean)} minutes per game · Historical injury/illness scenarios; rotations adjust. Projection inputs saved ${new Date(data.inputGeneratedAt).toLocaleDateString()}. ${selected.reviewRequired||""}`;
    $('projections').replaceChildren();
    for(const stat of Object.keys(names)){
      const model=selected.stats[stat],p=model?.average,percent=stat.endsWith('_PCT'),card=el('article',null,'card');
      card.append(el('h3',names[stat]),el('p',p?fmt(p.mean)+(percent?'%':''):'—','value'),el('p',percent?'Season shooting percentage':'Per game','stat-unit'),el('p',p?`Scenario range ${fmt(p.p10)}–${fmt(p.p90)}${percent?'%':''}`:'No projected minutes or attempts; unavailable.','note'));
      if(percent)card.append(el('p',`${fmt(model?.attemptsPerGame?.mean)} attempts per game · Descriptive projection`,'note'));
      $('projections').append(card);
    }
    manual();markets();comparisonGames();comparison();
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
  function comparisonGames(){
    const events=C.eventsFor(selected,consensus,gameForecasts);
    if(!events.some(e=>e.id===eventId))eventId=events[0]?.id||null;
    const select=$('comparisonGame');select.replaceChildren();
    if(!events.length){const o=el('option','No verified upcoming team matchup');o.value='';select.append(o);}
    for(const e of events){const o=el('option',`${e.away_team} at ${e.home_team} · ${new Date(e.commence_time).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}`);o.value=e.id;select.append(o);}
    select.value=eventId||'';select.disabled=!events.length;
  }
  function comparison(){
    if(!selected)return;
    const event=C.eventsFor(selected,consensus,gameForecasts).find(e=>e.id===eventId),forecast=gameForecasts?.scope==='game'?gameForecasts.events?.find(e=>e.eventId===eventId):null;
    $('comparisonFormat').textContent=view==='game'?'Single-game comparison':'Season averages · Per game';
    $('modelColumn').textContent=view==='game'?'NBAI game projection':'NBAI season average';
    $('gamePicker').hidden=view!=='game';$('comparisonRows').replaceChildren();$('bookEvidence').replaceChildren();
    $('consensusStatus').textContent=consensus?`${reloadFailed?'Reload failed · retained snapshot':'Saved bookmaker snapshot'} · Captured ${new Date(consensus.generatedAt).toLocaleString()} · ${consensus.coverage.eventsChecked} of ${consensus.coverage.eventsDiscovered} listed games checked. This is not an automatically refreshing feed.`:'Bookmaker snapshot unavailable. No consensus lines are being estimated.';
    $('matchupNote').textContent=view==='season'?'Current season-average lines from these apps are not verified. Single-game quotes are never substituted.':event&&forecast?`${gameForecasts?.runs||0} matchup scenarios · Conditional on playing · Historical injury scenarios, not current medical reports. Built ${gameForecasts?new Date(gameForecasts.generatedAt).toLocaleString():'date unavailable'}.`:event?'Matching game projection unavailable. Season averages are not substituted.':(selected.team?'No verified upcoming matchup in the current game inventory.':'Roster assignment unresolved; a team matchup cannot be chosen.');
    const gamePlayer=forecast?.players?.find(p=>p.id===selected.id);
    $('coverageNote').textContent=view==='season'?'Our season averages are available independently of bookmaker coverage.':gamePlayer?.projectionStatus==='no_projected_minutes'?'This player receives no minutes in the current simulated rotations. We cannot estimate conditional playing stats from zero appearances.':gamePlayer?.projectionStatus==='limited_participation'?`Limited rotation role: played in ${gamePlayer.playedScenarios} of ${gameForecasts.runs} simulations. Comparisons are withheld.`:event&&!hasLines(selected)?'Our matchup projection is available. The four checked sources returned no matching lines for this player in the collected snapshot.':gameForecasts?.coverage?`Model coverage: ${gameForecasts.coverage.rosteredPlayers} rostered players across ${gameForecasts.coverage.teams} teams and ${gameForecasts.coverage.events} games. Betting-line coverage is separate.`:'';
    const displayedProviders=Object.keys(C.PROVIDERS).filter(b=>['fanduel','prizepicks'].includes(b)||(consensus?.quotes||[]).some(q=>q.provider===b));
    for(const b of ['draftkings','betmgm'])$(b+'Column').hidden=!displayedProviders.includes(b);
    const displayedSources=new Map();
    for(const [stat,name] of Object.entries(names)){
      const call=view==='game'?C.compare(consensus,selected,event,stat,forecast):null;
      const p=view==='game'?call.model:selected.stats[stat]?.average,percent=stat.endsWith('_PCT'),row=el('tr');
      const title=el('th',name);title.scope='row';row.append(title);
      const model=el('td',p?p.mean.toFixed(percent?1:2)+(percent?'%':''):'—','model-cell');
      model.append(el('small',p?`${fmt(p.p10)}–${fmt(p.p90)}${percent?'%':''} scenario range`:call?.held?'Under review':call?.participation==='no_projected_minutes'||view==='season'&&selected.games?.mean===0?'No projected minutes':!selected.team?'Roster assignment unresolved':'Projection unavailable'));row.append(model);
      for(const provider of displayedProviders){
        const quote=call?.books[provider],cell=el('td',quote?String(quote.line):'—');
        if(quote){cell.append(el('small','Observed line'));displayedSources.set(provider+':'+stat,quote);}else cell.append(el('small',percent?'Percentage market not collected':view==='season'?'Season line not verified':'No matching line returned'));
        row.append(cell);
      }
      const market=el('td',call?.median!=null?String(Number(call.median.toFixed(2))):'—','consensus-cell');
      market.append(el('small',call?.median!=null?call.sourceCount+' sources · '+(call.fresh?'recent snapshot':'stale / closed'):call?.sourceCount===1?'1 source · no consensus':'No consensus'));row.append(market);
      const gap=call?.gap??call?.snapshotGap,delta=el('td',gap!=null?(gap>0?'+':'')+gap.toFixed(2):'—','gap-cell');
      delta.append(el('small',gap!=null?(call.fresh?'Research difference only':'Saved snapshot difference · not current'):view==='season'?'Season lines not verified':call.reason));row.append(delta);$('comparisonRows').append(row);
    }
    for(const q of displayedSources.values()){
      const odds=q.americanOdds?` · Over ${q.americanOdds.over>0?'+':''}${q.americanOdds.over} / Under ${q.americanOdds.under>0?'+':''}${q.americanOdds.under}`:'';
      $('bookEvidence').append(el('p',`${q.providerName} · ${names[q.stat]} ${q.line}${odds} · Provider updated ${new Date(q.sourceUpdatedAt).toLocaleString()} · Retrieved ${new Date(q.observedAt).toLocaleString()}`));
    }
    if(!displayedSources.size)$('bookEvidence').append(el('p','No verified matching quotes in this view.'));
  }
  async function loadComparison(){
    $('reloadComparison').disabled=true;
    const results=await Promise.allSettled(['player-consensus.json','player-game-projections.json'].map(f=>fetch(f,{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json();})));
    const valid=results[0].status==='fulfilled'&&results[0].value.schemaVersion===1&&Array.isArray(results[0].value.quotes)&&Array.isArray(results[0].value.events)&&results[0].value.coverage;
    reloadFailed=!valid;if(valid)consensus=results[0].value;
    if(results[1].status==='fulfilled'&&results[1].value.schemaVersion===1&&results[1].value.scope==='game')gameForecasts=results[1].value;
    comparisonGames();comparison();search();$('reloadComparison').disabled=false;
  }
  for(const button of document.querySelectorAll('[data-view]'))button.onclick=()=>{view=button.dataset.view;for(const b of document.querySelectorAll('[data-view]'))b.setAttribute('aria-pressed',String(b===button));comparison();};
  $('comparisonGame').onchange=()=>{eventId=$('comparisonGame').value;comparison();};
  $('reloadComparison').onclick=loadComparison;loadComparison();setInterval(comparison,30000);
  const refresh=NBAI_QUOTES.watch('player','player-markets.json',(d,ok)=>{quotes=d;live=ok;markets();});
  $('refreshQuotes').onclick=refresh;setInterval(markets,15000);
  $('search').oninput=()=>{visiblePlayers=12;search();};$('researchTeam').onchange=()=>{visiblePlayers=12;search();};$('morePlayers').onclick=()=>{visiblePlayers+=24;search();};
  $('manualStat').onchange=()=>{const percent=$('manualStat').value.endsWith('_PCT');$('manualLine').value='';$('lineUnit').textContent='Over / under line · '+(percent?'percentage (0–100)':'per game');if(percent)$('manualLine').max=100;else $('manualLine').removeAttribute('max');manual();};$('manualLine').oninput=manual;
  $('period').onchange=markets;$('marketStat').onchange=markets;
  fetch('player-projections.json').then(r=>{if(!r.ok)throw Error();return r.json();}).then(d=>{
    if(d.schemaVersion!==1||!d.players?.length)throw Error();data=d;
    for(const team of [...new Set(d.players.map(p=>p.team).filter(Boolean))].sort()){const o=el('option',team);o.value=team;$('researchTeam').append(o);}const unassigned=el('option','Unassigned roster');unassigned.value='unassigned';$('researchTeam').append(unassigned);
    selected=d.players.find(p=>p.id===1628973)||d.players[0];
    $('projectionStatus').textContent=`${d.season} season profiles · ${d.players.length} players · ${d.runs} full-league scenarios · Built ${new Date(d.generatedAt).toLocaleDateString()}`;
    $('method').textContent=d.assumptions;$('provenance').textContent=`Input snapshot: ${d.inputGeneratedAt}. Projected line = the average simulated outcome across ${d.runs} seasons, not a bookmaker line or a calibrated fair-price estimate.`;
    search();render();
  }).catch(()=>{$('projectionStatus').textContent='Player projections unavailable. No projections or picks are being invented.';});
})();
