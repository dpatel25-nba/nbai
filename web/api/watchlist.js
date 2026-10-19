// Fixed watchlist, public GETs only. No caller-provided tickers, URLs or credentials.
const {collect}=require('../lib/watchlist-quotes.cjs');
const targets=require('../lib/game-watchlist-targets.json'),cache=new Map(),pending=new Map();
module.exports=async function(req,res){
 const send=(code,data)=>{res.statusCode=code;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));};
 if(req.method!=='GET'){res.setHeader('Allow','GET');return send(405,{error:'GET only'});}
 const q=new URL(req.url,'https://nbai.space').searchParams,game=q.get('game'),quantity=Number(q.get('quantity')||20);
 if([...q.keys()].some(k=>!['game','quantity'].includes(k))||q.getAll('game').length!==1||q.getAll('quantity').length>1||!targets.games.some(x=>x.id===game)||![1,5,20,50,100].includes(quantity))return send(400,{error:'Unsupported watchlist request'});
 const key=game+':'+quantity;
 try{
  const expiry=Math.min(Date.parse(targets.expires_at),Date.parse(targets.games.find(x=>x.id===game).starts_at));
  if(Date.now()>=expiry)return send(200,{gameId:game,quantity,status:'Pregame monitoring ended',quotes:[]});
  if(!cache.has(key)||Date.now()-cache.get(key).at>10000){if(!pending.has(key))pending.set(key,collect(game,quantity).then(data=>cache.set(key,{at:Date.now(),data})).finally(()=>pending.delete(key)));await pending.get(key);}
  res.setHeader('Cache-Control','no-store');return send(200,cache.get(key).data);
 }catch(_){return send(503,{error:'Quotes unavailable',quotes:[]});}
};
