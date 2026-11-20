"""The joint scorecard replays the same meeting evidence without another CRM read."""
from datetime import datetime
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_adherence_preview import fetch_qualifying_process_cohort
from collect_scorecard_preview import collect
from shared_close_meetings import SharedCloseMeetings


class SharedCloseMeetingsTests(unittest.TestCase):
    def setUp(self):
        self.meeting = {
            "id": "meeting-one", "lead_id": "lead-one", "user_id": "rep-one",
            "title": "Vendingpreneurs Pinnacle - Next Steps",
            "starts_at": "2026-11-09T18:00:00Z", "ends_at": "2026-11-09T19:00:00Z",
            "users": ["rep-one", "rep-two"], "status": "completed",
        }
        self.catalog = SharedCloseMeetings([
            self.meeting,
            {**self.meeting, "id": "meeting-two", "lead_id": "lead-two", "status": "canceled"},
        ])

    def test_cohort_uses_the_shared_catalog_instead_of_listing_meetings(self):
        class Client:
            def paginate(self, endpoint):
                raise AssertionError(f"Unexpected {endpoint} read")

        with patch("build_adherence_preview.fetch_process_cohort",
                   return_value=[{"id": "lead-one"}, {"id": "lead-two"}]):
            cohort = fetch_qualifying_process_cohort(
                Client(), "2026-11-01", "2026-11-30", meetings=self.catalog.rows,
            )
        self.assertEqual(cohort, [{"id": "lead-one", "_process_candidate_date": "2026-11-09"}])

    def test_collect_reuses_scoped_meetings_with_end_times_and_attendees(self):
        class Client:
            request_count = 0

            def get(self, endpoint, params):
                return {'id': 'lead-one'}

            def paginate(self, endpoint, params):
                self.request_count += 1
                if endpoint == "/activity/meeting/":
                    raise AssertionError("Unexpected meeting read")
                return []

        lead = {"id": "lead-one"}
        dashboard = {
            "reps": [{"rep_owner_id": "rep-one", "adherence": {
                "lead_cohorts": {"recap_email": {"completed": [lead]}},
            }}],
            "adherence_meta": {"period": {"month": "2026-11"}},
        }
        result = collect(Client(), dashboard, opportunities=[], shared_meetings=self.catalog,
                         as_of=datetime(2026, 11, 20, tzinfo=ZoneInfo("America/Los_Angeles")))
        self.assertEqual(result["activities"]["meetings"], {"lead-one": [self.meeting]})
        self.assertEqual(result["meta"]["api_requests"], 3)

    def test_empty_catalog_is_distinct_from_no_catalog(self):
        with patch("build_adherence_preview.fetch_process_cohort", return_value=[]):
            self.assertEqual(fetch_qualifying_process_cohort(
                object(), "2026-11-01", "2026-11-30", meetings=SharedCloseMeetings([]).rows,
            ), [])
