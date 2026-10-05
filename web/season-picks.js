(function(root){
  const teams={ATL:'Atlanta Hawks',BOS:'Boston Celtics',BKN:'Brooklyn Nets',CHA:'Charlotte Hornets',CHI:'Chicago Bulls',CLE:'Cleveland Cavaliers',DAL:'Dallas Mavericks',DEN:'Denver Nuggets',DET:'Detroit Pistons',GSW:'Golden State Warriors',HOU:'Houston Rockets',IND:'Indiana Pacers',LAC:'LA Clippers',LAL:'Los Angeles Lakers',MEM:'Memphis Grizzlies',MIA:'Miami Heat',MIL:'Milwaukee Bucks',MIN:'Minnesota Timberwolves',NOP:'New Orleans Pelicans',NYK:'New York Knicks',OKC:'Oklahoma City Thunder',ORL:'Orlando Magic',PHI:'Philadelphia 76ers',PHX:'Phoenix Suns',POR:'Portland Trail Blazers',SAC:'Sacramento Kings',SAS:'San Antonio Spurs',TOR:'Toronto Raptors',UTA:'Utah Jazz',WAS:'Washington Wizards'};
  function identify(row,season='2026-27') {
    const year=season.slice(-2),win=new RegExp(`^KXNBAWINS-${year}([A-Z]{3})-(\\d+)$`).exec(row.ticker),champ=new RegExp(`^KXNBA-${year}-([A-Z]{3})$`).exec(row.ticker);
    if(win&&row.category==='Team wins'&&teams[win[1]]&&+win[2]<=82)return {team:win[1],threshold:+win[2]};
    if(champ&&row.category==='Championship'&&teams[champ[1]])return {team:champ[1],threshold:null};
    return null;
  }
  function group(rows,category,season) {
    return Object.entries(teams).map(([team,name])=>({team,name,markets:rows.filter(r=>r.category===category&&identify(r,season)?.team===team).sort((a,b)=>(identify(a,season).threshold??0)-(identify(b,season).threshold??0))}));
  }
  function nearest(rows,projection,season) {
    if(!rows.length)return null;
    // Default to the closest listed threshold; this is navigation, not a pick.
    if(!projection||projection.reviewRequired)return rows[Math.floor(rows.length/2)];
    return rows.reduce((a,b)=>Math.abs(identify(b,season).threshold-projection.injuries.mean)<Math.abs(identify(a,season).threshold-projection.injuries.mean)?b:a);
  }
  const api={teams,identify,group,nearest};if(typeof module!=='undefined')module.exports=api;else root.NBAI_PICKS=api;
})(typeof window!=='undefined'?window:globalThis);
