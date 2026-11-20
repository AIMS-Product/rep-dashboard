import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from adherence_rules import parse_datetime
from scorecard_rules import CASH_FIELD, aggregate_post_call, cash_collected, post_call_result, owner_touch_result


class PostCallScorecardTests(unittest.TestCase):
    def evaluate(self, **changes):
        args = dict(call_end='2026-11-17T12:00:00-08:00', owner_id='rep',
                    now=parse_datetime('2026-11-18T12:00:00-08:00'),
                    emails=[dict(id='recap', user_id='rep', direction='outbound', status='sent',
                                 date_sent='2026-11-17T13:00:00-08:00')],
                    tasks=[dict(id='task', assigned_to='rep', date='2026-11-20',
                                date_created='2026-11-17T14:00:00-08:00')])
        args.update(changes)
        args['tasks'] = [dict(task, lead_id=task.get('lead_id', 'lead_test')) for task in args.get('tasks', [])]
        return post_call_result(**args)

    def test_requires_recap_and_next_step(self):
        self.assertEqual(self.evaluate()['state'], 'completed')
        self.assertEqual(self.evaluate(tasks=[])['state'], 'missed')
        self.assertEqual(self.evaluate(emails=[])['state'], 'missed')

    def test_meeting_must_be_created_within_response_window(self):
        meeting = dict(id='meeting', date_created='2026-11-17T15:00:00-08:00',
                       starts_at='2026-11-25T12:00:00-08:00', status='upcoming')
        self.assertEqual(self.evaluate(tasks=[], meetings=[meeting])['state'], 'completed')
        meeting['date_created'] = '2026-11-18T12:00:01-08:00'
        self.assertEqual(self.evaluate(tasks=[], meetings=[meeting])['state'], 'missed')
        meeting['date_created'] = '2026-11-01T01:00:00-08:00'
        self.assertEqual(self.evaluate(tasks=[], meetings=[meeting])['state'], 'missed')

    def test_exact_deadline_counts_but_one_second_late_does_not(self):
        email = dict(user_id='rep', direction='outbound', status='sent',
                     date_sent='2026-11-18T00:00:00-08:00')
        self.assertEqual(self.evaluate(emails=[email])['state'], 'completed')
        email['date_sent'] = '2026-11-18T00:00:01-08:00'
        self.assertEqual(self.evaluate(emails=[email])['state'], 'missed')

    def test_tasks_created_before_call_or_after_deadline_do_not_count(self):
        for created in ['2026-11-01T14:00:00-08:00', '2026-11-18T13:00:00-08:00']:
            task = dict(assigned_to='rep', date='2026-11-20', date_created=created)
            self.assertEqual(self.evaluate(tasks=[task])['state'], 'missed')
        task['assigned_to'] = 'other'
        self.assertEqual(self.evaluate(tasks=[task])['state'], 'missed')

    def test_actual_send_time_is_required(self):
        for email in [dict(user_id='rep', direction='outbound', status='draft', date_created='2026-11-17T13:00:00-08:00'),
                      dict(user_id='rep', direction='outbound', status='sent', date_created='2026-11-17T13:00:00-08:00', date_sent='2026-11-18T14:00:00-08:00')]:
            self.assertEqual(self.evaluate(emails=[email])['state'], 'missed')

    def test_any_sent_email_counts_regardless_of_content_or_sender_but_sms_does_not(self):
        email = dict(user_id='other', direction='outgoing', status='sent',
                     date_sent='2026-11-17T13:00:00-08:00', subject='financing links')
        self.assertEqual(self.evaluate(emails=[email])['state'], 'completed')
        self.assertEqual(self.evaluate(emails=[],sms=[email])['state'], 'missed')

    def test_email_and_next_step_during_call_count_with_deadline_still_anchored_to_end(self):
        email = dict(direction='outbound',status='sent',date_sent='2026-11-17T11:30:00-08:00')
        task = dict(assigned_to='rep',date='2026-11-20',date_created='2026-11-17T11:15:00-08:00')
        result = self.evaluate(call_start='2026-11-17T11:00:00-08:00',emails=[email],tasks=[task])
        self.assertEqual(result['state'],'completed')
        self.assertEqual(result['deadline_at'],'2026-11-18T00:00:00-08:00')
        email['date_sent']='2026-11-17T10:59:59-08:00'
        self.assertEqual(self.evaluate(call_start='2026-11-17T11:00:00-08:00',emails=[email],tasks=[task])['state'],'missed')

    def test_pending_does_not_enter_denominator_even_when_done_early(self):
        early = self.evaluate(now=parse_datetime('2026-11-17T17:00:00-08:00'))
        self.assertEqual(early['state'], 'pending')
        totals = aggregate_post_call([early, self.evaluate(), self.evaluate(emails=[])])
        self.assertEqual((totals['done'], totals['eligible'], totals['pct'], totals['pending']), (1, 2, 50, 1))

    def test_missing_end_is_unknown_and_existing_exemptions_stay_exempt(self):
        self.assertEqual(self.evaluate(call_end=None)['state'], 'unknown')
        self.assertEqual(self.evaluate(exempt=True)['state'], 'exempt')

    def test_twelve_elapsed_hours_across_dst(self):
        result = self.evaluate(call_end='2026-10-31T20:00:00-07:00', emails=[], tasks=[])
        self.assertEqual(result['deadline_at'], '2026-11-01T07:00:00-08:00')

    def test_canceled_meeting_does_not_qualify(self):
        self.assertEqual(self.evaluate(tasks=[], meetings=[dict(date_created='2026-11-17T15:00:00-08:00',
                         starts_at='2026-11-25T12:00:00-08:00', status='canceled')])['state'], 'missed')


