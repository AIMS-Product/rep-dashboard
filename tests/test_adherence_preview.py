import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from adherence_rules import LOST_STATUS_ID, STEP_META, aggregate_rep_scores  # noqa: E402
from build_adherence_preview import (  # noqa: E402
    EXCLUDED_LEAD_STATUSES,
    add_adherence_to_dashboard,
    build_lead_cohorts,
    close_lead_url,
    scoring_rep_for_meeting,
)


class AdherencePreviewCohortTests(unittest.TestCase):
    def test_fixed_extract_keeps_upcoming_first_call_neutral_at_extract_end(self):
        lead = {
            "id": "lead_future", "display_name": "Future Customer", "status_id": "active",
            "custom.cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq": "2026-10-05",
            "custom.cf_OPyvpU45RdvjLqfm8V1VWwNxrGKogEH2IBJmfCj0Uhq": "",
            "custom.cf_gOfS9pFwext58oberEegLyix8hZzeHrxhCZOVh3P3rd": "user_rep",
        }
        extract = {
            "meta": {"month": "2026-10", "complete": True,
                     "started_at": "2026-10-05T12:00:00-07:00",
                     "ended_at": "2026-10-05T13:00:00-07:00", "api_requests": 7},
            "users": {"user_rep": "Rep Example"}, "leads": [lead],
            "activities": {"meetings": {"lead_future": [{
                "id": "meeting_future", "lead_id": "lead_future",
                "starts_at": "2026-10-05T21:00:00Z", "status": "upcoming", "user_id": "user_rep",
            }]}, "emails": {}, "sms": {}, "notes": {}, "task_completions": {}},
            "tasks": {},
        }
        result = add_adherence_to_dashboard(
            {"month_label": "October 2026", "reps": [{"name": "Rep Example"}]},
            preview_only=True, extract=extract,
        )
        lead_result = result["reps"][0]["adherence"]["lead_results"][0]
        self.assertEqual(set(lead_result["steps"].values()), {"Neutral"})
        self.assertEqual(result["adherence_meta"]["generated_at"], extract["meta"]["ended_at"])

    def test_lost_lead_remains_visible_and_exempts_sales_next_step_cells(self):
        lead = {
            "id": "lead_lost", "display_name": "Lost Customer", "status_id": LOST_STATUS_ID,
            "status_label": "💔 Lost",
            "custom.cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq": "2026-10-01",
            "custom.cf_OPyvpU45RdvjLqfm8V1VWwNxrGKogEH2IBJmfCj0Uhq": "Yes",
            "custom.cf_gOfS9pFwext58oberEegLyix8hZzeHrxhCZOVh3P3rd": "user_rep",
        }
        extract = {
            "meta": {"month": "2026-10", "complete": True,
                     "started_at": "2026-10-05T12:00:00-07:00",
                     "ended_at": "2026-10-05T13:00:00-07:00", "api_requests": 7},
            "users": {"user_rep": "Rep Example"}, "leads": [lead],
            "activities": {"meetings": {"lead_lost": [{
                "id": "meeting_lost", "lead_id": "lead_lost",
                "starts_at": "2026-10-01T17:00:00Z", "status": "completed", "user_id": "user_rep",
            }]}, "emails": {}, "sms": {}, "notes": {}, "task_completions": {}},
            "tasks": {},
        }
        result = add_adherence_to_dashboard(
            {"month_label": "October 2026", "reps": [{"name": "Rep Example"}]},
            preview_only=True, extract=extract,
        )
        adherence = result["reps"][0]["adherence"]
        lead_result = adherence["lead_results"][0]
        self.assertEqual(lead_result["steps"]["followup_task"], "Exempt")
        self.assertEqual(lead_result["steps"]["followup_completed"], "Exempt")
        self.assertEqual(adherence["steps"]["followup_task"]["exempt"], 1)

    def test_lead_lists_match_completed_missed_and_exempt_counts_without_neutral_leads(self):
        def evidence(**overrides):
            row = {key: {"eligible": False, "done": False} for key in STEP_META}
            row.update(overrides)
            return row

        leads = [
            ({"id": "lead_done", "name": "Acme", "booked_date": "2026-10-02"},
             evidence(precall_text={"eligible": True, "done": True})),
            ({"id": "lead_missed", "name": "Beta", "booked_date": "2026-10-01"},
             evidence(precall_text={"eligible": True, "done": False})),
            ({"id": "lead_exempt", "name": "Won", "booked_date": "2026-10-04", "status_label": "Closed / Won"},
             evidence(precall_text={"eligible": False, "done": False, "exempt": True})),
            ({"id": "lead_neutral", "name": "Gamma", "booked_date": "2026-10-03"},
             evidence()),
        ]
        aggregate = aggregate_rep_scores({"rep": [row for _, row in leads]})["rep"]
        cohorts = build_lead_cohorts(leads, aggregate)
        self.assertEqual([lead["id"] for lead in cohorts["precall_text"]["completed"]], ["lead_done"])
        self.assertEqual([lead["id"] for lead in cohorts["precall_text"]["missed"]], ["lead_missed"])
        self.assertEqual([lead["id"] for lead in cohorts["precall_text"]["exempt"]], ["lead_exempt"])
        self.assertEqual(cohorts["day_of_confirmation_text"], {"completed": [], "missed": [], "exempt": []})

    def test_lead_lists_reject_a_count_mismatch(self):
        lead = {"id": "lead_1", "name": "Acme", "booked_date": "2026-10-02"}
        evidence = {key: {"eligible": False, "done": False} for key in STEP_META}
        aggregate = aggregate_rep_scores({"rep": [evidence]})["rep"]
        aggregate["steps"]["precall_text"]["eligible"] = 1
        with self.assertRaisesRegex(ValueError, "precall_text"):
            build_lead_cohorts([(lead, evidence)], aggregate)

    def test_only_the_matching_close_lead_url_is_accepted(self):
        lead = {"id": "lead_private", "html_url": "https://app.close.com/lead/lead_private/"}
        self.assertEqual(close_lead_url(lead), lead["html_url"])
        lead["html_url"] = "https://example.com/lead/lead_private/"
        self.assertEqual(close_lead_url(lead), "")
        lead["html_url"] = "https://app.close.com/lead/lead_other/"
        self.assertEqual(close_lead_url(lead), "")

    def test_public_dashboard_serializes_lead_drilldowns_but_not_full_results(self):
        dashboard = {"month_label": "October 2026", "reps": [{"name": "Rep Example"}]}
        lead = {
            "id": "lead_private", "name": None, "display_name": "Private Customer",
            "html_url": "https://app.close.com/lead/lead_private/",
            "status_id": "active",
            "First Sales Call Booked Date": "2026-10-01",
            "Lead Owner": "Rep Example", "First Call Show Up (Opp)": "No",
        }
        meeting = {"starts_at": "2026-10-01T17:00:00Z", "user_id": "user_rep"}
        activities = [
            {"meetings": {"lead_private": [meeting]}},
            {"emails": {}, "sms": {}, "notes": {}, "task_completions": {}},
        ]
        with patch("build_adherence_preview.load_env_value", return_value="test-key"), \
             patch("build_adherence_preview.CloseClient"), \
             patch("build_adherence_preview.fetch_users", return_value={"user_rep": "Rep Example"}), \
             patch("build_adherence_preview.fetch_cohort", return_value=[lead]), \
             patch("build_adherence_preview.fetch_activities", side_effect=activities * 2), \
             patch("build_adherence_preview.fetch_tasks_by_lead", return_value={}):
            import copy
            production = add_adherence_to_dashboard(copy.deepcopy(dashboard), preview_only=False)
            preview = add_adherence_to_dashboard(copy.deepcopy(dashboard), preview_only=True)
        production_lists = production["reps"][0]["adherence"]["lead_cohorts"]
        self.assertEqual(
            production_lists["precall_text"]["missed"][0]["name"],
            "Private Customer",
        )
        self.assertEqual(
            production_lists["precall_text"]["missed"][0]["url"],
            "https://app.close.com/lead/lead_private/",
        )
        listed = preview["reps"][0]["adherence"]["lead_cohorts"]["precall_text"]["missed"]
        self.assertEqual([row["name"] for row in listed], ["Private Customer"])
        self.assertEqual(listed[0]["url"], "https://app.close.com/lead/lead_private/")
        self.assertEqual(listed[0]["scored_call_at"], "2026-10-01T10:00:00-07:00")
        self.assertEqual(preview["reps"][0]["adherence"]["lead_results"][0]["steps"]["followup_task"], "Missed")
        self.assertNotIn("lead_results", production["reps"][0]["adherence"])

    def test_terminal_lead_statuses_are_excluded(self):
        expected = {
            "stat_p3oblSTnbsyDAw4rWqZDePGYMOlKBgV2FjbqIMDrfvF": "disqualified",
            "stat_YV4ZngDB4IGjLjlOf0YTFEWuKZJ6fhNxVkzQkvKYfdB": "outside_us",
            "stat_U9MI7pqsvIjceTv3pCU7b1EghO8Q83h1HUcL6fGVyi6": "do_not_contact",
        }
        for status_id, reason in expected.items():
            self.assertEqual(EXCLUDED_LEAD_STATUSES.get(status_id), reason)

    def test_lost_leads_remain_in_process_cohort(self):
        self.assertNotIn(
            "stat_aR2jBa8YnTNZmHAnPsnlQuinBdaXpSBCkZGP3UvoBlV",
            EXCLUDED_LEAD_STATUSES,
        )

    def test_meeting_rep_takes_precedence_over_current_owner(self):
        rep_id, reason = scoring_rep_for_meeting(
            {"user_id": "user_meeting", "users": ["user_meeting"]},
            "user_current",
            {"user_meeting", "user_current"},
        )
        self.assertEqual((rep_id, reason), ("user_meeting", "meeting_rep"))

    def test_unassigned_meeting_falls_back_to_current_owner(self):
        self.assertEqual(
            scoring_rep_for_meeting({"users": []}, "user_current", {"user_current"}),
            ("user_current", "owner_fallback"),
        )

    def test_sole_visible_meeting_attendee_is_used_when_primary_is_not_visible(self):
        self.assertEqual(
            scoring_rep_for_meeting(
                {"user_id": "user_manager", "users": ["user_manager", "user_rep"]},
                "user_current",
                {"user_rep", "user_current"},
            ),
            ("user_rep", "meeting_rep"),
        )

    def test_meeting_assigned_only_to_nonvisible_rep_is_excluded(self):
        self.assertEqual(
            scoring_rep_for_meeting(
                {"user_id": "user_other", "users": ["user_other"]},
                "user_current",
                {"user_current"},
            ),
            (None, "meeting_rep_not_visible"),
        )

    def test_meeting_with_two_visible_reps_and_no_primary_is_ambiguous(self):
        self.assertEqual(
            scoring_rep_for_meeting(
                {"users": ["user_a", "user_b"]},
                "user_current",
                {"user_a", "user_b", "user_current"},
            ),
            (None, "ambiguous_meeting_rep"),
        )


if __name__ == "__main__":
    unittest.main()
