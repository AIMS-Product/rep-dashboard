import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from scheduled_sms_evidence import scheduling_receipts, collect_scheduling_receipts
from scorecard_rules import post_call_result
from adherence_rules import parse_datetime


class ScheduledSmsTests(unittest.TestCase):
    def evaluate(self, status='scheduled', lo='2026-11-17T22:00:00Z', hi=None, complete=True):
        sms = dict(id='sms', direction='outbound', status=status, date_created='2026-11-01T00:00:00Z',
                   date_scheduled='2026-11-25T22:00:00Z', scheduling_history_complete=complete,
                   scheduling_evidence=[] if lo is None else [dict(earliest_at=lo,latest_at=hi or lo,send_at='2026-11-25T22:00:00Z')])
        return post_call_result(call_start='2026-11-17T11:00:00-08:00',call_end='2026-11-17T12:00:00-08:00',
            now=parse_datetime('2026-11-18T12:00:00-08:00'),owner_id='rep',sms=[sms])

    def test_scheduled_text_counts_as_next_step_but_is_not_a_sent_recap(self):
        result=self.evaluate()
        self.assertTrue(result['next_step_done'])
        self.assertFalse(result['recap_done'])
        self.assertEqual(result['scheduled_sms_ids'],['sms'])

    def test_sent_scheduled_text_retains_next_step_credit(self):
        self.assertTrue(self.evaluate(status='sent')['next_step_done'])
        for status in ('draft','error','failed','deleted'):
            self.assertFalse(self.evaluate(status=status)['next_step_done'])

    def test_old_draft_created_before_call_can_be_scheduled_inside_window(self):
        self.assertTrue(self.evaluate()['next_step_done'])
        self.assertFalse(self.evaluate(lo='2026-11-18T08:00:01Z')['next_step_done'])
        self.assertTrue(self.evaluate(lo='2026-11-18T08:00:00Z')['next_step_done'])

    def test_unverifiable_or_boundary_straddling_schedules_are_unknown_not_missed(self):
        self.assertEqual(self.evaluate(lo=None,complete=False)['state'],'unknown')
        self.assertEqual(self.evaluate(lo='2026-11-18T07:59:59Z',hi='2026-11-18T08:00:01Z')['state'],'unknown')
        self.assertEqual(self.evaluate(lo=None,complete=True)['state'],'missed')

    def test_incomplete_later_history_cannot_hide_cancellation_after_valid_queue(self):
        self.assertEqual(self.evaluate(complete=False)['state'],'unknown')
        self.assertFalse(self.evaluate(complete=False)['next_step_done'])

    def test_receipt_uses_draft_to_scheduled_transition_not_message_creation(self):
        event=dict(id='event',action='updated',date_created='2026-11-17T22:00:00',date_updated='2026-11-17T22:00:02',
            changed_fields=['status','date_scheduled'],previous_data={'status':'draft'},
            data={'status':'scheduled','date_created':'2026-11-01T00:00:00Z','date_scheduled':'2026-11-25T22:00:00Z'})
        proof=scheduling_receipts([event])[0]
        self.assertEqual(proof['earliest_at'],'2026-11-17T22:00:00+00:00')
        self.assertEqual(proof['latest_at'],'2026-11-17T22:00:02+00:00')

    def test_cancellation_invalidates_old_queue_even_if_message_sends_later(self):
        queued=dict(id='queue',action='updated',date_created='2026-11-17T22:00:00Z',date_updated='2026-11-17T22:00:00Z',changed_fields=['status'],previous_data={'status':'draft'},data={'status':'scheduled','date_scheduled':'2026-11-25T22:00:00Z'})
        cancel=dict(queued,id='cancel',date_created='2026-11-17T23:00:00Z',date_updated='2026-11-17T23:00:00Z',previous_data={'status':'scheduled'},data={'status':'draft','date_scheduled':'2026-11-25T22:00:00Z'})
        sent=dict(queued,id='send',date_created='2026-11-18T22:00:00Z',date_updated='2026-11-18T22:00:00Z',previous_data={'status':'draft'},data={'status':'outbox','date_scheduled':'2026-11-25T22:00:00Z'})
        self.assertEqual(scheduling_receipts([sent,cancel,queued]),[])

    def test_redacted_event_history_is_not_marked_complete(self):
        class Client:
            def get(self,*args): return {'data':[{'object_id':'sms','object_type':'activity.sms','action':'created','data':{}}],'cursor_next':None}
        message={'id':'sms','status':'scheduled','date_scheduled':'2026-11-25T22:00:00Z','date_created':'2026-11-17T22:00:00Z'}
        collect_scheduling_receipts(Client(),[message],parse_datetime('2026-11-20T00:00:00Z'))
        self.assertFalse(message['scheduling_history_complete'])

    def test_event_collection_follows_cursors_and_requires_full_response(self):
        class Client:
            def __init__(self): self.calls=[]
            def get(self,path,params):
                self.calls.append(params)
                return {'data':[{'object_id':'sms','object_type':'activity.sms','action':'created','data':{'id':'sms','status':'draft','date_created':'2026-11-17T22:00:00Z','date_updated':'2026-11-17T22:00:00Z'}}], 'cursor_next':'next'} if len(self.calls)==1 else {'data':[], 'cursor_next':None}
        client=Client();message={'id':'sms','status':'scheduled','date_scheduled':'2026-11-25T22:00:00Z','date_created':'2026-11-17T22:00:00Z'}
        collect_scheduling_receipts(client,[message],parse_datetime('2026-11-20T00:00:00Z'))
        self.assertEqual(client.calls[1]['_cursor'],'next')
        self.assertTrue(message['scheduling_history_complete'])
        class Broken:
            def get(self,*args): return {'data':[]}
        with self.assertRaises(ValueError): collect_scheduling_receipts(Broken(),[message],parse_datetime('2026-11-20T00:00:00Z'))
