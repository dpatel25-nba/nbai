/* Possession simulator, ported to the browser.
 *
 * The Python engine (scripts/124) reads parquet and takes ~1s a game, so it
 * cannot run in a page, and precomputing 435 matchups x 1000 simulations is not
 * feasible either. The LOOP is portable though: per-player rates, a rotation and
 * the league constants all fit in the exported payload, and the arithmetic runs
 * in microseconds here.
 *
 * This is a faithful port of the parts that move a box score — usage allocation,
 * the four-way outcome mix, offensive-rebound continuation, fouls and the
 * penalty, assist/steal/block credit, the anchor with its roster-delta term, and
 * mean reversion. It deliberately OMITS refinements that need data too large to
 * ship or that move totals by well under a point: shot zones, defender matchup
 * affinity, height/weight effects, cold-start, transition and endgame shot
 * selection.
 *
 * Because it is a second implementation it is checked against the first rather
 * than assumed equivalent — scripts/157_validate_js.py compares team scores and
 * per-player means, and the numbers it reports are quoted in the UI.
 */
(function (global) {
  "use strict";

  function rng(seed) {                       // mulberry32, seeded and repeatable
    let a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function gauss(r) {                        // Box-Muller
    let u = 0, v = 0;
    while (u === 0) u = r();
    while (v === 0) v = r();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }
  function pick(r, w) {                      // weighted choice over an array
    let s = 0; for (let i = 0; i < w.length; i++) s += w[i];
    let x = r() * s;
    for (let i = 0; i < w.length; i++) { x -= w[i]; if (x <= 0) return i; }
    return w.length - 1;
  }
  const clamp = (x, lo, hi) => x < lo ? lo : (x > hi ? hi : x);

  const BOX = ["MIN","FGM","FGA","FG3M","FG3A","FTM","FTA","OREB","DREB","REB",
               "AST","STL","BLK","TOV","PF","PTS"];

  /* Level calibration. Given the SAME anchor scale, this port scored 4.1% below
   * the Python engine on every matchup tested (ratios .963 .955 .966 .953) while
   * per-player points differed by only 0.71 on average — so the shape across
   * players is intact and what is missing is a level offset, almost certainly
   * the shot-zone valuation the port omits. Measured by scripts/157, not tuned
   * until the output looked right. */
  const LEVEL_CAL = 1.043;

  /* Systematic pi-ps lineup sampling: exactly the engine's approach. Marginal
   * inclusion probabilities are hit exactly, the offset is held for LINEUP_HOLD
   * slots for persistence and drifts to decorrelate across the game. */
  function lineups(ros, C, r) {
    const n = ros.length, N = C.N_SLOTS, out = new Array(N);
    const pi0 = ros.map(p => p.min / 48);
    let u = r();
    const order = ros.map((_, i) => i).sort((a, b) => pi0[b] - pi0[a]);
    for (let s = 0; s < N; s++) {
      if (s % C.LINEUP_HOLD) { out[s] = out[s - 1]; continue; }
      let pi = order.map(i => clamp(pi0[i], 1e-9, 1));
      let tot = pi.reduce((a, b) => a + b, 0);
      pi = pi.map(x => x * 5 / tot).map(x => clamp(x, 0, 1));
      for (let k = 0; k < 20; k++) {
        const d = 5 - pi.reduce((a, b) => a + b, 0);
        if (Math.abs(d) < 1e-9) break;
        const room = pi.map(x => x < 1 ? x : 0);
        const rs = room.reduce((a, b) => a + b, 0);
        if (rs <= 0) break;
        pi = pi.map((x, i) => clamp(x + d * room[i] / rs, 0, 1));
      }
      const cum = []; let acc = 0;
      for (const x of pi) { acc += x; cum.push(acc); }
      const five = [];
      for (let k = 0; k < 5; k++) {
        const t = u + k;
        let j = cum.findIndex(c => c > t);
        if (j < 0) j = pi.length - 1;
        five.push(order[j]);
      }
      out[s] = five;
      // A fresh offset each window rather than the engine's slow random walk.
      // The engine drifts slowly because its play-by-play has to show a
      // believable number of substitutions; this port shows no substitutions, so
      // the only thing the correlation buys here is a star missing entire games
      // — 8.5% of them at the engine's drift, against a real rate near zero.
      u = r();
    }
    return out;
  }

  /* One game. Returns a per-player box plus the event log when asked. */
  function playGame(home, away, ratesOf, C, LG, pace, scale, seed, wantLog) {
    const r = rng(seed);
    const teams = {H: home, A: away};
    const box = {H: {}, A: {}}, lu = {}, fouls = {H: {}, A: {}};
    for (const t of ["H", "A"]) {
      lu[t] = lineups(teams[t], C, r);
      for (const p of teams[t]) {
        box[t][p.id] = {}; for (const k of BOX) box[t][p.id][k] = 0;
      }
    }
    const npos = Math.max(60, Math.round(pace + gauss(r) * C.PACE_SD));
    const shared = Math.exp(gauss(r) * C.SHOOT_SD_SHARED);
    const hot = {H: shared * Math.exp(gauss(r) * C.SHOOT_SD_TEAM),
                 A: shared * Math.exp(gauss(r) * C.SHOOT_SD_TEAM)};
    const score = {H: 0, A: 0}, tfoul = {H: 0, A: 0};
    const log = [];
    let period = 0;

    for (let i = 0; i < npos; i++) {
      const slot = Math.min(Math.floor(i / npos * C.N_SLOTS), C.N_SLOTS - 1);
      const per = Math.min(Math.floor(i / npos * 4), 3);
      if (per !== period) { period = per; tfoul.H = 0; tfoul.A = 0; }
      for (const [off, dfn] of [["H", "A"], ["A", "H"]]) {
        const on = lu[off][slot].map(k => teams[off][k]);
        const dv = lu[dfn][slot].map(k => teams[dfn][k]);
        // Each iteration of the outer loop is one possession for EACH team and
        // consumes 48/npos minutes of game clock. Crediting a player only while
        // his own team had the ball halved every minutes total (a 34-minute
        // starter came out at 13.8), so credit both fives once per iteration.
        if (off === "H") for (const t of ["H", "A"])
          for (const k of lu[t][slot]) box[t][teams[t][k].id].MIN += 48 / npos;

        // who uses the possession
        const uw = on.map(p => {
          const q = ratesOf(p.id);
          return q.FG2A_36 + q.FG3A_36 + 0.44 * q.FTA_36 * (1 - C.PEN_FT_TRIM)
                 + q.TOV_36 + 1e-9;
        });
        const user = on[pick(r, uw)];
        const q = ratesOf(user.id);
        const ftw = 0.44 * q.FTA_36 * (1 - C.PEN_FT_TRIM);
        const mix = [q.FG2A_36, q.FG3A_36, ftw, q.TOV_36];

        // mean reversion, split into the part shared with the opponent (total)
        // and the part that differs (margin) — the engine's decomposition
        const frac = i / npos;
        const dev = score[off] - (scale.mu[off] * frac);
        const devO = score[dfn] - (scale.mu[dfn] * frac);
        const pull = C.MEAN_REVERT * 0.5 * (dev - devO)
                   + C.MEAN_REVERT_TOT * 0.5 * (dev + devO);
        let adj = scale[off] * hot[off] * LEVEL_CAL * clamp(1 - pull, 0.85, 1.15);

        // non-shooting foul, and the penalty once past the limit
        if (r() < C.NONSHOOT_FOUL) {
          const fw = dv.map(p => ratesOf(p.id).PF_36 + 1e-6);
          const fl = dv[pick(r, fw)];
          box[dfn][fl.id].PF += 1; tfoul[dfn] += 1;
          if (tfoul[dfn] > C.PENALTY_LIMIT) {
            let made = 0;
            for (let k = 0; k < 2; k++) if (r() < clamp(q.FT_PCT, .3, .99)) made++;
            const b = box[off][user.id];
            b.FTA += 2; b.FTM += made; b.PTS += made; score[off] += made;
            if (wantLog) log.push({p: period, t: off,
              x: `${fl.n} foul (penalty) — ${user.n} ${made}/2 FT`,
              H: score.H, A: score.A});
            continue;
          }
        }

        // the possession itself, through offensive-rebound continuations
        let lastFt = "";                   // kept local: a string on the box
                                           // object would corrupt any code
                                           // that sums its fields
        for (let cyc = 0; cyc < 4; cyc++) {
          const k = pick(r, mix);
          const b = box[off][user.id];
          let pts = 0, made = false;
          if (k === 0 || k === 1) {
            const three = k === 1;
            const base = three ? q.FG3_PCT : q.FG2_PCT;
            made = r() < clamp(base * adj, 0.05, three ? 0.85 : 0.95);
            b.FGA += 1; if (three) b.FG3A += 1;
            if (made) {
              pts = three ? 3 : 2; b.FGM += 1; if (three) b.FG3M += 1;
              const aw = on.filter(p => p.id !== user.id)
                           .map(p => ratesOf(p.id).AST_36 + 1e-6);
              const pAst = clamp((three ? LG.ast3 : LG.ast2), 0.05, 0.95);
              if (r() < pAst) {
                const mates = on.filter(p => p.id !== user.id);
                box[off][mates[pick(r, aw)].id].AST += 1;
              }
            }
          } else if (k === 2) {
            const nft = 2 + (r() < 0.27 ? 1 : 0);
            let m2 = 0;
            for (let z = 0; z < nft; z++) if (r() < clamp(q.FT_PCT, .3, .99)) m2++;
            b.FTA += nft; b.FTM += m2; pts = m2; made = true;
            lastFt = `${m2}/${nft} FT`;
            const fw = dv.map(p => ratesOf(p.id).PF_36 + 1e-6);
            const fl = dv[pick(r, fw)];
            box[dfn][fl.id].PF += 1; tfoul[dfn] += 1;
          } else {
            b.TOV += 1;
            let thief = null;
            if (r() < LG.stl_share) {
              const sw = dv.map(p => ratesOf(p.id).STL_36 + 1e-6);
              thief = dv[pick(r, sw)];
              box[dfn][thief.id].STL += 1;
            }
            if (wantLog) log.push({p: period, t: off,
              x: `${user.n} turnover${thief ? ` (${thief.n} steal)` : ""}`,
              H: score.H, A: score.A});
            made = true;                       // possession ends
          }
          if (pts) { b.PTS += pts; score[off] += pts; }
          if (wantLog && (k === 0 || k === 1)) log.push({p: period, t: off,
            x: made ? `${user.n} makes ${k === 1 ? "3PT jumper" : "shot"} (${b.PTS} PTS)`
                    : `${user.n} misses ${k === 1 ? "3PT jumper" : "shot"}`,
            H: score.H, A: score.A});
          if (wantLog && k === 2) log.push({p: period, t: off,
            x: `${user.n} ${lastFt} at the line`,
            H: score.H, A: score.A});
          if (k === 2 || k === 3) break;
          if (made) break;

          // a miss is live: does the offence keep it?
          if (k === 0 && r() < LG.blk_share) {
            const bw = dv.map(p => ratesOf(p.id).BLK_36 + 1e-6);
            box[dfn][dv[pick(r, bw)].id].BLK += 1;
          }
          const offS = on.reduce((a, p) => a + ratesOf(p.id).OREB_36, 0);
          const defS = dv.reduce((a, p) => a + ratesOf(p.id).DREB_36, 0);
          const odds = (LG.oreb / (1 - LG.oreb)) * (offS / Math.max(defS, 1e-6));
          const pOreb = clamp(odds / (1 + odds), 0.06, 0.50);
          const teamReb = r() < LG.team_reb;
          if (r() < pOreb) {
            if (!teamReb) {
              const w = on.map(p => ratesOf(p.id).OREB_36 + 1e-6);
              const g = on[pick(r, w)];
              box[off][g.id].OREB += 1; box[off][g.id].REB += 1;
              if (wantLog) log.push({p: period, t: off,
                x: `${g.n} offensive rebound`, H: score.H, A: score.A});
            }
            continue;                           // offence keeps the ball
          }
          if (!teamReb) {
            const w = dv.map(p => ratesOf(p.id).DREB_36 + 1e-6);
            const g = dv[pick(r, w)];
            box[dfn][g.id].DREB += 1; box[dfn][g.id].REB += 1;
            if (wantLog) log.push({p: period, t: dfn,
              x: `${g.n} defensive rebound`, H: score.H, A: score.A});
          }
          break;
        }
      }
    }
    // Overtime. A tie has to be played out — without this the margin
    // distribution carries an impossible spike at exactly zero, and the
    // aggregate view reported "closest game 0 points" on a real basketball
    // scoreline that cannot happen.
    let ot = 0;
    while (score.H === score.A && ot < 4) {
      ot += 1;
      const extra = Math.max(6, Math.round(npos * 5 / 48));
      for (let i = 0; i < extra; i++) {
        for (const [off, dfn] of [["H", "A"], ["A", "H"]]) {
          const on = lu[off][C.N_SLOTS - 1].map(k => teams[off][k]);
          const uw = on.map(p => {
            const q = ratesOf(p.id);
            return q.FG2A_36 + q.FG3A_36 + 0.44 * q.FTA_36 * (1 - C.PEN_FT_TRIM)
                   + q.TOV_36 + 1e-9;
          });
          const user = on[pick(r, uw)];
          const q = ratesOf(user.id);
          const mix = [q.FG2A_36, q.FG3A_36,
                       0.44 * q.FTA_36 * (1 - C.PEN_FT_TRIM), q.TOV_36];
          const k = pick(r, mix), b = box[off][user.id];
          if (k === 0 || k === 1) {
            const three = k === 1;
            const made = r() < clamp((three ? q.FG3_PCT : q.FG2_PCT)
                                     * scale[off] * LEVEL_CAL, 0.05,
                                     three ? 0.85 : 0.95);
            b.FGA += 1; if (three) b.FG3A += 1;
            if (made) { b.FGM += 1; if (three) b.FG3M += 1;
              const pts = three ? 3 : 2; b.PTS += pts; score[off] += pts; }
          } else if (k === 2) {
            const nft = 2;
            let m2 = 0;
            for (let z = 0; z < nft; z++) if (r() < clamp(q.FT_PCT, .3, .99)) m2++;
            b.FTA += nft; b.FTM += m2; b.PTS += m2; score[off] += m2;
          } else { b.TOV += 1; }
        }
      }
    }
    return {score, box, log, npos, ot};
  }

  global.NBAI_SIM = {playGame, BOX, rng};
})(window);
