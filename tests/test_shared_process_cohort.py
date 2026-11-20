import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from build_adherence_preview import add_adherence_to_dashboard
from collect_scorecard_preview import cohort_for_rep


class SharedProcessCohortTests(unittest.TestCase):
    def fixture(self):
        meetings=[dict(id='first',lead_id='lead',user_id='rep',title='Vendingpreneurs Meridian – Next Steps',starts_at='2026-11-02T18:00:00Z',status='completed'),
                  dict(id='later',lead_id='lead',user_id='rep',title='Vendingpreneurs Meridian – Next Steps',starts_at='2026-11-15T18:00:00Z',status='completed')]
        extract={'meta':{'month':'2026-11','complete':True,'started_at':'2026-11-20T08:00:00-08:00','ended_at':'2026-11-20T08:00:00-08:00','api_requests':0},
                 'users':{'rep':'Rep'},'leads':[dict(id='lead',name='Lead',status_id='active',**{'Lead Owner':'rep','First Sales Call Booked Date':'2026-11-15','First Call Show Up (Opp)':'Yes'})],
                 'activities':{'meetings':{'lead':meetings},'sms':{},'emails':{},'notes':{},'calls':{}},'tasks':{}}
        return {'month_label':'November 2026','reps':[{'name':'Rep'}]},extract,meetings

    def test_shared_accepted_call_bypasses_old_title_classifier_and_booked_field(self):
        base,extract,meetings=self.fixture()
        result=add_adherence_to_dashboard(base,extract=extract,accepted_process_rows=[dict(lead_id='lead',owner='Rep',meeting_id='first',starts_at=meetings[0]['starts_at'])])
        rows=result['reps'][0]['adherence']['lead_cohorts']['loom_usage']['missed']
        self.assertEqual(rows[0]['meeting_id'],'first')
        self.assertEqual(rows[0]['booked_date'],'2026-11-02')
        self.assertEqual(result['adherence_meta']['period']['basis'],'shared_mtd_accepted_scheduled_calls')

    def test_distinct_accepted_calls_on_same_lead_are_not_collapsed_in_post_call(self):
        base,extract,meetings=self.fixture()
        result=add_adherence_to_dashboard(base,extract=extract,accepted_process_rows=[dict(lead_id='lead',owner='Rep',meeting_id=m['id'],starts_at=m['starts_at']) for m in meetings])
        self.assertEqual(result['reps'][0]['adherence']['steps']['loom_usage']['eligible'],2)
        self.assertEqual(len(cohort_for_rep(result['reps'][0])),2)

    def test_missing_accepted_call_evidence_does_not_silently_reduce_denominator(self):
        base,extract,_=self.fixture()
        with self.assertRaises(ValueError):
            add_adherence_to_dashboard(base,extract=extract,accepted_process_rows=[dict(lead_id='lead',owner='Rep',meeting_id='missing',starts_at='2026-11-02T18:00:00Z')])

    def test_changed_canceled_selected_meeting_fails_closed(self):
        base,extract,meetings=self.fixture()
        meetings[0]['status']='canceled'
        with self.assertRaises(ValueError):
            add_adherence_to_dashboard(base,extract=extract,accepted_process_rows=[dict(lead_id='lead',owner='Rep',meeting_id='first',starts_at=meetings[0]['starts_at'])])

    def test_same_start_distinct_meeting_ids_remain_distinct_post_call_entries(self):
        a={'id':'lead','meeting_id':'one','scored_call_at':'2026-11-02T18:00:00Z'}
        b=dict(a,meeting_id='two')
        rep={'adherence':{'lead_cohorts':{'follow_up_created':{'completed':[a,b]}}}}
        self.assertEqual(len(cohort_for_rep(rep)),2)

    def test_shared_call_taker_does_not_change_when_calendar_attendees_change(self):
        base,extract,meetings=self.fixture()
        extract['users']['new_owner']='Different Closer'
        extract['leads'][0]['Lead Owner']='new_owner'
        meetings[0]['user_id']='new_owner'
        result=add_adherence_to_dashboard(base,extract=extract,accepted_process_rows=[dict(lead_id='lead',owner='Rep',meeting_id='first',starts_at=meetings[0]['starts_at'])])
        self.assertEqual(result['reps'][0]['adherence']['steps']['loom_usage']['eligible'],1)
