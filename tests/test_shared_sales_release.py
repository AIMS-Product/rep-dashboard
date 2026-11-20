import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from shared_sales_contract import derive_rep,validate_pair,make_rep_base
from shared_sales_release import stage_release,accept_release,rollback_release,verify_release


def html_for(mtd):return '<body>MTD<script id="v2-data" type="application/json">'+json.dumps(mtd)+'</script></body>'


def fixture(cutoff='2026-11-19T13:45:00-08:00'):
    base={'team_quota':1000,'reps':[{'name':'Scott Seymour','rep_owner_id':'rep','quota':1000}]}
    calls=[{'meeting_id':'one','lead_id':'lead','owner':'Scott Seymour','bucket':'Marketing','outcome':'Completed'},
           {'meeting_id':'two','lead_id':'other','owner':'Scott Seymour','bucket':'Lane 2','outcome':'No Show'}]
    opportunities=[{'id':id,'lead_id':lead,'lead_name':lead,'user_id':owner} for id,lead,owner in [('a','lead','rep'),('b','lead','rep'),('c','historic','retired')]]
    totals={'occurred_calls':2,'showed':1,'no_shows':1,'scheduled_calls':3,'unresolved_calls':1,'qualified':1,'closed_won':3,'revenue':150}
    mtd={'source_cutoff':cutoff,'booking_scope':'vp-appointments.v2','rules_version':'MTD-approved','totals':totals,'team':[{'value':'Scott Seymour',**totals,'closed_won':2,'revenue':100},{'value':'Historical closer','occurred_calls':0,'closed_won':1,'revenue':50}]}
    canonical={'cutoff':cutoff,'rules_version':'MTD-approved','roster':[{'name':'Scott Seymour'}],'mtd':mtd,'occurred_rows':calls,'opportunities':opportunities}
    return derive_rep(base,canonical),mtd,canonical


class SharedSalesReleaseTests(unittest.TestCase):
    def test_mtd_opportunity_grain_and_historical_wins_are_preserved(self):
        rep,mtd,c=fixture();self.assertEqual(rep['reps'][0]['deals'],2)
        self.assertEqual(len(rep['reps'][0]['financial_details']['closed_leads']),2)
        self.assertEqual(rep['total_deals'],3);self.assertEqual(rep['team_close_rate'],150)
        self.assertTrue(validate_pair(rep,mtd,c)['matched'])
        self.assertEqual(rep['working_days_elapsed'],14)

    def test_pair_mismatch_and_missing_roster_are_rejected(self):
        rep,mtd,c=fixture();rep['reps'][0]['shown']=0
        with self.assertRaisesRegex(ValueError,'parity'):validate_pair(rep,mtd,c)
        c['roster'].append({'name':'Luke Herman'})
        with self.assertRaisesRegex(ValueError,'missing'):derive_rep({'team_quota':1000,'reps':[]},c)

    def test_duplicate_call_or_opportunity_ids_are_rejected(self):
        for key in ['occurred_rows','opportunities']:
            rep,mtd,c=fixture();c[key].append(copy.deepcopy(c[key][0]))
            with self.assertRaisesRegex(ValueError,'Duplicate'):validate_pair(rep,mtd,c)

    def test_atomic_accept_retry_stale_cutoff_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);site=root/'site';rep,mtd,c=fixture()
            first=stage_release(rep,mtd,c,html_for(mtd),root/'one');accept_release(root/'one',site)
            before=(site/'current.json').read_bytes();accept_release(root/'one',site);self.assertEqual(before,(site/'current.json').read_bytes())
            rep,mtd,c=fixture('2026-11-19T14:00:00-08:00');second=stage_release(rep,mtd,c,html_for(mtd),root/'two')
            with patch('shared_sales_release.write_pointer',side_effect=RuntimeError('simulated interruption')):
                with self.assertRaises(RuntimeError):accept_release(root/'two',site)
            self.assertEqual(json.loads((site/'current.json').read_text())['release_id'],first['release_id'])
            accept_release(root/'two',site)
            with self.assertRaisesRegex(ValueError,'Stale'):accept_release(root/'one',site)
            rollback_release(site,first['release_id']);self.assertEqual((site/'current.json').read_bytes(),before)
            self.assertTrue((site/'releases'/second['release_id']).is_dir())

    def test_corrupt_or_private_output_cannot_be_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);rep,mtd,c=fixture();stage_release(rep,mtd,c,html_for(mtd),root/'stage')
            (root/'stage/rep.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'hash'):accept_release(root/'stage',root/'site')
            self.assertFalse((root/'site/current.json').exists())
            rep['contacts']=[]
            with self.assertRaisesRegex(ValueError,'Private'):stage_release(rep,mtd,c,html_for(mtd),root/'private')

    def test_html_from_another_snapshot_is_rejected(self):
        rep,mtd,c=fixture();wrong=copy.deepcopy(mtd);wrong['totals']['closed_won']=99
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'HTML does not match'):stage_release(rep,mtd,c,html_for(wrong),Path(tmp))

    def test_rep_scaffold_does_not_run_the_old_crm_calculation(self):
        rep,mtd,c=fixture();c['user_map']={'rep':'Scott Seymour'}
        with patch('fetch_data.api_get',side_effect=AssertionError('legacy calculation used')):
            result=make_rep_base(c)
        self.assertEqual(result['total_booked'],2)
        self.assertEqual(result['total_deals'],3)
        self.assertEqual(result['reps'][0]['rep_owner_id'],'rep')

    def test_rule_version_and_historical_active_scope_must_match(self):
        rep,mtd,c=fixture();mtd['rules_version']='different'
        with self.assertRaisesRegex(ValueError,'Rule version'):validate_pair(rep,mtd,c)
        rep,mtd,c=fixture();mtd['team'][-1]['occurred_calls']=1
        with self.assertRaisesRegex(ValueError,'Historical'):validate_pair(rep,mtd,c)

    def test_source_revision_is_part_of_immutable_release_identity(self):
        rep,mtd,c=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            c['source_versions']={'mtd':'first'};one=stage_release(rep,mtd,c,html_for(mtd),Path(tmp)/'one')
            c['source_versions']={'mtd':'second'};two=stage_release(rep,mtd,c,html_for(mtd),Path(tmp)/'two')
        self.assertNotEqual(one['release_id'],two['release_id'])

    def test_earned_historical_calls_remain_in_the_mtd_denominator(self):
        rep,mtd,c=fixture()
        c['occurred_rows'].append({'meeting_id':'historic-call','lead_id':'historic','owner':'Historical closer','bucket':'Marketing','outcome':'No Show'})
        mtd['totals'].update(occurred_calls=3,no_shows=2,scheduled_calls=4)
        mtd['team'][-1].update(occurred_calls=1,no_shows=1,showed=0,qualified=0,scheduled_calls=1,unresolved_calls=0)
        result=derive_rep(rep,c)
        self.assertEqual(result['total_booked'],3)
        self.assertEqual(result['reps'][0]['booked'],2)
        self.assertEqual(result['historical_closers']['booked'],1)
        self.assertTrue(validate_pair(result,mtd,c)['matched'])
