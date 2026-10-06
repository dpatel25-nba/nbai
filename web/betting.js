(()=>{
'use strict';const $=id=>document.getElementById(id),D=NBAI_DAY,C=NBAI_CONSENSUS;
const el=(tag,text,cls)=>{const x=document.createElement(tag);if(text!=null)x.textContent=text;if(cls)x.className=cls;return x;};
const f=n=>Number.isFinite(n)?n.toFixed(1):'—',signed=n=>(n>0?'+':'')+f(n);
const propNumber=n=>Number.isFinite(n)?n.toFixed(2):'—',propDifference=n=>(n>0?'+':'')+propNumber(n);
let market=null,models=null,all=[],current=null,kind='spreads',failed=false,selectedPlayer=null,playerEvent=null;
const tip=e=>new Date(e.commence_time).toLocaleString('en-US',{timeZone:'America/New_York',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})+' ET';
function url(date,id){const u=new URL('betting.html',location.href);u.searchParams.set('date',date);if(id)u.searchParams.set('game',id);return u.pathname+u.search;}
function go(date,id){history.pushState({},'',url(date,id));render();if(id)$('detailTitle').focus();}
function button(text,fn){const b=el('button',text,'button secondary');b.type='button';b.onclick=fn;return b;}
function render(){
 const query=new URLSearchParams(location.search),today=D.day(Date.now()),first=all.find(e=>e.competition!=='Preseason'),defaultDay=first&&today<D.day(first.commence_time)?D.day(first.commence_time):today,requested=query.get('date'),date=requested&&/^\d{4}-\d{2}-\d{2}$/.test(requested)&&Number.isFinite(Date.parse(requested+'T12:00:00Z'))?requested:defaultDay;$('gameDate').value=date;
 $('boardStatus').textContent=market?`${failed?'Reload failed · retaining saved data. ':''}Market snapshot: ${new Date(market.generatedAt).toLocaleString()} · ${market.coverage.eventsChecked} games checked. Projections: ${models?.runs||0} scenarios per modeled matchup.`:'Market data unavailable. No prices are being estimated.';
 current=all.find(e=>e.id===query.get('game'))||null;
 $('dailyBoard').hidden=!!current;$('gameDetail').hidden=!current;
 if(current){detail();return;}
 $('slateTitle').textContent=new Date(date+'T12:00:00Z').toLocaleDateString('en-US',{timeZone:'America/New_York',weekday:'long',month:'long',day:'numeric'});
 const games=all.filter(e=>D.day(e.commence_time)===date);$('slateCount').textContent=games.length+' games in the saved schedule';$('gameCards').replaceChildren();
 if(!games.length){const box=el('div',null,'empty-day');box.append(el('h3','No matchups in this date’s saved schedule.'),el('p','Pick another date, or jump to the next regular-season slate. We’ll show forecasts and prices where the collected data supports them.'));$('gameCards').append(box);}
 for(const e of games){const p=D.projection(e),card=el('a',null,'matchup-card');card.href=url(date,e.id);card.onclick=event=>{if(event.metaKey||event.ctrlKey||event.shiftKey)return;event.preventDefault();go(date,e.id);};card.append(el('p',(e.competition==='Preseason'?'Preseason · ':'')+tip(e),'tip'));card.append(el('small','NBAI projected score','note'));
 for(const [name,score] of [[e.away_team,p?.awayScore.mean],[e.home_team,p?.homeScore.mean]]){const row=el('div',null,'team-line');row.append(el('h3',name),el('strong',f(score)));card.append(row);}
 card.append(el('p',p?(p.winner?p.winner+' by '+f(p.margin):'Evenly matched'):e.model?.reviewRequired?'Projection under review':'Forecast pending','forecast-call'));
 const count=(market?.quotes||[]).filter(q=>q.eventId===e.id).length,foot=el('div',null,'card-foot');foot.append(el('span',count?count+' player lines':'Player lines pending'),el('span','Open matchup ↗'));card.append(foot);$('gameCards').append(card);}
}
function detail(){
 const e=current,p=D.projection(e),started=Date.parse(e.commence_time)<=Date.now();$('backToBoard').href=url(D.day(e.commence_time));$('backToBoard').onclick=ev=>{ev.preventDefault();go(D.day(e.commence_time));};
 $('detailTitle').textContent=e.away_team+' at '+e.home_team;$('detailTime').textContent=(e.competition||'Regular season')+' · '+tip(e);$('detailBadge').textContent=started?'Tip time passed · pregame forecast':e.model?.reviewRequired?'Under review':'Pregame preview';
 $('forecastHero').replaceChildren();const lead=el('div');
 lead.append(el('p','NBAI GAME FORECAST','eyebrow'),el('h3',p?(p.winner?p.winner+' by '+f(p.margin):'An even matchup'):'Forecast unavailable'));
 lead.append(el('p',p?`Projected total: ${f(p.total.mean)} · Home win share: ${(p.homeWinShare*100).toFixed(0)}% of simulations.`:e.model?.reviewRequired||'A verified matchup model is not available yet. Bookmaker lines can still be shown.'));
 lead.append(el('p',p?`Home margin scenario range: ${signed(p.homeMargin.p10)} to ${signed(p.homeMargin.p90)} points.`:'Preseason games never borrow regular-season projections.','note'));
 $('forecastHero').append(lead);const scores=el('div',null,'score-pair');for(const [name,score] of [[e.away_team,p?.awayScore.mean],[e.home_team,p?.homeScore.mean]]){const cell=el('div');cell.append(el('span',name),el('strong',f(score)));scores.append(cell);}$('forecastHero').append(scores);
 $('gameMethod').textContent=`${models?.runs||0} possession-based matchup scenarios, with historical injury absences. Current medical reports and confirmed lineups are not connected. ${e.model?.reviewRequired||'The forecasts have not established a validated betting edge.'}`;
 if(playerEvent!==e.id){selectedPlayer=null;playerEvent=e.id;$('propSearch').value='';$('playerBrowser').open=false;}
 gameMarkets();playerRows();shortlist();
}
function gameMarkets(){
 $('teamMarketRows').replaceChildren();$('gameMarketForecast').replaceChildren();
 const e=current,rows=D.gameLines(market,e).filter(q=>q.market===kind),p=D.projection(e),home=e.home_team,away=e.away_team;
 $('marketSideA').textContent=kind==='totals'?'Over':away;$('marketSideB').textContent=kind==='totals'?'Under':home;
 const forecast=(label,value)=>{const box=el('div',null,'market-forecast-item');box.append(el('span',label),el('strong',value));$('gameMarketForecast').append(box);};
 if(!p)forecast('NBAI forecast','Under review / unavailable');
 else if(kind==='h2h'){forecast('NBAI · '+away,f((1-p.homeWinShare)*100)+'% to win');forecast('NBAI · '+home,f(p.homeWinShare*100)+'% to win');}
 else if(kind==='spreads')forecast('NBAI projected margin',p.winner?p.winner+' by '+f(p.margin):'Even matchup');
 else forecast('NBAI projected total',f(p.total.mean)+' combined points');
 $('gameMarketContext').textContent=kind==='h2h'?'Each team’s cell shows its moneyline and the book’s win probability with its margin removed. Model difference names the team we give a higher chance than that book. It is not an expected return.':kind==='spreads'?'Team columns show the actual handicap and odds. Model difference shows which team our projected margin favors relative to that spread.':'Compare our combined-points forecast with each book’s total. Odds appear below the line.';
 if(rows.length&&rows.every(q=>!q.fresh))$('gameMarketContext').append(el('span',' All quotes below are saved / stale; refresh before using them.','quote-caution'));
 if(!rows.length){const tr=el('tr'),cell=el('td','No matching lines in this snapshot.');cell.colSpan=4;tr.append(cell);$('teamMarketRows').append(tr);}
 for(const q of rows){
  const tr=el('tr'),name=el('th',null);name.scope='row';name.append(el('strong',q.providerName));
  const stamp=new Date(q.sourceUpdatedAt),time=Number.isFinite(stamp.getTime())?stamp.toLocaleString('en-US',{timeZone:'America/New_York',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})+' ET':'Time unavailable';
  const updated=el('small',(q.fresh?'Recent':'Saved / stale')+' · '+time,'quote-time');updated.title='Provider update: '+q.sourceUpdatedAt+'; collected: '+q.observedAt;name.append(updated);tr.append(name);
  const homeProb=D.probability(q.outcomes.find(o=>o.name===home)?.price),awayProb=D.probability(q.outcomes.find(o=>o.name===away)?.price);
  for(const side of kind==='totals'?['Over','Under']:[away,home]){
   const o=q.outcomes.find(o=>o.name===side),cell=el('td');
   cell.append(el('strong',o?(kind==='h2h'?D.money(o.price):kind==='spreads'?signed(o.point):f(o.point)):'—','line-value'));
   if(o&&kind!=='h2h')cell.append(el('small','Odds '+D.money(o.price)));
   if(kind==='h2h')cell.append(el('small',homeProb!=null&&awayProb!=null?f((side===home?homeProb:awayProb)/(homeProb+awayProb)*100)+'% book win probability':'Book probability unavailable'));
   tr.append(cell);
  }
  const cmp=D.teamComparison(q,e),delta=el('td',null,'model-difference');
  if(!cmp)delta.append(el('span','No comparison'),el('small',p?'A matching two-sided line is missing':'NBAI forecast unavailable'));
  else if(Math.abs(cmp.difference)<.05)delta.append(el('strong','In line with the book'),el('small','Less than 0.1 '+(kind==='h2h'?'percentage points':'points')+' apart'));
  else if(kind==='totals')delta.append(el('strong',f(Math.abs(cmp.difference))+' points '+(cmp.difference>0?'higher':'lower')),el('small','NBAI total vs. sportsbook total'));
  else {delta.append(el('strong',cmp.difference>0?home:away));delta.append(el('small',kind==='h2h'?'+'+f(Math.abs(cmp.difference))+' percentage points vs. book':f(Math.abs(cmp.difference))+' points toward this team'));}
  tr.append(delta);$('teamMarketRows').append(tr);
 }
}
function playerRows(){
 if(!current)return;
 const quotedIds=new Set(D.props(market,current).map(r=>r.player.id)),roster=D.roster(market,current),query=C.normalize($('propSearch').value),players=roster.filter(p=>C.normalize(p.name).includes(query));
 if(!players.some(p=>p.id===selectedPlayer))selectedPlayer=players.find(p=>quotedIds.has(p.id))?.id??players[0]?.id??null;
 $('playerChoices').replaceChildren();$('playerSearchStatus').textContent=players.length?`${players.length} of ${roster.length} players${query?' match your search':''} · Select a player below.`:roster.length?'No players match. Clear the search to see both rosters.':'The roster for this matchup is not available in this snapshot.';
 for(const p of players){const b=button(p.name+(p.team?' · '+p.team:''),()=>{selectedPlayer=p.id;playerRows();});b.setAttribute('aria-pressed',String(p.id===selectedPlayer));b.dataset.playerId=p.id;$('playerChoices').append(b);}
 const player=players.find(p=>p.id===selectedPlayer);$('selectedPlayerPanel').hidden=!player;$('propRows').replaceChildren();if(!player)return;
 $('selectedPlayerTitle').textContent=player.name+(player.team?' · '+player.team:'');$('propStatus').textContent='Consensus needs at least two sources. A missing betting line does not hide the player’s prediction.';
 for(const [stat,label] of Object.entries(C.STATS)){
  const r=C.compare(market,player,current,stat,current.model),percent=stat.endsWith('_PCT'),fmt=n=>propNumber(n)+(Number.isFinite(n)&&percent?'%':''),tr=el('tr'),name=el('th',label);name.scope='row';tr.append(name);
  const model=el('td',r.model?fmt(r.model.mean):'—');model.append(el('small',r.model?`${fmt(r.model.p10)}–${fmt(r.model.p90)} scenario range`:r.held?'Under review':player.projectionStatus==='no_projected_minutes'?'No projected minutes':'Projection unavailable'));tr.append(model);
  const consensus=el('td',r.median==null?'—':fmt(r.median));consensus.append(el('small',r.sourceCount<2?(r.sourceCount?'One source only':'Line unavailable'):`${r.sourceCount} sources · ${r.fresh?'recent':'saved / stale'}`));tr.append(consensus);
  const gap=r.gap??r.snapshotGap,delta=el('td',gap==null?'—':propDifference(gap));if(gap!=null)delta.append(el('small',r.fresh?'Model difference':'Saved difference'));tr.append(delta);
  const books=el('td');for(const q of Object.values(r.books).filter(Boolean)){const row=el('div',`${q.providerName}: ${q.line}`);row.title='Provider updated '+new Date(q.sourceUpdatedAt).toLocaleString();books.append(row);}if(!r.sourceCount)books.textContent=percent?'Percentage market not collected':'No matching line returned';tr.append(books);$('propRows').append(tr);
 }
}
function shortlist(){
 const comparisons=D.props(market,current),rows=D.featuredProps(comparisons,current);$('featuredProps').replaceChildren();
 $('shortlistExplanation').textContent='Up to three model leans, ranked by projection–consensus difference relative to the simulated range. Saved leans need refreshed prices. These are research selections; current injury checks and betting-edge validation are still pending.';
 if(!rows.length)$('featuredProps').append(el('p','No supported player leans yet. We need a usable projection and at least two matching sources; browse all players below for the available forecasts.','empty-message'));
 for(const [i,r] of rows.entries()){const card=el('article',null,'book-card');card.append(el('p',`#${i+1} · ${r.fresh?'Recent model lean':'Saved lean · refresh prices'}`,'eyebrow'),el('h3',r.player.name),el('p',r.direction+' '+C.STATS[r.stat].toLowerCase(),'book-line'));
 const values=el('div',null,'pick-values');for(const [label,value] of [['NBAI',propNumber(r.model.mean)],['Consensus',propNumber(r.median)],['Difference',propDifference(r.snapshotGap)]]){const cell=el('div');cell.append(el('small',label),el('strong',value));values.append(cell);}card.append(values);
 card.append(el('small',`${r.sourceCount} sources · consensus is a reference, not a selectable line.`));for(const q of Object.values(r.books).filter(Boolean))card.append(el('small',`${q.providerName}: ${q.line}`));
 card.append(button('View player',()=>{$('propSearch').value='';selectedPlayer=r.player.id;$('playerBrowser').open=true;playerRows();$('playerBrowser').scrollIntoView({behavior:'smooth'});}));$('featuredProps').append(card);}
 const combo=D.parlay(comparisons,current);$('parlayCard').replaceChildren();
 if(!combo){$('parlayCard').append(el('p','No supported parlay draft yet. We need at least two qualifying selections with available prices at the same sportsbook.'));return;}
 $('parlayCard').append(el('p',`${combo.legs.length}-pick draft · ${combo.providerName} · ${combo.fresh?'Recent lines':'Saved lines — refresh before use'}`,'eyebrow'));
 const list=el('ol',null,'parlay-legs');for(const r of combo.legs){const li=el('li');li.append(el('strong',`${r.player.name} · ${r.direction} ${r.quote.line} ${C.STATS[r.stat].toLowerCase()}`),el('span',`${D.money(r.price)} · NBAI ${propNumber(r.model.mean)} · Consensus ${propNumber(r.median)}`));list.append(li);}$('parlayCard').append(list);
 $('parlayCard').append(el('p','Draft only. These same-game outcomes can be correlated. Combined odds, joint win probability and sportsbook acceptance have not been verified; individual prices are not multiplied into a promised payout.','note'));
}

async function load(){
 $('reloadBoard').disabled=true;const results=await Promise.allSettled(['player-consensus.json','player-game-projections.json'].map(f=>fetch(f,{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json();})));
 failed=results.some(r=>r.status==='rejected');if(results[0].status==='fulfilled')market=results[0].value;if(results[1].status==='fulfilled')models=results[1].value;
 all=D.games(market,models);render();$('reloadBoard').disabled=false;
}
$('gameDate').onchange=()=>go($('gameDate').value);$('todayButton').onclick=()=>go(D.day(Date.now()));$('nextGames').onclick=()=>{const next=all.find(e=>e.competition!=='Preseason'&&Date.parse(e.commence_time)>Date.now());if(next)go(D.day(next.commence_time));};$('reloadBoard').onclick=load;
$('propSearch').oninput=playerRows;$('clearPropSearch').onclick=()=>{$('propSearch').value='';playerRows();$('propSearch').focus();};
for(const b of document.querySelectorAll('[data-market]'))b.onclick=()=>{kind=b.dataset.market;for(const other of document.querySelectorAll('[data-market]'))other.setAttribute('aria-pressed',String(other===b));gameMarkets();};
for(const a of document.querySelectorAll('a[href="#playerBrowser"]'))a.onclick=ev=>{ev.preventDefault();$('playerBrowser').open=true;$('playerBrowser').scrollIntoView({behavior:'smooth'});$('propSearch').focus({preventScroll:true});};
window.addEventListener('popstate',render);setInterval(()=>{if(!document.hidden)load();},300000);setInterval(()=>{if(current){gameMarkets();playerRows();shortlist();}},30000);load();
})();
