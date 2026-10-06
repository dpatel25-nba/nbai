"""Keep the main static website navigation consistent; leaves page bodies intact."""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]/'web'
PAGES={'index.html':'home','insights.html':'research','player-value.html':'research','betting.html':'picks',
       'simulators.html':'simulators','game.html':'game','season.html':'season'}


def header(active):
    links=[]
    for key,url,label in [('home','index.html','Home'),('picks','betting.html','Game day'),('research','insights.html','Research')]:
        current=' aria-current="page"' if active==key else ''
        links.append(f'<a href="{url}"{current}>{label}</a>')
    menu=[]
    for key,url,label,note in [('simulators','simulators.html','Explore simulators','Choose where to start'),('game','game.html','Game simulator','One matchup, every detail'),('season','season.html','Season simulator','Your team, week by week')]:
        current=' aria-current="page"' if active==key else ''
        menu.append(f'<a href="{url}"{current}>{label}<small>{note}</small></a>')
    selected=' is-current' if active in ('simulators','game','season') else ''
    return ('<header class="site-header"><div class="site-header-inner"><a class="site-brand" href="index.html" aria-label="NBAI home"><span class="site-mark" aria-hidden="true">◉</span> NBAI</a>'
            '<nav class="site-nav" aria-label="Main navigation">'+''.join(links)+f'<details class="site-menu{selected}"><summary>Simulators</summary><div class="site-dropdown">'+''.join(menu)+'</div></details></nav></div></header>')


def main():
    for filename,active in PAGES.items():
        path=ROOT/filename;text=path.read_text()
        block='<!-- site-header:start -->'+header(active)+'<!-- site-header:end -->'
        if '<!-- site-header:start -->' in text:
            text=re.sub(r'<!-- site-header:start -->.*?<!-- site-header:end -->',lambda _:block,text,count=1,flags=re.S)
        else:
            text,count=re.subn(r'<header\b[^>]*>.*?</header>',lambda _:block,text,count=1,flags=re.S)
            assert count==1,filename
        if 'href="site.css"' not in text:
            insertion='<link rel="stylesheet" href="site.css"><script src="site.js" defer></script>\n'
            if '</head>' in text:text=text.replace('</head>',insertion+'</head>',1)
            else:text=text.replace('<!-- site-header:start -->',insertion+'<!-- site-header:start -->',1)
        if active in ('game','season') and 'class="sim-switch"' not in text:
            links='<nav class="sim-switch" aria-label="Simulator selection"><a href="simulators.html">← Simulators</a>'
            for key,url,label in [('game','game.html','Single game'),('season','season.html','Full season')]:
                current=' aria-current="page"' if key==active else ''
                links+=f'<a href="{url}"{current}>{label}</a>'
            text=text.replace('<!-- site-header:end -->','<!-- site-header:end -->\n'+links+'</nav>',1)
        path.write_text(text)
    print('Updated navigation on '+str(len(PAGES))+' product pages')


if __name__=='__main__':main()
