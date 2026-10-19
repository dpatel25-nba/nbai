import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from game_availability import build


class GameAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 19, 15, tzinfo=timezone.utc)
        self.stamp = '2026-10-19T14:00:00Z'
        self.event = dict(EVENT_ID='one', HOME_TEAM='BOS', AWAY_TEAM='DET',
                          TIPOFF_UTC='2026-10-20T19:00:00Z', SEASON='2026-27')
        self.rosters = pd.DataFrame([dict(PLAYER_ID=1, PLAYER='Example', TEAM='BOS', SEASON='2026-27')])

    def result(self, **kwargs):
        args = dict(events=[self.event], rosters=self.rosters, statuses={1:dict(status='Out',out=True)},
                    exclusions=[], roster_at=self.stamp, injury_at=self.stamp,
                    provider_at=self.stamp, sources={'source':'hash'}, now=self.now)
        args.update(kwargs)
        return build(**args)

    def test_out_and_missing_status_are_distinct_and_expiry_uses_observation(self):
        out = self.result()
        self.assertEqual(out['validUntil'], '2026-10-19T20:00:00+00:00')
        self.assertTrue(out['events'][0]['players'][0]['out'])
        row = self.result(statuses={})['events'][0]['players'][0]
        self.assertEqual(row['status'], 'Not listed')
        self.assertFalse(row['out'])

    def test_stale_future_and_naive_observations_rejected(self):
        for stamp in ['2026-10-19T08:00:00Z','2026-10-19T16:00:00Z','2026-10-19T14:00:00']:
            with self.assertRaises(ValueError):self.result(roster_at=stamp)
        with self.assertRaises(ValueError):self.result(provider_at='2026-10-17T14:00:00Z')

    def test_exclusions_and_wrong_season_remain_holds(self):
        self.assertEqual(self.result(exclusions=[dict(event='one',reason='Unknown injury')])['events'][0]['holdReason'],'Unknown injury')
        self.rosters['SEASON']='2025-26'
        self.assertIn('season',self.result()['events'][0]['holdReason'])


if __name__ == '__main__':unittest.main()
