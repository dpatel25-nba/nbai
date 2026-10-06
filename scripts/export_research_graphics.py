"""Publication graphics from the same reviewed gallery snapshot (requires matplotlib)."""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Circle, Arc
R=Path(__file__).resolve().parents[1];D=json.loads((R/'web/visual-research.json').read_text());S=D['seasons'][-1];OUT=R/'web/research-graphics';OUT.mkdir(exist_ok=True)
BG='#f5f3ea';INK='#203e33';GREEN='#365d4c';ORANGE='#b45229';BLUE='#447a93'
plt.rcParams.update({'font.family':'DejaVu Sans','text.color':INK,'axes.labelcolor':INK,'xtick.color':INK,'ytick.color':INK,'font.size':12,'axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':'#a9b79f'})
def frame(title,sub):
 fig=plt.figure(figsize=(9,10),dpi=120,facecolor=BG)
 fig.text(.09,.955,'NBAI  /  THE RESEARCH ROOM',fontsize=11,weight='bold',color=GREEN)
 fig.text(.09,.897,title,fontsize=26,weight='bold');fig.text(.09,.859,sub,fontsize=11)
 return fig
for mode in ['impact','efficiency']:
 impact=mode=='impact';p=S['players'];xs=[x['off'] if impact else x['usage'] for x in p];ys=[x['defense'] if impact else x['ts']-S['leagueTS'] for x in p]
 fig=frame('Two sides of impact.' if impact else 'More work. Better shots?',f"{S['season']} regular season · {len(p)} players · Minimum {D['minMinutes']} minutes")
 ax=fig.add_axes([.11,.22,.81,.57],facecolor=BG);ax.grid(color='#dde2d5',zorder=0);ax.scatter(xs,ys,s=[20+x['minutes']/45 for x in p],c=GREEN,alpha=.55,edgecolors='none',zorder=3)
 ax.axhline(0,color='#879c83',ls='--',lw=1)
 if impact:ax.axvline(0,color='#879c83',ls='--',lw=1)
 leaders=sorted(range(len(p)),key=lambda i:p[i]['war'],reverse=True)[:5]
 for j,i in enumerate(leaders):
  ax.scatter([xs[i]],[ys[i]],s=75,c=ORANGE,zorder=4)
  ax.annotate(p[i]['name'],(xs[i],ys[i]),xytext=(-8,16 if j%2==0 else -23),textcoords='offset points',ha='right',fontsize=10,color=INK,bbox=dict(facecolor=BG,edgecolor='none',alpha=.8,pad=1),zorder=5)
 ax.set_xlabel('Offensive component · points / 100' if impact else 'Usage · %',labelpad=15);ax.set_ylabel('Defensive component · points / 100' if impact else 'True shooting vs. league · percentage points',labelpad=12);ax.margins(.13)
 fig.text(.09,.12,'Larger dots = more minutes. Orange dots = top five by historical WAR v4.',fontsize=10)
 fig.text(.09,.085,'Source: NBAI historical WAR v4 & archived NBA regular-season box scores.\nDescriptive research, not causal impact or a future-performance forecast.',fontsize=9,linespacing=1.7)
 fig.text(.09,.035,'nbai.space/insights.html',fontsize=11,weight='bold');fig.savefig(OUT/f'{mode}-{S["season"]}.png',facecolor=BG);plt.close(fig)
t=S['teams']['NYK'];fig=frame('Where the points live.',f"New York Knicks · {S['season']} regular season · {t['games']} games")
ax=fig.add_axes([.12,.24,.76,.57],facecolor=BG);ax.set_aspect('equal');lg={(c[0],c[1]):c for c in S['league']['cells']};maximum=max(c[2] for t2 in S['teams'].values() for c in t2['cells'])
for x,y,n,m in t['cells']:
 b=lg[(x,y)];enough=n>=D['cellMinAttempts'] and b[2]>=D['leagueCellMinAttempts'];delta=100*(m/n-b[3]/b[2]);color=ORANGE if enough and delta>3 else BLUE if enough and delta< -3 else '#b6bdb0'
 ax.scatter([(x+.5)*2.5],[min((y+.5)*2.5,46)],s=5+240*n/maximum,color=color,alpha=.85,zorder=2)
ax.add_patch(Rectangle((0,0),50,47,fill=False,ec='#87977d'));ax.add_patch(Rectangle((17,0),16,19,fill=False,ec='#87977d'));ax.add_patch(Circle((25,5.25),.75,fill=False,ec=INK));ax.plot([23,27],[4.25,4.25],color=INK,lw=1);ax.add_patch(Circle((25,19),6,fill=False,ec='#87977d',ls='--'));ax.plot([3,3],[0,14.22],color='#87977d');ax.plot([47,47],[0,14.22],color='#87977d');ax.add_patch(Arc((25,5.25),47.5,47.5,theta1=22.2,theta2=157.8,ec='#87977d'));ax.set(xlim=(-1,51),ylim=(-1,48));ax.axis('off')
fig.text(.09,.18,'Orange: > +3 pp vs. league    Blue: < −3 pp    Gray: near league / thin sample',fontsize=9)
fig.text(.09,.145,f"{t['attempts']:,} located attempts · {t['beyondHalfCourt']} beyond-half-court shots omitted\n2.5-ft bins · Color minimum: 20 team / 100 league attempts · Size = attempts",fontsize=10,linespacing=1.6)
fig.text(.09,.075,'Source: archived NBA play-by-play, aggregated by NBAI.\nSame-location FG% comparison. Historical results, not a forecast.',fontsize=9,linespacing=1.6);fig.text(.09,.035,'nbai.space/insights.html',fontsize=11,weight='bold');fig.savefig(OUT/f'nyk-shots-{S["season"]}.png',facecolor=BG);plt.close(fig)
print(json.dumps({'graphics':[p.name for p in OUT.glob('*.png')],'season':S['season']}))
