import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_yesterday_preview import daily_show_rate, serialize_preview  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
