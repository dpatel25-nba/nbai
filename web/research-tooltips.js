/* Shared chart inspection: mouse, touch and keyboard, with one visible tooltip. */
(()=>{'use strict';
let active=null;
function hide(){if(!active)return;active.tip.hidden=true;active.mark.classList.remove('is-hovered');active.mark.removeAttribute('aria-describedby');active=null;}
function attach(hostId,selector,describe,{tooltipId=hostId+'Tooltip',readoutId=null}={}){
 const host=document.getElementById(hostId);if(!host)return;
 hide();host.querySelector('.research-point-tooltip')?.remove();
 const tip=document.createElement('div');tip.id=tooltipId;tip.className='research-point-tooltip';tip.setAttribute('role','tooltip');tip.hidden=true;host.append(tip);
 host.querySelector('svg')?.setAttribute('role','group');
 for(const [index,mark] of [...host.querySelectorAll(selector)].entries()){
  const info=describe(mark);if(!info)continue;
  const label=[info.title,...info.lines].join(' · '),priorClick=mark.onclick;
  mark.dataset.tooltipKey=mark.dataset.player||mark.dataset.studyPoint||mark.dataset.scorer||mark.dataset.cell||mark.dataset.zone||String(index);
  mark.querySelector('title')?.remove();mark.classList.add('research-chart-point');mark.setAttribute('tabindex','0');mark.setAttribute('role','button');mark.setAttribute('aria-label',label);
  function position(event){const box=mark.getBoundingClientRect(),x=event?.clientX??box.right,y=event?.clientY??box.top;
   tip.style.left=Math.max(8,Math.min(x+14,innerWidth-tip.offsetWidth-8))+'px';
   tip.style.top=Math.max(8,Math.min(y+14+tip.offsetHeight>innerHeight?y-tip.offsetHeight-12:y+14,innerHeight-tip.offsetHeight-8))+'px';
  }
  function show(event){if(!mark.isConnected)return;if(active?.mark!==mark)hide();
   tip.replaceChildren();for(const [i,value] of [info.title,...info.lines].entries()){const line=document.createElement(i?'span':'strong');line.textContent=value;tip.append(line);}
   tip.hidden=false;mark.classList.add('is-hovered');mark.setAttribute('aria-describedby',tip.id);active={tip,mark,anchor:mark.getBoundingClientRect()};position(event);
   if(readoutId)document.getElementById(readoutId).textContent=label;
  }
  const dismiss=()=>{if(active?.mark===mark)hide();};
  mark.onpointerenter=show;mark.onpointermove=show;mark.onpointerleave=dismiss;mark.onfocus=()=>show();mark.onblur=dismiss;
  mark.onclick=event=>{priorClick?.call(mark,event);
   if(mark.isConnected)show(event);
   else {const replacement=[...host.querySelectorAll(selector)].find(point=>point.dataset.tooltipKey===mark.dataset.tooltipKey);replacement?.focus({preventScroll:true});}
  };
  mark.onkeydown=event=>{if(event.key==='Escape'){event.preventDefault();dismiss();}else if(event.key==='Enter'||event.key===' '){event.preventDefault();mark.onclick();}};
 }
}
for(const event of ['wheel','touchmove'])window.addEventListener(event,hide,{capture:true,passive:true});
window.addEventListener('scroll',()=>{if(!active)return;const box=active.mark.getBoundingClientRect();if(Math.abs(box.top-active.anchor.top)>.5||Math.abs(box.left-active.anchor.left)>.5)hide();},{capture:true,passive:true});
window.addEventListener('resize',hide);
document.addEventListener('keydown',event=>{if(event.key==='Escape')hide();});
document.addEventListener('pointerdown',event=>{if(active&&!active.mark.contains(event.target))hide();});
window.NBAI_CHART_TOOLTIPS={attach,hide};
})();