class CashCollectedTests(unittest.TestCase):
    def test_dollars_not_cents_and_no_deal_value_fallback(self):
        self.assertEqual(cash_collected({'custom': {CASH_FIELD: 1500}, 'value': 900000}), 1500)
        self.assertEqual(cash_collected({f'custom.{CASH_FIELD}': 0}), 0)
        self.assertIsNone(cash_collected({'value': 900000}))
        self.assertIsNone(cash_collected({'custom': {CASH_FIELD: 'NaN'}}))


class OwnerTouchTests(unittest.TestCase):
    def evaluate(self, activities, hours=72):
        return owner_touch_result({'owner_id': 'rep', 'owner_since': '2026-11-18'}, activities,
                                  parse_datetime('2026-11-18T12:00:00-08:00'), hours)

    def sms(self, **changes):
        result = dict(_type='SMS', user_id='rep', status='sent', direction='outbound',
                      date_sent='2026-11-15T12:00:00-08:00')
        result.update(changes)
        return result

    def test_exact_72h_is_overdue_but_five_day_measure_still_passes(self):
        self.assertEqual(self.evaluate([self.sms()])['state'], 'overdue')
        self.assertEqual(self.evaluate([self.sms()], hours=120)['state'], 'within_window')
        self.assertEqual(self.evaluate([self.sms(date_sent='2026-11-15T12:00:01-08:00')])['state'], 'within_window')

    def test_new_assignment_no_outreach_and_other_sender_do_not_clear_overdue(self):
        result = self.evaluate([self.sms(user_id='other', date_sent='2026-11-18T11:00:00-08:00')])
        self.assertEqual(result['state'], 'overdue')
        self.assertTrue(result['other_sender_recent'])
        self.assertIsNone(result['last_owner_touch_at'])

    def test_actual_delivered_send_timestamp_takes_precedence(self):
        result = self.evaluate([self.sms(status='delivered', date_sent='2026-11-18T11:00:00-08:00',
                                         activity_at='2026-11-01T12:00:00-08:00')])
        self.assertEqual(result['state'], 'within_window')
        self.assertEqual(result['hours_since_owner_touch'], 1)

    def test_inbound_draft_and_future_activity_do_not_count(self):
        for changes in [dict(direction='inbound'), dict(status='draft'),
                        dict(date_sent='2026-11-19T12:00:00-08:00')]:
            self.assertEqual(self.evaluate([self.sms(**changes)])['state'], 'overdue')
