// Allow a small posted-tip offset, never a different opponent, venue or day.
module.exports=function matchEvent(data,event){
 if(event.sport_key&&event.sport_key!=='basketball_nba')return null;
 const teamId=name=>Object.keys(data.teams).find(t=>data.teams[t].name===name);
 const home=teamId(event.home_team),away=teamId(event.away_team),start=Date.parse(event.commence_time);
 if(!home||!away||!Number.isFinite(start))return null;
 const choices=data.schedule.filter(g=>!g.provisional&&g.home===home&&g.away===away&&Math.abs(Date.parse(g.tip)-start)<=15*60000);
 if(choices.length!==1)return null;
 const game=choices[0];return {game,home,away,offsetMinutes:(start-Date.parse(game.tip))/60000};
};
