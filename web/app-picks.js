(()=>{
 'use strict';
 const E=NBAI_APP_PICKS,C=NBAI_CONSENSUS,$=id=>document.getElementById(id);
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
 const button=(text,fn)=>{const n=el('button',text,'button secondary');n.type='button';n.onclick=fn;return n;};
 const money=n=>n.toLocaleString('en-US',{style:'currency',currency:'USD'});
 const time=t=>new Date(t).toLocaleString('en-US',{timeZone:'America/New_York',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})+' ET';
 let rows=[],selected=new Set(),signature='',latest=null,app='prizepicks',viewSignature='';
 try{app=localStorage.getItem('nbai-picks-app')||app;}catch(_){}
 const requested=new URLSearchParams(location.search).get('app');
 if(E.APPS[requested])app=requested;
 if(!E.APPS[app])app='prizepicks';
 $('pickApp').value=app;
 function label(r){return `${r.player.name} · ${E.APPS[app][r.direction]} ${r.quote.line} ${C.STATS[r.stat].toLowerCase()}`;}
 function clearQuote(){for(const id of ['payoutMultiplier','entryStake'])$(id).value='';$('payoutResult').textContent='Enter the amount and total-return multiplier shown for this exact lineup in your app.';}
 function legs(){return rows.filter(r=>selected.has(r.key));}
 function renderPayout(){
  const valid=E.validCombo(legs());$('payoutInputs').disabled=!valid;
  const value=E.payout($('entryStake').valueAsNumber,$('payoutMultiplier').valueAsNumber,$('entryFormat').value);
  if(!valid){$('payoutResult').textContent='Choose 2–6 different players from at least two teams to explore a payout.';return;}
  if(!value){$('payoutResult').textContent='Enter the amount and total-return multiplier shown for this exact lineup in your app.';return;}
  const output=`Your entered multiplier: ${$('payoutMultiplier').value}× · Total return ${money(value.total)} · Net profit ${money(value.profit)} if that payout applies.`;
  $('payoutResult').textContent=output+(value.breakEven!==null?` An all-or-nothing entry would need a ${(value.breakEven*100).toFixed(1)}% joint win rate to break even, assuming no ties, voids or other payouts. This is a threshold, not our predicted win rate.`:' Partial-win tiers and leaderboard rewards are not calculated; the full payout schedule is needed to evaluate expected return.');
 }
 function renderSelection(){
  const picked=legs();$('lineupLegs').replaceChildren();
  signature=JSON.stringify(picked.map(r=>[r.key,r.quote.sourceUpdatedAt,r.quote.observedAt]));
  for(const r of picked){const li=el('li');li.append(el('strong',label(r)),el('small',`${r.event.away_team} at ${r.event.home_team} · ${r.fresh?'Recent line':'Saved / stale line'}`),button('Remove',()=>toggle(r)));$('lineupLegs').append(li);}
  $('lineupStatus').textContent=!picked.length?'Start with a draft below or add your own selections.':E.validCombo(picked)?`${picked.length}-pick research lineup · ${picked.every(r=>r.fresh)?'Recent lines':'Saved lines — verify each line in the app'}. App acceptance and payout are unverified.`:'Use 2–6 different players from at least two teams. App-specific restrictions may also apply.';
  for(const b of $('appLines').querySelectorAll('button[data-key]')){
   const on=selected.has(b.dataset.key);b.textContent=on?'Remove from lineup':'Add to lineup';b.setAttribute('aria-pressed',String(on));b.disabled=!on&&picked.length>=6;
  }
  renderPayout();
 }
 function toggle(r){
  if(selected.has(r.key))selected.delete(r.key);
  else {if(selected.size>=6)return;for(const other of legs())if(other.player.id===r.player.id)selected.delete(other.key);selected.add(r.key);}
  clearQuote();renderSelection();
 }
 function renderDrafts(){
  $('appCombos').replaceChildren();
  for(const draft of E.drafts(rows)){
   const card=el('article',null,'app-combo');
   card.append(el('p',`${draft.legs.length}-pick ${draft.fresh?'model draft':'saved draft · verify lines'}`,'eyebrow'));
   const list=el('ol');for(const r of draft.legs)list.append(el('li',label(r)));card.append(list);
   card.append(el('p','Payout not connected · Confirm this exact combination in your app.','note'));
   card.append(button('Explore this lineup',()=>{selected=new Set(draft.legs.map(r=>r.key));clearQuote();renderSelection();$('lineupBuilder').scrollIntoView({behavior:'smooth',block:'center'});}));
   $('appCombos').append(card);
  }
  if(!$('appCombos').children.length)$('appCombos').append(el('p','No supported draft from the saved lines. We need modeled players from at least two teams.'));
 }
 function render(market,events,date,failed=false){
  latest={market,events,date,failed};
  const urlApp=new URLSearchParams(location.search).get('app');
  if(E.APPS[urlApp]){app=urlApp;$('pickApp').value=app;}
  const view=app+'|'+date+'|'+events.map(e=>e.id).join(',');
  if(view!==viewSignature){selected.clear();clearQuote();viewSignature=view;}
  rows=E.candidates(market,events,app);
  const previous=[...selected];selected=new Set(previous.filter(k=>rows.some(r=>r.key===k)));
  const nextSignature=JSON.stringify(legs().map(r=>[r.key,r.quote.sourceUpdatedAt,r.quote.observedAt]));
  if(signature!==nextSignature){clearQuote();signature=nextSignature;}
  const config=E.APPS[app];$('appPicksTitle').textContent=config.name+' favorites';
  $('appMode').textContent=`Georgia · ${config.format} · NBA player props`;
  $('appRules').href=config.rules;$('openPickApp').href=config.url;$('openPickApp').textContent='Open '+config.name;
  const fresh=rows.filter(r=>r.fresh).length;
  $('appPicksStatus').textContent=`${failed?'Reload failed — saved data retained. ':''}${rows.length} model comparisons · ${fresh} lines updated within 15 minutes. ${fresh?'Check availability and prices in the app.':'No current favorites verified. Saved leans below are for research; confirm the lines before using them.'}`;
  $('appLines').replaceChildren();
  // Keep a second market for a player available to swap, but show the strongest players first.
  const first=[],rest=[],seen=new Set();
  for(const r of rows){if(seen.has(r.player.id))rest.push(r);else{first.push(r);seen.add(r.player.id);}}
  for(const r of [...first,...rest].slice(0,18)){
   const card=el('article',null,'app-line');
   card.append(el('span',r.fresh?'Recent model lean':'Saved lean · verify line','state-pill'),el('h3',label(r)),el('p',`${r.player.team} · ${r.event.away_team} at ${r.event.home_team}`,'note'));
   card.append(el('p',`Model average ${r.model.mean.toFixed(1)} · App line ${r.quote.line} · Difference ${r.gap>0?'+':''}${r.gap.toFixed(1)}`));
   card.append(el('small',`Tip ${time(r.event.commence_time)} · Line updated ${time(r.quote.sourceUpdatedAt)}`));
   if(r.player.injuryStatus&&r.player.injuryStatus!=='Not listed')card.append(el('small',r.player.injuryStatus+' · Projection assumes participation.'));
   const b=button('Add to lineup',()=>toggle(r));b.dataset.key=r.key;card.append(b);$('appLines').append(card);
  }
  if(!rows.length)$('appLines').append(el('p','No eligible model comparisons for this app and date. Started games, expired or held forecasts and unmatched lines are excluded.'));
  renderDrafts();renderSelection();
 }
 $('pickApp').onchange=()=>{
  app=$('pickApp').value;try{localStorage.setItem('nbai-picks-app',app);}catch(_){}
  const u=new URL(location.href);u.searchParams.set('app',app);history.replaceState({},'',u);
  if(latest)render(latest.market,latest.events,latest.date,latest.failed);
 };
 for(const id of ['entryStake','payoutMultiplier'])$(id).oninput=renderPayout;
 $('entryFormat').onchange=()=>{clearQuote();renderPayout();};
 $('clearLineup').onclick=()=>{selected.clear();clearQuote();renderSelection();};
 window.NBAI_APP_BOARD={render};
})();
