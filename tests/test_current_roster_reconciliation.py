import copy
import hashlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from current_roster_reconciliation import reconcile
import game_availability


class CurrentRosterReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.canonical = dict(season='2026-27', teams={'CHA': dict(id=1610612766, name='Charlotte Hornets')},
                              players={str(i): dict(id=i, n=f'Player {i}', team='CHA', rookie=False)
                                       for i in range(1, 7)})
        self.rows = pd.DataFrame([dict(PLAYER_ID=i, ESPN_ID=str(i+100), PLAYER=f'Player {i}',
                                      TEAM_ID=1610612766, TEAM='CHA', SEASON='2026-27',
                                      IS_ROOKIE=False, DRAFT_SLOT=None) for i in range(1, 6)])
        self.team = dict(id=1610612766, roster=[dict(PLAYER_ID=i, PLAYER=f'Player {i}', TeamID=1610612766,
                                                   SEASON='2026') for i in range(1, 7)])
        self.calls = []

    def fetch(self, url, **kwargs):
        self.calls.append(url)
        body = ('<script id="__NEXT_DATA__" type="application/json">'+
                json.dumps({'props': {'pageProps': {'team': self.team}}})+'</script>').encode()
        return SimpleNamespace(content=body, status_code=200, url=url, headers={'Date': 'unverified'})

    def run_it(self, **kwargs):
        return reconcile(self.rows, self.canonical, self.path, fetch=kwargs.pop('fetch', self.fetch), **kwargs)

    def held(self, expected, **kwargs):
        out, audit = self.run_it(**kwargs)
        pd.testing.assert_frame_equal(out, self.rows)
        self.assertEqual(audit['heldTeams'], ['CHA'])
        self.assertIn(expected, audit['teams']['CHA']['reason'])
        self.assertEqual(audit['appendedPlayerIds'], [])

    def test_exact_subset_adds_unknown_membership_only_and_pins_raw_receipt(self):
        original = self.rows.copy(deep=True)
        out, audit = self.run_it()
        self.assertEqual(audit['heldTeams'], [])
        self.assertEqual(audit['appendedPlayerIds'], [6])
        self.assertEqual(out.iloc[-1].ESPN_ID, 'nba:6')
        self.assertEqual(out.iloc[-1].PLAYER, 'Player 6')
        self.assertFalse({'status', 'out', 'starter', 'available'} & set(out.columns))
        self.assertFalse(audit['certifiesHealth'])
        pd.testing.assert_frame_equal(original, self.rows)
        detail = audit['teams']['CHA']
        for field in ['source', 'receipt']:
            pin = detail[field]
            self.assertEqual(hashlib.sha256(Path(pin['path']).read_bytes()).hexdigest(), pin['sha256'])
        receipt = json.loads(Path(detail['receipt']['path']).read_text())
        self.assertEqual(receipt['sha256'], detail['source']['sha256'])
        self.assertIsNone(receipt['publishedAt'])
        self.assertLessEqual(receipt['requestedAt'], receipt['receivedAt'])

    def test_equal_set_no_fetch_or_append(self):
        self.canonical['players'].pop('6')
        out, audit = self.run_it()
        self.assertEqual(self.calls, [])
        pd.testing.assert_frame_equal(out, self.rows)
        self.assertEqual(audit['teams']['CHA']['status'], 'already_equal')

    def test_unknown_espn_identity_not_hidden(self):
        self.held('unresolved ESPN', unresolved={'CHA': [{'name': 'Unresolved'}]})
        self.assertEqual(self.calls, [])

    def test_espn_extra_is_preserved_and_held(self):
        self.rows.loc[0, 'PLAYER_ID'] = 999
        self.held('ESPN extras')
        self.assertEqual(self.calls, [])

    def test_empty_team_not_filled(self):
        self.rows = self.rows.iloc[:0]
        self.held('empty', unresolved={'CHA': []})

    def test_duplicate_player_rejected(self):
        self.rows.loc[1, 'PLAYER_ID'] = 1
        self.held('duplicate')

    def test_duplicate_espn_id_rejected(self):
        self.rows.loc[1, 'ESPN_ID'] = self.rows.loc[0, 'ESPN_ID']
        self.held('duplicate')

    def test_missing_or_previously_synthetic_espn_identity_held(self):
        for value in [None, '', 'nba:1']:
            with self.subTest(value=value):
                self.rows.loc[0, 'ESPN_ID'] = value
                self.held('invalid ESPN identity')

    def test_wrong_espn_team_and_season(self):
        for column, value in [('TEAM_ID', 1610612749), ('SEASON', '2025-26')]:
            with self.subTest(column=column):
                original = self.rows.copy()
                self.rows.loc[0, column] = value
                self.held('ESPN season or team')
                self.rows = original

    def test_official_changed_set_held(self):
        self.team['roster'][-1]['PLAYER_ID'] = 999
        self.held('player set mismatch')

    def test_official_changed_name_held(self):
        self.team['roster'][-1]['PLAYER'] = 'Another Name'
        self.held('name mismatch')

    def test_official_wrong_season_and_team_and_duplicates(self):
        for field, value in [('SEASON', '2025'), ('TeamID', 1610612749), ('PLAYER_ID', 1)]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as path:
                original = copy.deepcopy(self.team)
                self.path = Path(path)
                self.team['roster'][-1][field] = value
                self.held('official')
                self.team = original

    def test_http_failure_and_redirect_are_archived_not_admitted(self):
        for field, value in [('status_code', 403), ('url', 'https://example.org/roster')]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as path:
                self.path = Path(path)
                def fetch(url, **kwargs):
                    response = self.fetch(url, **kwargs)
                    setattr(response, field, value)
                    return response
                self.held('HTTP source', fetch=fetch)
                self.assertTrue((self.path/'CHA.receipt.json').exists())

    def test_transport_error_retains_all_original_rows(self):
        def fetch(*args, **kwargs):
            raise requests.Timeout('timeout')
        self.held('timeout', fetch=fetch)

    def test_existing_archive_cannot_be_reused_or_overwritten(self):
        (self.path/'CHA.html').write_bytes(b'previous raw observation')
        self.held('File exists')
        self.assertEqual((self.path/'CHA.html').read_bytes(), b'previous raw observation')

    def test_wrong_canonical_key_fails_closed(self):
        self.canonical['players']['6']['id'] = 7
        with self.assertRaisesRegex(ValueError, 'canonical identity'):
            self.run_it()

    def prepare_export_fixture(self):
        root = self.path
        self.canonical['teams']['MIL'] = dict(id=1610612749, name='Milwaukee Bucks')
        opponent = []
        for pid in range(101, 107):
            self.canonical['players'][str(pid)] = dict(id=pid, n=f'Player {pid}', team='MIL', rookie=False)
            opponent.append(dict(PLAYER_ID=pid, ESPN_ID=str(pid+100), PLAYER=f'Player {pid}',
                                 TEAM_ID=1610612749, TEAM='MIL', SEASON='2026-27',
                                 IS_ROOKIE=False, DRAFT_SLOT=None))
        self.rows = pd.concat([self.rows, pd.DataFrame(opponent)], ignore_index=True)
        (root/'web').mkdir()
        (root/'scripts').mkdir()
        (root/'web/season-data.js').write_text('window.NBAI_SEASON_DATA = '+json.dumps(self.canonical)+';\n')
        for name in ['game_availability.py', 'current_roster_reconciliation.py']:
            (root/'scripts'/name).write_bytes((Path(__file__).resolve().parents[1]/'scripts'/name).read_bytes())
        run = root/'run'
        run.mkdir()
        self.rows.to_parquet(run/'rosters.parquet', index=False)
        original = (run/'rosters.parquet').read_bytes()
        now = game_availability.P.utcnow().isoformat()
        event = dict(EVENT_ID='one', HOME_TEAM='CHA', AWAY_TEAM='MIL',
                     SEASON='2026-27', TIPOFF_UTC=now)
        return root, run, original, now, event

    def test_export_pins_effective_roster_and_keeps_original_with_unknown_status(self):
        root, run, original, now, event = self.prepare_export_fixture()
        with patch.object(game_availability.P, 'ROOT', root), patch('current_roster_reconciliation.requests.get', self.fetch):
            path = game_availability.export(run, [event], self.rows, {}, [], now, now, now)
        result = json.loads(Path(path).read_text())
        added = next(p for p in result['events'][0]['players'] if p['id'] == 6)
        self.assertEqual(added['status'], 'Unknown')
        self.assertFalse(added['out'])
        self.assertIsNone(result['events'][0]['holdReason'])
        self.assertEqual((run/'rosters.parquet').read_bytes(), original)
        self.assertNotIn('run/rosters.parquet', result['sourceHashes'])
        self.assertIn('run/current_roster/rosters.parquet', result['sourceHashes'])
        audit = json.loads((run/'roster-reconciliation.json').read_text())
        self.assertEqual(audit['originalRoster']['sha256'], hashlib.sha256(original).hexdigest())
        effective = pd.read_parquet(run/'current_roster/rosters.parquet')
        self.assertEqual(len(effective), 12)
        self.assertEqual(effective.groupby('TEAM').size().to_dict(), {'CHA': 6, 'MIL': 6})

    def test_export_preserves_prior_identity_hold_and_never_fills_around_it(self):
        root, run, original, now, event = self.prepare_export_fixture()
        exclusions = [dict(event='one', reason='unresolved injury identities')]
        with patch.object(game_availability.P, 'ROOT', root), patch('current_roster_reconciliation.requests.get', self.fetch):
            path = game_availability.export(run, [event], self.rows, {}, exclusions, now, now, now)
        result = json.loads(Path(path).read_text())
        self.assertTrue(result['events'][0]['holdReason'])
        self.assertEqual(result['rosterReconciliation']['appendedPlayerIds'], [])
        self.assertEqual(self.calls, [])
        self.assertEqual((run/'rosters.parquet').read_bytes(), original)
        self.assertEqual(len(result['events'][0]['players']), 11)

    def test_export_rejects_original_pin_that_differs_from_supplied_rows(self):
        root, run, original, now, event = self.prepare_export_fixture()
        self.rows.loc[0, 'PLAYER'] = 'Different identity name'
        with patch.object(game_availability.P, 'ROOT', root), patch('current_roster_reconciliation.requests.get', self.fetch):
            with self.assertRaisesRegex(ValueError, 'Original roster pin does not match'):
                game_availability.export(run, [event], self.rows, {}, [], now, now, now)
        self.assertEqual(self.calls, [])
        self.assertEqual((run/'rosters.parquet').read_bytes(), original)
        self.assertFalse((root/'web/game-availability.json').exists())

    def test_export_accepts_reordered_equivalent_original_rows_and_columns(self):
        root, run, original, now, event = self.prepare_export_fixture()
        self.rows = self.rows.iloc[::-1, ::-1].reset_index(drop=True)
        with patch.object(game_availability.P, 'ROOT', root), patch('current_roster_reconciliation.requests.get', self.fetch):
            path = game_availability.export(run, [event], self.rows, {}, [], now, now, now)
        result = json.loads(Path(path).read_text())
        self.assertEqual(result['rosterReconciliation']['appendedPlayerIds'], [6])
        self.assertEqual((run/'rosters.parquet').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
