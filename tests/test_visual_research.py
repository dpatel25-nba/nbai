import importlib.util,json,unittest
from pathlib import Path
import pandas as pd
R=Path(__file__).resolve().parents[1];spec=importlib.util.spec_from_file_location('builder',R/'scripts/build_visual_research.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
class ResearchTests(unittest.TestCase):
 def test_coordinate_exclusions_and_zones(self):
  rows=[]
  for i,(x,y,dist,value,result) in enumerate([(0,10,1,2,'Made'),(230,0,23,3,'Missed'),(0,250,25,3,'Made'),(0,600,60,3,'Missed'),(None,0,1,2,'Made')]):
   rows.append(dict(GAME_ID='g',ACTION_NUMBER=i,SEASON_TYPE='Regular Season',IS_FIELD_GOAL=1,SHOT_RESULT=result,SHOT_VALUE=value,SHOT_X=x,SHOT_Y=y,SHOT_DISTANCE=dist,TEAM_TRICODE='NYK'))
  out=builder.aggregate_shots(pd.DataFrame(rows));t=out['teams']['NYK'];self.assertEqual(t['attempts'],4);self.assertEqual(t['excludedCoordinates'],1);self.assertEqual(t['beyondHalfCourt'],1);self.assertEqual(sum(c[2] for c in t['cells']),3);self.assertEqual(t['zones']['corner3']['attempts'],1);self.assertEqual(t['zones']['arc3']['attempts'],2)
  with self.assertRaises(ValueError):builder.aggregate_shots(pd.DataFrame(rows+[rows[0]]))
 def test_export_reconciles_league_and_team_counts(self):
  d=json.loads((R/'web/visual-research.json').read_text());self.assertEqual(len(d['seasons']),3)
  for s in d['seasons']:
   self.assertEqual(len(s['teams']),30);self.assertEqual(s['league']['games'],s['expectedGames']);self.assertEqual(s['league']['attempts'],sum(t['attempts'] for t in s['teams'].values()));self.assertEqual(len(s['players']),len({p['id'] for p in s['players']}));self.assertTrue(all(p['minutes']>=500 for p in s['players']))
   for t in s['teams'].values():
    self.assertEqual(t['games'],82);self.assertEqual(t['attempts'],sum(c[2] for c in t['cells'])+t['beyondHalfCourt']);self.assertEqual(t['attempts'],sum(z['attempts'] for z in t['zones'].values()));self.assertEqual(t['made'],sum(z['made'] for z in t['zones'].values()));self.assertTrue(all(0<=c[3]<=c[2] for c in t['cells']))
 def test_export_matches_archived_metrics(self):
  data=json.loads((R/'web/visual-research.json').read_text());war=pd.read_parquet(R/'data/parquet/player_seasons_war_v4.parquet');ps=pd.read_parquet(R/'data/parquet/player_seasons.parquet')
  for s in data['seasons']:
   rows=war[war.SEASON==s['season']].set_index('PLAYER_ID');base=ps[ps.SEASON==s['season']].set_index('PLAYER_ID')
   for p in s['players']:
    self.assertAlmostEqual(p['off'],rows.loc[p['id'],'OBPM4'],places=3);self.assertAlmostEqual(p['defense'],rows.loc[p['id'],'DBPM4'],places=3)
    r=base.loc[p['id']];self.assertAlmostEqual(p['ts'],100*r.points/(2*(r.fieldGoalsAttempted+.44*r.freeThrowsAttempted)),places=3)
if __name__=='__main__':unittest.main()
