const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const modelFiles=['web/season-data.js','web/season-engine.js','web/season-possession.js','web/season-availability.json','scripts/build_market_game_projections.cjs','scripts/match_market_schedule.cjs','scripts/season_projection_review.cjs','scripts/player_projection_stats.cjs','scripts/market_projection_inputs.cjs'];
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
function inputs(root,market){
 const sourceHashes=Object.fromEntries(modelFiles.map(f=>[f,sha(fs.readFileSync(path.join(root,f)))]));
 const inventory=market.events.map(e=>({id:e.id,sport:e.sport_key||'basketball_nba',home:e.home_team,away:e.away_team,start:e.commence_time})).sort((a,b)=>a.id.localeCompare(b.id));
 const marketInventoryHash=sha(JSON.stringify(inventory));
 return {sourceHashes,marketInventoryHash,inputSignature:sha(JSON.stringify({sourceHashes,marketInventoryHash,runs:200,version:1}))};
}
module.exports={inputs,sha};
