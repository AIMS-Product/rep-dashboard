import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_adherence_preview import EXCLUDED_LEAD_STATUSES, scoring_rep_for_meeting  # noqa: E402


class AdherencePreviewCohortTests(unittest.TestCase):
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
