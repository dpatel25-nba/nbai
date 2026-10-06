// Preserve links to research views that used to live on the front page.
const legacyViews=new Set(['simulator','research','ratings','predict','players','impact','props','defense','shots','playtypes','roadmap']);
function followLegacyLink(){if(legacyViews.has(location.hash.slice(1)))location.replace('game.html'+location.hash);}
window.addEventListener('hashchange',followLegacyLink);
followLegacyLink();
fetch('visual-research.json').then(r=>{if(!r.ok)throw Error('No snapshot');return r.json();}).then(data=>{
 const season=data.seasons.at(-1),list=document.getElementById('homeLeaders');if(!list)return;list.replaceChildren();
 const leaders=[...season.players].sort((a,b)=>b.war-a.war).slice(0,3);
 leaders.forEach((p,i)=>{const row=document.createElement('li'),rank=document.createElement('span'),name=document.createElement('div'),value=document.createElement('strong'),note=document.createElement('small');rank.className='rank';rank.textContent=String(i+1).padStart(2,'0');name.className='stat-name';name.textContent=p.name;note.textContent=p.team+' · historical value';name.append(note);value.textContent=p.war.toFixed(1);row.append(rank,name,value);list.append(row);});
 document.getElementById('homeSource').textContent=`${season.season} regular season · ≥ ${data.minMinutes} minutes · WAR v4 research metric, not a forecast`;
}).catch(()=>{const el=document.getElementById('homeSource');if(el)el.textContent='Research snapshot unavailable.';const list=document.getElementById('homeLeaders');if(list)list.textContent='Historical rankings unavailable for now.';});
