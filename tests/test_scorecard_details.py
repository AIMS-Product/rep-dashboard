import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from scorecard_details import attach_financial_details, financial_details
from scorecard_rules import CASH_FIELD


class FinancialDetailsTests(unittest.TestCase):
    def test_closed_counts_lead_once_while_cash_keeps_each_opportunity_and_zero(self):
        opportunities = [dict(id='one',lead_id='lead_one',user_id='rep',custom={CASH_FIELD:0}),
                         dict(id='two',lead_id='lead_one',user_id='rep')]
        result = financial_details(opportunities)
        self.assertEqual(len(result['closed_leads']),1)
        self.assertEqual([row['cash_amount'] for row in result['cash_opportunities']],[0,None])
        self.assertEqual(result['closed_leads'][0]['url'],'https://app.close.com/lead/lead_one/')

    def test_team_historical_wins_and_cash_roster_scope_are_preserved(self):
        opportunities = [dict(id='one',lead_id='lead_one',user_id='rep'),
                         dict(id='two',lead_id='lead_two',user_id='retired')]
        dashboard = dict(reps=[dict(name='Rep',rep_owner_id='rep',deals=1)],total_deals=2)
        result=attach_financial_details(dashboard,opportunities)
        self.assertEqual(len(result['team_financial_details']['closed_leads']),2)
        self.assertEqual(len(result['team_financial_details']['cash_opportunities']),1)
        dashboard['reps'][0]['deals']=2
        with self.assertRaises(ValueError): attach_financial_details(dashboard,opportunities)
