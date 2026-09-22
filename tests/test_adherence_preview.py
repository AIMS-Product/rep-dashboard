import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_adherence_preview import EXCLUDED_LEAD_STATUSES  # noqa: E402


class AdherencePreviewCohortTests(unittest.TestCase):
    def test_terminal_lead_statuses_are_excluded(self):
        expected = {
            "stat_aR2jBa8YnTNZmHAnPsnlQuinBdaXpSBCkZGP3UvoBlV": "lost",
            "stat_p3oblSTnbsyDAw4rWqZDePGYMOlKBgV2FjbqIMDrfvF": "disqualified",
            "stat_YV4ZngDB4IGjLjlOf0YTFEWuKZJ6fhNxVkzQkvKYfdB": "outside_us",
            "stat_U9MI7pqsvIjceTv3pCU7b1EghO8Q83h1HUcL6fGVyi6": "do_not_contact",
        }
        for status_id, reason in expected.items():
            self.assertEqual(EXCLUDED_LEAD_STATUSES.get(status_id), reason)


if __name__ == "__main__":
    unittest.main()
