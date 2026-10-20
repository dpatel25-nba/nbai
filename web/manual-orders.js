(()=>{
 'use strict';
 const E=window.NBAI_MANUAL_ORDERS,$=id=>document.getElementById(id);
 const state={watch:null,models:null,availability:null,reference:null,live:{},rows:[],view:'singles',busy:false,loaded:false};
 const money=n=>Number.isFinite(n)?'$'+n.toFixed(2):'—';
 const cents=n=>Number.isFinite(n)?Math.round(n*10000)/100+'¢':'—';
 const pct=n=>Number.isFinite(n)?(n*100).toFixed(1)+'%':'—';
 const clock=s=>s&&Number.isFinite(Date.parse(s))?new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'numeric',minute:'2-digit'}).format(new Date(s))+' ET':'—';
 const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
 function append(parent,...nodes){parent.append(...nodes);return parent;}
 function detail(label,value){return append(el('div','row-data'),el('small','',label),el('span','',value));}
 function toast(message){$('copyStatus').textContent=message;clearTimeout(toast.timer);toast.timer=setTimeout(()=>{$('copyStatus').textContent='';},6500);}
 function copyButton(row){
  const b=el('button','',row.copyable?'Copy worksheet':'On hold');b.type='button';b.disabled=!row.copyable;
  b.addEventListener('click',async()=>{
   try{
    // Rebuild at click time: a visible button is not proof the data is still valid.
    const current=buildRows().find(r=>r.id===row.id),text=E.worksheet(current||{});
    await navigator.clipboard.writeText(text);toast('Worksheet copied. Nothing has been submitted to Kalshi.');
   }catch(_){toast('Cannot copy. Refresh the quotes and check forecast validity.');render();}
  });return b;
 }
 function contract(row){
  const d=el('details','contract');append(d,el('summary','','Exact contract & side'),el('p','',row.contractTitle+' · BUY '+row.side.toUpperCase()),el('code','',row.ticker),el('p','','The side applies to this exact contract. Verify its settlement terms in Kalshi.'));
  return d;
 }
 function card(row){
  const c=el('article','order-card'),body=el('div','card-content'),bottom=el('div','card-bottom');
  append(body,append(el('div','card-top'),el('span','',row.matchup),el('span','',clock(row.startsAt))),el('h3','',row.label),el('span','side','BUY '+row.side.toUpperCase()));
  const price=append(el('div'),el('strong','',row.limitCents===null?'—':row.limitCents+'¢'),el('span','','Patient limit / contract'));
  const ask=append(el('div','ask-price'),el('b','',row.quoteFresh?cents(row.marketAsk):'—'),el('span','',row.quoteFresh?'Current ask':'Quote unavailable'));
  append(body,append(el('div','price-line'),price,ask),
   append(el('div','limit-info'),el('span','','Model ceiling '+(row.ceilingCents===null?'—':row.ceilingCents+'¢')),el('span','','Model chance '+pct(row.probability))));
  const size=el('div','size-grid');
  for(const [label,value] of [['Contracts',row.cost?.quantity??'—'],['Est. fee',money(row.cost?.fee)],['Est. total',money(row.cost?.total)]]){
   append(size,append(el('div'),el('span','',label),el('b','',value)));
  }
  append(body,size,el('p','fine','Principal '+money(row.cost?.principal)+' · Full-win payout '+money(row.cost?.maxPayout)),
   el('p','fine','Reference '+cents(row.referenceAsk)+' at '+clock(row.referenceAt)+' · Quote '+clock(row.quoteAt)),contract(row));
  append(bottom,el('span','badge'+(row.hold?' hold':''),row.hold||row.status),el('p','fine','Review / expire by '+clock(row.expiresAt)),copyButton(row));
  return append(c,body,bottom);
 }
 function compact(row){
  const r=el('article','watch-row');
  append(r,append(el('div'),el('h3','',row.label),el('p','fine',row.matchup+' · '+clock(row.startsAt)+' · BUY '+row.side.toUpperCase()),contract(row)),
   detail('Patient limit',row.limitCents===null?'—':row.limitCents+'¢'),
   detail('Ask / ceiling',(row.quoteFresh?cents(row.marketAsk):'—')+' / '+(row.ceilingCents===null?'—':row.ceilingCents+'¢')),
   detail('Qty / est. total',(row.cost?.quantity??'—')+' / '+money(row.cost?.total)),
   append(el('div','row-status'+(row.hold?' hold':'')),el('span','',row.hold||row.status),el('p','fine','Expires '+clock(row.expiresAt))),copyButton(row));
  return r;
 }
 function buildRows(){return E.build({...state,budget:Number($('orderBudget').value),discount:Number($('entryDiscount').value)});}
 function render(){
  if(!state.loaded)return;
  state.rows=buildRows();
  const selected=E.shortlist(state.rows),short=$('shortlist');short.replaceChildren();
  selected.forEach(r=>short.append(card(r)));
  if(!selected.length)short.append(el('p','empty','No current shortlist is available. Check the full watchlist for holds or refresh the quotes.'));
  $('gameCount').textContent=new Set(state.rows.map(r=>r.gameId)).size;
  $('pickCount').textContent=state.rows.length;
  $('readyCount').textContent=state.rows.filter(r=>r.copyable).length;
  $('singlesCount').textContent=state.rows.length;
  $('modelStatus').textContent='Model '+clock(state.watch.generatedAt)+' · Availability expires '+clock(state.watch.validUntil);
  const todayGames=state.watch.games.filter(g=>E.dateET(g.starts_at)===E.dateET(Date.now()));
  $('combosCount').textContent=todayGames.reduce((n,g)=>n+g.combos.length,0);
  const filter=$('gameFilter').value,list=$('watchRows');list.replaceChildren();
  if(state.view==='singles'){
   $('watchNote').textContent='The patient limit is your draft entry. The model ceiling is a boundary, not a reason to chase a rising price.';
   state.rows.filter(r=>filter==='all'||r.gameId===filter).forEach(r=>list.append(compact(r)));
  }else{
   $('watchNote').textContent='Combos need their own executable quote and settlement model. Individual leg prices cannot establish a combo payout; no combo worksheets are available yet.';
   for(const g of todayGames.filter(g=>filter==='all'||g.id===filter))for(const combo of g.combos){
    const held=combo.hold||g.singles.some(p=>E.modelHold(state.watch,state.models,state.availability,g,p,Date.now()));
    const desc=el('div');append(desc,el('p','eyebrow',g.away+' AT '+g.home),el('h3','',combo.label),
     el('p','fine',held?'Forecast on hold':'Simulated all-win share '+pct(combo.forecast?.probability)+' · Conditional on players playing'),
     el('p','fine','DNP / fractional settlement is not modeled.'));
    list.append(append(el('article','combo-row'),desc,el('span','chip','Exact combo quote unavailable')));
   }
  }
  if(!list.children.length)list.append(el('p','empty','No markets match this view.'));
 }
 async function json(url){
  const r=await fetch(url,{method:'GET',cache:'no-store',signal:AbortSignal.timeout(20000)});if(!r.ok)throw Error('Source unavailable');
  return r.json();
 }
 async function refresh(){
  if(state.busy||document.hidden)return;state.busy=true;$('refreshOrders').disabled=true;
  $('feedStatus').textContent='Refreshing forecasts and public quotes…';
  try{
   const [watch,models,availability,reference]=await Promise.all(['game-watchlist.json','player-game-projections.json','game-availability.json','manual-order-snapshot.json'].map(p=>json(p)));
   Object.assign(state,{watch,models,availability,reference,loaded:true});
   const filter=$('gameFilter'),previous=filter.value;
   filter.replaceChildren(new Option('All games','all'));
   for(const g of watch.games.filter(g=>E.dateET(g.starts_at)===E.dateET(Date.now())))filter.add(new Option(g.away+' at '+g.home,g.id));
   if([...filter.options].some(o=>o.value===previous))filter.value=previous;
   render();
   const games=watch.games.filter(g=>E.dateET(g.starts_at)===E.dateET(Date.now()));
   const responses=await Promise.allSettled(games.map(g=>json('/api/watchlist?game='+encodeURIComponent(g.id)+'&quantity=20')));
   state.live={};
   responses.forEach((result,i)=>{if(result.status==='fulfilled')state.live[games[i].id]=result.value;});
   render();
   const count=state.rows.filter(r=>r.quoteFresh).length;
   $('feedStatus').textContent=count+'/'+state.rows.length+' current quotes · Checked '+clock(new Date().toISOString())+' · Refreshes every 20s while visible';
  }catch(_){
   state.live={};render();$('feedStatus').textContent='Sources unavailable. Worksheets are on hold until a successful refresh.';
   if(!state.loaded){$('shortlist').replaceChildren(el('p','empty','Unable to load today’s forecasts. Refresh to try again.'));$('modelStatus').textContent='Forecast unavailable';}
  }finally{state.busy=false;$('refreshOrders').disabled=false;}
 }
 $('todayLabel').textContent=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',weekday:'long',month:'short',day:'numeric'}).format(new Date());
 $('refreshOrders').addEventListener('click',refresh);
 $('orderBudget').addEventListener('input',render);$('entryDiscount').addEventListener('change',render);$('gameFilter').addEventListener('change',render);
 for(const [id,view] of [['singlesTab','singles'],['combosTab','combos']])$(id).addEventListener('click',()=>{
  state.view=view;$('singlesTab').setAttribute('aria-pressed',String(view==='singles'));$('combosTab').setAttribute('aria-pressed',String(view==='combos'));render();
 });
 // Do not replace focused DOM controls during the periodic freshness check.
 setInterval(()=>{if(!document.hidden&&state.rows.some(r=>r.copyable&&(!E.fresh(r.quoteAt,Date.now())||Date.now()>=Date.parse(r.expiresAt))))render();},1000);
 setInterval(refresh,20000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden){state.live={};render();refresh();}});
 refresh();
})();
