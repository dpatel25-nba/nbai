const test=require('node:test'),assert=require('node:assert/strict');
const E=require('../web/trading-engine.js'),archive=require('../web/trading-replay.json');
const broad={bankroll:10000,dailySpend:10000,lossStop:10000};
function fixture(rows){return {schema_version:1,mode:'historical_sandbox',live_trading_authorized:false,source_sha256:'fixture',rows};}
function row(id,date='2026-01-01',cost=.6,payout=0){return {game_id:id,ticker:'TEST-'+id,date,home:'B',away:'A',status:'candidate',p_yes:.9,choice:{side:'yes',ask:'0.58'},cost_scenarios:{'quadratic_order_cent|slippage=0.01':{status:'hypothetical_fill',cost,payout,net:payout-cost,fee:.02}}};}
test('full archive reproduces recorded 405-contract cost and net, including every exclusion',()=>{
 const s=E.create(archive,broad);while(E.step(s,archive)){}
 assert.equal(s.ledger.length,813);assert.equal(new Set(s.ledger.map(r=>r.game_id)).size,813);
 assert.equal(s.filled,405);assert.equal(s.net/100,archive.baseline.hypothetical_net_dollars);
 assert.equal(s.spent/100,archive.baseline.hypothetical_cost_dollars);assert.equal(s.skipped,408);
 const end=JSON.stringify(s);assert.equal(E.step(s,archive),false);assert.equal(JSON.stringify(s),end);
});
test('daily spend includes fees and does not recycle settlements within the day',()=>{
 const d=fixture([row('1','2026-01-01',.6,1),row('2','2026-01-01',.6,1)]);
 const s=E.create(d,{bankroll:1,dailySpend:1,lossStop:1});E.step(s,d);
 assert.equal(s.filled,1);assert.equal(s.ledger[1].status,'limit');assert.equal(s.cash,140);
});
test('daily spend resets next day; insufficient balance still blocks spending',()=>{
 const d=fixture([row('1'),row('2','2026-01-02')]);
 const s=E.create(d,{bankroll:1,dailySpend:1,lossStop:1});E.step(s,d);E.step(s,d);
 assert.equal(s.filled,1);assert.equal(s.ledger[1].reason,'Insufficient virtual balance');assert.equal(s.cash,40);
});
test('loss pause applies after the day and prevents later days',()=>{
 const d=fixture([row('1'),row('2'),row('3','2026-01-02')]);
 const s=E.create(d,{bankroll:5,dailySpend:5,lossStop:1});E.step(s,d);
 assert.match(s.halt,/Loss pause/);assert.equal(s.net,-120);assert.equal(E.step(s,d),false);assert.equal(s.index,1);
});
test('missing or corrupt prices are excluded rather than fabricated',()=>{
 const a=row('1');delete a.cost_scenarios;const b=row('2');b.cost_scenarios['quadratic_order_cent|slippage=0.01'].cost=null;
 const c=row('3');c.cost_scenarios['quadratic_order_cent|slippage=0.01'].payout=5;
 const d=fixture([a,b,c]),s=E.create(d,broad);E.step(s,d);
 assert.deepEqual(s.ledger.map(r=>r.status),['no_fill','excluded','excluded']);assert.equal(s.net,0);assert.equal(s.filled,0);
});
test('NO fair-value settlement uses archived scenario payout without a binary conversion',()=>{
 const r=row('1','2026-01-01',.4,.375);r.choice.side='no';const d=fixture([r]),s=E.create(d,broad);E.step(s,d);
 assert.equal(s.ledger[0].payout,.375);assert.equal(s.net,-2.5);
});
test('saved position restores deterministically without trusting saved balances or ledger',()=>{
 const s=E.create(archive,broad);for(let i=0;i<8;i++)E.step(s,archive);
 const restored=E.restore(archive,{...E.checkpoint(s),cash:999999,ledger:[{net:999}]});assert.deepEqual(restored,s);
 assert.throws(()=>E.restore(archive,{...E.checkpoint(s),source:'different'}));
 assert.throws(()=>E.restore(archive,{...E.checkpoint(s),index:9999}));
});
test('invalid settings, duplicate identities and non-paper datasets fail closed',()=>{
 for(const input of [{bankroll:NaN},{dailySpend:101},{lossStop:-1},{slippage:'-1'},{bankroll:1.001}])assert.throws(()=>E.settings(input));
 assert.throws(()=>E.create(fixture([row('same'),row('same')]),broad));
 assert.throws(()=>E.create({...archive,live_trading_authorized:true},broad));
});
