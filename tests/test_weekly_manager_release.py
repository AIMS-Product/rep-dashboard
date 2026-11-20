"""Exercise the normal joint CLI, scorers, publishers and offline source boundaries."""

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

REPO = Path(__file__).resolve().parents[1]
DEPENDENCIES = REPO / ".reporting-dependencies"
if not DEPENDENCIES.is_dir():
    DEPENDENCIES = REPO.parent
sys.path.insert(0, str(REPO / "scripts"))

from shared_periods import PACIFIC, week_metadata, week_starts
from shared_sales_release import accept_release, verify_release


class WeeklyManagerReleaseTests(unittest.TestCase):
    def test_week_boundaries_follow_the_pacific_calendar(self):
        cutoff = datetime(2027, 1, 2, 9, tzinfo=PACIFIC)
        current, previous = week_starts(cutoff)
        self.assertEqual(current.isoformat(), "2026-12-28")
        self.assertEqual(week_metadata(current, cutoff)["end"], "2027-01-03")
        self.assertTrue(week_metadata(previous, cutoff)["complete"])
        cutoff = datetime(2026, 11, 3, 9, tzinfo=PACIFIC)
        _, previous = week_starts(cutoff)
        self.assertEqual(week_metadata(previous, cutoff)["end_exclusive"], "2026-11-02T00:00:00-08:00")

    def test_release_reconciles_periods_ownership_and_manager_scores(self):
        with TemporaryDirectory() as directory:
            release, traffic = build_fixture_release(Path(directory))
            manifest = verify_release(release)
            rep = read_json(release / "rep.json")
            manager = read_json(release / "manager.json")
            mtd = read_json(release / "mtd.json")
            self.assertEqual({view["publication"]["release_id"] for view in (rep, manager, mtd)},
                             {manifest["release_id"]})
            self.assertEqual([manager["total_booked"], rep["total_booked"], mtd["totals"]["occurred_calls"]],
                             [2, 2, 2])
            previous = manager["weeks"]["2026-10-26"]
            current = manager["weeks"]["2026-11-02"]
            self.assertEqual((previous["total_booked"], previous["total_deals"], previous["total_revenue"]),
                             (3, 1, 1250))
            self.assertEqual((current["total_booked"], current["total_deals"], current["total_revenue"]),
                             (1, 1, 750))
            self.assertEqual(current["total_unresolved_calls"], 1)
            self.assertEqual((previous["team_qa_score"], current["team_qa_score"], manager["team_qa_score"]),
                             (3.5, 3, 4))
            joe = find_rep(previous, "Joe Dysert")
            self.assertEqual((joe["pre_call_adherence"], joe["post_call_adherence"]), (100, 100))
            self.assertEqual(joe["post_call_12h"]["eligible"], 1)
            self.assertEqual(joe["adherence"]["lead_cohorts"]["recap_12h"]["completed"][0]["id"], "lead_joe")
            self.assertEqual(previous["team_post_call_12h"]["eligible"], 1)
            self.assertEqual(previous["team_post_call_adherence"], 0)
            self.assertEqual(previous["team_pre_call_adherence"], 0)

            scott = find_rep(previous, "Scott Seymour")
            self.assertEqual({row["meeting_id"] for row in scott["metric_details"]["booked"]},
                             {"meeting_transferred", "meeting_lost"})
            samples = scott["adherence"]["lead_cohorts"]
            self.assertIn("lead_lost", {row["id"] for row in samples["loom_usage"]["missed"]})
            self.assertEqual(samples["recap_12h"]["exempt"][0]["id"], "lead_lost")
            self.assertEqual(samples["recap_12h"]["missed"][0]["id"], "lead_transferred")
            self.assertTrue(samples["recap_12h"]["missed"][0]["reassigned"])
            self.assertEqual(find_rep(previous, "Luke Herman")["booked"], 0)
            warm = {row["name"]: row for row in rep["warm_followup_5d"]["groups"]}
            self.assertEqual(warm["Luke Herman"]["leads"][0]["id"], "lead_transferred")
            self.assertEqual(warm["Luke Herman"]["done"], 1)
            self.assertEqual(warm["Scott Seymour"]["eligible"], 0)
            self.assertEqual(sum(row["path"] == "/api/v1/activity/meeting/" for row in traffic), 1)
            self.assertEqual(sum(row["host"] == "api.avoma.com" for row in traffic), 1)

    def test_empty_week_pending_deadline_and_invalid_release_leave_last_snapshot(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            october = root / "october"
            october.mkdir()
            first, _ = build_fixture_release(october, cutoff="2026-10-31T17:00:00-07:00")
            published = root / "public"
            accepted = accept_release(first, published)
            empty = read_json(first / "manager.json")["weeks"]["2026-10-19"]
            self.assertEqual(empty["total_booked"], 0)
            self.assertEqual(empty["total_deals"], 0)
            self.assertIsNone(empty["team_close_rate"])

            monday = root / "monday"
            monday.mkdir()
            second, _ = build_fixture_release(
                monday, cutoff="2026-11-02T04:00:00-08:00", manager_base=first / "manager.json",
            )
            prior_week = read_json(second / "manager.json")["weeks"]["2026-10-26"]
            joe = find_rep(prior_week, "Joe Dysert")
            self.assertEqual(joe["post_call_12h"]["eligible"], 0)
            self.assertEqual(joe["post_call_12h"]["pending"], 1)
            self.assertNotIn("weeks", prior_week)
            self.assertNotIn("publication", prior_week)
            (second / "manager.html").write_text("Truncated release")
            with self.assertRaisesRegex(ValueError, "hash"):
                accept_release(second, published)
            self.assertEqual(read_json(published / "current.json")["release_id"], accepted["release_id"])


def read_json(path):
    return json.loads(path.read_text())


def find_rep(view, name):
    return next(row for row in view["reps"] if row["name"] == name)


def build_fixture_release(directory, *, cutoff="2026-11-03T09:00:00-08:00", manager_base=None):
    site = directory / "provider"
    site.mkdir()
    shutil.copyfile(Path(__file__).with_name("fixtures") / "weekly_providers.py", site / "sitecustomize.py")
    traffic = directory / "traffic.jsonl"
    env = {**os.environ, "PYTHONPATH": str(site), "WEEKLY_FIXTURE_TRAFFIC": str(traffic),
           "CLOSE_API_KEY": "fixture-only", "AVOMA_API_KEY": "fixture-only", "PYTHONDONTWRITEBYTECODE": "1"}
    base = directory / "base.json"
    base.write_text(json.dumps({"team_quota": 5000, "month_label": "November 2026", "reps": [
        {"name": "Scott Seymour", "rep_owner_id": "user_scott", "lane": 1},
        {"name": "Luke Herman", "rep_owner_id": "user_luke", "lane": 2},
        {"name": "Joe Dysert", "rep_owner_id": "user_joe", "is_manager": True},
    ]}))
    release = directory / "release"
    mtd = DEPENDENCIES / "mtd"
    if not (mtd / "v2" / "archives" / "nav.json").exists():
        mtd = directory / "mtd"
        shutil.copytree(DEPENDENCIES / "mtd", mtd)
        nav = mtd / "v2" / "archives" / "nav.json"
        nav.parent.mkdir(parents=True, exist_ok=True)
        nav.write_text('{"months": [], "weeks": []}')
    command = [sys.executable, str(REPO / "scripts" / "publish_sales_dashboards.py"),
               "build", "--mtd-root", str(mtd), "--manager-root", str(DEPENDENCIES / "manager"),
               "--base", str(base), "--manager-base", str(manager_base or base),
               "--output", str(release), "--cutoff", cutoff]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return release, [json.loads(line) for line in traffic.read_text().splitlines()]


if __name__ == "__main__":
    unittest.main()
