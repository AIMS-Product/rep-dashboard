import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_data  # noqa: E402


class ProductionAdherenceIntegrationTests(unittest.TestCase):
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
