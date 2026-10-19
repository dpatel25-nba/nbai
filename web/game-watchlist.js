(()=>{
 const $=id=>document.getElementById(id),node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
 const cents=x=>Number.isFinite(x)?(x*100).toFixed(1)+'¢':'—',pct=x=>Number.isFinite(x)?(x*100).toFixed(1)+'%':'—';
 let ticket=0,lastKey='',lastAt=0,current=null,models=null;
 async function render(event,bundle,force=false){
  current=event;models=bundle;const quantity=Number($('watchQuantity').value),key=event.gameId+':'+quantity+':'+bundle.inputSignature;
  if(!force&&key===lastKey&&Date.now()-lastAt<25000)return;
  const mine=++ticket;lastKey=key;lastAt=Date.now();$('watchStatus').textContent='Refreshing selected contracts…';$('watchRows').replaceChildren();$('watchCombos').replaceChildren();
  try{
   const [snapshot,quoteResponse]=await Promise.all([fetch('game-watchlist.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json();}),fetch('api/watchlist?'+new URLSearchParams({game:event.gameId,quantity}),{cache:'no-store'}).then(r=>r.ok?r.json():{status:'Live quotes unavailable',quotes:[]}).catch(()=>({status:'Live quotes unavailable',quotes:[]}))]);
   if(mine!==ticket)return;
   const game=snapshot.games.find(x=>x.id===event.gameId),now=Date.now(),usable=snapshot.inputSignature===bundle.inputSignature&&now<Date.parse(snapshot.validUntil)&&now<Date.parse(snapshot.expiresAt)&&!NBAI_CONSENSUS.forecastHold(event.model,now)&&now<Date.parse(event.commence_time);
   $('watchStatus').textContent=game?`${quoteResponse.status} · ${quantity} contracts · ${snapshot.runs.toLocaleString()} simulation scenarios. Quotes refresh every 30 seconds while this matchup is open.`:'No fixed watch targets for this game.';
   if(!game)return;
   for(const pick of game.singles){
    const q=quoteResponse.quotes?.find(x=>x.id===pick.id),fresh=q&&now-Date.parse(q.receivedAt)>=-60000&&now-Date.parse(q.receivedAt)<=30000,f=usable?pick.forecast:null,c=fresh?q.cost:null;
    const tr=node('tr',''),selection=node('td',pick.label);selection.append(node('small',pick.side.toUpperCase()+' · '+pick.ticker));tr.append(selection);
    const model=node('td',pct(f?.probability));model.append(node('small',f?'Simulation share · conditional on playing':pick.hold||'Forecast expired or updating'));tr.append(model);
    const target=node('td',cents(f?.targetAsk));target.append(node('small','Research watch price'));tr.append(target);
    const price=node('td',fresh?cents(q.ask):'—');price.append(node('small',fresh?`${q.askSize} contracts at best ask`:'No recent verified quote'));tr.append(price);
    const cost=node('td',c?cents(c.unitCost)+' / contract':'—');cost.append(node('small',c?`$${c.total.toFixed(2)} total · $${c.fee.toFixed(2)} estimated fee`:fresh?'Insufficient depth for quantity':'Cost unavailable'));tr.append(cost);
    const depth=f&&fresh?q.asks.filter(x=>x[0]<=f.targetAsk).reduce((v,x)=>v+x[1],0):0;
    const reached=f&&c&&depth>=quantity&&q.ask<=f.targetAsk&&f.samplingLower-c.unitCost>=.05;
    const status=node('td',!fresh?'Quote unavailable':!f?'Forecast on hold':!c?'Insufficient depth':reached?'Watch price reached · observe only':'Waiting for watch price');
    status.append(node('small',fresh?'Observed '+new Date(q.receivedAt).toLocaleTimeString():'No fill assumed'));tr.append(status);$('watchRows').append(tr);
   }
   for(const pick of game.combos){const row=node('article',''),f=usable?pick.forecast:null;row.className='book-card';row.append(node('h3',pick.label),node('p',`Joint simulation share: ${pct(f?.probability)} · Watch price: ${cents(f?.targetAsk)}`),node('small','Exact combo quote pending · observe only'));$('watchCombos').append(row);}
  }catch(_){if(mine===ticket)$('watchStatus').textContent='Watchlist unavailable. No entry condition can be evaluated.';}
 }
 $('watchQuantity').onchange=()=>{if(current)render(current,models,true);};
 window.NBAI_GAME_WATCHLIST={render};
})();
