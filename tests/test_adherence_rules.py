import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from adherence_rules import aggregate_rep_scores, score_lead  # noqa: E402


NOW = datetime(2026, 9, 21, 19, 0, tzinfo=timezone.utc)
OWNER = "user_rep"


def meeting(starts_at, status="completed", user_id=OWNER):
    return {
        "id": f"meeting_{starts_at}",
        "lead_id": "lead_1",
        "starts_at": starts_at,
        "status": status,
        "user_id": user_id,
    }


class AdherenceRulesTests(unittest.TestCase):
    def test_pre_call_rules_use_same_day_meeting_deadline(self):
        result = score_lead(
            booked_date="2026-09-10",
            show_state="Yes",
            owner_id=OWNER,
            lead_owner_id=OWNER,
            meetings=[meeting("2026-09-10T17:00:00Z")],
            notes=[{"activity_at": "2026-09-09T17:00:00Z", "note": "Watch https://loom.com/a"}],
            sms=[{"activity_at": "2026-09-10T16:59:00Z", "direction": "outbound", "status": "sent", "user_id": OWNER, "text": "See you soon"}],
            now=NOW,
        )
        self.assertEqual(result["loom_usage"], {"eligible": True, "done": True})
        self.assertEqual(result["precall_text"], {"eligible": True, "done": True})
        self.assertEqual(result["day_of_confirmation_text"], {"eligible": True, "done": True})

    def test_day_of_text_uses_lead_owner_not_scoring_rep(self):
        lead_owner = "user_lead_owner"
        base = dict(
            booked_date="2026-09-10", show_state="Yes", owner_id=OWNER,
            lead_owner_id=lead_owner, meetings=[meeting("2026-09-10T17:00:00Z")], now=NOW,
        )
        wrong_sender = score_lead(**base, sms=[{
            "activity_at": "2026-09-10T16:00:00Z", "direction": "outbound",
            "status": "sent", "user_id": OWNER, "text": "See you soon",
        }])
        self.assertTrue(wrong_sender["precall_text"]["done"])
        self.assertEqual(
            wrong_sender["day_of_confirmation_text"], {"eligible": True, "done": False}
        )
        lead_owner_sent = score_lead(**base, sms=[{
            "activity_at": "2026-09-10T16:00:00Z", "direction": "outbound",
            "status": "sent", "user_id": lead_owner, "text": "See you soon",
        }])
        self.assertTrue(lead_owner_sent["day_of_confirmation_text"]["done"])

    def test_day_of_text_must_arrive_before_the_meeting_begins(self):
        base = dict(
            booked_date="2026-09-10", show_state="Yes", owner_id=OWNER,
            lead_owner_id=OWNER, meetings=[meeting("2026-09-10T17:00:00Z")], now=NOW,
        )
        def sent(at):
            return [{"date_sent": at, "direction": "outbound", "status": "sent",
                     "user_id": OWNER, "text": "Confirmation"}]

        previous_day = score_lead(**base, sms=sent("2026-09-10T06:59:59Z"))
        next_day = score_lead(**base, sms=sent("2026-09-11T07:00:00Z"))
        self.assertEqual(previous_day["day_of_confirmation_text"], {"eligible": True, "done": False})
        self.assertEqual(next_day["day_of_confirmation_text"], {"eligible": True, "done": False})
        at_midnight = score_lead(**base, sms=sent("2026-09-10T07:00:00Z"))
        at_start = score_lead(**base, sms=sent("2026-09-10T17:00:00Z"))
        after_start = score_lead(**base, sms=sent("2026-09-10T17:00:01Z"))
        late_day = score_lead(**base, sms=sent("2026-09-11T06:59:59Z"))
        self.assertTrue(at_midnight["day_of_confirmation_text"]["done"])
        self.assertFalse(at_start["day_of_confirmation_text"]["done"])
        self.assertFalse(after_start["day_of_confirmation_text"]["done"])
        self.assertFalse(late_day["day_of_confirmation_text"]["done"])

    def test_day_of_text_waits_until_meeting_start_before_scoring_a_miss(self):
        base = dict(
            booked_date="2026-09-21", show_state="Yes", owner_id=OWNER,
            lead_owner_id=OWNER, meetings=[meeting("2026-09-21T17:00:00Z")],
        )
        before_start = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
        pending = score_lead(**base, now=before_start)
        self.assertEqual(pending["day_of_confirmation_text"], {"eligible": False, "done": False})
        sent = score_lead(**base, now=before_start, sms=[{
            "date_sent": "2026-09-21T15:00:00Z", "direction": "outbound",
            "status": "sent", "user_id": OWNER, "text": "See you soon",
        }])
        self.assertEqual(sent["day_of_confirmation_text"], {"eligible": False, "done": False})
        at_start = score_lead(
            **base, now=datetime(2026, 9, 21, 17, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(at_start["day_of_confirmation_text"], {"eligible": True, "done": False})
        after_meeting = score_lead(**base, now=NOW, sms=[{
            "date_sent": "2026-09-21T18:00:00Z", "direction": "outbound",
            "status": "sent", "user_id": OWNER, "text": "Following up",
        }])
        self.assertEqual(after_meeting["day_of_confirmation_text"], {"eligible": True, "done": False})

    def test_day_of_text_uses_send_time_when_activity_time_is_earlier(self):
        result = score_lead(
            booked_date="2026-09-10", show_state="Yes", owner_id=OWNER,
            lead_owner_id=OWNER, meetings=[meeting("2026-09-10T17:00:00Z")],
            sms=[{
                "activity_at": "2026-09-10T16:00:00Z",
                "date_sent": "2026-09-11T07:00:00Z",
                "direction": "outbound", "status": "sent", "user_id": OWNER,
                "text": "Message queued yesterday",
            }],
            now=NOW,
        )
        self.assertEqual(result["day_of_confirmation_text"], {"eligible": True, "done": False})

    def test_day_of_text_ignores_unsent_inbound_and_missing_owner(self):
        base = dict(
            booked_date="2026-09-10", show_state="Yes", owner_id=OWNER,
            meetings=[meeting("2026-09-10T17:00:00Z")], now=NOW,
        )
        invalid = [
            {"activity_at": "2026-09-10T16:00:00Z", "direction": "inbound", "status": "sent", "user_id": OWNER, "text": "Reply"},
            {"activity_at": "2026-09-10T16:00:00Z", "direction": "outbound", "status": "draft", "user_id": OWNER, "text": "Draft"},
            {"activity_at": "2026-09-10T16:00:00Z", "direction": "outbound", "status": "sent", "user_id": OWNER, "text": ""},
        ]
        result = score_lead(**base, lead_owner_id=OWNER, sms=invalid)
        self.assertEqual(result["day_of_confirmation_text"], {"eligible": True, "done": False})
        unknown_owner = score_lead(**base, sms=invalid)
        self.assertEqual(unknown_owner["day_of_confirmation_text"], {"eligible": False, "done": False})

    def test_day_of_text_is_neutral_without_same_day_meeting(self):
        base = dict(booked_date="2026-09-10", show_state="Yes", owner_id=OWNER,
                    lead_owner_id=OWNER, now=NOW)
        without_meeting = score_lead(**base)
        canceled_meeting = score_lead(**base, meetings=[meeting("2026-09-10T17:00:00Z", status="canceled")])
        self.assertFalse(without_meeting["day_of_confirmation_text"]["eligible"])
        self.assertFalse(canceled_meeting["day_of_confirmation_text"]["eligible"])

    def test_future_call_is_neutral(self):
        result = score_lead(
            booked_date="2026-09-29",
            show_state="",
            owner_id=OWNER,
            lead_owner_id=OWNER,
            meetings=[meeting("2026-09-29T17:00:00Z", status="upcoming")],
            now=NOW,
        )
        self.assertFalse(result["loom_usage"]["eligible"])
        self.assertFalse(result["precall_text"]["eligible"])
        self.assertFalse(result["day_of_confirmation_text"]["eligible"])
        self.assertFalse(result["followup_task"]["eligible"])

    def test_no_show_only_requires_next_steps_set(self):
        result = score_lead(
            booked_date="2026-09-10",
            show_state="No",
            owner_id=OWNER,
            meetings=[meeting("2026-09-10T17:00:00Z", status="completed")],
            tasks=[{"id": "task_1", "lead_id": "lead_1", "assigned_to": OWNER, "date": "2026-09-20"}],
            now=NOW,
        )
        self.assertEqual(result["followup_task"], {"eligible": True, "done": True})
        self.assertFalse(result["followup_completed"]["eligible"])
        self.assertFalse(result["recap_email"]["eligible"])

    def test_next_steps_set_accepts_meeting_or_task(self):
        base = [meeting("2026-09-10T17:00:00Z")]
        via_meeting = score_lead(
            booked_date="2026-09-10",
            show_state="Yes",
            owner_id=OWNER,
            meetings=base + [meeting("2026-09-15T17:00:00Z", status="upcoming")],
            now=NOW,
        )
        via_task = score_lead(
            booked_date="2026-09-10",
            show_state="Yes",
            owner_id=OWNER,
            meetings=base,
            tasks=[{"id": "task_1", "lead_id": "lead_1", "assigned_to": OWNER, "date": "2026-09-15"}],
            now=NOW,
        )
        self.assertTrue(via_meeting["followup_task"]["done"])
        self.assertTrue(via_task["followup_task"]["done"])

    def test_old_overdue_task_is_not_a_post_call_next_step(self):
        result = score_lead(
            booked_date="2026-09-10",
            show_state="Yes",
            owner_id=OWNER,
            meetings=[meeting("2026-09-10T17:00:00Z")],
            tasks=[{
                "id": "old_task", "lead_id": "lead_1", "assigned_to": OWNER,
                "date": "2026-09-08", "date_created": "2026-09-07T17:00:00Z",
            }],
            task_completions=[{"task_id": "old_task", "activity_at": "2026-09-12T17:00:00Z"}],
            now=NOW,
        )
        self.assertEqual(result["followup_task"], {"eligible": True, "done": False})
        self.assertEqual(result["followup_completed"], {"eligible": False, "done": False})

    def test_task_created_after_call_can_set_a_next_step(self):
        result = score_lead(
            booked_date="2026-09-10",
            show_state="Yes",
            owner_id=OWNER,
            meetings=[meeting("2026-09-10T17:00:00Z")],
            tasks=[{
                "id": "new_task", "lead_id": "lead_1", "assigned_to": OWNER,
                "date": "2026-09-09", "date_created": "2026-09-10T18:00:00Z",
            }],
            now=NOW,
        )
        self.assertTrue(result["followup_task"]["done"])

    def test_next_steps_completed_accepts_showed_meeting_or_completed_task(self):
        base = [meeting("2026-09-10T17:00:00Z")]
        via_meeting = score_lead(
            booked_date="2026-09-10",
            show_state="Yes",
            owner_id=OWNER,
            meetings=base + [meeting("2026-09-15T17:00:00Z", status="completed")],
            now=NOW,
        )
        via_task = score_lead(
            booked_date="2026-09-10",
            show_state="Yes",
            owner_id=OWNER,
            meetings=base,
            tasks=[{"id": "task_1", "lead_id": "lead_1", "assigned_to": OWNER, "date": "2026-09-15"}],
            task_completions=[{"task_id": "task_1", "activity_at": "2026-09-12T17:00:00Z"}],
            now=NOW,
        )
        self.assertTrue(via_meeting["followup_completed"]["done"])
        self.assertTrue(via_task["followup_completed"]["done"])

    def test_next_step_completion_is_neutral_until_due(self):
        call = [meeting("2026-09-21T17:00:00Z")]
        now = datetime(2026, 9, 21, 19, 0, tzinfo=timezone.utc)
        upcoming = score_lead(
            booked_date="2026-09-21", show_state="Yes", owner_id=OWNER,
            meetings=call,
            tasks=[{"id": "future", "lead_id": "lead_1", "assigned_to": OWNER,
                    "date": "2026-09-25"}],
            now=now,
        )
        self.assertEqual(upcoming["followup_completed"], {"eligible": False, "done": False})
        overdue = score_lead(
            booked_date="2026-09-10", show_state="Yes", owner_id=OWNER,
            meetings=[meeting("2026-09-10T17:00:00Z")],
            tasks=[{"id": "overdue", "lead_id": "lead_1", "assigned_to": OWNER,
                    "date": "2026-09-15"}],
            now=NOW,
        )
        self.assertEqual(overdue["followup_completed"], {"eligible": True, "done": False})

    def test_no_next_step_does_not_count_as_uncompleted_next_step(self):
        result = score_lead(
            booked_date="2026-09-10", show_state="Yes", owner_id=OWNER,
            meetings=[meeting("2026-09-10T17:00:00Z")], now=NOW,
        )
        self.assertEqual(result["followup_task"], {"eligible": True, "done": False})
        self.assertEqual(result["followup_completed"], {"eligible": False, "done": False})
        self.assertTrue(result["recap_email"]["eligible"])

    def test_post_call_followup_accepts_email_or_sms_through_24_hours(self):
        base = [meeting("2026-09-10T17:00:00Z")]
        email = score_lead(
            booked_date="2026-09-10",
            show_state="Yes",
            owner_id=OWNER,
            meetings=base,
            emails=[{"activity_at": "2026-09-11T17:00:00Z", "direction": "outgoing", "status": "sent", "body_text": "Recap"}],
            now=NOW,
        )
        sms = score_lead(
            booked_date="2026-09-10",
            show_state="Yes",
            owner_id=OWNER,
            meetings=base,
            sms=[{"activity_at": "2026-09-10T18:00:00Z", "direction": "outbound", "status": "sent", "text": "Recap"}],
            now=NOW,
        )
        self.assertTrue(email["recap_email"]["done"])
        self.assertTrue(sms["recap_email"]["done"])

    def test_late_post_call_message_does_not_pass(self):
        result = score_lead(
            booked_date="2026-09-10",
            show_state="Yes",
            owner_id=OWNER,
            meetings=[meeting("2026-09-10T17:00:00Z")],
            sms=[{"activity_at": "2026-09-11T17:00:01Z", "direction": "outbound", "status": "sent", "text": "Late"}],
            now=NOW,
        )
        self.assertTrue(result["recap_email"]["eligible"])
        self.assertFalse(result["recap_email"]["done"])

    def test_followup_window_is_neutral_until_it_closes(self):
        call = [meeting("2026-09-21T17:00:00Z")]
        pending = score_lead(
            booked_date="2026-09-21", show_state="Yes", owner_id=OWNER,
            meetings=call, now=datetime(2026, 9, 21, 19, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(pending["recap_email"], {"eligible": False, "done": False})
        sent = score_lead(
            booked_date="2026-09-21", show_state="Yes", owner_id=OWNER,
            meetings=call,
            sms=[{"activity_at": "2026-09-21T18:00:00Z", "direction": "outbound", "status": "sent", "text": "Recap"}],
            now=datetime(2026, 9, 21, 19, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(sent["recap_email"], {"eligible": True, "done": True})

    def test_sent_timestamp_overrides_earlier_activity_timestamp(self):
        result = score_lead(
            booked_date="2026-09-10", show_state="Yes", owner_id=OWNER,
            meetings=[meeting("2026-09-10T17:00:00Z")],
            sms=[{
                "activity_at": "2026-09-10T16:00:00Z",
                "date_sent": "2026-09-10T18:00:00Z",
                "direction": "outbound", "status": "sent", "text": "See https://loom.com/demo",
            }],
            now=NOW,
        )
        self.assertFalse(result["loom_usage"]["done"])
        self.assertFalse(result["precall_text"]["done"])
        self.assertTrue(result["recap_email"]["done"])

    def test_deleted_and_unsent_communications_do_not_pass(self):
        result = score_lead(
            booked_date="2026-09-10",
            show_state="Yes",
            owner_id=OWNER,
            meetings=[meeting("2026-09-10T17:00:00Z")],
            notes=[{"activity_at": "2026-09-09T17:00:00Z", "status": "archived", "note": "loom.com/a"}],
            sms=[
                {"activity_at": "2026-09-10T16:00:00Z", "direction": "outbound", "status": "draft", "text": "See you"},
                {"activity_at": "2026-09-10T18:00:00Z", "direction": "outbound", "status": "draft", "text": "Recap"},
            ],
            now=NOW,
        )
        self.assertFalse(result["loom_usage"]["done"])
        self.assertFalse(result["precall_text"]["done"])
        self.assertFalse(result["recap_email"]["done"])

    def test_phase_is_equal_average_of_step_percentages(self):
        yes = {key: {"eligible": True, "done": True} for key in (
            "loom_usage", "precall_text", "day_of_confirmation_text",
            "followup_task", "followup_completed", "recap_email"
        )}
        no = {key: {"eligible": True, "done": False} for key in yes}
        neutral_post = {
            "loom_usage": {"eligible": True, "done": True},
            "precall_text": {"eligible": True, "done": False},
            "day_of_confirmation_text": {"eligible": True, "done": False},
            "followup_task": {"eligible": True, "done": True},
            "followup_completed": {"eligible": False, "done": False},
            "recap_email": {"eligible": False, "done": False},
        }
        agg = aggregate_rep_scores({OWNER: [yes, no, neutral_post]})[OWNER]
        self.assertEqual(agg["steps"]["day_of_confirmation_text"]["pct"], 33)
        self.assertEqual(agg["pre_call_pct"], 44)
        self.assertEqual(agg["steps"]["followup_task"]["pct"], 67)
        self.assertEqual(agg["steps"]["followup_completed"]["pct"], 50)
        self.assertEqual(agg["steps"]["recap_email"]["pct"], 50)
        self.assertEqual(agg["post_call_pct"], 56)


if __name__ == "__main__":
    unittest.main()
