(()=>{
 'use strict';
 const E=NBAI_KALSHI_DAY,$=id=>document.getElementById(id);
 const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
 const pct=n=>Number.isFinite(n)?(n*100).toFixed(1)+'%':'—';
 const cents=n=>Number.isFinite(n)?Number((n*100).toFixed(2))+'¢':'—';
 const number=n=>Number.isFinite(n)?n.toFixed(1):'—';
 const et=t=>new Date(t).toLocaleString('en-US',{timeZone:'America/New_York',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit'})+' ET';
 const tip=t=>new Date(t).toLocaleString('en-US',{timeZone:'America/New_York',weekday:'short',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})+' ET';
 const link=(text,url)=>{const a=node('a',text);a.href=url;return a;};
 const external=(text,url)=>{const a=link(text,url);a.target='_blank';a.rel='noopener noreferrer';return a;};
 let models=null,quotes=null,board=null,loading=false,quoteFailed=false,modelFailed=false,first=true,lastModelAt=0;
 const detailPage=document.body.dataset.page==='matchup',query=new URLSearchParams(location.search);
 let selectedDate=/^\d{4}-\d{2}-\d{2}$/.test(query.get('date')||'')&&Number.isFinite(Date.parse(query.get('date')+'T12:00:00Z'))?query.get('date'):E.day(Date.now());
 const requestedGame=query.get('game');
 if(!detailPage&&requestedGame){location.replace('matchup.html?'+new URLSearchParams({game:requestedGame,date:selectedDate}));return;}
 function gameUrl(g){return 'matchup.html?'+new URLSearchParams({game:g.id,date:E.day(g.startsAt)});}
 function snapshotPrice(choice){
  if(!choice||choice.price===null)return 'No offer';
  return cents(choice.price)+(choice.fresh?'':' · saved');
 }
 function card(r,index,showGame=true){
  const box=node('article',null,'favorite-card');
  box.append(node('p','#'+(index+1)+' · '+(r.player?.team||'Game winner'),'eyebrow'),node('h3',E.label(r)));
  if(showGame)box.append(node('p',`${r.game.away} at ${r.game.home} · ${tip(r.game.startsAt)}`,'note'));
  const price=node('div',cents(r.price),'card-price');price.append(node('small',' current ask / contract'));box.append(price);
  box.append(node('p',`${r.side.toUpperCase()} on “${r.market.title}”`,'contract-side'));
  const metrics=node('div',null,'pick-metrics');
  for(const [title,value] of [['Model chance',pct(r.forecast.probability)],['Model margin*',cents(r.edge)]]){
   const cell=node('div');cell.append(node('span',title),node('strong',value));metrics.append(cell);
  }box.append(metrics);
  box.append(node('small',`Observed ${et(r.market.observedAt)} · ${Number(r.size.toFixed(2)).toLocaleString()} contracts shown at this ask.`));
  box.append(node('small',`*After a ${cents(r.fee)} estimated fee for one contract. Ranking uses the lower simulation sampling bound; model error is not included.`));
  box.append(showGame?link('View matchup →',gameUrl(r.game)):external('View exact Kalshi contract ↗',r.market.url));
  return box;
 }
 function favorites(target,games,showGame){
  target.replaceChildren();const picks=E.favorites(games);
  picks.forEach((r,i)=>target.append(card(r,i,showGame)));
  if(picks.length<3)target.append(node('p',picks.length?`Only ${picks.length} props currently meet the price, liquidity and forecast checks. More appear when they qualify.`:'No current favorites meet all checks. Fresh quotes, a usable pregame forecast, at least one contract at the ask and a positive margin after the estimated fee are required.','empty-picks'));
 }
 function marketStatus(){
  const at=quotes?.generatedAt,coverage=quotes?.coverage||[],complete=coverage.filter(c=>c.complete).length;
  const fresh=quotes?.markets?.filter(m=>Date.now()-Date.parse(m.observedAt)<=E.FRESH_MS&&Date.now()>=Date.parse(m.observedAt)-1000).length||0;
  $('quoteStatus').textContent=quotes?`${quoteFailed?'Refresh failed · retaining timestamped observations. ':''}${fresh?'Recent Kalshi observations':'Saved quotes only'} · ${complete}/${coverage.length} series checked · Last scan ${et(at)}. Refreshes every 30 seconds while this page is visible.`:'Kalshi prices unavailable. Retrying every 30 seconds.';
  $('quoteDot').classList.toggle('live',fresh>0&&!quoteFailed);
  $('modelStatus').textContent=models?`${modelFailed?'Forecast reload failed · saved model retained. ':''}Model: ${models.runs.toLocaleString()} scenarios per game · ${et(models.generatedAt)} · Availability valid until ${et(models.validUntil)}.`:'Forecast unavailable. Market prices can be reviewed once a schedule loads.';
  $('coverageDetails').replaceChildren();
  if(quotes){
   $('coverageDetails').append(node('p',quotes.scope),node('p',`${board?.unmatched||0} contracts could not be uniquely matched to the modeled schedule and are not ranked.`));
   const list=node('ul',null,'coverage-list');
   for(const c of coverage)list.append(node('li',`${c.series}: ${c.complete?`${c.accepted} recognized / ${c.rawCount} returned${c.unsupported?' · '+c.unsupported+' unsupported rules':''}`:'Unavailable — excluded from this scan'}`));
   $('coverageDetails').append(list);
  }
 }
 function render(){
  if(!models){
   marketStatus();
   if(detailPage){$('gameContent').hidden=true;$('missingGame').hidden=false;$('missingGame').textContent='Game analysis is unavailable while the forecast loads. Use Refresh prices to try again.';}
   else{$('dailyFavorites').replaceChildren(node('p','Favorites are unavailable while the forecast loads. Use Refresh prices to try again.','empty-picks'));$('gameCards').replaceChildren(node('p','The game schedule is temporarily unavailable.','empty-day'));}
   return;
  }
  board=E.build(models,quotes);marketStatus();
  if(detailPage){renderGame();return;}
  $('gameDate').value=selectedDate;
  const games=board.games.filter(g=>E.day(g.startsAt)===selectedDate);
  $('slateTitle').textContent=new Date(selectedDate+'T12:00:00Z').toLocaleDateString('en-US',{timeZone:'America/New_York',weekday:'long',month:'long',day:'numeric'});
  $('slateCount').textContent=games.length+' games';favorites($('dailyFavorites'),games,true);
  $('gameCards').replaceChildren();
  for(const g of games){
   const card=link('',gameUrl(g));card.className='matchup-card';card.append(node('p',tip(g.startsAt),'tip'));
   const p=!g.hold?g.model?.gameProjection:null;
   for(const [name,score] of [[g.awayName,p?.awayScore.mean],[g.homeName,p?.homeScore.mean]]){
    const row=node('div',null,'team-line');row.append(node('h3',name),node('strong',number(score)));card.append(row);
   }
   card.append(node('small',E.winnerSides(g).map(w=>`${w.team} win: ${snapshotPrice(w.choice)}`).join(' · ')));
   card.append(node('p',p?`${p.homeMargin.mean>=0?g.homeName:g.awayName} by ${Math.abs(p.homeMargin.mean).toFixed(1)} · Model projection`:g.hold||'Forecast unavailable','forecast-call'));
   card.append(node('small',`${g.props.length} Kalshi player lines · Analysis & all prices →`));$('gameCards').append(card);
  }
  if(!games.length)$('gameCards').append(node('p','No games in the saved schedule for this date.','empty-day'));
 }
 function currentGame(){return board?.games.find(g=>g.id===requestedGame||g.model?.eventId===requestedGame);}
 function renderGame(){
  const g=currentGame();$('gameContent').hidden=!g;$('missingGame').hidden=!!g;
  if(!g)return;
  $('backToBoard').href='betting.html?date='+E.day(g.startsAt);
  $('detailTitle').textContent=g.awayName+' at '+g.homeName;document.title=g.away+' at '+g.home+' · NBAI';
  $('detailTime').textContent=tip(g.startsAt);$('detailBadge').textContent=Date.parse(g.startsAt)<=Date.now()?'Pregame closed':g.hold?'Forecast on hold':'Pregame';
  const p=!g.hold?g.model?.gameProjection:null;$('forecastHero').replaceChildren();
  const lead=node('div');lead.append(node('p','OUR GAME READ','eyebrow'));
  lead.append(node('h3',p?`${p.homeMargin.mean>=0?g.homeName:g.awayName} by ${Math.abs(p.homeMargin.mean).toFixed(1)}`:'Forecast on hold'));
  lead.append(node('p',p?`Projected total ${number(p.total.mean)} · Home win share ${pct(p.homeWinShare)}.`:g.hold));$('forecastHero').append(lead);
  const scores=node('div',null,'score-pair');
  for(const [name,score] of [[g.awayName,p?.awayScore.mean],[g.homeName,p?.homeScore.mean]]){const cell=node('div');cell.append(node('span',name),node('strong',number(score)));scores.append(cell);}
  $('forecastHero').append(scores);
  $('gameOpinion').textContent=p?`Our simulator favors ${p.homeWinShare>=.5?g.homeName:g.awayName} to win (${pct(Math.max(p.homeWinShare,1-p.homeWinShare))} of scenarios). The middle 80% of simulated totals runs from ${number(p.total.p10)} to ${number(p.total.p90)}. The favorite to win is not automatically the best value at the current price.`:g.hold;
  $('winnerCards').replaceChildren();
  for(const w of E.winnerSides(g)){
   const r=w.choice,cell=node('article',null,'winner-card');cell.append(node('h3',w.name),node('div',snapshotPrice(r),'winner-price'));
   cell.append(node('small',r?`${r.side.toUpperCase()} on “${r.market.title}”`:'No matching winner quote'));
   cell.append(node('p',r?.forecast?`Model chance ${pct(r.forecast.probability)} · Market break-even ${r.price===null?'unavailable':pct(r.price+(r.fee||0))} including the estimated one-contract fee.`:'Model comparison unavailable'));
   cell.append(node('p',r?.eligible?r.rank>0?'Our view: a positive model margin at this ask.':'Our view: no conservative model margin at this ask.':g.hold||'Wait for a fresh, liquid quote.'));
   if(r)cell.append(node('small',`Observed ${et(r.market.observedAt)} · ${number(r.size)} contracts at this ask`),external('Exact contract ↗',r.market.url));
   $('winnerCards').append(cell);
  }
  favorites($('gameFavorites'),[g],false);renderCombo(g);playerLines();
  $('gameMethod').textContent=`${models.runs} possession-based scenarios using the roster and availability snapshot. Reported Out players are excluded. Other players are conditional on participation; confirmed starters and minutes restrictions are not modeled. Questionable or unaligned player samples are not ranked. Forecast accuracy remains under evaluation.`;
 }
 function renderCombo(g){
  const target=$('comboCard');target.replaceChildren();const c=E.combo(g);
  if(!c){target.append(node('p','No supported combo right now. We need a current winner quote and a qualifying player prop from each team.'));return;}
  target.append(node('p','WINNER + ONE PLAYER FROM EACH TEAM','eyebrow'));
  const list=node('ol');for(const r of c.legs){const li=node('li');li.append(node('strong',E.label(r)),node('small',`${r.side.toUpperCase()} on “${r.market.title}” · Single-contract ask ${cents(r.price)}`));list.append(li);}target.append(list);
  const metrics=node('div',null,'combo-metrics');
  for(const [label,value] of [['Joint model chance',pct(c.joint.probability)],['Kalshi combo price','Not available']]){const n=node('div');n.append(node('span',label),node('strong',value));metrics.append(n);}target.append(metrics);
  target.append(node('p',`The game script starts with ${c.winner.name} winning. The props are the strongest qualifying model-price comparisons from each team. All three outcomes occurred together in ${pct(c.joint.probability)} of the same simulated games; correlation is retained.`));
  target.append(node('p','Research combo only. Exact Kalshi acceptance, price and payout are not connected, so this is not a verified value recommendation. Individual ask prices are not added or multiplied into a combo price. DNP, cancellation and fractional settlements are not simulated.','note'));
 }
 function playerLines(){
  const g=currentGame();if(!g)return;
  const text=$('playerSearch').value.toLowerCase(),stat=$('statFilter').value;
  const rows=g.props.filter(r=>(r.market.player||'').toLowerCase().includes(text)&&(stat==='all'||r.market.stat===stat));
  const open=new Set([...$('allPlayerLines').querySelectorAll('details[open]')].map(n=>n.dataset.player));
  $('allPlayerLines').replaceChildren();$('playerLineCount').textContent=`${rows.length} of ${g.props.length} recognized Kalshi lines shown. YES and NO are separate purchase prices.`;
  const groups=new Map();for(const r of rows){const name=r.market.player;if(!groups.has(name))groups.set(name,[]);groups.get(name).push(r);}
  for(const [name,lines] of [...groups].sort((a,b)=>a[0].localeCompare(b[0]))){
   const details=node('details',null,'player-lines');details.dataset.player=name;details.open=!!text||open.has(name);
   const summary=node('summary',name);summary.append(node('small',lines.length+' lines'));details.append(summary);
   const scroll=node('div',null,'props-scroll');scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label',name+' Kalshi lines');
   const table=node('table',null,'kalshi-lines-table'),head=node('thead'),tr=node('tr');
   for(const t of ['Contract / YES outcome','YES ask','NO ask','Model YES chance','Our opinion']){const th=node('th',t);th.scope='col';tr.append(th);}head.append(tr);table.append(head);
   const body=node('tbody');
   for(const r of lines.sort((a,b)=>a.market.stat.localeCompare(b.market.stat)||a.market.threshold-b.market.threshold)){
    const row=node('tr'),title=node('td');title.append(external(r.market.title,r.market.url),node('small','Observed '+et(r.market.observedAt)));row.append(title);
    for(const s of [r.yes,r.no]){const cell=node('td',snapshotPrice(s));cell.append(node('small',s.liquid?number(s.size)+' contracts shown':'No verified size for one contract'));row.append(cell);}
    const probability=node('td',r.hits?pct(E.estimate(r.hits).probability):'—');
    if(r.average!==null&&!['DD','TD'].includes(r.market.stat))probability.append(node('small','Model average '+number(r.average)));
    row.append(probability);const best=r.best,opinion=node('td');
    if(r.hold)opinion.textContent=r.hold;
    else if(!best)opinion.textContent='No fresh, liquid offer to compare';
    else if(best.rank<=0)opinion.textContent='Pass at current prices';
    else opinion.append(node('strong',E.label(best),'opinion-positive'),node('small',`${best.side.toUpperCase()} · ${cents(best.edge)} model margin after estimated fee`));
    row.append(opinion);body.append(row);
   }
   table.append(body);scroll.append(table);details.append(scroll);$('allPlayerLines').append(details);
  }
  if(!rows.length)$('allPlayerLines').append(node('p',g.props.length?'No lines match your search.':'No recognized player markets returned for this game. See coverage details below.','empty-picks'));
 }
 async function json(url){const r=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(30000)});if(!r.ok)throw Error('Unavailable');return r.json();}
 async function load(force=false){
  if(loading)return;loading=true;$('refreshBoard').disabled=true;
  const reloadModel=force===true||!models||Date.now()-lastModelAt>=300000;
  const results=await Promise.allSettled([reloadModel?json('kalshi-board-model.json'):Promise.resolve(models),json('api/kalshi-board')]);
  if(results[0].status==='fulfilled'&&results[0].value.schemaVersion===1&&Array.isArray(results[0].value.games)&&Array.isArray(results[0].value.schedule)){
   models=results[0].value;if(reloadModel){modelFailed=false;lastModelAt=Date.now();}
  }else modelFailed=true;
  if(results[1].status==='fulfilled'&&results[1].value.schemaVersion===1&&Array.isArray(results[1].value.markets)&&Array.isArray(results[1].value.coverage)){
   quotes=results[1].value;quoteFailed=false;
  }else{quoteFailed=true;if(first&&!quotes){try{quotes=await json('kalshi-board-quotes.json');}catch(_){}}}
  first=false;loading=false;$('refreshBoard').disabled=false;render();
 }
 $('refreshBoard').onclick=()=>load(true);
 if(!detailPage){
  $('gameDate').onchange=()=>{selectedDate=$('gameDate').value||E.day(Date.now());history.replaceState({},'','betting.html?date='+selectedDate);render();};
  $('todayButton').onclick=()=>{selectedDate=E.day(Date.now());history.replaceState({},'','betting.html');render();};
 }else{
  $('playerSearch').oninput=playerLines;$('statFilter').onchange=playerLines;
  for(const [stat,title] of Object.entries(E.STATS)){const o=node('option',title);o.value=stat;$('statFilter').append(o);}
 }
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});
 setInterval(()=>{if(!document.hidden)load();},30000);
 setInterval(()=>{if(!document.hidden&&models)render();},10000);
 load();
})();
