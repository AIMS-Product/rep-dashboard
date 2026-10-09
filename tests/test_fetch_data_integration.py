import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_data  # noqa: E402


class ProductionAdherenceIntegrationTests(unittest.TestCase):
    @patch("fetch_data.fetch_mtd_totals", return_value=(10, 4, 3))
    @patch("fetch_data.fetch_meeting_data", return_value=({"Joe Dysert": 2}, {"Joe Dysert": 1}, {"Joe Dysert": 1}))
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
        self.assertEqual(joe["qualified"], 1)
        self.assertEqual(joe["close_rate"], 50.0)
        self.assertEqual(joe["revenue_per_lead"], 50.0)
        self.assertEqual(joe["aov"], 100.0)
        self.assertEqual(data["total_qualified"], 3)
        self.assertFalse(joe["exclude_meetings"])
        self.assertTrue(joe["is_manager"])

        fetch_opps.return_value = []
        no_deal_data = fetch_data.build_dashboard_data()
        self.assertIn("Joe Dysert", [row["name"] for row in no_deal_data["reps"]])
        joe_without_deals = next(row for row in no_deal_data["reps"] if row["name"] == "Joe Dysert")
        self.assertEqual(joe_without_deals["revenue_per_lead"], 0)
        self.assertIsNone(joe_without_deals["aov"])

    @patch("fetch_data.api_get")
    def test_qualified_uses_scorecard_field_on_booked_leads(self, api_get):
        owner = "user_rep"
        base = {
            "status_id": "active",
            f"custom.{fetch_data.CF_LEAD_OWNER_ID}": owner,
            f"custom.{fetch_data.CF_FIRST_SALES_CALL_BOOKED_ID}": "2026-10-02",
        }
        api_get.return_value = {"data": [
            {**base, f"custom.{fetch_data.CF_FIRST_CALL_SHOW_ID}": "Yes",
             f"custom.{fetch_data.CF_QUALIFIED_ID}": "Yes"},
            {**base, f"custom.{fetch_data.CF_FIRST_CALL_SHOW_ID}": "No",
             f"custom.{fetch_data.CF_QUALIFIED_ID}": "Yes"},
            {**base, "status_id": next(iter(fetch_data.EXCLUDED_LEAD_STATUSES)),
             f"custom.{fetch_data.CF_QUALIFIED_ID}": "Yes"},
        ], "has_more": False}

        booked, shown, qualified = fetch_data.fetch_meeting_data(
            2026, 10, "2026-10-04", {owner: "Joe Dysert"}, {"Joe Dysert": owner}
        )
        self.assertEqual((booked["Joe Dysert"], shown["Joe Dysert"], qualified["Joe Dysert"]), (2, 1, 2))

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
            candidate_booked_date_field=fetch_data.LATEST_BOOKED_DATE_FIELD,
            include_canceled_by_lead_status=True,
        )


if __name__ == "__main__":
    unittest.main()
