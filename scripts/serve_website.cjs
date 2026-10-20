// Local static preview plus the same read-only quote handler used on Vercel.
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../web'),handler=require('../web/api/kalshi.js');
const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml'};
http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname==='/api/kalshi')return handler(req,res);
  if(url.pathname==='/api/watchlist')return require('../web/api/watchlist.js')(req,res);
  if(url.pathname==='/api/kalshi-board')return require('../web/api/kalshi-board.js')(req,res);
  if(!['GET','HEAD'].includes(req.method)){res.writeHead(405);return res.end();}
  let file;try{file=path.resolve(root,'.'+decodeURIComponent(url.pathname==='/'?'/index.html':url.pathname));}catch(_){res.writeHead(400);return res.end();}
  if(!file.startsWith(root+path.sep)||file.split(path.sep).some(x=>x.startsWith('.'))){res.writeHead(403);return res.end();}
  fs.readFile(file,(err,body)=>{if(err){res.writeHead(404);return res.end();}res.setHeader('Content-Type',types[path.extname(file)]||'application/octet-stream');res.end(req.method==='HEAD'?undefined:body);});
}).listen(Number(process.env.NBAI_PREVIEW_PORT||8770),'127.0.0.1',()=>console.log('NBAI preview: http://127.0.0.1:'+(process.env.NBAI_PREVIEW_PORT||8770)));
