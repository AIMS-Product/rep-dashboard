import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from avoma_scores import score_for_close_email

class AvomaAliasTests(unittest.TestCase):
    def test_verified_alias(self):
        score = {"score":4, "count":5}
        self.assertEqual(score_for_close_email({"scott@modern-amenities.com":score}, " SCOTT@vendingpreneurshq.com "), score)

    def test_exact_match_takes_precedence(self):
        scores = {"scott@vendingpreneurshq.com":{"score":3}, "scott@modern-amenities.com":{"score":4}}
        self.assertEqual(score_for_close_email(scores,"scott@vendingpreneurshq.com"), {"score":3})

    def test_no_guessed_aliases(self):
        self.assertEqual(score_for_close_email({"unknown@modern-amenities.com":{"score":5}}, "unknown@vendingpreneurshq.com"), {})
