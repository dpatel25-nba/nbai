"""Resumable public-source retrieval and layout-aware historical injury parsing."""
import hashlib
import json
import re
import time
import threading
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request
from types import SimpleNamespace

import fitz
import pandas as pd
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/features/league_source_backfill_2026-09-20'
SLOTS=['09AM','11AM','01PM','05PM','08PM']
STATUSES={'Out','Available','Questionable','Probable','Doubtful'}
_NETWORK=threading.local()
_REQUEST_LOCK=threading.Lock()
_LAST_REQUEST=0.


@contextmanager
def session_open(request,timeout):
    # Each worker reuses its own TLS connection; concurrency remains capped.
    global _LAST_REQUEST
    with _REQUEST_LOCK:
        time.sleep(max(0.,.5-(time.monotonic()-_LAST_REQUEST)))
        _LAST_REQUEST=time.monotonic()
    if not hasattr(_NETWORK,'session'):_NETWORK.session=requests.Session()
    with _NETWORK.session.get(request.full_url,headers=dict(request.header_items()),timeout=timeout,stream=True) as response:
        if response.status_code>=400:raise HTTPError(request.full_url,response.status_code,response.reason,response.headers,None)
        yield SimpleNamespace(status=response.status_code,geturl=lambda:response.url,
            read=lambda limit:response.raw.read(limit,decode_content=True))


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,indent=2));tmp.replace(path)


def report_urls(day,slot):
    old=slot;new=slot[:2]+'_00'+slot[2:]
    suffixes=[new,old] if day>='2026-01-01' else [old,new]
    return [f'https://ak-static.cms.nba.com/referee/injury/Injury-Report_{day}_{s}.pdf' for s in suffixes]


def retrieve(url,raw,receipts,retry_failed=False,opener=None):
    """Each URL has a durable success/failure receipt; bytes and provenance agree."""
    raw=Path(raw);receipts=Path(receipts);raw.mkdir(parents=True,exist_ok=True);receipts.mkdir(parents=True,exist_ok=True)
    name=url.rsplit('/',1)[-1];path=raw/name;mp=path.with_suffix('.json')
    rp=receipts/(hashlib.sha256(url.encode()).hexdigest()+'.json')
    if path.exists() and mp.exists():
        meta=json.loads(mp.read_text())
        if meta['url']!=url or meta['sha256']!=sha(path):raise ValueError('cached source checksum/provenance mismatch')
        return dict(meta,status='cached',path=str(path))
    if rp.exists() and not retry_failed:
        receipt=json.loads(rp.read_text())
        if receipt['url']!=url:raise ValueError('receipt URL mismatch')
        if receipt['status']!='downloaded':
            if receipt.get('http_status') in {401,403,429}:receipt['status']='access_denied'
            return dict(receipt,resumed_failure=True)
    receipt=dict(url=url,retrieved_at=datetime.now(timezone.utc).isoformat())
    try:
        with (opener or session_open)(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=20) as response:
            body=response.read(20*1024*1024+1)
            receipt.update(final_url=response.geturl(),http_status=response.status)
        if len(body)>20*1024*1024:raise ValueError('document exceeds size limit')
        if not body.startswith(b'%PDF'):raise ValueError('response is not a PDF')
        # A redirected current report cannot impersonate a historical URL.
        if receipt['final_url'].rsplit('/',1)[-1]!=name:raise ValueError('redirect changed document identity')
        receipt.update(status='downloaded',sha256=hashlib.sha256(body).hexdigest(),bytes=len(body))
        tmp=path.with_suffix('.pdf.tmp');tmp.write_bytes(body);tmp.replace(path)
        atomic_json(mp,receipt)
    except HTTPError as error:receipt.update(status='access_denied' if error.code in {401,403,429} else 'http_error',http_status=error.code,error=str(error))
    except Exception as error:receipt.update(status='error',error=str(error))
    atomic_json(rp,receipt)
    time.sleep(.03)
    return dict(receipt,path=str(path) if receipt['status']=='downloaded' else None)


