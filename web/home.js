// Preserve links to research views that used to live on the front page.
const legacyViews=new Set(['simulator','research','ratings','predict','players','impact','props','defense','shots','playtypes','roadmap']);
function followLegacyLink(){if(legacyViews.has(location.hash.slice(1)))location.replace('game.html'+location.hash);}
window.addEventListener('hashchange',followLegacyLink);
followLegacyLink();
fetch('player-projections.json').then(r=>{if(!r.ok)throw Error('No snapshot');return r.json();}).then(data=>{
  const list=document.getElementById('homeLeaders');if(!list)return;
  list.replaceChildren();
  const leaders=[...data.players].filter(p=>typeof p.stats?.PTS?.average?.mean==='number').sort((a,b)=>b.stats.PTS.average.mean-a.stats.PTS.average.mean).slice(0,3);
  leaders.forEach((p,i)=>{
    const row=document.createElement('li'),rank=document.createElement('span'),name=document.createElement('div'),value=document.createElement('strong'),note=document.createElement('small');
    rank.className='rank';rank.textContent=String(i+1).padStart(2,'0');name.className='stat-name';name.textContent=p.name;note.textContent=p.team+' · fixed-roster scenario';name.append(note);value.textContent=p.stats.PTS.average.mean.toFixed(1);row.append(rank,name,value);list.append(row);
  });
  document.getElementById('homeSource').textContent=`${data.season} · ${data.runs} full-league scenarios · All players available · Built ${new Date(data.generatedAt).toLocaleDateString()}`;
}).catch(()=>{const el=document.getElementById('homeSource');if(el)el.textContent='Player snapshot unavailable. Explore the research page for methodology.';const list=document.getElementById('homeLeaders');if(list)list.textContent='Rankings unavailable for now.';});
