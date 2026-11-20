import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from collect_scorecard_preview import enrich, add_followup_snapshot
from scorecard_rules import CASH_FIELD


class PreviewIntegrationTests(unittest.TestCase):
    def test_post_call_counts_reconcile_without_changing_pre_call_evidence(self):
        lead = dict(id='lead_one',name='One',scored_call_at='2026-11-17T10:00:00-08:00')
        cohorts = {'follow_up_created': {'completed':[lead], 'missed':[], 'exempt':[]}}
        dashboard = {'reps':[dict(name='Rep',rep_owner_id='rep',deals=1,shown=2,no_shows=1,booked=3,close_rate=33.3,
            adherence={'pre_call_pct':75, 'steps':{'loom_usage':{'pct':50}},'lead_cohorts':cohorts})],
            'total_shown':2,'total_no_shows':1,'total_booked':3,'total_deals':1,'team_close_rate':33.3}
        capture = dict(meta={'as_of':'2026-11-18T12:00:00-08:00'},cohorts={'rep':[dict(lead,exempt=False)]},
            activities={'emails':{'lead_one':[dict(user_id='rep',direction='outbound',status='sent',
                date_sent='2026-11-17T12:00:00-08:00')]},'sms':{},'meetings':{'lead_one':[
                    dict(starts_at=lead['scored_call_at'],ends_at='2026-11-17T11:00:00-08:00')]}},
            tasks={'lead_one':[dict(lead_id='lead_one',assigned_to='rep',date_created='2026-11-17T12:00:00-08:00',date='2026-11-20')]},
            opportunities=[dict(id='opp_one',lead_id='lead_one',lead_name='One',user_id='rep',custom={CASH_FIELD:1500})])
        original = copy.deepcopy(dashboard['reps'][0]['adherence'])
        result = enrich(dashboard,capture)
        rep = result['reps'][0]
        self.assertEqual(rep['post_call_12h']['pct'],100)
        self.assertEqual(rep['adherence']['pre_call_pct'],original['pre_call_pct'])
        self.assertEqual(rep['adherence']['steps']['loom_usage'],original['steps']['loom_usage'])
        self.assertEqual(len(rep['adherence']['lead_cohorts']['post_call_complete_12h']['completed']),1)
        self.assertEqual(rep['cash_collected']['amount'],1500)
        self.assertEqual(result['team_cash_collected']['amount'],1500)
        self.assertEqual(result['reps'][0]['close_rate'],33.3)
        self.assertEqual(result['team_close_rate'],33.3)
        self.assertNotIn('shown_close_rate',result['reps'][0])
        self.assertNotIn('team_shown_close_rate',result)

    def test_followup_uses_verified_current_owner_and_correct_status_window(self):
        hot='stat_kY1aMGKOui3jjlgniY2LQWMadXN78cr7vTHVMPDCliy'
        warm='stat_fOwpEPCfmjC2LhrJEZE5T1btfKJRcgkMinycBxLPwS5'
        dashboard={'reps':[dict(name='Rep',rep_owner_id='rep')],'scorecard_meta':{}}
        def item(id,status,owner='rep'):
            return {'lead':dict(id=id,name=id,status_id=status,owner_id=owner,business_line=''),
                    'activities':[dict(_type='SMS',user_id='rep',direction='outbound',status='sent',
                                       date_sent='2026-11-14T12:00:00-08:00')]}
        capture={'meta':{'started_at':'2026-11-18T12:00:00-08:00','ended_at':'2026-11-18T12:10:00-08:00'},
                 'results':[item('hot',hot),item('warm',warm),item('moved',hot,'other')]}
        result=add_followup_snapshot(dashboard,capture)
        self.assertEqual((result['hot_followup_72h']['eligible'],result['hot_followup_72h']['overdue']),(1,1))
        self.assertEqual(result['warm_followup_5d']['pct'],100)
        capture['results'].append({'error':'unverified'})
        with self.assertRaises(ValueError): add_followup_snapshot(dashboard,capture)
