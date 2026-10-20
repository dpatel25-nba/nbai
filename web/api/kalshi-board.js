// Public market observations only. No credentials, orders, RFQs or arbitrary URLs.
const {collect}=require('../lib/kalshi-board.cjs');
let cached=null,pending=null;
module.exports=async(req,res)=>{
 const send=(code,data)=>{res.statusCode=code;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));};
 if(req.method!=='GET'){res.setHeader('Allow','GET');return send(405,{error:'GET only'});}
 if(new URL(req.url,'https://nbai.space').search)return send(400,{error:'No query parameters supported'});
 try{
  if(!cached||Date.now()-cached.time>20000){
   if(!pending)pending=collect().then(data=>{cached={time:Date.now(),data};}).finally(()=>{pending=null;});
   await pending;
  }
  res.setHeader('Cache-Control','public, max-age=0, s-maxage=20');
  return send(200,cached.data);
 }catch(_){res.setHeader('Cache-Control','no-store');return send(503,{error:'Kalshi observations unavailable',markets:[]});}
};
