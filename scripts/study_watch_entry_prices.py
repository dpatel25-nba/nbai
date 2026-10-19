"""Observed full-depth price opportunity replay. No inferred fills or ROI."""
import json,sqlite3,hashlib
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run():
 groups=defaultdict(list);receipts=[];unversioned=0
 for version in ('v1','v2'):
  file=ROOT/f'data/raw/kalshi/watchlist/20261020-{version}/observations.sqlite3'
  if not file.exists():continue
  db=sqlite3.connect('file:'+str(file)+'?mode=ro',uri=True)
  for at,ident,body in db.execute('SELECT at,id,body FROM observations ORDER BY at'):
   row=json.loads(body);f=row.get('forecast');q=row.get('quote')
   if not f or not q:continue
   model=f.get('input_hashes',{}).get('web/player-game-projections.json')
   if not model:unversioned+=1;continue
   for n,c in q.get('cost_scenarios',{}).items():
    if c:groups[(version,ident,model,n)].append((at,c['cost_per_contract']))
   receipts.append(hashlib.sha256(body.encode()).hexdigest())
  db.close()
 rows=[]
 for (version,ident,model,n),points in groups.items():
  if len(points)<3:continue
  first=points[0];limit=first[1]-.02;trigger=next((i for i,p in enumerate(points[1:],1) if p[1]<=limit),None)
  confirmation=None if trigger is None or trigger+1>=len(points) else points[trigger+1]
  confirmed=confirmation is not None and confirmation[0]-points[trigger][0]<=180 and confirmation[1]<=limit
  rows.append(dict(version=version,id=ident,model_sha256=model,quantity=int(n),observations=len(points),initialUnitCost=first[1],triggerAt=None if trigger is None else points[trigger][0],confirmedNextObservedQuote=confirmed,observedSaving=None if not confirmed else first[1]-confirmation[1],actualFills=0))
 report=dict(classification='Exploratory observed-price comparison; fee applicability unverified and no execution inference',policy=dict(anchor='First observation within each contract/model-version/quantity segment',dip=.02,confirmation='Next observed full-depth quote within 180 seconds must remain at least 2 cents cheaper',segmentation='Never join observations across changed model hashes',selection='All recorded fixed watch targets; no return-based selection'),observation_digest=hashlib.sha256(''.join(receipts).encode()).hexdigest(),segments=len(rows),confirmedSegments=sum(r['confirmedNextObservedQuote'] for r in rows),results=rows,limitations=['Two-minute polling misses subsecond moves and queues.','The scheduled prospective timing trial remains a separate frozen experiment.','No settled outcomes, actual fills or profitability claim.'])
 report['excludedUnversionedRows']=unversioned
 out=ROOT/'data/features/game_stability/20261019-v1/entry-price-study.json';out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:report[k] for k in ['segments','confirmedSegments','classification']}))
if __name__=='__main__':run()
