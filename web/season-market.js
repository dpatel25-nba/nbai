/* Strict preseason waiver claims. Financial evidence is required, never inferred from BPM. */
(function (global) {
  'use strict';
  const POLICY=Object.freeze({version:2,mode:'strict_waiver_claim',waiverHours:48,minMinutes:8});
  const finite=n=>typeof n==='number'&&Number.isFinite(n)&&n>=0;
  const clone=x=>JSON.parse(JSON.stringify(x));
  const roles=p=>[...new Set(String(p||'').toUpperCase().match(/[GFC]/g)||[])];
  const strength=(data,r)=>r.reduce((s,p)=>s+p.min*data.players[p.id].bpm/48,0);
  function readiness(data,membership,financials) {
    const missing=[];
    if(!financials)return {ready:false,missing:['Reconciled 2026–27 contracts, cap ledgers and waiver priority are not loaded.']};
    const f=financials,teams=Object.keys(data.teams).sort();
    if(f.season!==data.season||f.rosterHash!==data.sourceHashes['data/parquet/team_rosters.parquet'])missing.push('Financial snapshot does not match these season rosters.');
    const start=Date.parse(f.waiverStart||f.asOf),snapshot=Date.parse(f.asOf),claim=Date.parse(f.claimAt);
    if(f.reviewed!==true||!Array.isArray(f.sources)||!f.sources.length||![snapshot,start,claim].every(Number.isFinite)||start<snapshot||claim<start+48*3600000)missing.push('A reviewed snapshot and a full 48-hour claim window are required.');
    const opening=Math.min(...data.schedule.map(g=>Date.parse(g.tip)));
    if(f.phase!=='preseason'||!finite(f.salaryCap)||!Number.isFinite(opening)||claim>=opening)missing.push('Only reviewed preseason financial snapshots are supported.');
    if(!Array.isArray(f.waiverOrder)||f.waiverOrder.length!==teams.length||new Set(f.waiverOrder).size!==teams.length||f.waiverOrder.some(t=>!data.teams[t]))missing.push('Complete waiver priority, including tie resolution, is required.');
    for(const id of Object.keys(data.players))if((f.membership||{})[id]!==membership[id]){missing.push('Roster edits have changed the financial snapshot; reconcile contracts before auto-pickup.');break;}
    for(const team of teams){
      const t=f.teams?.[team];
      if(!t||![t.capSalary,t.apronSalary].every(finite)||!(t.hardCap===null||finite(t.hardCap))||!Array.isArray(t.exceptions)||t.exceptionsReviewed!==true){missing.push(`${team}: cap salary, apron salary, hard-cap status or exception evidence is incomplete.`);continue;}
      if(t.hardCap!==null&&t.apronSalary>t.hardCap)missing.push(`${team}: the financial snapshot already exceeds its hard cap.`);
      const ids=Object.keys(data.players).filter(id=>membership[id]===team);
      if(ids.some(id=>!['standard','two_way'].includes(f.contracts?.[id]?.kind)))missing.push(`${team}: standard/two-way contract classifications are incomplete.`);
      else if(ids.length>21||ids.filter(id=>f.contracts[id].kind==='two_way').length>3)missing.push(`${team}: the source roster exceeds preseason roster limits and must be reconciled.`);
    }
    return {ready:missing.length===0,missing};
  }
  function eligibility(data,f,team,playerId,from) {
    const c=f.contracts[playerId],t=f.teams[team],ids=Object.keys(f.membership).filter(id=>f.membership[id]===team);
    const deny=reason=>({team,status:'ineligible',reason}),unknown=reason=>({team,status:'needs_verification',reason});
    if(team===from||c.reacquisitionBlockedTeams.includes(team))return deny('Reacquisition restriction');
    if(ids.length>=21)return deny('No preseason roster slot');
    if(c.kind!=='standard')return unknown('Two-way waiver claims require a separate contract review');
    const apronAfter=t.apronSalary+c.claimApronCharge;
    if(t.hardCap!==null&&apronAfter>t.hardCap)return deny('Claim would exceed the team hard cap');
    if(t.capSalary+c.claimCapCharge<=f.salaryCap)return {team,status:'eligible',route:'cap_room'};
    if(c.minimumExceptionEligible===true&&Number.isInteger(c.originalTermYears)&&c.originalTermYears>=1&&c.originalTermYears<=2)return {team,status:'eligible',route:'minimum_exception'};
    // Unmodeled exception routes remain unknown, rather than falsely ruled illegal.
    if(t.exceptions.length)return unknown('Available exceptions need contract-specific waiver review');
    return deny('Insufficient cap room and no applicable waiver exception');
  }
  function release(data,membership,playerId,autoPickup=true,financials=null) {
    const player=data.players[playerId],from=membership[playerId],api=global.NBAI_SEASON;
    if(!player||!data.teams[from])throw new Error('Choose a rostered player to release.');
    if(Object.values(membership).filter(t=>t===from).length<=5)throw new Error(`${from} needs to keep at least five players.`);
    const next={...membership,[playerId]:null};
    if(!autoPickup)return {membership:next,financials:null,transaction:{playerId:player.id,from,to:null,autoPickup:false,policyVersion:2,reason:'auto_pickup_off',candidates:[]}};
    const check=readiness(data,membership,financials);
    if(!check.ready)throw new Error('Auto-pickup needs verification. '+check.missing[0]+' No roster change was made.');
    const f=clone(financials),c=f.contracts[playerId];
    if(!c||c.reviewed!==true||!c.source||![c.capCharge,c.apronCharge,c.claimCapCharge,c.claimApronCharge,c.waivedCapCharge,c.waivedApronCharge].every(finite)||!Array.isArray(c.reacquisitionBlockedTeams)||typeof c.minimumExceptionEligible!=='boolean')throw new Error('The player’s claim salary, guarantees and reacquisition restrictions need verification. No roster change was made.');
    const candidates=[],checks=[];
    for(const team of Object.keys(data.teams).sort()){
      if(team===from)continue;
      const legal=eligibility(data,f,team,playerId,from);checks.push(legal);
      if(legal.status==='ineligible')continue;
      let before,after;
      try{before=api.rotation(data,team,next);after=api.rotation(data,team,{...next,[playerId]:team});}catch(_){throw new Error('A team has an invalid rotation. Repair the roster before auto-pickup.');}
      const minutes=after.find(p=>p.id===player.id)?.min||0,gain=strength(data,after)-strength(data,before);
      if(minutes<POLICY.minMinutes||gain<=0)continue;
      if(legal.status==='needs_verification')throw new Error(`${team} may want this player, but ${legal.reason.toLowerCase()}. No claim can be awarded until competing claims are resolved.`);
      const totals={G:0,F:0,C:0};for(const p of before){const r=roles(data.players[p.id].pos);for(const pos of r)totals[pos]+=p.min/r.length;}
      const r=roles(player.pos),target={G:96,F:96,C:48};
      const need=r.length?r.reduce((s,pos)=>s+Math.max(0,1-totals[pos]/target[pos]),0)/r.length:0;
      candidates.push({...legal,minutes,impactGain:gain,positionNeed:need,score:10*gain+minutes/4+3*need,priority:f.waiverOrder.indexOf(team)});
    }
    // Interest is modeled; the winner follows waiver priority, not highest fit score.
    candidates.sort((a,b)=>a.priority-b.priority);
    const chosen=candidates[0]||null;
    if(chosen){next[playerId]=chosen.team;f.teams[chosen.team].capSalary+=c.claimCapCharge;f.teams[chosen.team].apronSalary+=c.claimApronCharge;}
    f.teams[from].capSalary+=chosen?-c.capCharge:c.waivedCapCharge-c.capCharge;
    f.teams[from].apronSalary+=chosen?-c.apronCharge:c.waivedApronCharge-c.apronCharge;
    if(!finite(f.teams[from].capSalary)||!finite(f.teams[from].apronSalary))throw new Error('Source team salary does not reconcile with the player contract.');
    f.membership={...next};
    // Every new release gets another complete simulated waiver window.
    const resolvedAt=f.claimAt;f.waiverStart=resolvedAt;f.claimAt=new Date(Date.parse(resolvedAt)+48*3600000).toISOString();
    return {membership:next,financials:f,transaction:{playerId:player.id,from,to:chosen?chosen.team:null,autoPickup:true,policyVersion:2,
      reason:chosen?'waiver_claim':'cleared_unclaimed',resolvedAt,candidates,checks}};
  }
  global.NBAI_MARKET={POLICY,readiness,eligibility,release};
})(typeof window!=='undefined'?window:globalThis);
