/* nba-research-board/v1: presentation only, no bet selection or ledger writes. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NBAI_RESEARCH = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const VERSION = 'nba-research-board/v1';
  // A UI warning policy, NOT a strategy decision-time or market-liquidity guarantee.
  const MAX_AGE_MS = 15 * 60 * 1000;
  const fields = ['id', 'game', 'startsAt', 'quoteId', 'sourceRef', 'bookmaker',
    'market', 'side', 'line', 'price', 'quoteAt', 'forecastAt', 'modelVersion',
    'probability', 'uncertainty', 'rationale', 'exclusions'];
  const text = x => typeof x === 'string' && x.trim().length > 0 && x.length <= 2000;
  const number = x => typeof x === 'number' && Number.isFinite(x);
  const probability = x => number(x) && x >= 0 && x <= 1;
  const nullable = (x, check) => x === null || check(x);
  const record = x => x !== null && typeof x === 'object' && !Array.isArray(x);
  const exact = (x, keys) => record(x) && Object.keys(x).length === keys.length &&
    keys.every(k => Object.prototype.hasOwnProperty.call(x, k));
  const list = x => Array.isArray(x) && x.length <= 1000;
  function timestamp(x) {
    if (typeof x !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(x)) return false;
    const ms = Date.parse(x);
    return Number.isFinite(ms) && new Date(ms).toISOString() === x.replace('Z', '.000Z');
  }
  function price(x) {
    return exact(x, ['format', 'value']) && number(x.value) &&
      ((x.format === 'american' && Number.isInteger(x.value) && Math.abs(x.value) >= 100) ||
       (x.format === 'decimal' && x.value > 1));
  }
  function uncertainty(x) {
    return exact(x, ['lower', 'upper', 'method']) && probability(x.lower) &&
      probability(x.upper) && x.lower <= x.upper && text(x.method);
  }
  function validateBoard(input, now = Date.now()) {
    const errors = [];
    const fail = message => errors.push(message);
    if (!number(now) || !Number.isFinite(new Date(now).getTime())) fail('Invalid display clock');
    if (!exact(input, ['schemaVersion', 'generatedAt', 'historicalEvidence', 'researchSignals', 'forwardRecords']))
      return {ok:false, errors:errors.concat('Expected the complete v1 envelope')};
    if (input.schemaVersion !== VERSION) fail('Unsupported schema version');
    if (!nullable(input.generatedAt, timestamp)) fail('Invalid generatedAt');
    for (const key of ['historicalEvidence', 'researchSignals', 'forwardRecords'])
      if (!list(input[key])) fail('Invalid ' + key + ' list');
    if (errors.length) return {ok:false, errors};
    if (input.forwardRecords.length) fail('v1 does not accept forward records; no verified ledger connected');
    const generated = input.generatedAt === null ? null : Date.parse(input.generatedAt);
    if (generated !== null && generated > now) fail('Snapshot is in the future');
    if (generated === null && (input.researchSignals.length || input.historicalEvidence.length))
      fail('Populated snapshots require generatedAt');
    const historicalIds = new Set();
    input.historicalEvidence.forEach((h, i) => {
      if (!exact(h, ['id', 'title', 'summary', 'sourceRef']) ||
          !['id', 'title', 'summary', 'sourceRef'].every(k => text(h[k]))) {
        fail('Invalid historical evidence ' + i); return;
      }
      if (historicalIds.has(h.id)) fail('Duplicate historical ID');
      historicalIds.add(h.id);
    });
    const ids = new Set();
    input.researchSignals.forEach((s, i) => {
      const prefix = 'Signal ' + i + ': ';
      if (!exact(s, fields)) { fail(prefix + 'expected complete signal fields'); return; }
      if (!text(s.id) || !text(s.game)) fail(prefix + 'invalid identity');
      if (ids.has(s.id)) fail(prefix + 'duplicate signal ID');
      ids.add(s.id);
      for (const key of ['quoteId', 'sourceRef', 'bookmaker', 'modelVersion', 'rationale'])
        if (!nullable(s[key], text)) fail(prefix + 'invalid ' + key);
      for (const key of ['startsAt', 'quoteAt', 'forecastAt'])
        if (!nullable(s[key], timestamp)) fail(prefix + 'invalid ' + key);
      if (!nullable(s.market, x => x === 'game_total')) fail(prefix + 'unsupported market');
      if (!nullable(s.side, x => ['over', 'under'].includes(x))) fail(prefix + 'invalid side');
      if (!nullable(s.line, x => number(x) && x > 0)) fail(prefix + 'invalid line');
      if (!nullable(s.price, price)) fail(prefix + 'invalid actual price');
      if (!nullable(s.probability, probability)) fail(prefix + 'invalid probability');
      if (!nullable(s.uncertainty, uncertainty)) fail(prefix + 'invalid uncertainty');
      if (s.uncertainty !== null && uncertainty(s.uncertainty) &&
          (!probability(s.probability) || s.probability < s.uncertainty.lower || s.probability > s.uncertainty.upper))
        fail(prefix + 'interval must contain supplied probability');
      if (!list(s.exclusions) || !s.exclusions.every(text)) fail(prefix + 'invalid exclusions');
      const q = timestamp(s.quoteAt) ? Date.parse(s.quoteAt) : null;
      const f = timestamp(s.forecastAt) ? Date.parse(s.forecastAt) : null;
      const start = timestamp(s.startsAt) ? Date.parse(s.startsAt) : null;
      if (q !== null && f !== null && q > f) fail(prefix + 'quote after forecast');
      for (const t of [q, f]) {
        if (t !== null && (t > now || (generated !== null && t > generated)))
          fail(prefix + 'quote/forecast after snapshot or display time');
        if (t !== null && start !== null && t >= start) fail(prefix + 'not a pregame snapshot');
      }
    });
    return {ok:errors.length === 0, errors};
  }
  const escape = x => String(x).replace(/[&<>"']/g, c =>
    ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const pct = x => (x * 100).toFixed(1) + '%';
  function priceLabel(p) {
    if (p === null) return null;
    return (p.format === 'american' && p.value > 0 ? '+' : '') + p.value +
      (p.format === 'american' ? ' (American)' : ' (Decimal)');
  }
  function field(label, value) {
    return '<div><dt>' + escape(label) + '</dt><dd>' +
      (value === null ? 'Unavailable' : escape(value)) + '</dd></div>';
  }
  function signalState(s, generatedAt, now) {
    const missing = fields.filter(k => s[k] === null);
    const reasons = [];
    for (const [label, time] of [['Snapshot', generatedAt], ['Quote', s.quoteAt], ['Forecast', s.forecastAt]])
      if (time !== null && now - Date.parse(time) > MAX_AGE_MS) reasons.push(label + ' older than 15 minutes');
    if (s.startsAt !== null && Date.parse(s.startsAt) <= now) reasons.push('Game already started');
    return {
      status:reasons.length ? 'Stale — not actionable' :
        s.exclusions.length ? 'Excluded — not actionable' :
        missing.length ? 'Missing inputs — not actionable' : 'Research only — not a paper pick',
      reasons:reasons.concat(missing.length ? ['Missing inputs: ' + missing.join(', ')] : [], s.exclusions)
    };
  }
  function renderSignal(s, generatedAt, now) {
    const state = signalState(s, generatedAt, now);
    const interval = s.uncertainty === null ? null :
      pct(s.uncertainty.lower) + '–' + pct(s.uncertainty.upper) + ' · ' + s.uncertainty.method;
    return '<article class="signal"><div class="signal-head"><h3>' + escape(s.game) +
      '</h3><span class="badge">' + escape(state.status) + '</span></div><dl class="quote-grid">' +
      field('Bookmaker', s.bookmaker) + field('Market', s.market) + field('Side', s.side) +
      field('Line', s.line) + field('Actual price', priceLabel(s.price)) +
      field('Quote time (UTC)', s.quoteAt) + field('Forecast time (UTC)', s.forecastAt) +
      field('Game time (UTC)', s.startsAt) + field('Model version', s.modelVersion) +
      field('Probability', s.probability === null ? null : pct(s.probability)) +
      field('Uncertainty', interval) + field('Quote ID', s.quoteId) +
      field('Source reference', s.sourceRef) + field('Signal ID', s.id) +
      '</dl><dl>' + field('Rationale / availability assumptions', s.rationale) +
      field('Supplied exclusions', s.exclusions.length ? s.exclusions.join('; ') :
        'None supplied; not independently verified') + '</dl>' +
      (state.reasons.length ? '<ul class="warnings">' + state.reasons.map(r => '<li>' + escape(r) + '</li>').join('') + '</ul>' : '') +
      '</article>';
  }
  function forwardSection() {
    return '<section id="forward" aria-labelledby="forward-title"><p class="eyebrow">03 / Forward evidence</p>' +
      '<h2 id="forward-title">Forward paper record</h2><p class="metric">0 verified forward observations</p>' +
      '<p>No persisted ledger is connected. No verified decisions or settlements are loaded; performance is unvalidated.</p>' +
      '<p>Losses, passes, pushes, voids and pending decisions must be retained alongside wins. ' +
      'Corrections must preserve original history. These are requirements, not implemented persistence.</p>' +
      '<dl class="quote-grid">' + field('Net return', null) + field('Calibration', null) +
      field('Drawdown', null) + field('Closing-line comparison', null) + '</dl></section>';
  }
  function renderBoard(input, now = Date.now()) {
    const absent = input === null || input === undefined;
    const result = absent ? {ok:false, errors:[]} : validateBoard(input, now);
    const valid = result.ok;
    let banner = absent ? 'Data unavailable — no snapshot loaded.' :
      !valid ? 'Malformed data — snapshot rejected; no signals displayed.' :
      input.generatedAt === null ? 'Snapshot time unavailable — no populated data.' :
      input.researchSignals.length === 0 && input.historicalEvidence.length > 0 ? 'Historical evidence only — no live signals.' :
      now - Date.parse(input.generatedAt) > MAX_AGE_MS ? 'Stale — snapshot older than 15 minutes.' :
      'Snapshot within display window — research only.';
    const history = valid ? input.historicalEvidence : [];
    const signals = valid ? input.researchSignals : [];
    return '<div class="data-status" role="status">' + escape(banner) +
      (valid && input.generatedAt !== null ? '<br>Snapshot generated: ' + escape(input.generatedAt) + ' (UTC)' : '') +
      '</div>' + (!valid && !absent ? '<details><summary>Contract errors</summary><ul>' +
        result.errors.map(e => '<li>' + escape(e) + '</li>').join('') + '</ul></details>' : '') +
      '<section id="historical" aria-labelledby="historical-title"><p class="eyebrow">01 / Historical evidence</p>' +
      '<h2 id="historical-title">Retrospective research</h2><p>Retrospective, not an untouched holdout. ' +
      'Minutes-model evidence does not establish a betting edge. Simulated games are not paper picks.</p>' +
      (history.length ? history.map(h => '<article class="history"><h3>' + escape(h.title) + '</h3><p>' +
        escape(h.summary) + '</p><details><summary>Evidence references</summary><p class="source">Source: ' + escape(h.sourceRef) + ' · Evidence ID: ' +
        escape(h.id) + '</p></details></article>').join('') : '<p class="empty">No historical summaries loaded. Coverage, lineage and market evaluation remain separate gates.</p>') +
      '</section><section id="signals" aria-labelledby="signals-title"><p class="eyebrow">02 / Research signals</p>' +
      '<h2 id="signals-title">Quote &amp; forecast board</h2>' +
      '<p>Actual same-book quotes only; never consensus-derived prices. Probabilities and intervals may be unavailable. ' +
      'Completeness is not independent verification or a recommendation.</p>' +
      '<p class="source">Freshness warning: quote, forecast or snapshot older than 15 minutes, or game already started. ' +
      'This display policy is not a frozen betting strategy. All times are UTC.</p>' +
      (signals.length ? signals.map(s => renderSignal(s, input.generatedAt, now)).join('') :
        '<p class="empty">No research signals. Audited real quotes and pregame forecasts are not connected; no odds or picks have been invented.</p>') +
      '</section>' + forwardSection();
  }
  async function loadBoard(root, {fetcher, now = Date.now, schedule = null, cancel = () => {}}) {
    root.innerHTML = renderBoard(null, now());
    let data;
    try {
      const response = await fetcher('research-data.json', {cache:'no-store'});
      if (!response.ok) throw new Error('Snapshot unavailable');
      data = await response.json();
    } catch (_) {
      root.innerHTML = renderBoard(null, now());
      return () => {};
    }
    const refresh = () => { root.innerHTML = renderBoard(data, now()); };
    refresh();
    // Re-age the SAME snapshot; never fabricate a refresh or change its timestamps.
    const timer = schedule ? schedule(refresh, 60000) : null;
    return () => { if (timer !== null) cancel(timer); };
  }
  return {VERSION, MAX_AGE_MS, validateBoard, renderBoard, loadBoard};
});
