import copy,json,tempfile,unittest
from pathlib import Path
from test_shared_sales_release import fixture,html_for
from scorecard_rules import aggregate_post_call
from shared_manager import derive_manager,validate_manager
from shared_sales_release import stage_release,accept_release,verify_release


def three():
    rep,mtd,c=fixture();c['mtd']['channels']=[{'channel':'Test','occurred_calls':2}]
    for i,row in enumerate(c['occurred_rows']):row['qualified_contribution']=i==0
    c['scheduled_rows']=[*c['occurred_rows'],{'meeting_id':'unresolved','lead_id':'pending','owner':'Scott Seymour'}]
    for o in c['opportunities']:o['reporting_revenue']=50
    post = aggregate_post_call([{'state': 'missed', 'recap_done': True, 'next_step_done': False}])
    rep['month_label']='November 2026';rep['reps'][0]['adherence']={'post_call_12h':post}
    rep['reps'][0]['post_call_12h']=post
    base={'reps':[{'name':'Scott Seymour','lane':1},{'name':'Eric Piccione','lane':1}],'targets':{'booked':15,'close_rate':20,'post_call_adherence':90}}
    manager=derive_manager(base,rep,c,{});return rep,mtd,c,manager


def manager_html(data):return '<body><script type="application/json" id="shared-dashboard-data">'+json.dumps(data)+'</script></body>'


class ThreeDashboardReleaseTests(unittest.TestCase):
    def test_three_consumers_share_one_verified_release(self):
        rep,mtd,c,manager=three();self.assertTrue(validate_manager(manager,rep,c))
        self.assertEqual(manager['targets']['booked'],15)
        self.assertEqual(manager['targets']['close_rate'],20)
        self.assertEqual(manager['targets']['post_call_adherence'],90)
        self.assertEqual([r['name'] for r in manager['reps']],['Scott Seymour'])
        self.assertEqual(manager['reps'][0]['post_call_adherence'], 50)
        self.assertEqual(manager['team_post_call_adherence'], 50)
        self.assertEqual(manager['lane_post_call_adherence'], {'1': 50, '2': None})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);m=stage_release(rep,mtd,c,html_for(mtd),root/'stage',manager=manager,manager_html=manager_html(manager))
            self.assertEqual(m['schema_version'],2);self.assertEqual(len(m['files']),5)
            accept_release(root/'stage',root/'site');self.assertEqual(verify_release(root/'site/releases'/m['release_id']),m)
            for name in ['rep.json','mtd.json','manager.json']:
                self.assertEqual(json.loads((root/'stage'/name).read_text())['publication']['release_id'],m['release_id'])
            (root/'stage/manager.html').write_text('incomplete')
            with self.assertRaisesRegex(ValueError,'hash'):verify_release(root/'stage')
    def test_manager_disagreement_blocks_all_three(self):
        rep,mtd,c,manager=three();manager['reps'][0]['qa_score']=4
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'scorecard'):stage_release(rep,mtd,c,html_for(mtd),Path(tmp),manager=manager,manager_html=manager_html(manager))
    def test_manager_html_and_drilldown_must_match(self):
        rep,mtd,c,manager=three();wrong=copy.deepcopy(manager);wrong['total_booked']=99
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'Manager HTML'):stage_release(rep,mtd,c,html_for(mtd),Path(tmp),manager=manager,manager_html=manager_html(wrong))
        manager['reps'][0]['metric_details']['deals'].pop()
        with self.assertRaisesRegex(ValueError,'drill-down'):validate_manager(manager,rep,c)
