import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from game_market_quotes import normalize_game
class GameMarkets(unittest.TestCase):
 def normalize(self,key,outcomes):
  event=dict(id='g',sport_key='basketball_nba',home_team='Home',away_team='Away',commence_time='2099-01-01T00:00:00Z',bookmakers=[dict(key='fanduel',markets=[dict(key=key,last_update='2026-10-06T00:00:00Z',outcomes=outcomes)])])
  return normalize_game(event,dict(receivedAt='2026-10-06T00:01:00Z',url='https://example.test',sha256='receipt'),{'fanduel':'FanDuel'})
 def test_spread_sign_and_prices_preserved(self):
  r=self.normalize('spreads',[dict(name='Home',point=-3.5,price=-110),dict(name='Away',point=3.5,price=-105)])
  self.assertEqual(r[0]['outcomes'][0]['point'],-3.5);self.assertEqual(r[0]['outcomes'][1]['price'],-105)
 def test_ambiguous_or_invalid_markets_excluded(self):
  self.assertEqual(self.normalize('spreads',[dict(name='Home',point=-3.5,price=-110),dict(name='Away',point=4.5,price=-105)]),[])
  self.assertEqual(self.normalize('totals',[dict(name='Over',point=220.5,price=-110),dict(name='Under',point=221.5,price=-110)]),[])
  self.assertEqual(self.normalize('h2h',[dict(name='Home',price=-180),dict(name='Draw',price=300)]),[])
if __name__=='__main__':unittest.main()
