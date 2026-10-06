(()=>{
'use strict';const $=id=>document.getElementById(id),D=NBAI_DAY,C=NBAI_CONSENSUS;
const el=(tag,text,cls)=>{const x=document.createElement(tag);if(text!=null)x.textContent=text;if(cls)x.className=cls;return x;};
const f=n=>Number.isFinite(n)?n.toFixed(1):'—',signed=n=>(n>0?'+':'')+f(n);
let market=null,models=null,all=[],current=null,kind='spreads',failed=false;
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
 gameMarkets();playerRows();shortlist();$('rotationRows').replaceChildren();
 for(const player of e.model?.players||[]){const card=el('article');card.append(el('strong',player.name),el('p',e.model.reviewRequired?'Under rotation review':player.stats.PTS?`${f(player.stats.PTS.mean)} PTS · ${f(player.stats.AST?.mean)} AST · ${f(player.stats.REB?.mean)} REB`:'No projected rotation minutes'),el('small',`${player.playedScenarios}/${models.runs} simulated appearances`));$('rotationRows').append(card);}
 if(!e.model?.players?.length)$('rotationRows').append(el('p','Player matchup projections are not available for this game.'));
}
function gameMarkets(){
 $('teamMarketRows').replaceChildren();const e=current,rows=D.gameLines(market,e).filter(q=>q.market===kind);
 $('gameMarketContext').textContent=kind==='spreads'?'A negative spread means that team must win by more than that number. Differences below use the home-team margin.':kind==='h2h'?'Moneyline picks a winner. The comparison uses the home team’s simulated win share and the bookmaker’s two-way, margin-adjusted price.':'Total compares both teams’ combined points. A positive difference means our model projects a higher total.';
 if(!rows.length)$('teamMarketRows').append(el('p','No matching '+(kind==='h2h'?'moneyline':kind==='spreads'?'spread':'total')+' returned in this snapshot.','empty-message'));
 for(const q of rows){const card=el('article',null,'book-card');card.append(el('h3',q.providerName));
 for(const o of q.outcomes)card.append(el('p',`${o.name}${Number.isFinite(o.point)?' '+(kind==='spreads'?signed(o.point):f(o.point)):''} (${D.money(o.price)})`,'book-line'));
 const cmp=D.teamComparison(q,e);if(cmp)card.append(el('p',kind==='h2h'?`Home win share ${f(cmp.model)}% · Book reference ${f(cmp.market)}%`:`NBAI ${f(cmp.model)} · Book ${f(cmp.market)} · Difference ${signed(cmp.difference)}`,'difference'));
 card.append(el('small',(q.fresh?'Recent snapshot':'Saved / stale price')+' · '+new Date(q.observedAt).toLocaleString()),el('small','Provider updated '+new Date(q.sourceUpdatedAt).toLocaleString()));$('teamMarketRows').append(card);}
}
function playerRows(){
 if(!current)return;const rows=D.props(market,current).filter(r=>C.normalize(r.player.name).includes(C.normalize($('propSearch').value))&&(!$('propStat').value||r.stat===$('propStat').value));$('propRows').replaceChildren();$('propsTableWrap').hidden=!rows.length;
 $('propStatus').textContent=rows.length?rows.length+' player/stat comparisons · Per-game projections conditional on playing. Two sources are required for consensus.':'No matching player props in this snapshot. The full matchup projections below remain available when modeled.';
 for(const r of rows){const tr=el('tr'),name=el('th',r.player.name+' · '+C.STATS[r.stat]);name.scope='row';tr.append(name);const model=el('td',r.model?f(r.model.mean):'—');model.append(el('small',r.model?`${f(r.model.p10)}–${f(r.model.p90)} scenario range`:'Projection unavailable'));tr.append(model);
 const books=el('td');for(const q of Object.values(r.books).filter(Boolean))books.append(el('div',`${q.providerName}: ${q.line}`));tr.append(books);const median=el('td',r.median==null?'—':f(r.median));median.append(el('small',r.sourceCount+' source'+(r.sourceCount===1?'':'s')));tr.append(median);
 const gap=r.gap??r.snapshotGap,delta=el('td',gap==null?'—':signed(gap));delta.append(el('small',gap==null?r.reason:r.fresh?'Research difference':'Saved difference · not current'));tr.append(delta);$('propRows').append(tr);}
}
function shortlist(){
 const rows=D.watchlist(D.props(market,current),current),teamRows=D.teamWatchlist(market,current);$('gamePicks').replaceChildren();$('shortlistExplanation').textContent='No vetted bets yet. Current injury checks and calibrated price-based validation are still required. The model watchlist below ranks fresh game-line and player-prop differences relative to their simulated ranges; it is not an expected-return ranking.';
 if(!rows.length&&!teamRows.length){$('gamePicks').append(el('p','No current model leans to highlight. We need fresh matching lines and a usable projection before shortlisting a play.','empty-message'));return;}
 for(const r of teamRows){const card=el('article',null,'book-card');card.append(el('p','GAME WATCHLIST · NOT A VETTED BET','eyebrow'),el('h3',r.outcome.name+' '+(r.quote.market==='spreads'?signed(r.outcome.point):f(r.outcome.point))),el('p',r.quote.providerName+' · '+D.money(r.outcome.price),'book-line'),el('p',`NBAI ${f(r.comparison.model)} vs. book ${f(r.comparison.market)} · ${signed(r.comparison.difference)} difference.`),el('small','Price and injury validation still required.'));$('gamePicks').append(card);}
 for(const r of rows){const card=el('article',null,'book-card');card.append(el('p','MODEL WATCHLIST · NOT A VETTED BET','eyebrow'),el('h3',r.player.name),el('p',r.direction+' '+C.STATS[r.stat].toLowerCase(),'book-line'),el('p',`NBAI ${f(r.model.mean)} vs. consensus ${f(r.median)} · ${signed(r.gap)} difference.`));for(const q of Object.values(r.books).filter(Boolean))card.append(el('small',`${q.providerName} ${q.line}${q.americanOdds?' · '+r.direction+' '+D.money(q.americanOdds[r.direction.toLowerCase()]):''}`));$('gamePicks').append(card);}
}
async function load(){
 $('reloadBoard').disabled=true;const results=await Promise.allSettled(['player-consensus.json','player-game-projections.json'].map(f=>fetch(f,{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json();})));
 failed=results.some(r=>r.status==='rejected');if(results[0].status==='fulfilled')market=results[0].value;if(results[1].status==='fulfilled')models=results[1].value;
 all=D.games(market,models);render();$('reloadBoard').disabled=false;
}
$('gameDate').onchange=()=>go($('gameDate').value);$('todayButton').onclick=()=>go(D.day(Date.now()));$('nextGames').onclick=()=>{const next=all.find(e=>e.competition!=='Preseason'&&Date.parse(e.commence_time)>Date.now());if(next)go(D.day(next.commence_time));};$('reloadBoard').onclick=load;
$('propSearch').oninput=playerRows;$('propStat').onchange=playerRows;
for(const b of document.querySelectorAll('[data-market]'))b.onclick=()=>{kind=b.dataset.market;for(const other of document.querySelectorAll('[data-market]'))other.setAttribute('aria-pressed',String(other===b));gameMarkets();};
window.addEventListener('popstate',render);setInterval(()=>{if(!document.hidden)load();},300000);setInterval(()=>{if(current){gameMarkets();playerRows();shortlist();}},30000);load();
})();
