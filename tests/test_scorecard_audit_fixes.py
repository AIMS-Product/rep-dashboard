import copy
import io
import json
from datetime import datetime
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from call_attribution import scoring_rep_for_meeting
from mtd_v28.aggregates import vp_closer_bookings
from collect_scorecard_preview import apply_preview_roster
from dashboard_qa import add_avoma_scores_to_dashboard
import avoma_scores


class ScorecardAuditFixTests(unittest.TestCase):
    def test_meeting_closer_wins_and_inactive_closer_is_not_reassigned(self):
        historical={'active','retired','second'}
        self.assertEqual(scoring_rep_for_meeting({'user_id':'active'},'setter',historical),('active','meeting_rep'))
        uid,_=scoring_rep_for_meeting({'user_id':'retired','users':['active']},'active',historical)
        self.assertEqual(uid,'retired')
        self.assertEqual(vp_closer_bookings([{'owner':uid}],{'active':'Scott Seymour','retired':'Eric Piccione'}),[])
        self.assertIsNone(scoring_rep_for_meeting({'users':['active','second']},'active',historical)[0])
        self.assertIsNone(scoring_rep_for_meeting({'user_id':'outsider'},'active',historical)[0])
        self.assertEqual(scoring_rep_for_meeting({},'active',historical),('active','owner_fallback'))

    def test_roster_change_requires_complete_source_and_rebuilds_neutral_future_calls(self):
        dashboard={'month_label':'November 2026','reps':[{'name':'Scott Seymour'},{'name':'Eric Piccione'}],
                   'adherence_meta':{'period':{'month':'2026-11'}},
                   **{k:0 for k in ['total_booked','total_shown','total_qualified','total_no_shows','total_occurred_calls','total_scheduled_calls','total_unresolved_calls','total_deals']}}
        with self.assertRaisesRegex(ValueError,'complete process extract'):apply_preview_roster(copy.deepcopy(dashboard))
        def lead(id,owner):return {'id':id,'display_name':id,'status_id':'active','First Sales Call Booked Date':'2026-11-25','Lead Owner':owner}
        extract={'meta':{'month':'2026-11','complete':True,'started_at':'2026-11-18T12:00:00-08:00','ended_at':'2026-11-18T12:01:00-08:00','api_requests':0},
                 'users':{'scott':'Scott Seymour','eric':'Eric Piccione'},'leads':[lead('active','scott'),lead('retired','eric')], 'tasks':{},
                 'activities':{'emails':{},'notes':{},'sms':{},'calls':{},'meetings':{id:[{'starts_at':'2026-11-25T10:00:00-08:00','status':'upcoming','user_id':uid}] for id,uid in [('active','scott'),('retired','eric')]}}}
        result=apply_preview_roster(dashboard,process_extract=extract)
        self.assertEqual(result['adherence_meta']['cohort']['included'],1)
        self.assertEqual(result['adherence_meta']['cohort']['exclusion_counts']['meeting_rep_not_visible'],1)
        self.assertEqual(result['reps'][0]['adherence']['steps']['loom_usage']['eligible'],0)

    def test_qa_preserves_fractional_means_through_team_percent(self):
        scores={'one@test':{'score':2.0361732773827037,'count':182}}
        client=Mock();client.paginate.return_value=[{'id':'one','email':'one@test'}]
        with patch('dashboard_qa.fetch_avoma_mtd_scores',return_value=(scores,{'available':True})),patch('dashboard_qa.load_env_value',return_value='test'),patch('dashboard_qa.CloseClient',return_value=client):
            result=add_avoma_scores_to_dashboard({'reps':[{'rep_owner_id':'one'}]})
        self.assertEqual(result['team_qa_score'],scores['one@test']['score'])
        self.assertEqual(round(result['team_qa_score']*20,1),40.7)
        self.assertEqual(round(result['team_qa_score'],2),2.04)

    def test_avoma_adapter_keeps_raw_mean_until_rendering(self):
        cut=datetime.fromisoformat('2026-11-18T11:14:09-08:00')
        class Frozen(datetime):
            @classmethod
            def now(cls,tz=None):return cut.astimezone(tz) if tz else cut.replace(tzinfo=None)
        rows=[{'scorecard_template_name':'VP Closing Call Scorecard','meeting':{'uuid':str(i),'start_at':'2026-11-02T10:00:00-08:00'},'scored_for':{'email':'one@test'},'overall_score':score,'modified':'2026-11-03T10:00:00Z'} for i,score in enumerate([2.031,2.041])]
        with patch.object(avoma_scores,'datetime',Frozen),patch.object(avoma_scores,'urlopen',return_value=io.BytesIO(json.dumps({'results':rows,'next':None}).encode())):
            scores,_=avoma_scores.fetch_avoma_mtd_scores('fixture')
        self.assertAlmostEqual(scores['one@test']['score'],2.036)
        self.assertEqual(round(scores['one@test']['score']*20,1),40.7)
