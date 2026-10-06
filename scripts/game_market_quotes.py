"""Normalize two-sided main game markets; preserve prices, timestamps and source receipts."""
import math
from datetime import datetime

def normalize_game(event,receipt,books):
    rows=[]
    for book in event.get('bookmakers',[]):
        if book.get('key') not in books or book['key'] in ('prizepicks','underdog'):continue
        for market in book.get('markets',[]):
            kind=market.get('key');outcomes=market.get('outcomes',[])
            if kind not in ('h2h','spreads','totals') or len(outcomes)!=2:continue
            names=['Over','Under'] if kind=='totals' else [event['home_team'],event['away_team']]
            if {o.get('name') for o in outcomes}!=set(names):continue
            pair={o['name']:o for o in outcomes}
            if any(type(o.get('price')) not in (int,float) or not math.isfinite(o['price']) or abs(o['price'])<100 for o in outcomes):continue
            if kind!='h2h':
                if any(type(o.get('point')) not in (int,float) or not math.isfinite(o['point']) for o in outcomes):continue
                if kind=='spreads' and abs(sum(o['point'] for o in outcomes))>1e-8:continue
                if kind=='totals' and (outcomes[0]['point']!=outcomes[1]['point'] or outcomes[0]['point']<=0):continue
            updated=market.get('last_update')
            try:
                if not updated or datetime.fromisoformat(updated.replace('Z','+00:00')).tzinfo is None:continue
            except ValueError:continue
            rows.append(dict(eventId=event['id'],sportKey=event['sport_key'],scope='game',homeTeam=event['home_team'],awayTeam=event['away_team'],startsAt=event['commence_time'],provider=book['key'],providerName=books[book['key']],market=kind,outcomes=outcomes,observedAt=receipt['receivedAt'],sourceUpdatedAt=updated,sourceUrl=receipt['url'],receiptSha256=receipt['sha256']))
    return rows