def parse_historical_report(path):
    """Use displayed PDF coordinates for modern, rotated, and category layouts.

    Rotated PDFs start their first data row at y=77, above the modern parser's
    old cutoff. Headers define the columns, including 2019's previous status
    column which must never be confused with current status.
    """
    with fitz.open(path) as doc:
        header=' '.join(doc[0].get_text().split())
        match=re.search(r'Injury Report: (\d{2}/\d{2}/\d{2}) (\d{2}:\d{2}) (AM|PM)',header)
        if not match:raise ValueError('missing injury report timestamp')
        stamp=pd.to_datetime(' '.join(match.groups()),format='%m/%d/%y %I:%M %p').tz_localize('America/New_York')
        labels=['Game Date','Game Time','Matchup','Team','Player Name','Current Status','Reason','Category','Previous Status']
        columns={};output=[];submitted={};day=tip=game=team=None;previous_bottom=None
        for page_no,page in enumerate(doc,1):
            if abs(page.rect.width-842)>2:raise ValueError('unknown displayed PDF geometry')
            lines=defaultdict(list);heading_y=None
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines',[]):
                    box=fitz.Rect(line['bbox'])*page.rotation_matrix
                    text=' '.join(''.join(s['text'] for s in line['spans']).split())
                    if not text:continue
                    if text in labels:
                        if page_no==1:columns[text]=box.x0
                        if text=='Game Date':heading_y=box.y0
                    lines[round(box.y0,1)].append((box.x0,text))
            if page_no==1:
                if not set(labels[:7]).issubset(columns):raise ValueError('unrecognized injury report columns')
            starts=sorted((x-3,name) for name,x in columns.items());anchors=[];items=[]
            lower=heading_y+10 if heading_y is not None else (35 if page.rotation==90 else 70)
            for y,parts in sorted(lines.items()):
                if y<lower or y>page.rect.height-25:continue
                fields=defaultdict(list)
                for x,text in sorted(parts):
                    if re.fullmatch(r'Page \d+ of \d+',text):continue
                    possible=[name for start,name in starts if start<=x]
                    if possible:fields[possible[-1]].append(text)
                f={k:' '.join(v) for k,v in fields.items()}
                if f.get('Game Date')=='Game Date':continue
                if not f:continue
                status=f.get('Current Status','');unsubmitted='NOT YET SUBMITTED' in ' '.join(f.values())
                all_available='ALL PLAYERS AVAILABLE' in ' '.join(f.values())
                if status in STATUSES or unsubmitted or all_available:
                    anchors.append(dict(y=y,fields=defaultdict(list),unsubmitted=unsubmitted,all_available=all_available))
                elif status:raise ValueError('unknown current injury status: '+status)
                items.append((y,f))
            # Team/player names can wrap around a vertically centered status.
            # Gather their fragments into the row before carrying team context.
            for y,f in items:
                if not anchors:raise ValueError('report text without current row anchors')
                nearest=min(anchors,key=lambda a:abs(a['y']-y))
                if y<min(a['y'] for a in anchors)-14 and previous_bottom is not None and y<95:
                    if set(f)-{'Reason'}:raise ValueError('ambiguous page continuation')
                    previous_bottom['reason']+=' '+f['Reason'];continue
                for field,text in f.items():nearest['fields'][field].append(text)
            for anchor in anchors:
                f={k:' '.join(v) for k,v in anchor['fields'].items()}
                date=f.get('Game Date','');tim=f.get('Game Time','');matchup=f.get('Matchup','');tm=f.get('Team','')
                if date:
                    if not re.fullmatch(r'\d{2}/\d{2}/\d{4}',date):raise ValueError('ambiguous game date cell')
                    day=date
                if tim:
                    if not re.fullmatch(r'\d{2}:\d{2} \(ET\)',tim):raise ValueError('ambiguous game time cell')
                    tip=tim
                if '@' in matchup:game=matchup;team=None
                if tm:team=tm
                name=f.get('Player Name','');status=f.get('Current Status','')
                unsubmitted=anchor['unsubmitted'];all_available=anchor['all_available']
                if status in STATUSES or unsubmitted or all_available:
                    if not day or not game or not team or not tip:raise ValueError('injury row lacks dated team context')
                    key=(day,game,team)
                    submitted[key]=dict(date=day,matchup=game,team=team,reported_time=tip,submitted=not unsubmitted)
                    if unsubmitted or all_available:
                        marker='NOT YET SUBMITTED' if unsubmitted else 'ALL PLAYERS AVAILABLE'
                        leftover=' '.join(f.get(k,'') for k in ['Player Name','Current Status','Category','Reason']).replace(marker,'').strip()
                        if leftover:raise ValueError('extra text assigned to team-level report status')
                        if all_available:submitted[key]['all_players_available']=True
                        anchor['row']=None;continue
                    if ',' not in name:raise ValueError('player identity missing from status row')
                    family,first=name.split(',',1)
                    row=dict(date=day,matchup=game,team=team,reported_time=tip,player=first.strip()+' '+family.strip(),
                        status=status,reason=' '.join(f.get(k,'') for k in ['Category','Reason']).strip(),page=page_no)
                    output.append(row);anchor['row']=row
                elif name and status:raise ValueError('unknown current injury status: '+status)
            actual=[a for a in anchors if a['row'] is not None]
            if any(not a['row']['reason'].strip() for a in actual):raise ValueError('empty injury reason')
            previous_bottom=actual[-1]['row'] if actual and actual[-1]['y']>page.rect.height-110 and anchors[-1]['row'] is not None else None
        for row in output:row['reason']=' '.join(row['reason'].split())
        keys=[(r['date'],r['matchup'],r['team'],r['player']) for r in output]
        if len(set(keys))!=len(keys):raise ValueError('duplicate reported player')
        return stamp,output,list(submitted.values())


def choose_snapshot(candidates,day,lead=60):
    """Choose by pregame evidence only, with disagreements and gaps explicit."""
    if not candidates:return None,'no_parsed_report_for_case'
    times={c['reported_time'] for c in candidates}
    if len(times)!=1:return None,'reported_tip_times_disagree'
    match=re.fullmatch(r'(\d{2}):(\d{2}) \(ET\)',next(iter(times)))
    if not match:return None,'unresolved_reported_tip'
    hour,minute=map(int,match.groups())
    if not 1<=hour<=23 or not 0<=minute<60:return None,'invalid_reported_tip'
    # NBA reports use the noon/evening ET clock. Mark the interpretation in the
    # coverage output; no independent archived schedule-time proof is claimed.
    hour=hour+12 if hour<12 else hour
    tip=pd.Timestamp(day).tz_localize('America/New_York')+pd.Timedelta(hours=hour,minutes=minute)
    cutoff=tip-pd.Timedelta(minutes=lead)
    timely=[c for c in candidates if pd.Timestamp(c['report_at'])<=cutoff and pd.Timestamp(c['document_latest_at'])<=cutoff]
    if not timely:return None,'no_snapshot_before_cutoff'
    ready=[c for c in timely if c['submitted']]
    if not ready:return None,'team_not_submitted_before_cutoff'
    chosen=max(ready,key=lambda c:(c['report_at'],c['document_latest_at'],c['file']))
    return dict(chosen,cutoff=cutoff.isoformat(),tip_bound=tip.isoformat()),'accepted'
