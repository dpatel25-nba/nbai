// Native details remain usable without JavaScript; add familiar dismissal behavior.
document.addEventListener('keydown',event=>{
  if(event.key!=='Escape')return;
  document.querySelectorAll('.site-menu[open]').forEach(menu=>{menu.open=false;menu.querySelector('summary').focus();});
});
document.addEventListener('click',event=>{
  document.querySelectorAll('.site-menu[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;});
});
