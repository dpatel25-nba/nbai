// Preserve research and simulator bookmarks from the earlier home page.
const legacyViews=new Set(['simulator','research','ratings','predict','players','impact','props','defense','shots','playtypes','roadmap']);
function followLegacyLink(){if(legacyViews.has(location.hash.slice(1)))location.replace('game.html'+location.hash);}
window.addEventListener('hashchange',followLegacyLink);
followLegacyLink();
