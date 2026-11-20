import sys,unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from collect_shared_followup import collect_followup,OWNER,BUSINESS,STATUSES


class SharedFollowupCaptureTests(unittest.TestCase):
 def test_activity_filter_uses_matching_sort_and_rechecks_native_lead_status(self):
  lead={'id':'lead_one','display_name':'One','status_id':STATUSES[0],'custom.'+OWNER:'rep','custom.'+BUSINESS:'Vendingpreneurs (VP)'}
  response=Mock(status_code=200);response.json.return_value={'data':[lead]}
  session=Mock();session.post.return_value=response
  client=Mock();client.paginate.return_value=[];client.get.return_value={**lead,'status_id':STATUSES[1]}
  with patch('collect_shared_followup.requests.Session',return_value=session),patch('collect_shared_followup.CloseClient',return_value=client):
   result=collect_followup('fixture',[{'rep_owner_id':'rep'}],datetime.fromisoformat('2026-11-19T14:00:00-08:00'))
  query=client.paginate.call_args.args[1]
  self.assertEqual(query['_order_by'],'-activity_at');self.assertIn('activity_at__lte',query)
  self.assertEqual(result['results'][0]['lead']['status_id'],STATUSES[1])
  self.assertNotIn('custom',result['results'][0]['lead'])

 def test_failed_census_does_not_create_a_zero_followup_book(self):
  session=Mock();session.post.return_value=Mock(status_code=500)
  with patch('collect_shared_followup.requests.Session',return_value=session):
   with self.assertRaises(RuntimeError):collect_followup('fixture',[{'rep_owner_id':'rep'}],datetime.fromisoformat('2026-11-19T14:00:00-08:00'))
