// Recognize complete game-winner rules; unsupported contracts stay unmapped.
const names=['Atlanta','Boston','Brooklyn','Charlotte','Chicago','Cleveland','Dallas','Denver','Detroit','Golden State','Houston','Indiana','Los Angeles C','Los Angeles L','Memphis','Miami','Milwaukee','Minnesota','New Orleans','New York','Oklahoma City','Orlando','Philadelphia','Phoenix','Portland','Sacramento','San Antonio','Toronto','Utah','Washington'];
const codes=['ATL','BOS','BKN','CHA','CHI','CLE','DAL','DEN','DET','GSW','HOU','IND','LAC','LAL','MEM','MIA','MIL','MIN','NOP','NYK','OKC','ORL','PHI','PHX','POR','SAC','SAS','TOR','UTA','WAS'];
const cities=Object.fromEntries(names.map((n,i)=>[n,codes[i]]));
Object.assign(cities,{'LA Clippers':'LAC','Los Angeles Clippers':'LAC','Los Angeles Lakers':'LAL','New York K':'NYK'});
module.exports=function identity(m){
 if(m.market_type!=='binary'||Number(m.notional_value_dollars)!==1||m.mve_selected_legs?.length)return null;
 const match=/^If (.+) wins the (?:Game [1-7]: )?(.+?) (?:vs|at) (.+?) (?:professional basketball|Pro Basketball) game (?:originally )?scheduled for ([A-Z][a-z]{2} \d{1,2}, \d{4}), then the market resolves to Yes\.$/.exec(m.rules_primary||'');
 if(!match)return null;
 const [,winner,a,b,date]=match,time=Date.parse(date+' 12:00:00 GMT');
 if(![a,b].includes(winner)||a===b||![winner,a,b].every(n=>cities[n])||m.yes_sub_title!==winner||!Number.isFinite(time))return null;
 return {yesTeam:cities[winner],teams:[cities[a],cities[b]].sort(),date:new Date(time).toISOString().slice(0,10)};
};
