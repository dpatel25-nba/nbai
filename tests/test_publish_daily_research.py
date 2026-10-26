import copy
from datetime import datetime,timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock
from tempfile import TemporaryDirectory
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from publish_daily_research import candidate, merge_reviews, parse_box, official_game_hold, official_report

NOW=datetime(2026,10,26,18,tzinfo=timezone.utc)

class DailyResearchTests(unittest.TestCase):
    def fixture(self):
        market={'stat':'PTS','threshold':19.5,'yesAsk':.40,'yesSize':10,'quoteValid':True,'observedAt':NOW.isoformat()}
        history={s:{'meanMinutes':30.,'stats':{'PTS':{'n':50,'counts':{'10':15,'25':35}},'FGA':{'n':50,'counts':{'15':50}}}} for s in ['2024-25','2025-26']}
        recent=[{'played':True,'minutes':30.,'stats':{'FGA':15}},{'played':True,'minutes':32.,'stats':{'FGA':15}}]
        return market,history,recent

    def test_exact_price_limit_and_two_seasons(self):
        m,h,r=self.fixture();result=candidate(m,{},h,r,'Not listed',NOW)
        self.assertEqual(result['maxPrice'],.60)
        m['yesAsk']=.61;self.assertIsNone(candidate(m,{},h,r,'Not listed',NOW))
        m['yesAsk']=.4;del h['2024-25'];self.assertIsNone(candidate(m,{},h,r,'Not listed',NOW))

    def test_unknown_dnp_changed_workload_and_injuries_withhold(self):
        for status in ['Out','Questionable','Doubtful','Unknown']:
            m,h,r=self.fixture();self.assertIsNone(candidate(m,{},h,r,status,NOW))
        for change in [lambda r:r[0].update(played=False),lambda r:r[0].update(played=None),
                       lambda r:r[0].update(minutes=12),lambda r:r[0].update(minutes=65)]:
            m,h,r=self.fixture();change(r);self.assertIsNone(candidate(m,{},h,r,'Not listed',NOW))

    def test_stale_future_thin_and_nonfinite_quotes(self):
        for change in [dict(observedAt='2026-10-25T18:00:00Z'),dict(observedAt='2026-10-27T18:00:00Z'),
                       dict(yesSize=0),dict(quoteValid=False),dict(yesAsk=float('nan')),dict(threshold=float('inf'))]:
            m,h,r=self.fixture();m.update(change);self.assertIsNone(candidate(m,{},h,r,'Not listed',NOW))

    def test_reduced_opportunity_cannot_reuse_old_scoring_role(self):
        m,h,r=self.fixture();r[0]['stats']['FGA']=4;r[1]['stats']['FGA']=6
        self.assertIsNone(candidate(m,{},h,r,'Not listed',NOW))

    def test_both_official_team_submissions_required(self):
        rows=[{'date':'2026-10-26','matchup':'A@B','team':name,'submitted':True} for name in ['Team A','Team B']]
        self.assertIsNone(official_game_hold({'submissions':rows},'2026-10-26','A','B','Team B','Team A'))
        rows[1]['submitted']=False
        self.assertIsNotNone(official_game_hold({'submissions':rows},'2026-10-26','A','B','Team B','Team A'))
        self.assertIsNotNone(official_game_hold({'submissions':rows[:1]},'2026-10-26','A','B','Team B','Team A'))

    def test_official_parser_dates_join_iso_schedule(self):
        submissions=[{'date':'10/26/2026','matchup':'A@B','team':name,'submitted':True} for name in ['Team A','Team B']]
        rows=[{'date':'10/26/2026','matchup':'A@B','team':'Team A','player':'Player A','status':'Out'}]
        parser=SimpleNamespace(parse_historical_report=lambda p:(SimpleNamespace(to_pydatetime=lambda:NOW),rows,submissions))
        response=MagicMock();response.__enter__.return_value.status=200
        response.__enter__.return_value.read.return_value=b'%PDF fixture'
        with TemporaryDirectory() as folder, patch.dict(sys.modules,{'historical_sources':parser}), patch('urllib.request.urlopen',return_value=response):
            report=official_report(Path(folder),NOW)
        self.assertEqual(report['rows'][0]['date'],'2026-10-26')
        self.assertIsNone(official_game_hold(report,'2026-10-26','A','B','Team B','Team A'))

    def test_closed_reviews_preserved_and_upcoming_replaced(self):
        old=[{'gameId':'old','startsAt':'2026-10-25T22:00:00Z','picks':['original']},
             {'gameId':'new','startsAt':'2026-10-26T23:00:00Z','picks':['earlier']}]
        before=copy.deepcopy(old)
        updates=[{**old[0],'picks':['hindsight']},{**old[1],'picks':['refreshed']}]
        merged=merge_reviews(old,updates,NOW)
        self.assertEqual(merged[0],before[0]);self.assertEqual(merged[1]['picks'],['refreshed']);self.assertEqual(old,before)
        with self.assertRaises(ValueError):merge_reviews(old+[old[0]],updates,NOW)

    def test_official_tip_not_delayed_provider_closes_archive(self):
        old={'gameId':'g','startsAt':'2026-10-26T18:10:00Z','officialStartsAt':NOW.isoformat(),'picks':['original']}
        self.assertEqual(merge_reviews([old],[{**old,'picks':['late']}],NOW),[old])

    def test_box_zero_and_unknown_not_treated_as_played(self):
        players=[{'athlete':{'displayName':'Example A'},'didNotPlay':True,'stats':[]},
                 {'athlete':{'displayName':'Example B'},'stats':['bad','20','5','4','3-5']}]
        box={'boxscore':{'players':[{'team':{'abbreviation':'NY'},'statistics':[{'labels':['MIN','PTS','REB','AST','3PT'],'athletes':players}]}]}}
        rows=parse_box(box)['NYK'];self.assertFalse(rows['examplea']['played']);self.assertIsNone(rows['exampleb']['played'])

if __name__=='__main__':unittest.main()
