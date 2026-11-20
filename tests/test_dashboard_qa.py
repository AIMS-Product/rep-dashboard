import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dashboard_qa import add_avoma_scores_to_dashboard


class DashboardQaTests(unittest.TestCase):
    def test_scores_match_by_close_user_email_and_team_is_call_weighted(self):
        payload = {'reps': [{'name':'One','rep_owner_id':'one'},
                            {'name':'Two','rep_owner_id':'two'},
                            {'name':'No scored calls','rep_owner_id':'three'}]}
        scores = {'one@example.test':{'score':4,'count':3},
                  'two@example.test':{'score':2,'count':1},
                  'inactive@example.test':{'score':5,'count':99}}
        client = Mock()
        client.paginate.return_value = [
            {'id':'one','email':'ONE@example.test'}, {'id':'two','email':'two@example.test'},
            {'id':'three','email':'three@example.test'}]
        with patch('dashboard_qa.fetch_avoma_mtd_scores',return_value=(scores,{'available':True})), \
             patch('dashboard_qa.load_env_value',return_value='test-key'), \
             patch('dashboard_qa.CloseClient',return_value=client):
            result = add_avoma_scores_to_dashboard(payload)
        self.assertIs(result,payload)
        self.assertEqual(result['reps'][0]['qa_score'],4)
        self.assertEqual(result['reps'][0]['qa_score_count'],3)
        self.assertIsNone(result['reps'][2]['qa_score'])
        self.assertEqual(result['team_qa_score'],3.5)
        self.assertEqual(result['team_qa_score_count'],4)

    def test_missing_key_stays_blank_and_does_not_query_crm(self):
        with patch('dashboard_qa.fetch_avoma_mtd_scores',return_value=({}, {'available':False,'reason':'missing_api_key'})), \
             patch('dashboard_qa.load_env_value',return_value=''), \
             patch('dashboard_qa.CloseClient') as client:
            result = add_avoma_scores_to_dashboard({'reps':[{'name':'One'}]})
        client.assert_not_called()
        self.assertIsNone(result['reps'][0]['qa_score'])
        self.assertIsNone(result['team_qa_score'])

    def test_source_failure_does_not_overwrite_the_last_published_snapshot(self):
        with patch('dashboard_qa.fetch_avoma_mtd_scores',side_effect=RuntimeError('source unavailable')), \
             patch('dashboard_qa.load_env_value',return_value='test-key'):
            with self.assertRaisesRegex(RuntimeError,'source unavailable'):
                add_avoma_scores_to_dashboard({'reps':[]})
