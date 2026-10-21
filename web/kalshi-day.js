(()=>{
 'use strict';
 const E=NBAI_KALSHI_DAY,R=NBAI_RESEARCH_PICKS,$=id=>document.getElementById(id);
 const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
 const cents=n=>Number.isFinite(n)?Number((n*100).toFixed(2))+'¢':'Unavailable';
 const et=t=>Number.isFinite(Date.parse(t))?new Date(t).toLocaleString('en-US',{timeZone:'America/New_York',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})+' ET':'Unknown';
 const link=(text,url)=>{const a=node('a',text);a.href=url;return a;};
 const external=(text,url)=>{const a=link(text,url);a.target='_blank';a.rel='noopener noreferrer';return a;};
 let models=null,quotes=null,research=null,board=null,loading=false,quoteFailed=false,researchFailed=false,first=true,lastModelAt=0;
 const detailPage=document.body.dataset.page==='matchup',query=new URLSearchParams(location.search);
 let selectedDate=/^\d{4}-\d{2}-\d{2}$/.test(query.get('date')||'')&&Number.isFinite(Date.parse(query.get('date')+'T12:00:00Z'))?query.get('date'):E.day(Date.now());
 const requestedGame=query.get('game');
 if(!detailPage&&requestedGame){location.replace('matchup.html?'+new URLSearchParams({game:requestedGame,date:selectedDate}));return;}
 const gameUrl=g=>'matchup.html?'+new URLSearchParams({game:g.id,date:E.day(g.startsAt)});
 const currentGame=()=>board?.games.find(g=>g.id===requestedGame||g.model?.eventId===requestedGame);
 const snapshotPrice=r=>r?.price!=null?cents(r.price)+(r.fresh?'':' · saved'):'No offer';
 function sources(target,list){
  const links=node('div',null,'source-links');for(const s of list||[])if(/^https:\/\//.test(s.url))links.append(external(s.title+' ↗',s.url));target.append(links);
 }
 function card(p,index,showGame){
  const {pick,game,row,review}=p,box=node('article',null,'favorite-card');
  box.append(node('p',String(index+1).padStart(2,'0')+' / '+pick.team,'eyebrow'),node('span',p.status,'pick-state '+p.state),node('h3',pick.label));
  if(showGame)box.append(link(game.away+' at '+game.home+' · '+et(E.tip(game)),gameUrl(game)));
  const price=node('div',snapshotPrice(row),'card-price');price.append(node('small',' / contract · before fees'));box.append(price);
  box.append(node('p',pick.side.toUpperCase()+' on “'+(row?.market.title||pick.contractTitle)+'”','contract-side'));
  box.append(node('p','Price limit '+cents(pick.maxPrice)+' · '+pick.conviction,'price-limit'),node('p',pick.thesis,'pick-thesis'));
  const risk=node('p',null,'pick-risk');risk.append(node('strong','What could go wrong: '),document.createTextNode(pick.risk));box.append(risk);
  const detail=node('details',null,'pick-evidence');detail.dataset.disclosure='pick-'+pick.ticker;detail.append(node('summary','Research & sources'));
  for(const line of pick.evidence||[])detail.append(node('p',line));sources(detail,pick.sources);
  detail.append(node('small','Research reviewed '+et(review.reviewedAt)+'. Historical results and preseason samples are context, not today’s win probability.'));box.append(detail);
  box.append(node('small',row?'Quote observed '+et(row.market.observedAt)+'. '+(row.fresh?'':'Saved observation; refresh before using. '):'No matching offer was returned.'));
  if(row)box.append(external('View exact Kalshi contract ↗',row.market.url));
  return box;
 }
 function favorites(target,games,showGame){
  target.replaceChildren();const picks=R.favorites(games,research);
  picks.forEach((p,i)=>target.append(card(p,i,showGame)));
  if(picks.length<3)target.append(node('p',picks.length?'Only '+picks.length+' players have completed research reviews for this slate.':'Research is pending for this slate. Favorites appear after a source-backed review; unreviewed lines stay on the player board.','empty-picks'));
 }
 function marketStatus(){
  const fresh=quotes?.markets?.some(m=>Date.now()-Date.parse(m.observedAt)<=E.FRESH_MS&&Date.now()>=Date.parse(m.observedAt)-1000);
  $('quoteStatus').textContent=quotes?`${quoteFailed?'Live refresh unavailable. ':''}${fresh?'Recent Kalshi quotes':'Saved quotes only'} · Last scan ${et(quotes.generatedAt)}. Refreshes every 30 seconds while visible.`:'Prices unavailable. Retrying every 30 seconds.';
  $('quoteDot').classList.toggle('live',!!fresh&&!quoteFailed);
  $('researchStatus').textContent=research?`${researchFailed?'Research refresh failed; retaining the dated review. ':''}News, role and historical-data reviews · Updated ${et(research.generatedAt)}. Price limits are judgments, not guaranteed edges.`:'Research reviews are loading. Picks are not generated from prices alone.';
  $('coverageDetails').replaceChildren();
  if(quotes){const list=node('ul');for(const c of quotes.coverage||[])list.append(node('li',c.series+': '+(c.complete?c.accepted+' recognized contracts':'Unavailable in this scan')));$('coverageDetails').append(list);}
 }
 function tabs(games,selected){
  $('gameTabs').replaceChildren();for(const g of games){const a=link(g.away+' at '+g.home,gameUrl(g));if(g.id===selected)a.setAttribute('aria-current','page');$('gameTabs').append(a);}
 }
 function render(){
  const opened=new Set([...document.querySelectorAll('[data-disclosure][open]')].map(n=>n.dataset.disclosure));
  const focused=document.activeElement?.closest('[data-disclosure]')?.dataset.disclosure;
  renderContent();
  for(const n of document.querySelectorAll('[data-disclosure]')){
   if(opened.has(n.dataset.disclosure))n.open=true;
   if(focused===n.dataset.disclosure)n.querySelector('summary')?.focus({preventScroll:true});
  }
 }
 function renderContent(){
  marketStatus();
  if(!models){
   if(detailPage){$('gameContent').hidden=true;$('missingGame').hidden=false;}
   else{$('dailyFavorites').replaceChildren(node('p','The schedule is unavailable. Refresh to try again.','empty-picks'));$('gameCards').replaceChildren();$('dailyCombo').replaceChildren();}
   return;
  }
  board=E.build(models,quotes);
  if(detailPage){renderGame();return;}
  $('gameDate').value=selectedDate;const games=board.games.filter(g=>E.day(g.startsAt)===selectedDate);
  $('slateTitle').textContent=new Date(selectedDate+'T12:00:00Z').toLocaleDateString('en-US',{timeZone:'America/New_York',weekday:'long',month:'long',day:'numeric'});
  $('slateCount').textContent=games.length+' games';favorites($('dailyFavorites'),games,true);tabs(games);
  const combos=games.map(g=>R.combo(g,research)).filter(Boolean).sort((a,b)=>(a.closed?2:a.ready?0:1)-(b.closed?2:b.ready?0:1)||(a.priority??99)-(b.priority??99));
  renderCombo($('dailyCombo'),combos[0],true);$('gameCards').replaceChildren();
  for(const g of games){const review=R.reviewFor(research,g),a=link('',gameUrl(g));a.className='matchup-card';a.append(node('p',et(E.tip(g)),'tip'),node('h3',g.awayName+' at '+g.homeName));
   a.append(node('p',review?.headline||'Matchup research pending'),node('small',Date.now()>=Date.parse(E.tip(g))?'Pregame closed · Read the saved research':R.favorites([g],research).length+' reviewed props · Open game analysis →'));$('gameCards').append(a);}
  if(!games.length)$('gameCards').append(node('p','No games in the saved schedule for this date.','empty-picks'));
 }
 function renderGame(){
  const g=currentGame();$('gameContent').hidden=!g;$('missingGame').hidden=!!g;if(!g)return;
  const review=R.reviewFor(research,g);tabs(board.games.filter(x=>E.day(x.startsAt)===E.day(g.startsAt)),g.id);
  $('backToBoard').href='betting.html?date='+E.day(g.startsAt);$('detailTitle').textContent=g.awayName+' at '+g.homeName;document.title=g.away+' at '+g.home+' · NBAI';
  $('detailTime').textContent=et(E.tip(g));$('detailBadge').textContent=Date.now()>=Date.parse(E.tip(g))?'Pregame closed':'Pregame research';
  $('gameHeadline').textContent=review?.headline||'Research in progress';$('gameOpinion').textContent=review?.analysis||'A matchup opinion will appear once the news, roles and historical context have been reviewed.';
  $('gameSources').replaceChildren();sources($('gameSources'),review?.sources);
  favorites($('gameFavorites'),[g],false);renderCombo($('comboCard'),R.combo(g,research),false);
  $('winnerCards').replaceChildren();for(const w of E.winnerSides(g)){const r=w.choice,cell=node('article',null,'winner-card');cell.append(node('h3',w.name),node('div',snapshotPrice(r),'winner-price'));
   cell.append(node('small',r?r.side.toUpperCase()+' on “'+r.market.title+'”':'No matching winner offer'));
   cell.append(node('p',review?.winnerTeam===w.team?review.winnerReason:'See the matchup script above. A game lean alone does not establish value at this price.'));
   if(r)cell.append(node('small','Observed '+et(r.market.observedAt)),external('Exact contract ↗',r.market.url));$('winnerCards').append(cell);}
  playerLines();
 }
 function renderCombo(target,c,showGame){
  target.replaceChildren();if(!c){target.append(node('p','A favorite game script will appear after the matchup and its individual legs have been reviewed.'));return;}
  target.append(node('p',c.closed?'SAVED PREGAME SCRIPT':'WINNER + A PLAYER FROM EACH TEAM','eyebrow'),node('h3',c.title));
  if(showGame)target.append(link(c.game.away+' at '+c.game.home+' · '+et(E.tip(c.game)),gameUrl(c.game)));
  target.append(node('p',c.script));const list=node('ol');
  const win=node('li');win.append(node('strong',c.winner?.name+' to win'),node('small',c.winner?.choice?c.winner.choice.side.toUpperCase()+' on “'+c.winner.choice.market.title+'” · Single ask '+snapshotPrice(c.winner.choice):'Winner quote unavailable'));list.append(win);
  for(const p of c.props){const li=node('li');li.append(node('strong',p.pick.label),node('small',p.pick.side.toUpperCase()+' · Single ask '+snapshotPrice(p.row)+' · '+p.status));list.append(li);}target.append(list);
  target.append(node('p','What breaks the script: '+c.risk),node('span','Combo price & payout unverified','pick-state wait'));
  target.append(node('p','This is a game-script idea, not a priced parlay recommendation. Kalshi may not offer these legs together. Single prices do not determine the combo payout; correlated legs require an actual combo quote.'));
  if(!c.ready)target.append(node('small',c.closed?'This game has started. The pregame script is retained for review.':'One or more legs need a price or availability refresh.'));
  sources(target,c.sources);
 }
 function playerLines(){
  const g=currentGame();if(!g)return;const search=$('playerSearch').value.toLowerCase(),stat=$('statFilter').value;
  const rows=g.props.filter(r=>(r.market.player||'').toLowerCase().includes(search)&&(stat==='all'||r.market.stat===stat));
  $('playerLineCount').textContent=rows.length+' recognized lines · Prices before fees';$('allPlayerLines').replaceChildren();
  const grouped=new Map();for(const r of rows){const name=r.market.player;if(!grouped.has(name))grouped.set(name,[]);grouped.get(name).push(r);}
  const reviews=R.picks([g],research);
  for(const [name,markets] of [...grouped].sort((a,b)=>a[0].localeCompare(b[0]))){
   const details=node('details',null,'player-lines');details.dataset.disclosure='player-'+name;if(search)details.open=true;details.append(node('summary',name+' · '+markets.length+' lines'));
   const scroll=node('div',null,'table-scroll'),table=node('table',null,'kalshi-lines-table'),head=node('thead'),tr=node('tr');for(const title of ['Contract','YES ask','NO ask','Research view'])tr.append(node('th',title));head.append(tr);table.append(head);const body=node('tbody');
   for(const r of markets){const tr=node('tr'),title=node('td');title.append(external(r.market.title+' ↗',r.market.url),node('small','Observed '+et(r.market.observedAt)));tr.append(title,node('td',snapshotPrice(r.yes)),node('td',snapshotPrice(r.no)));
    const p=reviews.find(p=>p.pick.ticker===r.market.ticker),view=node('td');view.textContent=p?p.pick.side.toUpperCase()+' · '+p.status+' · Limit '+cents(p.pick.maxPrice):'Not reviewed';tr.append(view);body.append(tr);}
   table.append(body);scroll.append(table);details.append(scroll);$('allPlayerLines').append(details);
  }
  if(!rows.length)$('allPlayerLines').append(node('p','No recognized lines match this selection.','empty-picks'));
 }
 async function json(url){const r=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(30000)});if(!r.ok)throw Error('Unavailable');return r.json();}
 async function load(force=false){
  if(loading)return;loading=true;$('refreshBoard').disabled=true;
  const reload=force||!models||Date.now()-lastModelAt>=300000;
  try{
   const results=await Promise.allSettled([reload?json('kalshi-board-model.json'):Promise.resolve(models),json('api/kalshi-board'),json('research-picks.json')]);
   if(results[0].status==='fulfilled'&&results[0].value.schemaVersion===1&&Array.isArray(results[0].value.games)&&Array.isArray(results[0].value.schedule)){models=results[0].value;if(reload)lastModelAt=Date.now();}
   if(results[1].status==='fulfilled'&&results[1].value.schemaVersion===1&&Array.isArray(results[1].value.markets)&&Array.isArray(results[1].value.coverage)){quotes=results[1].value;quoteFailed=false;}
   else{quoteFailed=true;if(first&&!quotes){try{quotes=await json('kalshi-board-quotes.json');}catch(_){}}}
   if(results[2].status==='fulfilled'&&results[2].value.schemaVersion===1&&Array.isArray(results[2].value.games)){research=results[2].value;researchFailed=false;}else researchFailed=true;
  }finally{first=false;loading=false;$('refreshBoard').disabled=false;render();}
 }
 $('refreshBoard').onclick=()=>load(true);
 if(!detailPage){
  const base=location.pathname.split('/').pop()||'index.html';
  $('gameDate').onchange=()=>{selectedDate=$('gameDate').value||E.day(Date.now());history.replaceState({},'',base+'?date='+selectedDate);render();};
  $('todayButton').onclick=()=>{selectedDate=E.day(Date.now());history.replaceState({},'',base);render();};
 }else{
  $('playerSearch').oninput=playerLines;$('statFilter').onchange=playerLines;
  for(const [stat,title] of Object.entries(E.STATS)){const o=node('option',title);o.value=stat;$('statFilter').append(o);}
 }
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});
 setInterval(()=>{if(!document.hidden)load();},30000);
 // Time gates keep updating even while the reader has an evidence panel open.
 setInterval(()=>{if(!document.hidden&&models)render();},10000);
 load();
})();
