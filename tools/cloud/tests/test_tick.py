"""Offline tests for the tick (what is due when). No network: every step is replaced by a fake.
Run: python -m unittest discover -s tools/cloud/tests -v"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tick  # noqa: E402

UTC = dt.timezone.utc


def g(gid, status, tip, home=100, away=90):
    return {"game_id": gid, "status": status, "tip_utc": tip, "home": {"score": home}, "away": {"score": away}}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "data/nba_cloud").mkdir(parents=True)
        (self.root / "data/nba").mkdir(parents=True)
        self.calls = []
        self.logs = []
        os.environ["ODDS_API_KEY"] = "k"
        self.addCleanup(os.environ.pop, "ODDS_API_KEY", None)
        # odds and rumors read their own module paths: point them at this test folder
        import odds
        import rumors
        for mod, attrs in ((odds, ("LINES", "STATUS", "RESULTS")), (rumors, ("REPORTS", "STATUS"))):
            for a in attrs:
                saved = getattr(mod, a)
                setattr(mod, a, self.root / saved.relative_to(saved.parents[2]))
                self.addCleanup(setattr, mod, a, saved)

    def config(self, **kw):
        (self.root / "data/pipeline.json").write_text(json.dumps({"data_writer": "pc", "odds_pretip": "pc", **kw}), encoding="utf-8")

    def manifest(self, updated):
        (self.root / "data/nba_cloud/manifest.json").write_text(json.dumps({"updated": updated.isoformat()}), encoding="utf-8")

    def steps(self, games, wrote=False, fail=()):
        def rec(name, ret=None):
            def f(*a, **k):
                self.calls.append((name, a[1:] if name != "rumors" else k.get("all_teams")))  # odds: (key,)
                if name in fail:
                    raise RuntimeError("boom")
                return ret
            return f
        return {"live": lambda out, writer, now, log: (self.calls.append(("live", writer)) or (games, wrote)),
                "injuries": rec("injuries", (None, "Nov 24, 5:00 PM ET", True)), "nba_data": rec("nba_data", 0),
                "compare": rec("compare"), "odds": rec("odds", 0), "rumors": rec("rumors", 0)}

    def run_tick(self, now, games, **kw):
        code = tick.run(now=now, root=self.root, log=self.logs.append, steps=self.steps(games, **kw))
        return code, (self.root / ".tick_message").read_text(encoding="utf-8")

    def names(self):
        return [c[0] for c in self.calls]


class Due(Base):
    def test_morning_reads_data_lines_and_all_team_feeds(self):
        self.config()
        now = dt.datetime(2026, 11, 25, 17, 50, tzinfo=UTC)          # 09:50 Pacific
        self.manifest(now - dt.timedelta(hours=8))                    # last read overnight
        code, msg = self.run_tick(now, [g("0022600300", 1, "2026-11-26T00:00:00Z")])
        self.assertEqual(code, 0)
        self.assertEqual(self.names(), ["live", "nba_data", "compare", "odds", "rumors"])
        self.assertEqual([a for n, a in self.calls if n == "odds"], [("k",)])
        self.assertTrue(self.calls[-1][1])                              # all 30 team feeds
        self.assertIn("morning betting lines", msg)

    def test_quiet_tick_does_nothing(self):
        self.config()
        now = dt.datetime(2026, 11, 25, 19, 7, tzinfo=UTC)             # 11:07 Pacific, everything read recently
        self.manifest(now - dt.timedelta(minutes=30))
        (self.root / "data/markets").mkdir(parents=True)
        (self.root / "data/markets/odds_lines.csv").write_text("game_id,date,away,home,kind,read_at,tip_utc,home_margin,p_home,books\n"
                                                                "0022600300,2026-11-25,BOS,LAL,morning,2026-11-25T17:47Z,2026-11-26T00:00Z,5,0.66,8\n")
        (self.root / "data/trades").mkdir(parents=True)
        (self.root / "data/trades/status.json").write_text(json.dumps({"updated": (now - dt.timedelta(minutes=40)).isoformat(), "feeds": 31}))
        code, msg = self.run_tick(now, [g("0022600300", 1, "2026-11-26T00:00:00Z")])
        self.assertEqual(self.names(), ["live"])
        self.assertEqual(msg, "Tick: nothing new")

    def test_final_game_not_on_file_brings_the_data_read(self):
        self.config()
        now = dt.datetime(2026, 11, 26, 6, 30, tzinfo=UTC)
        self.manifest(now - dt.timedelta(minutes=20))
        self.run_tick(now, [g("0022600300", 3, "2026-11-26T03:30:00Z")])
        self.assertIn("nba_data", self.names())

    def test_final_game_not_reread_within_15_minutes(self):
        self.config()
        now = dt.datetime(2026, 11, 26, 6, 30, tzinfo=UTC)
        self.manifest(now - dt.timedelta(minutes=5))
        self.run_tick(now, [g("0022600300", 3, "2026-11-26T03:30:00Z")])
        self.assertNotIn("nba_data", self.names())

    def test_pretip_only_when_the_ticks_have_that_job(self):
        now = dt.datetime(2026, 11, 26, 0, 10, tzinfo=UTC)
        self.manifest(now)
        games = [g("0022600300", 1, "2026-11-26T00:30:00Z")]
        lines = self.root / "data/markets"; lines.mkdir(parents=True)
        (lines / "odds_lines.csv").write_text("game_id,date,away,home,kind,read_at,tip_utc,home_margin,p_home,books\n"
                                              "0022600300,2026-11-25,BOS,LAL,morning,2026-11-25T17:47Z,2026-11-26T00:30Z,5,0.66,8\n")
        rows = tick.read_csv(lines / "odds_lines.csv")
        self.assertEqual(tick.odds_due(games, {"odds_pretip": "pc"}, now, rows, {}), None)
        self.assertEqual(tick.odds_due(games, {"odds_pretip": "tick"}, now, rows, {}), "pretip")
        recent = {"attempts": {"pretip": (now - dt.timedelta(minutes=10)).isoformat()}}
        self.assertEqual(tick.odds_due(games, {"odds_pretip": "tick"}, now, rows, recent), None)  # one read per group

    def test_morning_lines_retry_every_3_hours_at_most_and_never_below_the_reserve(self):
        games = [g("0022600300", 1, "2026-11-26T03:00:00Z")]          # not priced yet: no morning row on file
        morning = dt.datetime(2026, 11, 25, 17, 50, tzinfo=UTC)
        tried = lambda t, left=400: {"attempts": {"morning": t.isoformat()}, "remaining": left}
        self.assertEqual(tick.odds_due(games, {}, morning, [], {}), "morning")
        self.assertEqual(tick.odds_due(games, {}, morning, [], tried(morning - dt.timedelta(hours=20))), "morning")  # yesterday's
        self.assertIsNone(tick.odds_due(games, {}, morning + dt.timedelta(minutes=5), [], tried(morning)))
        self.assertIsNone(tick.odds_due(games, {}, morning + dt.timedelta(hours=2), [], tried(morning)))
        self.assertEqual(tick.odds_due(games, {}, morning + dt.timedelta(hours=3), [], tried(morning)), "morning")
        self.assertIsNone(tick.odds_due(games, {}, morning + dt.timedelta(hours=3), [], tried(morning, 20)))
        # a status file from before "attempts" was kept counts as the last try of its kind
        self.assertIsNone(tick.odds_due(games, {}, morning + dt.timedelta(minutes=5), [], {"kind": "morning", "updated": morning.isoformat()}))

    def test_no_morning_read_at_night_for_tomorrows_games(self):
        night = dt.datetime(2026, 11, 26, 5, 30, tzinfo=UTC)            # 21:30 Pacific, already Nov 26 in the East
        games = [g("0022600310", 1, "2026-11-27T00:00:00Z")]             # tomorrow evening's game
        self.assertIsNone(tick.odds_due(games, {}, night, [], {"attempts": {"morning": "2026-11-25T17:50:00+00:00"}}))

    def test_all_team_feeds_once_a_day(self):
        morning = dt.datetime(2026, 11, 25, 17, 0, tzinfo=UTC)          # 09:00 Pacific
        self.assertEqual(tick.rumors_due({"updated": "2026-11-25T08:00:00+00:00", "feeds": 1,
                                          "all_teams_updated": "2026-11-24T17:00:00+00:00"}, morning), (True, True))
        later = {"updated": "2026-11-25T19:00:00+00:00", "feeds": 1, "all_teams_updated": "2026-11-25T17:00:00+00:00"}
        self.assertEqual(tick.rumors_due(later, morning + dt.timedelta(hours=2, minutes=30)), (False, False))
        self.assertEqual(tick.rumors_due(later, morning + dt.timedelta(hours=4)), (True, False))

    def test_injury_report_on_the_half_hour_before_games(self):
        self.config(data_writer="cloud")
        (self.root / "data/nba/manifest.json").write_text(json.dumps({"updated": "2026-11-25T20:30:00+00:00"}), encoding="utf-8")
        now = dt.datetime(2026, 11, 25, 20, 31, tzinfo=UTC)
        self.manifest(now)
        self.run_tick(now, [g("0022600300", 1, "2026-11-26T00:00:00Z")])
        self.assertIn("injuries", self.names())
        self.calls.clear()
        self.run_tick(now.replace(minute=40), [g("0022600300", 1, "2026-11-26T00:00:00Z")])
        self.assertNotIn("injuries", self.names())

    def test_no_half_hourly_injury_reads_during_the_side_by_side_check(self):
        self.config()
        now = dt.datetime(2026, 11, 25, 20, 31, tzinfo=UTC)
        self.manifest(now)
        self.run_tick(now, [g("0022600300", 1, "2026-11-26T00:00:00Z")])
        self.assertNotIn("injuries", self.names())

    def test_one_broken_step_does_not_stop_the_rest(self):
        self.config()
        now = dt.datetime(2026, 11, 25, 17, 50, tzinfo=UTC)
        self.manifest(now - dt.timedelta(hours=8))
        code, msg = self.run_tick(now, [g("0022600300", 1, "2026-11-26T00:00:00Z")], fail=("nba_data",))
        self.assertEqual(code, 0)
        self.assertIn("odds", self.names())
        self.assertIn("rumors", self.names())
        self.assertTrue(any("game data failed" in l for l in self.logs))

    def test_cloud_writer_writes_data_nba_and_skips_the_comparison(self):
        self.config(data_writer="cloud")
        now = dt.datetime(2026, 11, 25, 17, 50, tzinfo=UTC)
        self.run_tick(now, [])
        self.assertIn("nba_data", self.names())
        self.assertNotIn("compare", self.names())
        self.assertIn(("live", "cloud"), self.calls)


class FakeLive:
    def __init__(self, games, board_date="2026-11-25"):
        self.games, self.board_date, self.OUT, self.wrote = games, board_date, None, 0

    def fetch_board(self):
        return self.board_date, []

    def read_previous(self):
        return {}

    def check(self, board, prev):
        return self.games

    def write(self, board_date, games):
        self.wrote += 1
        self.OUT.write_text(json.dumps({"updated": self.now.isoformat(), "board_date": board_date, "games": games}))


class LiveCadence(Base):
    def live(self, games, now, writer="cloud"):
        fake = FakeLive(games); fake.now = now
        out = self.root / "data/nba_cloud"
        return tick.live_pass(out, writer, now, self.logs.append, live=fake), fake

    def test_first_pass_writes(self):
        now = dt.datetime(2026, 11, 26, 1, 0, tzinfo=UTC)
        (_, wrote), fake = self.live([g("1", 2, "2026-11-26T00:30:00Z")], now)
        self.assertTrue(wrote)

    def test_score_change_waits_for_the_cadence_but_a_final_goes_at_once(self):
        now = dt.datetime(2026, 11, 26, 1, 0, tzinfo=UTC)
        self.live([g("1", 2, "2026-11-26T00:30:00Z", 50, 40)], now)
        (_, wrote), _ = self.live([g("1", 2, "2026-11-26T00:30:00Z", 55, 40)], now + dt.timedelta(minutes=5))
        self.assertFalse(wrote)                                           # 5 minutes after the last write: wait
        (_, wrote), _ = self.live([g("1", 2, "2026-11-26T00:30:00Z", 57, 40)], now + dt.timedelta(minutes=8))
        self.assertTrue(wrote)                                            # 8 minutes: write
        (_, wrote), _ = self.live([g("1", 3, "2026-11-26T00:30:00Z", 99, 90)], now + dt.timedelta(minutes=10))
        self.assertTrue(wrote)                                            # final: at once

    def test_nothing_changed_nothing_written(self):
        now = dt.datetime(2026, 11, 26, 1, 0, tzinfo=UTC)
        self.live([g("1", 1, "2026-11-26T03:30:00Z")], now)
        (_, wrote), _ = self.live([g("1", 1, "2026-11-26T03:30:00Z")], now + dt.timedelta(hours=1))
        self.assertFalse(wrote)


if __name__ == "__main__":
    unittest.main()
