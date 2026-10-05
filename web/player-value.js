(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const text = (tag, value, cls) => {const el=document.createElement(tag);el.textContent=value;if(cls) el.className=cls;return el;};
  const numeric = n => typeof n === 'number' && Number.isFinite(n);
  const display = n => numeric(n) ? n.toFixed(1) : '—';
  const labels = {war:'WAR v4',off:'Offensive contribution',def:'Defensive contribution'};
  const normalized = s => s.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  let players=[];
  function ranked(rows,key) {return [...rows].sort((a,b) => (numeric(b[key])?b[key]:-Infinity)-(numeric(a[key])?a[key]:-Infinity) || a.player.localeCompare(b.player));}
  function render() {
    const query=normalized($('search').value), key=$('metric').value;
    const filtered=ranked(players.filter(p => normalized(p.player).includes(query) && (!$('team').value || p.team === $('team').value)),key);
    $('leaders').replaceChildren();
    filtered.forEach((p,i) => {
      const tr=document.createElement('tr');
      [i+1,p.player,p.team,display(p.war),display(p.off),display(p.def),display(p.mpg)].forEach(v=>tr.append(text('td',v)));
      $('leaders').append(tr);
    });
    if (!filtered.length) {const tr=document.createElement('tr'),td=text('td','No players match these filters.','empty');td.colSpan=7;tr.append(td);$('leaders').append(tr);}
    $('count').textContent=`${filtered.length} of ${players.length} players · Ranked within current filters by ${labels[key]}`;
  }
  function compare() {
    const a=players.find(p=>String(p.id)===$('playerA').value),b=players.find(p=>String(p.id)===$('playerB').value);
    $('comparison').replaceChildren();if(!a || !b)return;
    for(const key of ['war','off','def']) {
      const card=document.createElement('article');card.append(text('h3',labels[key]),text('p',`${a.player}: ${display(a[key])}`),text('p',`${b.player}: ${display(b[key])}`));
      const diff=numeric(a[key]) && numeric(b[key])?a[key]-b[key]:null;
      card.append(text('p',diff===null?'Difference unavailable':`${a.player} minus ${b.player}: ${diff>0?'+':''}${display(diff)}${key==='war'?' wins':' points / 100'}`,'difference'));
      $('comparison').append(card);
    }
  }
  fetch('insights-data.json').then(r=>{if(!r.ok)throw Error('Unavailable');return r.json();}).then(data=>{
    if(data.schemaVersion!==1 || !Array.isArray(data.players) || !data.players.length)throw Error('Invalid snapshot');
    players=data.players;
    $('snapshot').textContent=`${data.season} season · Snapshot ${data.generatedAt} · Historical metrics`;
    $('population').textContent=data.population;
    $('provenance').textContent=`Source snapshot SHA-256: ${data.sourceSha256}. Formula source SHA-256: ${data.formulaSourceSha256}. These identify the saved inputs, not independent validation.`;
    for(const team of [...new Set(players.map(p=>p.team))].sort()) $('team').add(new Option(team,team));
    for(const id of ['playerA','playerB']) for(const p of [...players].sort((a,b)=>a.player.localeCompare(b.player))) $(id).add(new Option(p.player,String(p.id)));
    $('playerA').value=String(players[0].id);$('playerB').value=String(players[1]?.id ?? players[0].id);
    for(const [key,caption] of [['war','Most total value in this displayed population'],['off','Highest offensive component in this displayed population'],['def','Highest defensive component in this displayed population']]) {
      const p=ranked(players.filter(p=>numeric(p[key])),key)[0];if(!p)continue;
      const card=document.createElement('article');card.className='card';
      card.append(text('p',labels[key],'eyebrow'),text('h3',p.player),text('p',display(p[key]),'value'),text('p',key==='war'?'Wins above replacement':'Points / 100 above average','note'),text('p',caption));$('highlights').append(card);
    }
    for(const id of ['search','team','metric'])$(id).addEventListener('input',render);
    for(const id of ['playerA','playerB'])$(id).addEventListener('change',compare);
    render();compare();
  }).catch(()=>{$('snapshot').textContent='Research snapshot unavailable. No statistics are being estimated.';});
})();
