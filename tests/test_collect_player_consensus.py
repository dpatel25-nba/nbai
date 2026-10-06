import importlib.util,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'scripts/collect_player_consensus.py'
spec=importlib.util.spec_from_file_location('consensus',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class NormalizeTests(unittest.TestCase):
 def payload(self,key='fanduel',market='player_points',extra=None):
  outcomes=[dict(name=side,description='Jalen Brunson',point=25.5,price=-110) for side in ('Over','Under')]
  if extra:outcomes.append(extra)
  return dict(id='g',sport_key='basketball_nba',home_team='New York Knicks',away_team='Philadelphia 76ers',commence_time='2026-10-20T23:00:00Z',bookmakers=[dict(key=key,markets=[dict(key=market,last_update='2026-10-05T22:00:00Z',outcomes=outcomes)])])
 def test_main_line_and_no_dfs_synthetic_prices(self):
  receipt=dict(receivedAt='2026-10-05T22:00:10Z',url='https://api.the-odds-api.com/v4/test',sha256='a'*64)
  f=m.normalize(self.payload(),receipt);p=m.normalize(self.payload(key='prizepicks'),receipt)
  self.assertEqual(f[0]['line'],25.5);self.assertEqual(f[0]['americanOdds']['over'],-110);self.assertIsNone(p[0]['americanOdds']);self.assertEqual(f[0]['scope'],'game')
 def test_alternates_and_ambiguous_lines_excluded(self):
  self.assertEqual(m.normalize(self.payload(market='player_points_alternate'),{}),[])
  self.assertEqual(m.normalize(self.payload(extra=dict(name='Over',description='Jalen Brunson',point=30.5)),{}),[])

# Network behavior is tested against a synthetic transport; no API key or credits used.
import tempfile,json,os,contextlib,io
from unittest.mock import patch
class CollectionTests(unittest.TestCase):
 def response(self,payload,status=200,credits=0):
  from types import SimpleNamespace
  return SimpleNamespace(content=json.dumps(payload).encode(),status_code=status,ok=status==200,headers={'x-requests-last':str(credits),'x-requests-remaining':'90000'},json=lambda:payload)
 def run_scan(self,responses,max_events=60,max_credits=300):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  (self.root/'web').mkdir();(self.root/'web/player-consensus.json').write_text('{"previous":true}')
  self.calls=[]
  def fetch(url,**kw):
   self.calls.append((url,kw));return responses.pop(0)
  with patch.dict(os.environ,{'THE_ODDS_API_KEY':'test-secret-never-publish'}),contextlib.redirect_stdout(io.StringIO()):
   m.collect(max_events,max_credits,self.root,fetch)
 def event(self,sport='basketball_nba',id='g'):
  return dict(id=id,sport_key=sport,home_team='New York Knicks',away_team='Philadelphia 76ers',commence_time='2099-10-20T23:00:00Z')
 def test_full_scan_preserves_competition_and_expanded_sources(self):
  e=self.event();pre=self.event('basketball_nba_preseason','pre')
  raw=NormalizeTests().payload(key='fanatics');raw.update(e)
  self.run_scan([self.response([{'key':s,'active':True} for s in m.SPORTS]),self.response([e]),self.response([pre]),self.response(raw,credits=1),self.response({**pre,'bookmakers':[]})])
  x=json.loads((self.root/'web/player-consensus.json').read_text())
  self.assertEqual(x['coverage']['eventsChecked'],2);self.assertEqual(x['coverage']['providerQuoteCounts']['fanatics'],1)
  self.assertEqual(x['quotes'][0]['competition'],'Regular season');self.assertEqual(x['events'][1]['competition'],'Preseason')
  self.assertTrue(json.loads((self.root/'web/player-consensus-status.json').read_text())['success'])
  self.assertTrue(any('/basketball_nba_preseason/' in c[0] for c in self.calls))
  for f in self.root.rglob('*.json'):self.assertNotIn('test-secret-never-publish',f.read_text())
 def test_failure_preserves_snapshot_and_stops(self):
  with self.assertRaisesRegex(RuntimeError,'source failure'):
   self.run_scan([self.response([{'key':'basketball_nba','active':True}]),self.response([self.event(),self.event(id='h')]),self.response({'error':'test-secret-never-publish'},503)])
  self.assertEqual(len(self.calls),3);self.assertEqual(json.loads((self.root/'web/player-consensus.json').read_text()),{'previous':True})
  self.assertFalse(json.loads((self.root/'web/player-consensus-status.json').read_text())['success'])
  for f in self.root.rglob('*.json'):self.assertNotIn('test-secret-never-publish',f.read_text())
 def test_scan_limit_does_not_publish_partial_inventory(self):
  with self.assertRaisesRegex(RuntimeError,'scan limit'):
   self.run_scan([self.response([{'key':'basketball_nba','active':True}]),self.response([self.event(),self.event(id='h')])],max_events=1)
  self.assertEqual(len(self.calls),2);self.assertEqual(json.loads((self.root/'web/player-consensus.json').read_text()),{'previous':True})
 def test_credit_cap_does_not_publish_partial_snapshot(self):
  with self.assertRaisesRegex(RuntimeError,'credit limit'):
   self.run_scan([self.response([{'key':'basketball_nba','active':True}]),self.response([self.event(),self.event(id='h')]),self.response({**self.event(),'bookmakers':[]},credits=5)],max_credits=5)
  self.assertEqual(len(self.calls),3);self.assertEqual(json.loads((self.root/'web/player-consensus.json').read_text()),{'previous':True})
 def test_underdog_preseason_no_synthetic_prices(self):
  payload=NormalizeTests().payload(key='underdog');payload['sport_key']='basketball_nba_preseason'
  r=m.normalize(payload,dict(receivedAt='2026-10-05T22:00:10Z',url='https://api.the-odds-api.com/v4/test',sha256='a'*64))
  self.assertEqual(r[0]['competition'],'Preseason');self.assertIsNone(r[0]['americanOdds'])

if __name__=='__main__':unittest.main()
