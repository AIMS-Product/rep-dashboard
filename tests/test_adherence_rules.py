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
            meetings=[meeting("2026-09-10T17:00:00Z")],
            notes=[{"activity_at": "2026-09-09T17:00:00Z", "note": "Watch https://loom.com/a"}],
            sms=[{"activity_at": "2026-09-10T16:59:00Z", "direction": "outbound", "status": "sent", "text": "See you soon"}],
            now=NOW,
        )
        self.assertEqual(result["loom_usage"], {"eligible": True, "done": True})
        self.assertEqual(result["precall_text"], {"eligible": True, "done": True})

    def test_future_call_is_neutral(self):
        result = score_lead(
            booked_date="2026-09-29",
            show_state="",
            owner_id=OWNER,
            meetings=[meeting("2026-09-29T17:00:00Z", status="upcoming")],
            now=NOW,
        )
        self.assertFalse(result["loom_usage"]["eligible"])
        self.assertFalse(result["precall_text"]["eligible"])
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
            "loom_usage", "precall_text", "followup_task", "followup_completed", "recap_email"
        )}
        no = {key: {"eligible": True, "done": False} for key in yes}
        neutral_post = {
            "loom_usage": {"eligible": True, "done": True},
            "precall_text": {"eligible": True, "done": False},
            "followup_task": {"eligible": True, "done": True},
            "followup_completed": {"eligible": False, "done": False},
            "recap_email": {"eligible": False, "done": False},
        }
        agg = aggregate_rep_scores({OWNER: [yes, no, neutral_post]})[OWNER]
        self.assertEqual(agg["pre_call_pct"], 50)
        self.assertEqual(agg["steps"]["followup_task"]["pct"], 67)
        self.assertEqual(agg["steps"]["followup_completed"]["pct"], 50)
        self.assertEqual(agg["steps"]["recap_email"]["pct"], 50)
        self.assertEqual(agg["post_call_pct"], 56)


if __name__ == "__main__":
    unittest.main()
