// Captures public observations for an inert, manual-entry research worksheet.
// No accounts, RFQs, order creation, or execution endpoints.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),{collect}=require('../web/lib/watchlist-quotes.cjs');
async function main(){
 const source=fs.readFileSync(path.join(root,'web/game-watchlist.json')),watch=JSON.parse(source);
 if(!(Date.parse(watch.validUntil)>Date.now()))throw Error('Refresh availability and projections first');
 const models=JSON.parse(fs.readFileSync(path.join(root,'web/player-game-projections.json')));
 const availability=JSON.parse(fs.readFileSync(path.join(root,'web/game-availability.json')));
 if(watch.inputSignature!==models.inputSignature||watch.generatedAt!==models.generatedAt||watch.validUntil!==models.availabilityValidUntil||watch.validUntil!==availability.validUntil)throw Error('Forecast bundle is updating');
 const dest=path.join(root,'web/manual-order-snapshot.json');
 let previous=null;
 if(!process.argv.includes('--reset-reference')&&fs.existsSync(dest))previous=JSON.parse(fs.readFileSync(dest));
 const results=await Promise.allSettled(watch.games.map(g=>collect(g.id,20)));
 const quotes=results.map((result,i)=>result.status==='fulfilled'?result.value:{
  gameId:watch.games[i].id,quantity:20,generatedAt:new Date().toISOString(),status:'Quote collection unavailable',quotes:[],actualOrders:0
 });
 // Keep the initial reference through routine model/availability rebuilds.
 // Only a new/expired contract reference or an explicit reset moves the anchor.
 let retainedReferences=0;
 for(const game of quotes)for(let i=0;i<game.quotes.length;i++){
  const current=game.quotes[i],prior=previous?.quotes?.find(g=>g.gameId===game.gameId)?.quotes?.find(q=>q.id===current.id);
  const age=Date.now()-Date.parse(prior?.receivedAt);
  if(current.status==='Observed'&&prior?.status==='Observed'&&prior.rulesHash===current.rulesHash&&age>=0&&age<=21600000){
   game.quotes[i]=prior;retainedReferences++;
  }
 }
 if(!source.equals(fs.readFileSync(path.join(root,'web/game-watchlist.json'))))throw Error('Forecast changed during reference capture; retry');
 const result={schemaVersion:1,generatedAt:new Date().toISOString(),inputSignature:watch.inputSignature,
  forecastSha256:crypto.createHash('sha256').update(source).digest('hex'),patientDiscountCents:2,
  note:'Fixed reference asks for a research worksheet. Two-cent improvement is an unvalidated timing hypothesis. No fills or orders.',
  retainedReferences,quotes,actualOrders:0};
 fs.writeFileSync(dest+'.tmp',JSON.stringify(result,null,2)+'\n');fs.renameSync(dest+'.tmp',dest);
 const dir=path.join(root,'data/features/manual_orders/20261020-v1');fs.mkdirSync(dir,{recursive:true});
 fs.writeFileSync(path.join(dir,'reference-'+Date.now()+'.json'),JSON.stringify(result,null,2)+'\n');
 console.log(JSON.stringify({games:quotes.length,observed:quotes.flatMap(g=>g.quotes).filter(q=>q.status==='Observed').length,retainedReferences,generatedAt:result.generatedAt}));
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
