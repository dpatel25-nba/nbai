/* Static market snapshots. No account access, forecasts or order submission. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const PAGE_SIZE = 12;
  let rows = [], visible = PAGE_SIZE, generatedAt = null;
  const searchText = s => s.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  function updateFreshness() {
    const time = Date.parse(generatedAt), age = Date.now() - time;
    const state = !Number.isFinite(time) || age < -60000 ? 'Unverified snapshot time' : age > 86400000 ? 'Stale snapshot' : 'Captured snapshot';
    $('snapshot').textContent = `${state} · ${Number.isFinite(time) ? new Date(time).toLocaleString() : 'Time unavailable'}. Refresh before acting on any price.`;
  }
  const cents = value => value == null ? 'Unavailable' : `${(value * 100).toFixed(1).replace(/\.0$/, '')}¢`;
  const node = (tag, text, className) => {const el = document.createElement(tag); if (text != null) el.textContent = text; if (className) el.className = className; return el;};
  async function loadShortlist() {
    try {
      const response=await fetch('season-shortlist.json',{cache:'no-store'});
      if(!response.ok)throw Error('Missing research');
      const data=await response.json();
      if(data.schemaVersion!==1 || !Array.isArray(data.items))throw Error('Invalid research');
      $('shortlistStatus').textContent=`${data.simulationSeasons} full-league scenario runs · ${data.teams} teams · No trade-ready recommendations`;
      $('shortlistCards').replaceChildren();
      for(const item of data.items){
        const current=rows.find(row=>row.ticker===item.ticker);
        const matched=current && current.receiptSha256===item.receiptSha256 && current.observedAt===item.observedAt && current.yesAsk===item.reviewedAsk;
        const aged=Date.now()-Date.parse(item.observedAt)>86400000;
        const card=node('article',null,'card');
        card.append(node('p',item.status,'eyebrow'),node('h3',item.title),node('p',`${item.side} ${cents(item.reviewedAsk)} · ${matched&&!aged?'observed research price':'historical price; needs review'}`,'review-price'),node('p',`Captured ${new Date(item.observedAt).toLocaleString()} · Before fees`,'note'));
        card.append(node('h4','The case'),node('p',item.reason),node('h4','What could break it'),node('p',item.risk));
        const details=node('details');details.append(node('summary','Evidence & next check'),node('p',item.nextCheck));
        if(item.metrics){const m=item.metrics;details.append(node('p',`Baseline scenario hit rate: ${(100*m.scenarioHitRate).toFixed(1)}%. Five-win adverse stress: ${(100*m.adverseFiveWinHitRate).toFixed(1)}%. These are outputs of an unvalidated scenario engine, not real-world odds.`),node('p',`Simulation sampling interval: ${(100*m.monteCarloInterval[0]).toFixed(1)}–${(100*m.monteCarloInterval[1]).toFixed(1)}%. This excludes model error. Mean wins: ${m.meanWins.toFixed(2)}.`));}
        else details.append(node('p','Own MVP probability: unavailable. Historical WAR is not an award probability.'));
        if(item.contextUrl){const source=node('a','NBA source');source.href=item.contextUrl;source.target='_blank';source.rel='noopener noreferrer';details.append(node('p',item.context),source);}
        const link=node('a','View the exact contract ↗','market-link');link.href=item.url;link.target='_blank';link.rel='noopener noreferrer';
        card.append(details,link);$('shortlistCards').append(card);
      }
    }catch(_){$('shortlistStatus').textContent='Shortlist evidence unavailable. Market browsing remains available.';}
  }
  function render() {
    const query = searchText($('search').value.trim()), category = $('category').value;
    const selected = rows.filter(row => (!category || row.category === category) && searchText(`${row.title} ${row.outcome} ${row.ticker}`).includes(query));
    selected.sort($('sort').value === 'probability' ? (a,b) => (b.marketProbability ?? -1) - (a.marketProbability ?? -1) || a.title.localeCompare(b.title) : (a,b) => a.title.localeCompare(b.title));
    $('markets').replaceChildren();
    for (const row of selected.slice(0, visible)) {
      const card = node('article', null, 'card');
      const title = row.category === 'Team wins' ? row.title.replace(/^Will the /,'').replace(' Pro Basketball team win at least ', ' · ').replace(/ games in the \d{4}-\d{2} regular season\?$/, '+ wins') : row.title;
      card.append(node('p', row.category, 'eyebrow'), node('h3', title), node('p', row.outcome, 'outcome'));
      const p = node('div', null, 'probability');
      p.append(node('strong', row.marketProbability == null ? '—' : `${Math.round(row.marketProbability * 100)}%`), node('span', row.marketProbability == null ? 'No two-sided reference' : 'Market reference'));
      const meter = node('div', null, 'meter'), fill = node('div'); fill.style.width = `${(row.marketProbability ?? 0) * 100}%`; meter.append(fill);
      const quotes = node('div', null, 'quotes'); quotes.append(node('span', `YES ${cents(row.yesAsk)}`), node('span', `NO ${cents(row.noAsk)}`));
      const details = node('details'); details.append(node('summary', 'Outcome, source & timestamp'), node('p', row.rules), node('p', `Observed ${new Date(row.observedAt).toLocaleString()}. These are buy quotes before fees; depth and fill not verified.`), node('p', row.sourceUpdatedAt ? `Exchange metadata updated ${new Date(row.sourceUpdatedAt).toLocaleString()}; this is not a verified price-change timestamp.` : 'Exchange update time unavailable.'), node('p', row.ticker, 'receipt'), node('p', `Receipt SHA-256 ${row.receiptSha256}`, 'receipt'));
      if (row.supplementaryRules) details.append(node('p', row.supplementaryRules));
      if (row.earlyCloseCondition) details.append(node('p', `Early close: ${row.earlyCloseCondition}`));
      details.append(node('p', `Observed bids: YES ${cents(row.yesBid)} · NO ${cents(row.noBid)}. Bid prices are distinct from the buy quotes above.`));
      const link = node('a', 'View market on Kalshi ↗', 'market-link'); link.href = row.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
      card.append(p, meter, quotes, details, link); $('markets').append(card);
    }
    $('count').textContent = `${selected.length} markets · Showing ${Math.min(visible,selected.length)}`;
    if (!selected.length) $('markets').append(node('p','No markets match this search.'));
    $('more').hidden = selected.length <= visible;
  }
  for (const id of ['category','search','sort']) $(id).addEventListener('input', () => {visible=PAGE_SIZE;render();});
  $('more').addEventListener('click', () => {visible+=PAGE_SIZE;render();});
  fetch('season-betting.json', {cache:'no-store'}).then(r => {if (!r.ok) throw Error('Snapshot unavailable');return r.json();}).then(data => {
    if (data.schemaVersion !== 1 || !Array.isArray(data.markets)) throw Error('Unsupported snapshot');
    rows = data.markets;
    generatedAt = data.generatedAt;
    updateFreshness();
    setInterval(updateFreshness,60000);
    render();
    loadShortlist();
  }).catch(() => {$('snapshot').textContent='Market snapshot unavailable. No prices are being estimated.';});
})();
