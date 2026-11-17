"""Offline tests for the watchdog. Run: python -m unittest discover -s tools/cloud/tests -v"""
import datetime as dt
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import watchdog as w  # noqa: E402

NOW = dt.datetime(2026, 11, 17, 11, 23, tzinfo=w.PT)   # the scheduled run time
HEAD = "date,away,home,away_pts,home_pts,ot,game_id,cup,neutral\n"


def game(date, away, home, pts=True):
    return f"{date},{away},{home},{'101' if pts else ''},{'99' if pts else ''},0,00{abs(hash((date, away))) % 10**8:08d},,0\n"


class Repo:
    def __init__(self, games, updated=NOW - dt.timedelta(hours=2), steps=None, odds=None):
        root = Path(tempfile.mkdtemp())
        unittest.addModuleCleanup(shutil.rmtree, root, True)
        (root / "data/nba").mkdir(parents=True)
        (root / "data/nba/league_results.csv").write_text(HEAD + "".join(games), encoding="utf-8")
        (root / "data/nba/manifest.json").write_text(json.dumps({"updated": updated.isoformat(), "steps": steps or {}}), encoding="utf-8")
        if odds is not None:
            (root / "data/markets").mkdir(parents=True)
            (root / "data/markets/odds_status.json").write_text(json.dumps(odds), encoding="utf-8")
        self.root = root

    def run(self, commits=("c1", "c2"), now=NOW):
        return w.run(self.root, now=now, fetch=lambda since, until: None if commits is None else list(commits))


def levels(results):
    return {name: lvl for lvl, name, _ in results}


GOOD = [game("2026-11-14", "DEN", "NOP"), game("2026-11-15", "LAL", "PHX"), game("2026-11-16", "ORL", "BOS"),
        game("2026-11-17", "CHA", "LAL", pts=False)]


class Watchdog(unittest.TestCase):
    def test_all_published(self):
        r = Repo(GOOD).run()
        self.assertEqual(w.report(r, summary=None), 0)
        self.assertTrue(all(lvl == w.OK for lvl, _, _ in r))

    def test_yesterday_missing_fails(self):
        r = Repo(GOOD[:2] + [game("2026-11-16", "ORL", "BOS", pts=False)] + GOOD[3:]).run()
        self.assertEqual(levels(r)["Yesterday's scores"], w.FAIL)
        self.assertEqual(w.report(r, summary=None), 1)

    def test_one_older_missing_only_warns(self):  # a postponed game
        r = Repo([game("2026-11-14", "DEN", "NOP", pts=False)] + GOOD[1:]).run()
        self.assertEqual(levels(r)["Scores, last 3 days"], w.WARN)
        self.assertEqual(w.report(r, summary=None), 0)

    def test_two_missing_fail(self):
        r = Repo([game("2026-11-14", "DEN", "NOP", pts=False), game("2026-11-15", "LAL", "PHX", pts=False)] + GOOD[2:]).run()
        self.assertEqual(levels(r)["Scores, last 3 days"], w.FAIL)

    def test_stale_update_fails(self):
        r = Repo(GOOD, updated=NOW - dt.timedelta(hours=36)).run()
        self.assertEqual(levels(r)["Last update"], w.FAIL)

    def test_failed_step_warns(self):
        r = Repo(GOOD, steps={"quarters": {"ok": False, "error": "RuntimeError: x | y"}}).run()
        self.assertEqual(levels(r)["Update step"], w.WARN)
        self.assertEqual(w.report(r, summary=None), 0)

    def test_offseason_checks_nothing(self):
        r = Repo([game("2026-06-10", "BOS", "DAL")], updated=NOW - dt.timedelta(days=90)).run()
        self.assertEqual([lvl for lvl, _, _ in r], [w.OK])

    def test_live_watcher_silent_fails(self):
        self.assertEqual(levels(Repo(GOOD).run(commits=()))["Live watcher"], w.FAIL)

    def test_live_check_unavailable_warns(self):
        self.assertEqual(levels(Repo(GOOD).run(commits=None))["Live watcher"], w.WARN)

    def test_odds_not_set_up(self):
        self.assertEqual(levels(Repo(GOOD).run())["Odds read"], w.OK)

    def test_odds_read_today(self):
        odds = {"updated": (NOW - dt.timedelta(hours=1)).isoformat(), "ok": True, "remaining": 420}
        self.assertEqual(levels(Repo(GOOD, odds=odds).run())["Odds read"], w.OK)

    def test_odds_from_yesterday_fails(self):
        odds = {"updated": (NOW - dt.timedelta(days=1)).isoformat(), "ok": True}
        self.assertEqual(levels(Repo(GOOD, odds=odds).run())["Odds read"], w.FAIL)

    def test_odds_error_fails(self):
        odds = {"updated": (NOW - dt.timedelta(hours=1)).isoformat(), "ok": False, "message": "the key was refused (401)"}
        self.assertEqual(levels(Repo(GOOD, odds=odds).run())["Odds read"], w.FAIL)

    def test_odds_not_checked_before_read_time(self):
        odds = {"updated": (NOW - dt.timedelta(days=1)).isoformat(), "ok": True}
        early = NOW.replace(hour=9, minute=0)
        self.assertEqual(levels(Repo(GOOD, odds=odds).run(now=early))["Odds read"], w.OK)


if __name__ == "__main__":
    unittest.main()
