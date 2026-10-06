// Preserve source names and resolve only unique roster matches on the event's teams.
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'),C=require('../web/consensus-analysis.js');
const {sha}=require('./market_projection_inputs.cjs');
const withoutSuffix=name=>C.normalize(String(name).replace(/\s+(?:jr\.?|sr\.?|ii|iii|iv)$/i,''));
function resolvePlayer(quote,event,players,teams){
 const home=Object.keys(teams).find(t=>teams[t].name===event?.home_team),away=Object.keys(teams).find(t=>teams[t].name===event?.away_team);
 if(!home||!away)return {status:'unresolved',reason:'Event teams are not in the model roster'};
 const roster=players.filter(p=>[home,away].includes(p.team));
 let candidates=roster.filter(p=>C.normalize(p.n||p.name)===C.normalize(quote.player)),method='exact';
 if(!candidates.length){candidates=roster.filter(p=>withoutSuffix(p.n||p.name)===withoutSuffix(quote.player));method='unique_suffix_variant';}
 if(candidates.length!==1)return {status:'unresolved',reason:candidates.length?'Ambiguous player identity':'No matching player on either event team'};
 return {status:'matched',playerId:candidates[0].id,canonicalPlayer:candidates[0].n||candidates[0].name,method};
}
function build(market,models,data){
 const players=Object.values(data.players),quotes=market.quotes.map(q=>{const {playerId,canonicalPlayer,identityMethod,identityStatus,...source}=q;const r=resolvePlayer(q,market.events.find(e=>e.id===q.eventId),players,data.teams);return {...source,identityStatus:r.status,...(r.status==='matched'?{playerId:r.playerId,canonicalPlayer:r.canonicalPlayer,identityMethod:r.method}:{})};});
 const unmatched=quotes.filter(q=>!q.playerId).map(q=>({eventId:q.eventId,player:q.player,provider:q.provider,stat:q.stat,reason:resolvePlayer(q,market.events.find(e=>e.id===q.eventId),players,data.teams).reason}));
 const events=market.events.map(e=>{
  const rows=quotes.filter(q=>q.eventId===e.id),model=models.events.find(m=>m.eventId===e.id),rejection=models.rejected?.find(m=>m.eventId===e.id);
  return {eventId:e.id,competition:e.competition||'Regular season',startsAt:e.commence_time,home:e.home_team,away:e.away_team,quoteCount:rows.length,playersWithLines:new Set(rows.map(q=>q.player)).size,matchedQuotes:rows.filter(q=>q.playerId).length,projectionStatus:model?model.reviewRequired?'under_review':'available':'unavailable',reason:model?.reviewRequired||rejection?.reason||null};
 });
 return {quotes,report:{schemaVersion:1,generatedAt:new Date().toISOString(),marketGeneratedAt:market.generatedAt,modelGeneratedAt:models.generatedAt,summary:{events:events.length,eventsWithLines:events.filter(e=>e.quoteCount).length,quoteCount:quotes.length,matchedQuotes:quotes.filter(q=>q.playerId).length,unmatchedQuotes:unmatched.length,eventsWithProjections:events.filter(e=>e.projectionStatus==='available').length,eventsUnderReview:events.filter(e=>e.projectionStatus==='under_review').length},unmatched,events}};
}
if(require.main===module){
 global.window=global;require('../web/season-data.js');
 const dest=path.join(root,'web/player-consensus.json'),market=JSON.parse(fs.readFileSync(dest)),models=JSON.parse(fs.readFileSync(path.join(root,'web/player-game-projections.json')));
 const {quotes,report}=build(market,models,NBAI_SEASON_DATA);market.quotes=quotes;
 fs.writeFileSync(dest+'.tmp',JSON.stringify(market,null,2)+'\n');fs.renameSync(dest+'.tmp',dest);
 report.marketSha256=sha(fs.readFileSync(dest));report.modelSha256=sha(fs.readFileSync(path.join(root,'web/player-game-projections.json')));
 const coverage=path.join(root,'web/player-line-coverage.json');fs.writeFileSync(coverage+'.tmp',JSON.stringify(report,null,2)+'\n');fs.renameSync(coverage+'.tmp',coverage);
 console.log(JSON.stringify(report.summary));
}
module.exports={resolvePlayer,build};
