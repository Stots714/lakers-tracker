"""Offline tests for the live watcher's pre-tip odds trigger (no network, no gh needed).

  python -m unittest discover -s tools/nba/tests -v        (needs nba_api importable, like the CatchUp tests)
"""
import datetime as dt
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NOW = dt.datetime(2026, 11, 18, 0, 10, tzinfo=dt.timezone.utc)


def game(tip):
    return {"tip_utc": tip, "status": 1}


class OddsTrigger(unittest.TestCase):
    def setUp(self):
        try:
            import live
        except Exception as e:  # nba_api missing
            self.skipTest(f"nba_api not importable here: {e}")
        self.live = live
        live._gh_missing = False
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.ready = self.dir / "odds_status.json"
        self.ready.write_text("{}", encoding="utf-8")
        self.calls = []
        self.logs = []
        self.addCleanup(setattr, live, "log", live.log)
        live.log = self.logs.append

    def gh(self, code=0):
        def run(cmd, **kw):
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, code, "", "" if code == 0 else "HTTP 404: workflow not found")
        return run

    def ask(self, waiting, asked, run=None, ready=None):
        return self.live.ask_for_odds(waiting, asked, now=NOW, run=run or self.gh(), ready=ready or self.ready)

    def test_one_read_per_group_of_tipoffs(self):
        asked = []
        waiting = [game("2026-11-18T00:30:00Z"), game("2026-11-18T00:30:00Z"), game("2026-11-18T00:31:00Z")]
        self.assertEqual(len(self.ask(waiting, asked)), 1)
        self.assertEqual(self.calls, [["gh", "workflow", "run", "odds.yml", "-f", "kind=pretip"]])
        self.ask(waiting, asked)  # the next check, 7 minutes later: already asked
        self.assertEqual(len(self.calls), 1)

    def test_not_too_early_or_too_late(self):
        self.ask([game("2026-11-18T01:00:00Z")], [])     # 50 minutes ahead: too early
        self.ask([game("2026-11-18T00:11:00Z")], [])     # 1 minute ahead: too late
        self.assertEqual(self.calls, [])

    def test_later_group_gets_its_own_read(self):
        asked = [dt.datetime(2026, 11, 17, 23, 0, tzinfo=dt.timezone.utc)]   # a 3:00 pm Pacific group, asked earlier
        self.ask([game("2026-11-18T00:30:00Z")], asked)
        self.assertEqual(len(self.calls), 1)

    def test_nothing_until_the_odds_job_is_set_up(self):
        self.ask([game("2026-11-18T00:30:00Z")], [], ready=self.dir / "missing.json")
        self.assertEqual(self.calls, [])

    def test_failed_request_is_logged_and_not_repeated(self):
        asked = []
        self.ask([game("2026-11-18T00:30:00Z")], asked, run=self.gh(code=1))
        self.ask([game("2026-11-18T00:30:00Z")], asked, run=self.gh(code=1))
        self.assertEqual(len(self.calls), 1)
        self.assertIn("not started", self.logs[-1])

    def test_missing_gh_turns_reads_off(self):
        def run(cmd, **kw):
            raise FileNotFoundError("gh")
        self.ask([game("2026-11-18T00:30:00Z")], [], run=run)
        self.assertTrue(self.live._gh_missing)
        self.ask([game("2026-11-18T00:40:00Z")], [])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
