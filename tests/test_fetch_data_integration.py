import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_data  # noqa: E402


class ProductionAdherenceIntegrationTests(unittest.TestCase):
    @patch("fetch_data.fetch_mtd_totals", return_value=(10, 4))
    @patch("fetch_data.fetch_meeting_data", return_value=({"Joe Dysert": 2}, {"Joe Dysert": 1}))
    @patch("fetch_data.fetch_closed_won_opportunities")
    @patch("fetch_data.fetch_org_users")
    @patch.object(fetch_data, "CLOSE_API_KEY", "test-key")
    def test_manager_meetings_and_close_rate_are_included(
        self, fetch_users, fetch_opps, _fetch_meetings, _fetch_totals
    ):
        fetch_users.return_value = {"joe-id": "Joe Dysert"}
        fetch_opps.return_value = [{
            "user_id": "joe-id", "value": 10000, "lead_id": "lead-1", "date_won": "2026-10-01"
        }]

        data = fetch_data.build_dashboard_data()
        joe = next(row for row in data["reps"] if row["name"] == "Joe Dysert")

        self.assertEqual(joe["deals"], 1)
        self.assertEqual(joe["booked"], 2)
        self.assertEqual(joe["shown"], 1)
        self.assertEqual(joe["close_rate"], 50.0)
        self.assertFalse(joe["exclude_meetings"])
        self.assertTrue(joe["is_manager"])

        fetch_opps.return_value = []
        no_deal_data = fetch_data.build_dashboard_data()
        self.assertIn("Joe Dysert", [row["name"] for row in no_deal_data["reps"]])

    @patch("fetch_data.add_adherence_to_dashboard")
    @patch("fetch_data.build_dashboard_data")
    def test_live_payload_is_enriched_before_writing(self, build_dashboard, add_adherence):
        base_payload = {"month_label": "September 2026", "reps": []}
        enriched_payload = {**base_payload, "adherence_meta": {"preview_only": False}}
        build_dashboard.return_value = base_payload
        add_adherence.return_value = enriched_payload

        result = fetch_data.build_live_dashboard_data()

        self.assertIs(result, enriched_payload)
        add_adherence.assert_called_once_with(
            base_payload,
            source="close_crm",
            preview_only=False,
        )


if __name__ == "__main__":
    unittest.main()
