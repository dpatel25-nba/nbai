(function () {
  'use strict';
  const esc=x=>String(x??'Unavailable').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number=x=>typeof x==='number'&&Number.isFinite(x)?x.toFixed(1):'Unavailable';
  function status(data,now=Date.now()) {
    const age=now-Date.parse(data.forecast?.captured_at);
    return !Number.isFinite(age)||age<0||age>300000||data.collector_status!=='complete'
      ?'Archived context — not a current forecast.':'Recently captured research forecast — not a betting signal.';
  }
  function render(data,now=Date.now()) {
    const f=data?.schema==='nba-forecast-explanation/v1'?data.forecast:null;
    if(!f||!Array.isArray(f.players))return '<p>No saved forecast available.</p>';
    return `<p class="data-status">${status(data,now)}</p>`+
      `<h2>${esc(f.event.AWAY_TEAM)} at ${esc(f.event.HOME_TEAM)}</h2><p>Captured ${esc(f.captured_at)} · Scheduled ${esc(f.event.TIPOFF_UTC)}</p>`+
      `<p>${esc(f.meaning)}</p><p>Model ${esc(f.model_id)} · training through ${esc(f.training_last_date)}. Uncertainty interval: unavailable.</p>`+
      f.players.map(p=>`<article class="history"><h3>${esc(p.name)}</h3><p>Points: baseline ${number(p.points.baseline)} · candidate ${number(p.points.candidate)} · incumbent ${number(p.points.incumbent)}.</p>`+
        `<p>Candidate minutes: ${number(p.candidate_minutes)}. Recorded availability: ${esc(p.recorded_status)}.</p>`+
        `<details><summary>Recorded inputs and rules</summary><p>Prior minutes/game: ${number(p.recorded_inputs.prior_mpg)}; last game: ${number(p.recorded_inputs.last_min)}; last five: ${number(p.recorded_inputs.min5)}; rest days: ${number(p.recorded_inputs.days_rest)}.</p>`+
        `<p>Model participation probability: ${typeof p.recorded_inputs.p_play==='number'&&Number.isFinite(p.recorded_inputs.p_play)?(p.recorded_inputs.p_play*100).toFixed(1)+'%':'Unavailable'}; this is not a probability of a bet winning.</p>`+
        `<p>${p.known_rules.length?p.known_rules.map(esc).join(' '):'No explicit override recorded. These inputs are not measured feature contributions.'}</p></details></article>`).join('');
  }
  function paperLedger(p) {
    const heading='<h3>Persisted paper ledger</h3>';
    if(p?.available!==true)return heading+'<p>Paper ledger unavailable. '+esc(p?.reason??'No ledger snapshot supplied; no counts inferred.')+'</p>';
    const classes=[['forward_paper','Forward paper'],['retrospective','Retrospective'],['synthetic_fixture','Synthetic fixtures (not observations)']];
    if(!p.summaries||classes.some(([key])=>!p.summaries[key]))return heading+'<p>Paper ledger unavailable: incomplete snapshot.</p>';
    const exact=x=>typeof x==='string'&&/^-?\d+(?:\/[1-9]\d*)?$/.test(x)?esc(x):'Unavailable';
    return heading+`<p>Source: ${esc(p.source)} · As of ${esc(p.as_of)} · Through sequence ${esc(p.through_sequence)} · Grading: ${esc(p.grading)}.</p>`+
      `<p>Snapshot SHA-256: ${esc(p.snapshot_sha256)}. ${esc(p.hash_scope)}.</p>`+
      (p.empty?'<p>Empty ledger snapshot: no persisted events at this cutoff.</p>':'')+
      '<p>Paper records only, not betting recommendations. No profitability or independent strategy-validation claim.</p>'+
      classes.map(([key,label])=>{
        const s=p.summaries[key];
        return `<h4>${label}</h4><p>Opportunities: ${esc(s.opportunities)} · Decisions: ${esc(s.decisions)} · Picks: ${esc(s.picks)} · Passes: ${esc(s.passes)} · Pending: ${esc(s.pending)}.</p>`+
          `<p>Wins: ${esc(s.win)} · Losses: ${esc(s.loss)} · Pushes: ${esc(s.push)} · Voids: ${esc(s.void)}.</p>`+
          `<p>Settled net: ${exact(s.settled_net)} · Settled risk: ${exact(s.settled_risk)} · Pending risk: ${exact(s.pending_risk)} · Voided risk: ${exact(s.voided_risk)} (stake units, exact).</p>`+
          `<p>ROI (exact ratio): ${exact(s.roi)}${s.roi===null?' — no settled risk':''}.</p>`+
          `<p>Integrity warnings: ${esc(s.integrity_failures)} · Late audits: ${esc(s.late_audits)} · Processing gaps: ${Array.isArray(s.processing_gaps)?s.processing_gaps.length:'Unavailable'}.</p>`+
          (s.processing_gaps?.length?'<ul>'+s.processing_gaps.map(g=>'<li>'+esc(g.join(' / '))+'</li>').join('')+'</ul>':'');
      }).join('')+`<p>Recorded conflict events: ${esc(p.conflict_events)}.</p><p>${esc(p.completeness_note)}</p><p>${esc(p.financial_encoding)}</p>`;
  }
  function report(d){
    if(d?.schema!=='nba-daily-product/v1')return '<h2>Daily product report</h2><p>Report unavailable.</p>';
    const c=d.studio?.registered_product_counts;
    return `<h2>Daily product report · ${esc(d.day)}</h2><p>Updated ${esc(d.generated_at)}. Collector: ${esc(d.collector.status)}. New forecast files in its latest run: ${esc(d.forecasts_in_latest_run)}. Recorded errors: ${esc(d.collector_errors)}.</p>`+
      (c?`<p>Registry: ${esc(c.applied_code_changes)} applied changes; ${esc(c.integrated_product_results)} integrated results; ${esc(c.github_pushes)} GitHub publications. Live deployment verification: ${c.verified_live_deployments==null?'unavailable':esc(c.verified_live_deployments)}.</p>`:'<p>Studio status unavailable; no progress inferred.</p>')+
      (d.model_comparison?`<h3>Saved model comparison</h3><p>${esc(d.model_comparison.aggregate.eligible)} games on the same population. Retrospective accuracy, not betting performance. Lower Brier score and log loss are better.</p>`+
        Object.entries(d.model_comparison.aggregate.scores).map(([name,s])=>`<p><strong>${esc(name.replaceAll('_',' '))}</strong>: Brier ${typeof s.brier==='number'?s.brier.toFixed(4):'unavailable'} · log loss ${typeof s.log_loss==='number'?s.log_loss.toFixed(4):'unavailable'}.</p>`).join('')+
        `<details><summary>Comparison limitations</summary><ul>${d.model_comparison.limitations.map(x=>'<li>'+esc(x)+'</li>').join('')}</ul></details>`:'')+
      paperLedger(d.paper_ledger)+
      '<ul>'+d.remaining_gates.map(g=>`<li>${esc(g)}</li>`).join('')+'</ul>';
  }
  if(typeof module==='object')module.exports={render,report};
  if(typeof document!=='undefined')for(const [id,file,fn] of [['forecastContext','forecast-research.json',render],['dailyReport','product-report.json',report]])
    fetch(file,{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('Unavailable');return r.json();}).then(d=>{document.getElementById(id).innerHTML=fn(d);if(id==='forecastContext')setInterval(()=>{const banner=document.querySelector('#forecastContext .data-status');if(banner)banner.textContent=status(d);},30000);}).catch(()=>{document.getElementById(id).textContent='Data unavailable; no current forecast or progress claim can be shown.';});
})();
