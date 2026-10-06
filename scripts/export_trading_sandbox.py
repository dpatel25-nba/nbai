"""Publish a small allowlisted historical replay dataset, never account information."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/features/kalshi_history/backtest-runs/20261004-v1/report.json'


def build(source=SOURCE):
    raw = source.read_bytes()
    report = json.loads(raw)
    assert report['live_trading_authorized'] is False
    rows = []
    for row in report['rows']:
        if row['split'] != 'evaluation':
            continue
        quote = row.get('quote', {})
        item = {key: row.get(key) for key in (
            'ticker', 'game_id', 'date', 'home', 'away', 'status', 'reason',
            'p_yes', 'market_midpoint', 'settlement_yes', 'choice', 'cost_scenarios')}
        item['quote'] = {key: quote.get(key) for key in (
            'decision_ts', 'candle_end_ts', 'age_seconds', 'yes_bid_dollars',
            'yes_ask_dollars', 'available')}
        receipt = quote.get('receipt', {})
        item['source'] = {key: receipt[key] for key in ('url', 'sha256') if key in receipt}
        item['cost_scenarios'] = {
            key: value for key, value in (row.get('cost_scenarios') or {}).items()
            if key.startswith('quadratic_order_cent|slippage=')
        }
        rows.append(item)
    rows.sort(key=lambda row: (row['date'], row['quote']['decision_ts'] or 0, row['ticker']))
    if len({row['game_id'] for row in rows}) != len(rows):
        raise ValueError('Replay requires one contract per game')
    summary = report['summaries']['evaluation']
    return {
        'schema_version': 1, 'mode': 'historical_sandbox',
        'source_sha256': hashlib.sha256(raw).hexdigest(),
        'source_created_at': report['created_at'],
        'model': 'Fixed pregame Elo · research baseline v1',
        'live_trading_authorized': False,
        'coverage': {key: summary[key] for key in ('games', 'admitted_quotes', 'teams', 'first_date', 'last_date')},
        'baseline': summary['cost_scenarios']['quadratic_order_cent|slippage=0.01'],
        'limitations': report['limitations'], 'rows': rows,
    }


if __name__ == '__main__':
    data = build()
    output = ROOT / 'web/trading-replay.json'
    output.write_text(json.dumps(data, separators=(',', ':'), allow_nan=False) + '\n')
    print(f'Exported {len(data["rows"])} games across {len(data["coverage"]["teams"])} teams to {output.name}')
