(function () {
  "use strict";
  const $ = id => document.getElementById(id), data = window.NBAI_SEASON_DATA, api = window.NBAI_SEASON, calendar = window.NBAI_CALENDAR;
  const esc = x => String(x == null ? "" : x).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fmt = (n,d=1) => Number(n).toFixed(d), number = n => n.toLocaleString();
  const dayLabel = day => new Date(day+"T12:00:00").toLocaleDateString("en-US",{month:"short",day:"numeric"});
  const percent = (made,tried) => tried ? fmt(100*made/tried) : "—";
  const sign = n => n>0?"+"+n:String(n);
  const changeClass = n => n>0?"positive":n<0?"negative":"";
  function error(message) { $("message").textContent=message; $("message").hidden=!message; }
  if (!data || !api || !calendar || !window.NBAI_SIM) {
    error("The season data could not load. Run the season exporter and reload this page.");
    $("runWeek").disabled=$("runSeason").disabled=$("runDay").disabled=true;return;
  }
  const original = api.assignments(data), teams = Object.keys(data.teams).sort();
  let membership = {...original}, season=null, baseline=null, running=false, pauseRequested=false;
  let conference="East", tab="standings", page=0, myTeam="NYK", viewedWeek=0;
  const weeks=calendar.weeks(data.schedule);
  const storageKey="nbai-season-scenario-v1";
  const sourceIdentity=data.scheduleSha256+":"+data.sourceHashes["data/parquet/team_rosters.parquet"];
  try {
    const saved=JSON.parse(localStorage.getItem(storageKey)||"null");
    if (saved && teams.includes(saved.myTeam)) myTeam=saved.myTeam;
    if (saved && saved.hash===sourceIdentity) {
      for (const [id,team] of Object.entries(saved.membership||{}))
        if (id in membership && (team===null || teams.includes(team))) membership[id]=team;
      if (Number.isInteger(saved.seed) && saved.seed>=0 && saved.seed<=4294967295) $("seed").value=saved.seed;
      $("completeSchedule").checked=saved.complete!==false;
      $("compare").checked=saved.compare!==false;
    }
  } catch (_) { /* Private browsing may disable local storage. */ }
  const teamOptions=teams.map(t=>`<option value="${t}">${t} · ${esc(data.teams[t].name)}</option>`).join("");
  $("myTeam").innerHTML=$("rosterTeam").innerHTML=teamOptions;
  $("myTeam").value=$("rosterTeam").value=myTeam;
  for (const id of ["gamesTeam","statsTeam"]) $(id).innerHTML='<option value="">All teams</option>'+teamOptions;
  $("statsTeam").insertAdjacentHTML("beforeend",'<option value="unassigned">Unassigned</option>');
  $("gamesMonth").innerHTML='<option value="">All months</option>'+[...new Set(data.schedule.map(g=>g.date.slice(0,7)))].map(m=>
    `<option value="${m}">${new Date(m+"-15T12:00:00").toLocaleDateString("en-US",{month:"long",year:"numeric"})}</option>`).join("");
  $("scheduleLink").href=data.scheduleSource;
  $("sourceNote").textContent=`1,200 official games · Schedule published ${data.scheduleAsOf} · 30 teams`;
  $("modelNote").textContent=data.modelNote;
  $("dataNote").textContent=`Roster snapshot: ${data.rosterAsOf}. Player history through ${data.ratesThrough}; team ratings from ${data.ratingsSeason}. ${Object.keys(data.players).length} rostered players, including rookies and bench players. Players without NBA history use rookie or replacement priors. Roster snapshot date is the local roster file date.`;
  function edited() { return Object.keys(original).filter(id=>membership[id]!==original[id]).length; }
  function persist() {
    try {localStorage.setItem(storageKey,JSON.stringify({hash:sourceIdentity,myTeam,membership,seed:Number($("seed").value),complete:$("completeSchedule").checked,compare:$("compare").checked}));} catch (_) {}
  }
  function reset() {
    if (running) return;
    season=baseline=null;page=0;viewedWeek=0;error("");persist();render();
  }
  function playerChoices() {
    const team=$("rosterTeam").value, q=$("playerSearch").value.toLowerCase().trim();
    const options=Object.values(data.players).filter(p=>membership[p.id]!==team && (p.n.toLowerCase().includes(q)||String(membership[p.id]||"unassigned").toLowerCase().includes(q)))
      .sort((a,b)=>a.n.localeCompare(b.n));
    $("playerChoice").innerHTML=options.map(p=>`<option value="${p.id}">${esc(p.n)} · ${esc(membership[p.id]||"Unassigned")}</option>`).join("");
    $("addPlayer").disabled=running||!options.length;
  }
  function renderRoster() {
    const team=$("rosterTeam").value, players=Object.values(data.players).filter(p=>membership[p.id]===team).sort((a,b)=>b.MPG-a.MPG||a.n.localeCompare(b.n));
    let minutes={};try {minutes=Object.fromEntries(api.rotation(data,team,membership).map(p=>[p.id,p.min]));} catch (_) {}
    $("rosterCount").textContent=`${players.length} players · ${Object.keys(minutes).length} in rotation`;
    $("editCount").textContent=edited()?`${edited()} moved`:"Original";
    $("rosterList").innerHTML=players.map(p=>`<div class="roster-row"><span class="player-initials" aria-hidden="true">${esc(p.n.split(" ").map(s=>s[0]).slice(0,2).join(""))}</span><div class="player-info"><b title="${esc(p.n)}">${esc(p.n)}</b><small>${esc(p.pos)}${p.rookie?" · Rookie":""}${membership[p.id]!==original[p.id]?" · Added":""}</small></div><span class="roster-min">${minutes[p.id]?fmt(minutes[p.id],0)+" min":"Bench"}</span><button class="remove-player" data-remove="${p.id}" aria-label="Remove ${esc(p.n)}" ${running?"disabled":""}>×</button></div>`).join("")||'<p class="empty">Add at least five players.</p>';
    playerChoices();
  }
  function emptyStandings() {return teams.map(team=>({team,w:0,l:0,pf:0,pa:0,homeW:0,homeL:0,awayW:0,awayL:0}));}
  function renderStandings() {
    const rows=season?season.ranked(conference):emptyStandings().filter(t=>!conference||data.teams[t.team].conference===conference);
    const leader=rows[0];
    $("standingsBody").innerHTML=rows.map((r,i)=>{
      const gp=r.w+r.l, delta=baseline?r.w-baseline.standings[r.team].w:null;
      const gb=gp?((leader.w-r.w)+(r.l-leader.l))/2:0;
      return `<tr class="standings-row ${r.team===myTeam?"my-team-row":""}" ${r.team===myTeam?'aria-label="Your team"':""}><td class="rank">${i+1}</td><td class="left team-cell"><span class="team-mark"></span><abbr title="${esc(data.teams[r.team].name)}">${r.team}</abbr></td><td><b>${r.w}</b></td><td>${r.l}</td><td>${gp?fmt(r.w/gp,3):"—"}</td><td>${gb?fmt(gb):"—"}</td><td>${r.homeW}–${r.homeL}</td><td>${r.awayW}–${r.awayL}</td><td class="${changeClass(r.pf-r.pa)}">${gp?sign(Number(fmt((r.pf-r.pa)/gp))):"—"}</td><td class="${changeClass(delta)}">${delta===null?"—":sign(delta)}</td></tr>`;
    }).join("");
  }
  function renderGames() {
    const schedule=season?season.schedule:api.makeSchedule(data,$("completeSchedule").checked);
    const resultMap=new Map((season?season.results:[]).map(g=>[g.id,g]));
    const team=$("gamesTeam").value, month=$("gamesMonth").value;
    const filtered=schedule.filter(g=>(!team||g.home===team||g.away===team)&&(!month||g.date.startsWith(month)));
    page=Math.max(0,Math.min(page,Math.ceil(filtered.length/20)-1));
    $("gameCount").textContent=`${number(filtered.length)} games`;
    $("gameList").innerHTML=filtered.slice(page*20,page*20+20).map(g=>{
      const r=resultMap.get(g.id);
      return `<button class="game-row" data-game="${esc(g.id)}" ${r?"":"disabled"}><span class="game-date">${dayLabel(g.date)}</span><span class="game-match">${g.away} <span style="font-weight:400;color:var(--muted)">at</span> ${g.home}${g.provisional?'<span class="provisional">PROVISIONAL</span>':g.neutral?'<span class="provisional">NEUTRAL SITE</span>':""}</span><span class="game-score">${r?`${r.score.A} – ${r.score.H}`:g.timeET}</span><span class="game-state">${r?"FINAL"+(r.ot?` · ${r.ot}OT`:"")+" ↗":"ET · Scheduled"}</span></button>`;
    }).join("")||'<p class="empty">No games match these filters.</p>';
    $("gamePage").textContent=`${filtered.length?page+1:0} / ${Math.ceil(filtered.length/20)}`;
    $("prevGames").disabled=page===0;$("nextGames").disabled=(page+1)*20>=filtered.length;
  }
  function renderPlayers() {
    if (!season || !season.index) {$("playersBody").innerHTML='<tr><td colspan="13" class="empty">Simulate a game day to start building season stats.</td></tr>';$("playerCount").textContent="";return;}
    const q=$("statsSearch").value.toLowerCase().trim(),team=$("statsTeam").value,avg=$("statsMode").value==="average",sort=$("statsSort").value;
    const value=p=>p[sort]/(avg?(p.gp||1):1);
    const rows=Object.values(season.players).filter(p=>(!q||data.players[p.id].n.toLowerCase().includes(q))&&(!team||(team==="unassigned"?!p.team:p.team===team)))
      .sort((a,b)=>value(b)-value(a)||b.gp-a.gp||a.id-b.id);
    $("playersBody").innerHTML=rows.map(p=>{
      const d=avg?(p.gp||1):1;
      return `<tr><td class="left">${esc(data.players[p.id].n)}</td><td>${p.team||"—"}</td><td>${p.gp}</td>${["MIN","PTS","REB","AST","STL","BLK","TOV"].map(k=>`<td>${fmt(p[k]/d,k==="MIN"||avg?1:0)}</td>`).join("")}<td>${percent(p.FGM,p.FGA)}</td><td>${percent(p.FG3M,p.FG3A)}</td><td>${percent(p.FTM,p.FTA)}</td></tr>`;
    }).join("");
    $("playerCount").textContent=`${rows.length} players · ${avg?"Averages per game played":"Cumulative season totals"} · Shooting percentages use total makes / attempts.`;
  }
  function currentSchedule() { return season?season.schedule:api.makeSchedule(data,$("completeSchedule").checked); }
  function nextWeekIndex() {
    const schedule=currentSchedule(), i=season?season.index:0;
    return calendar.index(weeks,schedule[Math.min(i,schedule.length-1)].date);
  }
  function selectTeam(team) {
    myTeam=team;$("myTeam").value=$("rosterTeam").value=team;
    $("gamesTeam").value=$("statsTeam").value=team;page=0;
    conference=data.teams[team].conference;
    document.querySelectorAll("[data-conference]").forEach(b=>b.classList.toggle("selected",b.dataset.conference===conference));
    persist();renderRoster();render();
  }
  function renderTeamHub() {
    viewedWeek=Math.max(0,Math.min(weeks.length-1,viewedWeek));
    const schedule=currentSchedule(), results=season?season.results:[], resultMap=new Map(results.map(g=>[g.id,g]));
    const teamGames=schedule.filter(g=>g.home===myTeam||g.away===myTeam);
    const played=results.filter(g=>g.home===myTeam||g.away===myTeam);
    const record=season?season.standings[myTeam]:{w:0,l:0};
    const won=g=>g.score[g.home===myTeam?"H":"A"]>g.score[g.home===myTeam?"A":"H"];
    $("teamName").textContent=data.teams[myTeam].name;
    $("teamCode").textContent=myTeam;
    $("teamRecord").textContent=`${record.w}–${record.l}`;
    $("teamContext").textContent=`${data.teams[myTeam].conference}ern Conference · ${teamGames.length} games · Your team hub`;
    $("recentForm").innerHTML=played.length?'<span>LAST 5</span>'+played.slice(-5).map(g=>`<span class="form-chip ${won(g)?"win":"loss"}" title="${dayLabel(g.date)} · ${g.away} at ${g.home}">${won(g)?"W":"L"}</span>`).join(""):'<span>Opening week awaits</span>';
    const next=teamGames.find(g=>!resultMap.has(g.id));
    $("nextOpponent").textContent=next?`${next.home===myTeam?"vs":"@"} ${data.teams[next.home===myTeam?next.away:next.home].name}`:"Regular season complete";
    $("nextGameNote").textContent=next?`${dayLabel(next.date)} · ${next.timeET} ET${next.neutral?" · Neutral site":""}${next.provisional?" · Provisional":""}`:`Final record ${record.w}–${record.l}`;
    const week=weeks[viewedWeek], games=teamGames.filter(g=>g.date>=week.start&&g.date<=week.end), finals=games.filter(g=>resultMap.has(g.id));
    $("weekTitle").textContent=`Week ${viewedWeek+1} / ${weeks.length}`;
    $("weekRange").textContent=`${dayLabel(week.start)} – ${dayLabel(week.end)}, ${week.end.slice(0,4)}`;
    $("prevWeek").disabled=viewedWeek===0;$("nextWeek").disabled=viewedWeek===weeks.length-1;
    $("weekSummary").textContent=games.length?`${myTeam} · ${games.length} games · ${finals.length} final${finals.length?` · ${finals.filter(g=>won(resultMap.get(g.id))).length}W–${finals.filter(g=>!won(resultMap.get(g.id))).length}L`:""}`:`${myTeam} · No games this week`;
    $("weekDays").innerHTML=week.days.map(day=>{
      const g=games.find(g=>g.date===day), r=g&&resultMap.get(g.id);
      const label=new Date(day+"T12:00:00").toLocaleDateString("en-US",{weekday:"short"});
      const dateHead=`<div class="calendar-date"><span>${label}</span><b>${Number(day.slice(-2))}</b></div>`;
      if(!g)return `<div class="calendar-day rest-day">${dateHead}<span class="off-label">OFF DAY</span><span class="rest-line"></span></div>`;
      const home=g.home===myTeam, opponent=home?g.away:g.home;
      return `<${r?"button":"div"} class="calendar-day matchup-day ${r?(won(r)?"game-win":"game-loss"):""}" ${r?`data-game="${esc(g.id)}" aria-label="${dayLabel(day)} ${myTeam} ${won(r)?"win":"loss"} against ${opponent}, open box score"`:""}>${dateHead}<span class="venue">${g.neutral?"NEUTRAL":home?"HOME":"AWAY"}</span><strong class="opponent-code">${opponent}</strong><span class="opponent-name">${esc(data.teams[opponent].name)}</span><span class="calendar-score">${r?`${won(r)?"W":"L"} ${r.score[home?"H":"A"]}–${r.score[home?"A":"H"]}`:g.timeET+" ET"}</span>${g.provisional?'<span class="provisional">PROVISIONAL</span>':""}<span class="calendar-status">${r?`Box score ↗${r.ot?" · OT":""}`:"Upcoming"}</span></${r?"button":"div"}>`;
    }).join("");
  }
  function render() {
    const n=season?season.index:0,total=season?season.schedule.length:($("completeSchedule").checked?1230:1200);
    $("progress").max=total;$("progress").value=n;
    $("progressText").textContent=`${number(n)} of ${number(total)} games`;
    const record=season?season.standings[myTeam]:{w:0,l:0};
    $("gamesMetric").textContent=`${record.w+record.l} / ${$("completeSchedule").checked?82:80}`;
    $("currentDate").textContent=n?`Through ${dayLabel(season.results[n-1].date)}`:"Opening night · Oct 20";
    $("runTitle").textContent=n===total?"The season is in the books":running?"The season is unfolding":n?"Your season, in progress":"Ready for opening night";
    $("runStatus").textContent=running?"Simulating":n===total?"Complete":n?"Paused":"Not started";
    $("runSeason").textContent=n===total?"Run another season ↗":n?"Continue season ↗":"Simulate season ↗";
    $("runWeek").disabled=running||n===total;
    $("runWeek").textContent=n===total?"Season complete":`Simulate week ${nextWeekIndex()+1} →`;
    $("runSeason").disabled=running;$("runDay").disabled=running||n===total;
    $("resetSeason").disabled=running;$("pause").hidden=!running;$("download").disabled=!n||running;
    document.querySelectorAll("[data-edit]").forEach(el=>{el.disabled=running;});
    $("cupNote").textContent=$("completeSchedule").checked?"Includes 30 provisional Cup-window games. Their matchups are generated, not official.":"Official posted schedule only: 80 assigned games per team. Two Cup-dependent games remain unassigned.";
    const rank=n && record.w+record.l?season.ranked(data.teams[myTeam].conference).findIndex(t=>t.team===myTeam)+1:null;
    $("leaderMetric").textContent=rank?`#${rank} · ${data.teams[myTeam].conference}`:"Unranked";
    const top=n?Object.values(season.players).filter(p=>p.team===myTeam && p.gp>0).sort((a,b)=>b.PTS/b.gp-a.PTS/a.gp)[0]:null;
    $("scorerMetric").textContent=top?`${data.players[top.id].n} · ${fmt(top.PTS/top.gp)}`:"—";
    renderTeamHub();
    renderStandings();if(tab==="games") renderGames();if(tab==="players") renderPlayers();
  }
  function start() {
    const seed=Number($("seed").value);
    if (!Number.isInteger(seed)||seed<0||seed>4294967295||$("seed").value==="") throw new Error("Enter a whole-number seed from 0 to 4,294,967,295.");
    const options={seed,membership,complete:$("completeSchedule").checked};
    season=new api.Season(data,options);
    baseline=$("compare").checked?(edited()?new api.Season(data,{...options,membership:original}):season):null;
  }
  async function run(mode) {
    if (running) return;
    error("");
    try {
      if (season && season.index===season.schedule.length) {$("seed").value=(Number($("seed").value)+1)>>>0;season=baseline=null;persist();}
      if (!season) start();
      running=true;pauseRequested=false;render();renderRoster();
      const day=season.schedule[season.index].date;
      viewedWeek=nextWeekIndex();
      const through=mode==="week"?weeks[viewedWeek].end:mode==="day"?day:null;
      while (season.index<season.schedule.length && !pauseRequested) {
        const began=performance.now();
        do {
          season.next();if (baseline && baseline!==season) baseline.next();
          if (through && (season.index===season.schedule.length||season.schedule[season.index].date>through)) {pauseRequested=true;break;}
        } while (season.index<season.schedule.length && performance.now()-began<35);
        if (mode!=="week" && season.index) viewedWeek=calendar.index(weeks,season.results[season.index-1].date);
        render();await new Promise(resolve=>setTimeout(resolve,0));
      }
    } catch (e) {error(e.message);}
    finally {running=false;render();renderRoster();}
  }
  function showBox(id) {
    const game=season&&season.results.find(g=>g.id===id);if(!game)return;
    $("boxDate").textContent=`${dayLabel(game.date)}, ${game.date.slice(0,4)} · Simulated final${game.provisional?" · Provisional matchup":""}${game.ot?` · ${game.ot} overtime${game.ot>1?"s":""}`:""}`;
    $("boxTitle").textContent=`${game.away} ${game.score.A} — ${game.score.H} ${game.home}`;
    $("boxContent").innerHTML=["A","H"].map(side=>{
      const team=side==="A"?game.away:game.home, total=Object.fromEntries(window.NBAI_SIM.BOX.map(k=>[k,0]));
      const cells=b=>`<td>${fmt(b.MIN)}</td><td>${b.FGM}–${b.FGA}</td><td>${b.FG3M}–${b.FG3A}</td><td>${b.FTM}–${b.FTA}</td>${["OREB","DREB","REB","AST","STL","BLK","TOV","PF","PTS"].map(k=>`<td>${b[k]}</td>`).join("")}`;
      const rows=Object.entries(game.box[side]).sort((a,b)=>b[1].MIN-a[1].MIN).map(([pid,b])=>{for(const k of window.NBAI_SIM.BOX)total[k]+=b[k];return `<tr><td class="left">${esc(data.players[pid].n)}</td>${cells(b)}</tr>`;}).join("");
      return `<div class="box-heading"><strong>${esc(data.teams[team].name)}</strong><span>${game.score[side]} PTS</span></div><div class="table-wrap"><table><thead><tr><th class="left">Player</th>${["MIN","FG","3PT","FT","OREB","DREB","REB","AST","STL","BLK","TOV","PF","PTS"].map(k=>`<th>${k}</th>`).join("")}</tr></thead><tbody>${rows}<tr class="box-total"><td class="left">Team totals</td>${cells(total)}</tr></tbody></table></div>`;
    }).join("");$("boxDialog").showModal();
  }
  $("runWeek").onclick=()=>run("week");$("runSeason").onclick=()=>run("season");$("runDay").onclick=()=>run("day");$("pause").onclick=()=>{pauseRequested=true;};
  $("resetSeason").onclick=reset;$("rosterTeam").onchange=()=>selectTeam($("rosterTeam").value);
  $("myTeam").onchange=()=>selectTeam($("myTeam").value);
  $("prevWeek").onclick=()=>{viewedWeek--;renderTeamHub();};
  $("nextWeek").onclick=()=>{viewedWeek++;renderTeamHub();};
  $("currentWeek").onclick=()=>{viewedWeek=nextWeekIndex();renderTeamHub();};
  $("weekDays").onclick=e=>{const b=e.target.closest("[data-game]");if(b)showBox(b.dataset.game);};$("playerSearch").oninput=playerChoices;
  $("rosterList").onclick=e=>{const b=e.target.closest("[data-remove]");if(!b||running)return;membership[b.dataset.remove]=null;reset();renderRoster();};
  $("addPlayer").onclick=()=>{const id=$("playerChoice").value;if(!id||running)return;membership[id]=$("rosterTeam").value;$("playerSearch").value="";reset();renderRoster();};
  $("resetRoster").onclick=()=>{membership={...original};reset();renderRoster();};
  for (const id of ["seed","completeSchedule","compare"]) $(id).onchange=reset;
  document.querySelectorAll("[data-tab]").forEach(b=>{b.onclick=()=>{tab=b.dataset.tab;document.querySelectorAll("[data-tab]").forEach(t=>{t.classList.toggle("selected",t===b);t.setAttribute("aria-selected",String(t===b));$("view-"+t.dataset.tab).hidden=t!==b;});render();};});
  document.querySelectorAll("[data-conference]").forEach(b=>{b.onclick=()=>{conference=b.dataset.conference;document.querySelectorAll("[data-conference]").forEach(t=>t.classList.toggle("selected",t===b));renderStandings();};});
  for (const id of ["gamesTeam","gamesMonth"]) $(id).onchange=()=>{page=0;renderGames();};
  $("prevGames").onclick=()=>{page--;renderGames();};$("nextGames").onclick=()=>{page++;renderGames();};
  $("gameList").onclick=e=>{const b=e.target.closest("[data-game]");if(b)showBox(b.dataset.game);};
  for (const id of ["statsSearch","statsTeam","statsMode","statsSort"]) $(id).addEventListener(id==="statsSearch"?"input":"change",renderPlayers);
  $("closeBox").onclick=()=>$("boxDialog").close();
  $("download").onclick=()=>{
    if(!season)return;const payload={scenario:season.export(),baseline:baseline?baseline.export():null};
    const url=URL.createObjectURL(new Blob([JSON.stringify(payload)],{type:"application/json"}));
    const a=document.createElement("a");a.href=url;a.download=`nbai-${data.season}-seed-${season.seed}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  selectTeam(myTeam);
})();
