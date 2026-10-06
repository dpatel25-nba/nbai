/* Historical paper sandbox only. No network, credentials or order submission. */
(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NBAITrading = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  'use strict';
  const DEFAULTS = Object.freeze({bankroll:100, dailySpend:10, lossStop:20, slippage:'0.01'});
  const cents = value => Math.round(Number(value) * 100);
  function settings(input) {
    const value = {...DEFAULTS, ...input};
    for (const key of ['bankroll','dailySpend','lossStop']) {
      if (typeof value[key] !== 'number' || !Number.isFinite(value[key]) || value[key] < 1 || value[key] > 10000 || cents(value[key]) / 100 !== value[key])
        throw new Error('Use amounts from $1 to $10,000, with at most two decimal places.');
    }
    if (value.dailySpend > value.bankroll || value.lossStop > value.bankroll)
      throw new Error('Daily spend and loss pause must be no greater than your starting balance.');
    if (!['0','0.01','0.02','0.05'].includes(value.slippage)) throw new Error('Choose a supported price assumption.');
    return {bankroll:value.bankroll, dailySpend:value.dailySpend, lossStop:value.lossStop, slippage:value.slippage};
  }
  function validate(data) {
    if (data?.schema_version !== 1 || data.mode !== 'historical_sandbox' || data.live_trading_authorized !== false || !Array.isArray(data.rows) || !data.rows.length)
      throw new Error('The historical replay data is unavailable or unsupported.');
    const seen = new Set();
    for (const row of data.rows) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(row.date) || !row.game_id || seen.has(row.game_id) || !['candidate','pass','excluded'].includes(row.status))
        throw new Error('Replay identities or dates could not be verified.');
      seen.add(row.game_id);
    }
    return data;
  }
  function create(data, input) {
    validate(data);
    const config = settings(input);
    return {config, source:data.source_sha256, days:[...new Set(data.rows.map(r=>r.date))].sort(), index:0,
      cash:cents(config.bankroll), net:0, spent:0, fees:0, filled:0, skipped:0, peak:cents(config.bankroll), drawdown:0,
      halt:null, ledger:[], history:[]};
  }
  function step(state, data) {
    if (state.source !== data.source_sha256) throw new Error('Replay source changed. Start a new session.');
    if (state.halt || state.index >= state.days.length) return false;
    const date = state.days[state.index];
    let spend = 0, payout = 0, fees = 0;
    // Commit the entire day's decisions before crediting any settlements.
    // Daily accounting is an explicit sandbox assumption, not exchange settlement timing.
    for (const row of data.rows.filter(r=>r.date===date)) {
      let status='pass', reason='Below the frozen 5¢ model-surplus threshold', cost=0, paid=0, fee=0;
      const scenario = row.cost_scenarios?.['quadratic_order_cent|slippage='+state.config.slippage];
      if (row.status==='excluded') {status='excluded';reason=row.reason || 'Missing eligible observation';}
      else if (row.status==='candidate') {
        if (!scenario || scenario.status!=='hypothetical_fill') {status='no_fill';reason='No price under this scenario';}
        else if (![scenario.cost,scenario.payout,scenario.fee,scenario.net].every(v=>typeof v==='number' && Number.isFinite(v)) || scenario.cost<=0 || scenario.cost>1.10 || scenario.payout<0 || scenario.payout>1 || scenario.fee<0 || scenario.fee>scenario.cost || Math.abs(scenario.net-(scenario.payout-scenario.cost))>1e-8) {
          status='excluded';reason='Invalid cost or settlement record';
        } else {
          const requested = cents(scenario.cost);
          if (spend+requested > cents(state.config.dailySpend)) {status='limit';reason='Daily spending limit';}
          else if (spend+requested > state.cash) {status='limit';reason='Insufficient virtual balance';}
          else {
            status='simulated';reason='Frozen model rule · one hypothetical contract';
            cost=requested;paid=Number(scenario.payout)*100;fee=cents(scenario.fee);
            spend+=cost;payout+=paid;fees+=fee;
          }
        }
      }
      state.ledger.push({date,game_id:row.game_id,ticker:row.ticker,matchup:row.away+' @ '+row.home,
        side:row.choice?.side || null,model_yes:row.p_yes ?? null,market_reference:row.market_midpoint ?? null,
        quoted_ask:row.choice?.ask || null,status,reason,cost:cost/100,fee:fee/100,payout:paid/100,
        net:(paid-cost)/100,quote:row.quote,source:row.source});
      if(status==='simulated') state.filled++; else state.skipped++;
    }
    state.cash += payout-spend;state.net += payout-spend;state.spent+=spend;state.fees+=fees;
    state.peak=Math.max(state.peak,state.cash);state.drawdown=Math.max(state.drawdown,state.peak-state.cash);
    state.history.push({date,balance:state.cash/100,net:state.net/100,spent:spend/100});state.index++;
    if (state.net <= -cents(state.config.lossStop)) state.halt='Loss pause reached at the end of this replay day.';
    else if(state.cash<1) state.halt='Virtual balance exhausted.';
    return true;
  }
  function restore(data, saved) {
    if (saved.version!==1 || saved.source!==data.source_sha256 || !Number.isInteger(saved.index) || saved.index<0 || saved.index>data.rows.length)
      throw new Error('Saved session does not match this archive. Start a new session.');
    const state=create(data,saved.config);
    if(saved.index>state.days.length) throw new Error('Invalid saved replay position.');
    for(let i=0;i<saved.index;i++) if(!step(state,data)) throw new Error('Saved replay position is beyond its loss pause.');
    return state;
  }
  const checkpoint = state => ({version:1,source:state.source,config:state.config,index:state.index});
  return {DEFAULTS,settings,validate,create,step,restore,checkpoint};
});
