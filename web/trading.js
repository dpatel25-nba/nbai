/* Browser-local historical replay. Only a static same-origin dataset is fetched. */
(async function() {
  'use strict';
  const E=window.NBAITrading, $=id=>document.getElementById(id), KEY='nbai.trading.sandbox.v1';
  const money=value=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(value);
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const PAGE_SIZE=12;
  let data,state,timer=null,page=0,storageOK=true;
  function message(text,error=false){$('message').textContent=text;$('message').classList.toggle('error',error);}
  function save(){try{localStorage.setItem(KEY,JSON.stringify(E.checkpoint(state)));}catch(_){storageOK=false;}$('storageStatus').textContent=storageOK?'Saved on this browser only. Reloading restores the run paused; closing this page stops playback.':'Browser storage is unavailable. Download your session to keep its results.';}
  function readSettings(){return E.settings({bankroll:Number($('bankroll').value),dailySpend:Number($('dailySpend').value),lossStop:Number($('lossStop').value),slippage:$('slippage').value});}
  function stop(){clearInterval(timer);timer=null;}
  function chart(){
    if(!state.history.length){$('equityChart').innerHTML='<p class="empty">Your virtual balance history will appear here.</p>';return;}
    const values=[state.config.bankroll,...state.history.map(r=>r.balance)],low=Math.min(...values)-1,high=Math.max(...values)+1;
    const xy=values.map((v,i)=>[75+i/(values.length-1)*825,135-(v-low)/(high-low)*115]);
    $('equityChart').innerHTML=`<svg viewBox="0 0 930 160" preserveAspectRatio="none" role="img" aria-label="Virtual balance from ${escape(money(values[0]))} to ${escape(money(values.at(-1)))}. Daily-close drawdown ${escape(money(state.drawdown/100))}."><line x1="75" x2="900" y1="135" y2="135" stroke="#d7ddcf"/><polyline fill="none" stroke="#38604b" stroke-width="2.5" points="${xy.map(p=>p.join(',')).join(' ')}"/><text x="0" y="25">${escape(money(high))}</text><text x="0" y="135">${escape(money(low))}</text></svg>`;
  }
  function render(){
    const done=state.index>=state.days.length,blocked=!!state.halt||done;
    $('settings').disabled=state.index>0||!!timer;$('run').disabled=blocked||!!timer;$('step').disabled=blocked||!!timer;$('pause').disabled=!timer;$('reset').disabled=false;
    $('run').firstChild.textContent=state.index?'Resume replay ':'Run replay ';
    $('download').disabled=!state.index;$('balance').textContent=money(state.cash/100);$('net').textContent=money(state.net/100);$('net').classList.toggle('negative',state.net<0);
    $('fills').textContent=state.filled;$('passes').textContent=state.skipped;$('drawdown').textContent='Daily-close drawdown: '+money(state.drawdown/100);
    $('progress').textContent=`${state.index} / ${state.days.length} days replayed${state.index?' · Through '+state.days[state.index-1]:''}`;
    $('progressBar').max=state.days.length;$('progressBar').value=state.index;
    $('costs').textContent=`Virtual cost: ${money(state.spent/100)} · Includes ${money(state.fees/100)} in assumed fees · ${state.config.slippage==='0'?'No':Number(state.config.slippage)*100+'¢'} adverse price allowance per contract. All fills are hypothetical.`;
    const filter=$('ledgerFilter').value,rows=state.ledger.filter(r=>filter==='all'||(filter==='simulated'?r.status==='simulated':r.status!=='simulated')).slice().reverse();
    const pages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));page=Math.min(page,pages-1);
    $('ledger').innerHTML=rows.slice(page*PAGE_SIZE,page*PAGE_SIZE+PAGE_SIZE).map(r=>{
      const probability=r.side==='no'?1-r.model_yes:r.model_yes;
      return `<tr><td>${escape(r.date)}<small>${escape(r.matchup)}</small></td><td>${escape(r.ticker.split('-').at(-1))} · ${escape(r.side?.toUpperCase()||'—')}<small>${escape(r.ticker)}</small></td><td>${r.side&&r.model_yes!==null?(probability*100).toFixed(1)+'%':'—'}</td><td>${r.quoted_ask!==null?(Number(r.quoted_ask)*100).toFixed(1)+'¢':'—'}</td><td>${r.status==='simulated'?money(r.cost):'—'}</td><td class="${r.net<0?'negative':''}">${r.status==='simulated'?money(r.net):'—'}</td><td>${escape(r.status==='simulated'?'Simulated · settled':r.status.replace('_',' '))}<small>${escape(r.reason)}</small></td></tr>`;
    }).join('')||'<tr><td colspan="7" class="empty">No decisions in this view yet.</td></tr>';
    $('pageStatus').textContent=rows.length?`Page ${page+1} of ${pages} · ${rows.length} decisions`:'0 decisions';$('previous').disabled=page===0;$('next').disabled=page>=pages-1;
    chart();
  }
  function advance(){
    if(!state.index) state=E.create(data,readSettings());
    E.step(state,data);page=0;
    if(state.halt||state.index>=state.days.length){stop();message(state.halt||'Replay complete. These hypothetical results are not evidence of live profitability.');}
    else message(timer?'Replaying historical days. No real orders are sent.':'Paused. Your next replay day is '+state.days[state.index]+'.');
    save();render();
  }
  try{
    const response=await fetch('trading-replay.json',{cache:'no-cache'});if(!response.ok)throw new Error('Could not load the archive. Reload to try again.');
    data=E.validate(await response.json());state=E.create(data,E.DEFAULTS);
    let restored=false,restoreNotice='';
    try{const raw=localStorage.getItem(KEY);if(raw){state=E.restore(data,JSON.parse(raw));restored=true;}}catch(_){restoreNotice='Saved session could not be restored. A fresh sandbox is ready.';}
    for(const key of Object.keys(E.DEFAULTS))$(key).value=state.config[key];
    const c=data.coverage;$('coverage').textContent=`${c.games} historical games · ${c.admitted_quotes} admitted quotes · ${c.teams.length} teams · ${c.first_date} through ${c.last_date}. Model: ${data.model}. The full recorded population, including exclusions, is retained.`;
    $('source').textContent='Source report SHA-256: '+data.source_sha256;
    message(restoreNotice||(restored?'Saved session restored, paused.':`Ready to explore ${c.games} historical games with virtual money.`));save();render();
  }catch(error){message(error.message,true);return;}
  $('setup').addEventListener('submit',event=>{event.preventDefault();try{if(!state.index)state=E.create(data,readSettings());timer=setInterval(()=>{try{advance();}catch(error){stop();message(error.message,true);render();}},450);advance();}catch(error){stop();message(error.message,true);render();}});
  $('pause').addEventListener('click',()=>{stop();message('Paused. Your session is saved on this browser.');save();render();});
  $('step').addEventListener('click',()=>{if(!$('setup').reportValidity())return;try{advance();}catch(error){message(error.message,true);}});
  $('reset').addEventListener('click',()=>{stop();state=E.create(data,state.config);page=0;message('New session ready. You can change the settings before starting.');save();render();});
  $('ledgerFilter').addEventListener('change',()=>{page=0;render();});$('previous').addEventListener('click',()=>{page--;render();});$('next').addEventListener('click',()=>{page++;render();});
  $('download').addEventListener('click',()=>{
    const result={...E.checkpoint(state),exported_at:new Date().toISOString(),mode:'historical_sandbox',live_trading_authorized:false,
      classification:'hypothetical_returns_not_verified_fills',accounting:'Daily batch; no intraday cash recycling; actual settlement timing unverified',
      summary:{balance:state.cash/100,net:state.net/100,spent:state.spent/100,fees:state.fees/100,drawdown:state.drawdown/100,simulated_contracts:state.filled,skipped:state.skipped,halt:state.halt},ledger:state.ledger,history:state.history,limitations:data.limitations};
    const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='nbai-historical-sandbox.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&timer){stop();message('Paused while this tab is hidden.');save();render();}});
})();
