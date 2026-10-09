import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from adherence_rules import LOST_STATUS_ID, STEP_META, aggregate_rep_scores  # noqa: E402
from build_adherence_preview import (  # noqa: E402
    EXCLUDED_LEAD_STATUSES,
    add_adherence_to_dashboard,
    build_lead_cohorts,
    close_lead_url,
    fetch_cohort,
    fetch_process_cohort,
    fetch_qualifying_process_cohort,
    has_active_first_meeting,
    process_candidate_date,
    scoring_rep_for_meeting,
    LATEST_BOOKED_DATE_FIELD,
)
from qualifying_meeting_rules import SOURCE_SHA256, latest_qualifying_dates_in_period  # noqa: E402


class AdherencePreviewCohortTests(unittest.TestCase):
    def test_activity_dates_keep_intended_next_steps_exceptions(self):
        meetings = [
            {"id": "scraper", "lead_id": "lead_scraper",
             "title": "Vendingpreneurs Momentum - Next Steps with Pat",
             "starts_at": "2026-10-08T00:30:00Z", "status": "completed"},
            {"id": "vendhub", "lead_id": "lead_vendhub", "title": "VendHub Next Steps Call",
             "starts_at": "2026-10-07T17:00:00Z", "status": "upcoming"},
            {"id": "ordinary", "lead_id": "lead_followup", "title": "Vendingpreneur Follow-up",
             "starts_at": "2026-10-07T17:00:00Z", "status": "completed"},
            {"id": "rescheduled", "lead_id": "lead_reschedule",
             "title": "Vendingpreneurs Consultation - Rescheduled",
             "starts_at": "2026-10-07T17:00:00Z", "status": "upcoming"},
            {"id": "declined", "lead_id": "lead_declined", "title": "Vendingpreneurs Consultation",
             "starts_at": "2026-10-07T17:00:00Z", "status": "declined-by-org"},
        ]
        self.assertEqual(latest_qualifying_dates_in_period(meetings, "2026-10-07", "2026-10-07"), {
            "lead_scraper": "2026-10-07", "lead_vendhub": "2026-10-07",
        })

    def test_pinned_classifier_matches_updater_source_in_workspace(self):
        source = Path(__file__).resolve().parents[2] / "close-first-sales-meeting" / "update_field.py"
        if source.exists():
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), SOURCE_SHA256)

    def test_monthly_activity_cohort_uses_latest_qualifying_date_inside_month(self):
        first = "custom.cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq"
        latest = f"custom.{LATEST_BOOKED_DATE_FIELD[0]}"
        middle = {"id": "lead_middle", first: "2026-09-01", latest: "2026-11-02"}
        unpopulated = {"id": "lead_unpopulated"}
        meetings = [
            {"id": "a", "lead_id": "lead_middle", "title": "Vendingpreneurs Consultation",
             "starts_at": "2026-10-07T17:00:00Z", "status": "completed"},
            {"id": "b", "lead_id": "lead_middle", "title": "Vendingpreneurs Consultation",
             "starts_at": "2026-10-09T17:00:00Z", "status": "upcoming"},
            {"id": "c", "lead_id": "lead_middle", "title": "Vendingpreneurs Consultation",
             "starts_at": "2026-10-12T17:00:00Z", "status": "declined-by-org"},
            {"id": "d", "lead_id": "lead_middle", "title": "Vendingpreneur Follow-up",
             "starts_at": "2026-10-14T17:00:00Z", "status": "upcoming"},
            {"id": "e", "lead_id": "lead_unpopulated",
             "title": "Vendingpreneurs Summit - Next Steps with Pat",
             "starts_at": "2026-10-08T17:00:00Z", "status": "completed"},
        ]
        client = Mock()
        client.paginate.side_effect = [iter(meetings), iter([]), iter([])]
        client.get.side_effect = lambda endpoint: {
            "/lead/lead_middle/": middle, "/lead/lead_unpopulated/": unpopulated,
        }[endpoint]
        selected = fetch_qualifying_process_cohort(client, "2026-10-01", "2026-10-31")
        self.assertEqual(selected, [middle | {"_process_candidate_date": "2026-10-09"}])
        self.assertEqual(process_candidate_date(selected[0], "2026-10-01", "2026-10-31"),
                         ("2026-10-09", "meeting_activity"))

    def test_process_query_unions_first_and_latest_without_duplicate_leads(self):
        first = {"id": "lead_katina"}
        both = {"id": "lead_both"}
        latest = {"id": "lead_vyomesh"}
        client = Mock()
        client.paginate.side_effect = [iter([first, both]), iter([both, latest])]
        self.assertEqual(fetch_process_cohort(client, "2026-10-07", "2026-10-07"),
                         [first, both, latest])
        queries = [call.args[1]["query"] for call in client.paginate.call_args_list]
        self.assertIn("First Sales Call Booked Date", queries[0])
        self.assertIn("Latest Sales Call Booked Date", queries[1])

    def test_first_date_fallback_keeps_joe_but_rejects_declined_activity(self):
        first = "cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq"
        latest = LATEST_BOOKED_DATE_FIELD[0]
        katina = {f"custom.{first}": "2026-10-07", f"custom.{latest}": "2026-12-07"}
        maria = {f"custom.{first}": "2026-10-07", f"custom.{latest}": "2026-10-12"}
        self.assertEqual(process_candidate_date(katina, "2026-10-07", "2026-10-07"),
                         ("2026-10-07", "first_fallback"))
        self.assertEqual(process_candidate_date(maria, "2026-10-07", "2026-10-07"),
                         ("2026-10-07", "first_fallback"))
        middle = {f"custom.{first}": "2026-10-01", f"custom.{latest}": "2026-10-12",
                  "_process_candidate_date": "2026-10-07"}
        self.assertEqual(process_candidate_date(middle, "2026-10-07", "2026-10-07"),
                         ("2026-10-07", "meeting_activity"))
        self.assertTrue(has_active_first_meeting([
            {"starts_at": "2026-10-07T17:30:00Z", "status": "completed"},
        ], "2026-10-07"))
        self.assertFalse(has_active_first_meeting([
            {"starts_at": "2026-10-07T11:30:00Z", "status": "declined-by-org"},
        ], "2026-10-07"))

    def test_monthly_process_query_uses_latest_date(self):
        client = Mock()
        client.paginate.return_value = iter([])
        self.assertEqual(fetch_cohort(
            client, 2026, 10, field_name=LATEST_BOOKED_DATE_FIELD[1],
        ), [])
        client.paginate.assert_called_once_with("/lead/", {
            "query": '"Latest Sales Call Booked Date" >= "2026-10-01" '
                     '"Latest Sales Call Booked Date" <= "2026-10-31"',
        })

    def test_scott_october_seventh_latest_process_cohort_uses_rebooked_dates(self):
        first_id = "cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq"
        latest_id = "cf_2PQJIcagevN5HvUHfmWGWR22pCvzLZk6tJPTicDvuS3"
        owner_id = "cf_gOfS9pFwext58oberEegLyix8hZzeHrxhCZOVh3P3rd"
        # These titles, timestamps, and activity statuses are from Scott's Close meetings.
        cases = [
            ("Milton Hunt", "lead_TWEOH11AgRpxBqVYFjCyHLlcICTf2Q5CQqHDDF8Jgfi", "2026-10-07", "2026-10-07T16:00:00Z", "Milton Hunt and Vendingprenuers Consultation", "stat_hWIGHjzyNpl4YjIFSFz3VK4fp2ny10SFJLKAihmo4KT"),
            ("Joshua Hernandez", "lead_RozBCmk1dy84idSw4wcralSl9OudIXMIvSJn1PNmTVr", "2026-10-07", "2026-10-07T18:00:00Z", "Joshua Hernandez and Vendingprenuers Consultation", "active"),
            ("Alisa Boronda", "lead_3myJq3tbid3IUhnwswYzRZHwv81q3uP6tugK9Rf6QVs", "2026-10-07", "2026-10-07T20:00:00Z", "Alisa Boronda and Vendingprenuers Consultation", "active"),
            ("Vyomesh Mistry", "lead_p8FVwdW3af21WyetiK5gs96J94EmQjgTrrQCTAhnhNE", "2026-10-01", "2026-10-07T21:00:00Z", "Vending Consult Call with Vyomesh and Scott Seymour", "active"),
            ("Sheri Lindemann", "lead_8K0sP11r3ocvyyeqpNl3yyBCyimVXsBUq0MKdkBkBtt", "2026-10-07", "2026-10-07T22:00:00Z", "Sheri Lindemann and Vendingprenuers Consultation", "active"),
            ("Lemuel", "lead_YSVxXIjqoRkQX9kI8GdwClbH7Fz6bXaWMKgLvbxfqi0", "2026-04-15", "2026-10-07T22:45:00Z", "Vending Discovery Call - Next Steps with lemuel and Scott Seymour", "active"),
        ]
        leads = [{"id": lead_id, "display_name": name, "status_id": status,
                  f"custom.{first_id}": first_date, f"custom.{latest_id}": "2026-10-07",
                  f"custom.{owner_id}": "user_scott"}
                 for name, lead_id, first_date, _, _, status in cases]
        meetings = {lead_id: [{"lead_id": lead_id, "starts_at": starts_at,
                               "title": title, "status": "completed", "user_id": "user_scott"}]
                    for _, lead_id, _, starts_at, title, _ in cases}
        extract = {
            "meta": {"month": "2026-10", "complete": True,
                     "started_at": "2026-10-08T09:00:00-07:00",
                     "ended_at": "2026-10-08T09:01:00-07:00", "api_requests": 1},
            "users": {"user_scott": "Scott Seymour"}, "leads": leads,
            "activities": {"meetings": meetings, "emails": {}, "sms": {}, "calls": {}, "notes": {}},
            "tasks": {},
        }
        result = add_adherence_to_dashboard(
            {"month_label": "October 2026", "reps": [{"name": "Scott Seymour"}]},
            preview_only=True, extract=extract,
            include_canceled_by_lead_status=True,
            candidate_booked_date_field=(latest_id, "Latest Sales Call Booked Date"),
        )
        scored = result["reps"][0]["adherence"]["lead_results"]
        self.assertEqual({row["name"] for row in scored}, {case[0] for case in cases})
        self.assertEqual({row["booked_date"] for row in scored}, {"2026-10-07"})
        self.assertEqual({row["scored_call_at"][:10] for row in scored}, {"2026-10-07"})
        self.assertEqual(leads[3][f"custom.{first_id}"], "2026-10-01")
        self.assertEqual(leads[5][f"custom.{first_id}"], "2026-04-15")

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

    def test_lost_lead_remains_visible_and_exempts_all_post_call_cells(self):
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
        self.assertEqual(lead_result["steps"]["task_created"], "Exempt")
        self.assertEqual(lead_result["steps"]["fu_meeting_created"], "Exempt")
        self.assertEqual(lead_result["steps"]["recap_email"], "Exempt")
        self.assertEqual(adherence["steps"]["task_created"]["exempt"], 1)

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
        self.assertEqual(preview["reps"][0]["adherence"]["lead_results"][0]["steps"]["task_created"], "Missed")
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
