const fs=require('node:fs'),path=require('node:path');
function lower(hits){const n=hits.length,p=hits.reduce((a,b)=>a+b,0)/n,z=1.96;return (p+z*z/(2*n)-z*Math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n);}
function forecast(hits){const p=hits.reduce((a,b)=>a+b,0)/hits.length,l=lower(hits);return {probability:p,samplingLower:l,targetAsk:Math.max(0,Math.floor((l-.07)*100+1e-9)/100),scenarios:hits.length};}
function build(models,samples,targets){
 const out={schemaVersion:1,generatedAt:models.generatedAt,inputSignature:models.inputSignature,validUntil:models.availabilityValidUntil,expiresAt:targets.expires_at,runs:models.runs,note:'Conditional simulation shares. Sampling bounds exclude model error. Watch prices reserve 5 cents of surplus and 2 cents for fees; quantity costs use an unverified general-fee scenario. No orders.',games:[]};
 for(const game of targets.games){
  const entry=samples.find(x=>x.game.id===game.id),model=models.events.find(x=>x.gameId===game.id),hits={};
  const matched=entry&&model&&entry.game.home===game.home&&entry.game.away===game.away&&entry.providerStartsAt===game.starts_at&&model.startsAt===game.starts_at&&entry.scores.length===models.runs;
  const hold=!matched?'Forecast bundle mismatch':entry.reviewRequired||model.reviewRequired;
  const singles=targets.singles.filter(x=>x.game_id===game.id).map(p=>{
   let values=null,reason=hold;
   if(!reason){
    if(p.kind==='winner')values=entry.scores.map(s=>Number(p.yesTeam===game.home?s.H>s.A:s.A>s.H));
    else if(p.kind==='total')values=entry.scores.map(s=>Number(s.H+s.A>p.threshold));
    else {
     const player=entry.players.find(x=>x.player.id===p.player_id),status=entry.availability.players.find(x=>x.id===p.player_id)?.status;
     if(player?.samples.length===models.runs&&['Not listed','Active','Available','Probable'].includes(status))values=player.samples.map(s=>Number(s[p.stat]>p.threshold));
     else reason='Participation or aligned samples unavailable';
    }
   }
   if(values){if(p.side==='no')values=values.map(x=>1-x);hits[p.id]=values;}
   return {...p,forecast:values?forecast(values):null,hold:reason||null};
  });
  const combos=targets.combos.filter(x=>x.game_id===game.id).map(p=>{const legs=p.legs.map(x=>hits[x.market_ticker+':'+x.side]);const values=legs.every(Boolean)?Array.from({length:models.runs},(_,i)=>Number(legs.every(x=>x[i]))):null;const marginalProduct=legs.every(Boolean)?legs.reduce((v,x)=>v*x.reduce((a,b)=>a+b,0)/x.length,1):null;return {...p,forecast:values?{...forecast(values),marginalProduct}:null,hold:values?null:'Aligned leg forecasts unavailable',quoteStatus:'Exact combo quote unavailable',settlementNote:'Joint all-win share conditional on playing; fractional/DNP settlement not modeled. No payout inferred from leg prices.'};});
  out.games.push({...game,eventId:model?.eventId,singles,combos});
 }
 return out;
}
function exportFile(root,models,samples){const targets=JSON.parse(fs.readFileSync(path.join(root,'web/lib/game-watchlist-targets.json'))),out=build(models,samples,targets),dest=path.join(root,'web/game-watchlist.json');fs.writeFileSync(dest+'.tmp',JSON.stringify(out,null,2)+'\n');fs.renameSync(dest+'.tmp',dest);return out;}
module.exports={build,forecast,exportFile};
