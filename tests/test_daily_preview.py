import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_yesterday_preview import (  # noqa: E402
    daily_show_rate,
    fetch_latest_process_cohort,
    serialize_preview,
    build,
)


class DailyPreviewMetricTests(unittest.TestCase):
    def test_show_rate_is_recalculated_to_one_decimal_place(self):
        self.assertEqual(daily_show_rate(3, 1), 33.3)
        self.assertEqual(daily_show_rate(3, 2), 66.7)

    def test_show_rate_with_no_booked_calls_is_zero_for_na_rendering(self):
        self.assertEqual(daily_show_rate(0, 0), 0)

    def test_serialized_daily_rep_keeps_recalculated_show_rate(self):
        serialized = serialize_preview({
            "daily_meta": {},
            "adherence_meta": {},
            "reps": [{"name": "Rep Example", "booked": 3, "shown": 1,
                      "qualified": 0, "show_rate": 33.3}],
        })
        self.assertEqual(serialized["reps"][0]["show_rate"], 33.3)

    def test_eod_process_candidates_include_a_qualifying_meeting_on_latest_date(self):
        field = "cf_2PQJIcagevN5HvUHfmWGWR22pCvzLZk6tJPTicDvuS3"
        lead = {"id": "lead_scott", f"custom.{field}": "2026-10-07",
                "custom.cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq": "2026-10-01"}
        client = Mock()
        client.paginate.return_value = [{
            "id": "meet_scott", "lead_id": "lead_scott", "title": "Vendingpreneurs Consultation",
            "starts_at": "2026-10-07T17:00:00Z", "status": "completed",
        }]
        client.get.return_value = lead
        self.assertEqual(fetch_latest_process_cohort(client, "2026-10-07"),
                         [lead | {"_process_candidate_date": "2026-10-07"}])
        client.get.assert_called_once_with("/lead/lead_scott/")

    def test_eod_process_candidates_include_missing_dates_and_exclude_followup_or_canceled(self):
        first = "custom.cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq"
        latest = "custom.cf_2PQJIcagevN5HvUHfmWGWR22pCvzLZk6tJPTicDvuS3"
        lead = {"id": "lead_middle", first: "2026-10-01", latest: "2026-10-12"}
        client = Mock()
        client.paginate.return_value = [
            {"id": "meet_middle", "lead_id": "lead_middle", "title": "Vendingpreneurs Consultation",
             "starts_at": "2026-10-08T00:30:00Z", "status": "completed"},
            {"id": "meet_scraper", "lead_id": "lead_scraper",
             "title": "Vendingpreneurs Momentum - Next Steps with Pat",
             "starts_at": "2026-10-07T17:00:00Z", "status": "completed"},
            {"id": "meet_unpopulated", "lead_id": "lead_unpopulated",
             "title": "Vendingpreneurs Summit - Next Steps with Pat",
             "starts_at": "2026-10-07T17:00:00Z", "status": "completed"},
            {"id": "meet_followup", "lead_id": "lead_followup",
             "title": "Vendingpreneur Follow-up", "starts_at": "2026-10-07T17:00:00Z",
             "status": "completed"},
            {"id": "meet_canceled", "lead_id": "lead_canceled",
             "title": "Vendingpreneurs Consultation", "starts_at": "2026-10-07T17:00:00Z",
             "status": "declined-by-org"},
        ]
        client.get.side_effect = lambda endpoint: {
            "/lead/lead_middle/": lead,
            "/lead/lead_scraper/": {"id": "lead_scraper", first: "2026-09-01", latest: "2026-10-12"},
            "/lead/lead_unpopulated/": {"id": "lead_unpopulated"},
        }[endpoint]
        candidates = fetch_latest_process_cohort(client, "2026-10-07")
        self.assertEqual({row["id"] for row in candidates}, {"lead_middle", "lead_scraper", "lead_unpopulated"})
        self.assertTrue(all(row["_process_candidate_date"] == "2026-10-07" for row in candidates))
        self.assertEqual(client.get.call_count, 3)

    def test_colbys_real_qualifying_title_and_date_need_no_booked_fields(self):
        client = Mock()
        client.paginate.return_value = [{
            "id": "acti_2xjDGyXxoCe1f1yyaOST56dyf04tTdEwRrRFVf1RIhU",
            "lead_id": "lead_8fpI1TR6OA0rHUsnW14u917HD4ya5qqg1wxhftIYBht",
            "title": "Vendingpreneurs Keystone - Next Steps with Colby and Joseph Vaughan",
            "starts_at": "2026-10-08T22:00:00+00:00", "status": "completed",
            "user_id": "user_7HSxi55O8q5jO11khvrTcAGoL2nlcoa3kZ6loAY6i78",
        }]
        client.get.return_value = {
            "id": "lead_8fpI1TR6OA0rHUsnW14u917HD4ya5qqg1wxhftIYBht",
            "display_name": "Colby Anderson",
        }
        self.assertEqual(fetch_latest_process_cohort(client, "2026-10-08"), [
            client.get.return_value | {"_process_candidate_date": "2026-10-08"},
        ])
        client.paginate.assert_called_once_with("/activity/meeting/")

    def test_daily_booked_metrics_and_process_use_separate_cohorts(self):
        metric_lead = {"id": "lead_original", "display_name": "Original"}
        process_lead = {"id": "lead_rebooked", "display_name": "Rebooked"}
        activities = {kind: {} for kind in ("emails", "sms", "calls", "notes", "meetings")}
        client = Mock(request_count=0)

        def record_process(dashboard, **kwargs):
            self.assertEqual(kwargs["extract"]["leads"], [process_lead])
            self.assertEqual(kwargs["candidate_booked_date_field"][1], "Latest Sales Call Booked Date")
            self.assertTrue(kwargs["include_canceled_by_lead_status"])
            self.assertEqual(kwargs["candidate_date_range"], ("2026-10-07", "2026-10-07"))
            dashboard["adherence_meta"] = {"period": {}}
            return dashboard

        with TemporaryDirectory() as directory, \
             patch("build_yesterday_preview.load_env_value", return_value="test-key"), \
             patch("build_yesterday_preview.CloseClient", return_value=client), \
             patch("build_yesterday_preview.fetch_users", return_value={}), \
             patch("build_yesterday_preview.fetch_leads_by_booked_date_range", return_value=[metric_lead]), \
             patch("build_yesterday_preview.fetch_latest_process_cohort", return_value=[process_lead]), \
             patch("build_yesterday_preview.fetch_activities", return_value=activities), \
             patch("build_yesterday_preview.fetch_tasks_by_lead", return_value={}), \
             patch("build_yesterday_preview.aggregate_meeting_leads", return_value=({}, {}, {}, 0, 0)), \
             patch("build_yesterday_preview.meeting_lead_rows", return_value=[]), \
             patch("build_yesterday_preview.add_adherence_to_dashboard", side_effect=record_process):
            extract_path = Path(directory) / "extract.json"
            build(Path(directory) / "output.json", extract_path,
                  report_date="2026-10-07", write_local_preview=False)
            extract = json.loads(extract_path.read_text())
        self.assertEqual(extract["leads"], [metric_lead])
        self.assertEqual(extract["process_extract"]["leads"], [process_lead])


if __name__ == "__main__":
    unittest.main()
