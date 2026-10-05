// Poll only while visible; retain original observation times when refresh fails.
(function(root){
  function watch(group,snapshot,onUpdate){
    let data=null,busy=false,timer;
    async function refresh(){
      if(busy||document.hidden)return;busy=true;
      try{
        const r=await fetch('/api/kalshi?group='+group,{cache:'no-store',signal:AbortSignal.timeout(25000)});
        if(!r.ok)throw Error('Unavailable');const next=await r.json();
        if(next.schemaVersion!==1||next.group!==group||!Array.isArray(next.markets)||!next.coverage?.length||next.coverage.some(c=>!c.complete))throw Error('Incomplete');
        data=next;onUpdate(data,true);
      }catch(_){
        if(!data){try{const r=await fetch(snapshot,{cache:'no-store'});if(!r.ok)throw Error();data=await r.json();if(data.schemaVersion!==1||!Array.isArray(data.markets))data=null;}catch(_){data=null;}}
        onUpdate(data,false);
      }finally{busy=false;clearTimeout(timer);timer=setTimeout(refresh,60000);}
    }
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});refresh();return refresh;
  }
  root.NBAI_QUOTES={watch};
})(window);
