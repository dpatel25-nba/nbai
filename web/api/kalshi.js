// Public GET-only, fixed-source quote proxy. No credentials or account/order endpoints.
const {collect,GROUPS}=require('../lib/kalshi.cjs');
const cache=new Map(),pending=new Map();
module.exports=async function(req,res){
  res.setHeader('Content-Type','application/json');
  const send=(status,data)=>{res.statusCode=status;res.end(JSON.stringify(data));};
  if(req.method!=='GET'){res.setHeader('Allow','GET');return send(405,{error:'GET only'});}
  const query=new URL(req.url,'https://nbai.space').searchParams,group=query.get('group')||'player';
  if(!Object.hasOwn(GROUPS,group)||[...query.keys()].some(k=>k!=='group'))return send(400,{error:'Unsupported query'});
  try{
    let entry=cache.get(group);
    if(!entry||Date.now()-entry.time>30000){
      if(!pending.has(group))pending.set(group,collect(group).then(data=>{cache.set(group,{time:Date.now(),data});return data;}).finally(()=>pending.delete(group)));
      await pending.get(group);entry=cache.get(group);
    }
    res.setHeader('Cache-Control','public, max-age=0, s-maxage=30');send(200,entry.data);
  }catch(e){res.setHeader('Cache-Control','no-store');send(503,{error:'Live quotes unavailable. Use the timestamped saved snapshot.',observedAt:new Date().toISOString()});}
};
