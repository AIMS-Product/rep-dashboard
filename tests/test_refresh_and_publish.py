"""Exercise publication races with real local repositories and fixture generators."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PUBLISH_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/refresh_and_publish.sh"
FETCH_FIXTURE = '''
import os
from pathlib import Path
import subprocess

def git(*args):
    subprocess.run(["git", "-C", os.environ["COMPETITOR"], *args], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)

counter = Path(".git/generation-count")
attempt = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(attempt))
if os.environ.get("FAIL_GENERATION"):
    raise RuntimeError("fixture generation failure")
Path("data.json").write_text(Path("input.txt").read_text())
mode = os.environ.get("RACE_MODE")
if mode == "always" or (mode == "once" and attempt == 1):
    git("pull", "--ff-only")
    competitor = Path(os.environ["COMPETITOR"])
    (competitor / "input.txt").write_text(f"source revision {attempt}")
    (competitor / "data.json").write_text("competing snapshot")
    (competitor / "archives/other.json").write_text("preserved archive")
    git("add", ".")
    git("commit", "-m", f"Concurrent writer {attempt}")
    git("push", "origin", "main")
'''


class RefreshAndPublishTests(unittest.TestCase):
    def git(self, repo, *args):
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], stderr=subprocess.STDOUT,
            text=True, env=self.env,
        ).strip()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.remote = root / "remote.git"
        self.repo = root / "runner"
        self.competitor = root / "competitor"
        self.env = dict(os.environ, GITHUB_ACTIONS="true", COMPETITOR=str(self.competitor),
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        self.git(root, "init", "--bare", "--initial-branch=main", str(self.remote))
        self.git(root, "clone", str(self.remote), str(self.repo))
        self.git(self.repo, "config", "user.name", "Fixture")
        self.git(self.repo, "config", "user.email", "fixture@example.com")
        (self.repo / "scripts").mkdir()
        (self.repo / "archives").mkdir()
        shutil.copyfile(PUBLISH_SCRIPT, self.repo / "scripts/refresh_and_publish.sh")
        (self.repo / "scripts/fetch_data.py").write_text(FETCH_FIXTURE)
        (self.repo / "scripts/build_yesterday_preview.py").write_text(
            'from pathlib import Path\nPath("daily-preview.html").write_text("preview")\n'
        )
        (self.repo / "archives/base.json").write_text("base archive")
        (self.repo / "input.txt").write_text("initial source")
        (self.repo / "data.json").write_text("initial data")
        (self.repo / "daily-preview.html").write_text("preview")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-m", "Initial fixture")
        self.git(self.repo, "push", "origin", "main")
        self.git(root, "clone", str(self.remote), str(self.competitor))
        self.git(self.competitor, "config", "user.name", "Other writer")
        self.git(self.competitor, "config", "user.email", "other@example.com")

    def publish(self, **env):
        return subprocess.run(
            ["bash", "scripts/refresh_and_publish.sh"], cwd=self.repo,
            env=dict(self.env, **env), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
        )

    def test_concurrent_data_and_source_changes_are_preserved_and_regenerated(self):
        result = self.publish(RACE_MODE="once")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.git(self.remote, "show", "main:data.json"), "source revision 1")
        self.assertEqual(self.git(self.remote, "show", "main:input.txt"), "source revision 1")
        self.assertEqual(self.git(self.remote, "show", "main:archives/other.json"), "preserved archive")
        self.assertEqual((self.repo / ".git/generation-count").read_text(), "2")
        self.assertEqual(self.git(self.remote, "rev-list", "--count", "main"), "3")

    def test_unchanged_data_does_not_create_a_commit(self):
        self.publish()
        before = self.git(self.remote, "rev-parse", "main")
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), before)

    def test_repeated_races_stop_after_three_attempts_without_overwriting_remote(self):
        result = self.publish(RACE_MODE="always")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("all three publication attempts", result.stdout)
        self.assertEqual((self.repo / ".git/generation-count").read_text(), "3")
        self.assertEqual(self.git(self.remote, "show", "main:data.json"), "competing snapshot")

    def test_permission_failure_does_not_regenerate(self):
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        result = self.publish()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("without a concurrent update", result.stdout)
        self.assertEqual((self.repo / ".git/generation-count").read_text(), "1")

    def test_generation_failure_does_not_publish(self):
        before = self.git(self.remote, "rev-parse", "main")
        result = self.publish(FAIL_GENERATION="true")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), before)

    def test_dirty_or_non_actions_checkout_is_rejected(self):
        before = self.git(self.repo, "rev-parse", "HEAD")
        result = self.publish(GITHUB_ACTIONS="false")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        (self.repo / "input.txt").write_text("user edit")
        result = self.publish()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.repo / "input.txt").read_text(), "user edit")
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), before)


if __name__ == "__main__":
    unittest.main()
