/* Season orchestration. No DOM, network calls or unseeded randomness. */
(function (global) {
  "use strict";
  const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
  function hash(text) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
    return h >>> 0;
  }
  function assignments(data) {
    return Object.fromEntries(Object.values(data.players).map(p => [p.id, p.team]));
  }
  function makeSchedule(data, complete) {
    const games = data.schedule.map(g => ({...g}));
    if (complete) {
      const random = global.NBAI_SIM.rng(202627);
      const occupied = new Set(games.flatMap(g => [g.date+g.home, g.date+g.away]));
      for (const conference of ["East", "West"]) {
        const teams = Object.keys(data.teams).filter(t => data.teams[t].conference === conference).sort();
        for (let i = teams.length-1; i > 0; i--) {
          const j = Math.floor(random()*(i+1)); [teams[i],teams[j]] = [teams[j],teams[i]];
        }
        // A cycle gives every team exactly one extra home and one extra away
        // game. These are scenario placeholders, not a simulated Cup bracket.
        for (let i = 0; i < teams.length; i++) {
          const home = teams[i], away = teams[(i+1)%teams.length];
          const dates = [4,7,10,5,8,6,9].map(n => `2026-12-${String(n).padStart(2,"0")}`);
          const date = dates.find(d => !occupied.has(d+home) && !occupied.has(d+away));
          if (!date) throw new Error("No free date for provisional game");
          games.push({id:`provisional-${conference}-${i}`,date,home,away,tip:date+"T19:00:00-05:00",
            timeET:"7:00 PM",provisional:true,cup:false,neutral:false});
          occupied.add(date+home); occupied.add(date+away);
        }
      }
    }
    return games.sort((a,b) => a.tip.localeCompare(b.tip) || a.id.localeCompare(b.id));
  }
  function rotation(data, team, membership) {
    const available = Object.values(data.players).filter(p => membership[p.id] === team)
      .sort((a,b) => b.MPG-a.MPG || a.id-b.id).slice(0,12);
    if (available.length < 5) throw new Error(`${team} needs at least five players. Add players before simulating.`);
    // Preserve projected starter roles; the marginal bench player absorbs an
    // overflowing rotation, matching the existing Python roster builder.
    const candidates = [], minutes = [];let assigned = 0;
    for (const p of available) {
      const min = Math.min(clamp(p.MPG,1,48),240-assigned);
      if (min<=1e-9) break;
      candidates.push(p);minutes.push(min);assigned+=min;
    }
    let remaining = 240, active = minutes.map((_,i) => i), final = minutes.map(() => 0);
    while (active.length) {
      const total = active.reduce((s,i) => s+minutes[i],0);
      const capped = active.filter(i => remaining*minutes[i]/total > 48);
      if (!capped.length) { active.forEach(i => {final[i] = remaining*minutes[i]/total;}); break; }
      capped.forEach(i => {final[i] = 48; remaining -= 48;});
      active = active.filter(i => !capped.includes(i));
    }
    return candidates.map((p,i) => ({id:p.id,n:p.n,min:final[i]}));
  }
  class Season {
    constructor(data, options = {}) {
      this.data = data;
      this.seed = String(options.seed == null ? "202627" : options.seed);
      this.membership = {...(options.membership || assignments(data))};
      for (const [id,team] of Object.entries(this.membership)) {
        if (!data.players[id] || (team !== null && !data.teams[team])) throw new Error("Invalid roster assignment");
      }
      this.schedule = makeSchedule(data, options.complete !== false);
      this.rosters = {}; this.delta = {}; this.calibrated = new Map();
      for (const team of Object.keys(data.teams)) {
        this.rosters[team] = rotation(data,team,this.membership);
        this.delta[team] = this.rosters[team].reduce((n,p) => n+p.min*data.players[p.id].bpm/48,0)-data.teams[team].baseBpm;
      }
      this.index = 0; this.results = [];
      this.standings = Object.fromEntries(Object.keys(data.teams).map(team => [team,
        {team,w:0,l:0,pf:0,pa:0,homeW:0,homeL:0,awayW:0,awayL:0}]));
      this.players = Object.fromEntries(Object.values(data.players).map(p => [p.id,
        {id:p.id,team:this.membership[p.id],gp:0,...Object.fromEntries(global.NBAI_SIM.BOX.map(k => [k,0]))}]));
    }
    matchup(game) {
      const key = `${game.home}:${game.away}:${game.neutral}`;
      if (this.calibrated.has(key)) return this.calibrated.get(key);
      const d = this.data, h = d.teams[game.home], a = d.teams[game.away];
      const H = this.rosters[game.home], A = this.rosters[game.away];
      const pace = (h.pace+a.pace)/2;
      const adjustment = (this.delta[game.home]-this.delta[game.away])*pace/200;
      const mu = {H:clamp(d.mu0+h.off-a.defense+(game.neutral?0:d.hca)+adjustment,75,160),
                  A:clamp(d.mu0+a.off-h.defense-adjustment,75,160)};
      const scale = {H:1,A:1,mu};
      // Fixed warm-up seeds keep the estimated level independent of season
      // randomness. Roster impact enters mu before calibration, so moving a
      // player cannot be calibrated back to an unchanged team rating.
      for (let pass = 0; pass < 2; pass++) {
        const total = {H:0,A:0};
        for (let i = 0; i < 8; i++) {
          const g = global.NBAI_SIM.playGame(H,A,id=>d.players[id],d.constants,d.league,pace,scale,
            hash(`calibration:${key}:${pass}:${i}`),false,d.calibration);
          total.H += g.score.H; total.A += g.score.A;
        }
        for (const side of ["H","A"]) scale[side] = clamp(scale[side]*mu[side]/(total[side]/8),.55,1.6);
      }
      const state = {H,A,pace,scale};this.calibrated.set(key,state);return state;
    }
    next() {
      if (this.index >= this.schedule.length) return null;
      const game = this.schedule[this.index], state = this.matchup(game), d = this.data;
      const result = global.NBAI_SIM.playGame(state.H,state.A,id=>d.players[id],d.constants,d.league,
        state.pace,state.scale,hash(this.seed+":"+game.id),false,d.calibration);
      if (result.score.H === result.score.A) throw new Error("A final game cannot be tied");
      for (const [side,team,other,venue] of [["H",game.home,"A","home"],["A",game.away,"H","away"]]) {
        const row = this.standings[team], win = result.score[side] > result.score[other];
        row[win?"w":"l"]++; row[venue+(win?"W":"L")]++;
        row.pf += result.score[side]; row.pa += result.score[other];
        for (const [id,box] of Object.entries(result.box[side])) {
          const p = this.players[id];
          if (box.MIN > 0) p.gp++;
          for (const stat of global.NBAI_SIM.BOX) p[stat] += box[stat];
        }
      }
      const stored = {...game,score:result.score,box:result.box,ot:result.ot};
      this.results.push(stored);this.index++;return stored;
    }
    ranked(conference) {
      return Object.values(this.standings).filter(t=>!conference || this.data.teams[t.team].conference===conference)
        .sort((a,b) => (b.w/(b.w+b.l||1)-a.w/(a.w+a.l||1)) || (b.pf-b.pa)-(a.pf-a.pa) || a.team.localeCompare(b.team));
    }
    export() {
      return {version:1,season:this.data.season,seed:this.seed,scheduleSource:this.data.scheduleSource,
        scheduleSha256:this.data.scheduleSha256,sourceHashes:this.data.sourceHashes,
        generated:this.data.generated,modelNote:this.data.modelNote,
        membership:this.membership,provisionalGames:this.schedule.filter(g=>g.provisional).length,
        completed:this.index,scheduled:this.schedule.length,standings:this.ranked(),
        players:Object.values(this.players),games:this.results};
    }
  }
  global.NBAI_SEASON = {Season,assignments,rotation,makeSchedule,hash};
})(typeof window !== "undefined" ? window : globalThis);
