import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from season_impact import impact_profiles


class SeasonImpactTests(unittest.TestCase):
    def test_absence_carries_history_without_using_future_results(self):
        rows = [dict(PLAYER_ID=1, SEASON=s, BPM3=b) for s, b in
                [('2023-24', 1.7777), ('2024-25', 1.866318),
                 ('2026-27', -9), ('2027-28', 20)]]
        p = impact_profiles(rows, [1], '2026-27')[1]
        self.assertEqual(p['bpm'], 1.8663)
        self.assertEqual(p['impactSource'], 'historical_carry_forward')
        self.assertEqual(p['impactSeason'], '2024-25')
        self.assertEqual(p['impactSeasonGap'], 2)

    def test_finite_current_negative_rating_is_preserved(self):
        rows = [dict(PLAYER_ID=1, SEASON=s, BPM3=b) for s, b in
                [('2024-25', 5), ('2025-26', -2)]]
        p = impact_profiles(rows, [1], '2026-27')[1]
        self.assertEqual(p['bpm'], -2)
        self.assertEqual(p['impactSource'], 'previous_season')

    def test_invalid_recent_rating_uses_latest_finite_evidence(self):
        for invalid in [None, float('nan'), float('inf')]:
            rows = [dict(PLAYER_ID=1, SEASON=s, BPM3=b) for s, b in
                    [('2024-25', 2), ('2025-26', invalid)]]
            self.assertEqual(impact_profiles(rows, [1], '2026-27')[1]['bpm'], 2)

    def test_lookback_boundary_stale_and_unknown_are_distinct(self):
        rows = [dict(PLAYER_ID=1, SEASON='2023-24', BPM3=3),
                dict(PLAYER_ID=2, SEASON='2022-23', BPM3=8)]
        p = impact_profiles(rows, [1, 2, 3], '2026-27')
        self.assertEqual(p[1]['bpm'], 3)
        self.assertEqual(p[2]['bpm'], -1.5)
        self.assertEqual(p[2]['impactSource'], 'stale_history_prior')
        self.assertIsNone(p[2]['impactSeason'])
        self.assertEqual(p[2]['impactLastAvailableSeason'], '2022-23')
        self.assertEqual(p[3]['impactSource'], 'no_history_prior')
        self.assertIsNone(p[3]['impactSeasonGap'])

    def test_duplicate_evidence_fails_instead_of_silently_selecting(self):
        row = dict(PLAYER_ID=1, SEASON='2024-25', BPM3=1)
        with self.assertRaises(ValueError):
            impact_profiles([row, row], [1], '2026-27')


if __name__ == '__main__':
    unittest.main()
