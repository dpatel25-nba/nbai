'use strict';
const matchEvent=require('./match_market_schedule.cjs');

// Public schedule identities supplement the odds inventory; they contain no quotes.
module.exports=function projectionEvents(data,market,availability){
 const events=[...market.events];
 const covered=new Set(events.map(e=>matchEvent(data,e)?.game.id).filter(Boolean));
 for(const entry of availability.events||[]){
  if(!entry.eventId||!data.teams[entry.home]||!data.teams[entry.away])continue;
  const event={id:'espn:'+entry.eventId,sport_key:'basketball_nba',
   home_team:data.teams[entry.home].name,away_team:data.teams[entry.away].name,
   commence_time:entry.startsAt,identitySource:'espn-availability-schedule'};
  const match=matchEvent(data,event);
  if(!match||covered.has(match.game.id))continue;
  events.push(event);covered.add(match.game.id);
 }
 return events;
};
