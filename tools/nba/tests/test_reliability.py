"""Offline tests for the pipeline's safety parts: run lock, atomic writes, which final games get published,
publishing to GitHub, and the late-game catch-up. No network: git runs against a local test remote.

  tools\\nba\\.venv\\Scripts\\python.exe tools\\nba\\tests\\test_reliability.py        (on the PC)
  python3 tools/nba/tests/test_reliability.py                                     (anywhere with nba_api installed)
"""
import csv
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import rows  # noqa: E402
from lock import RunLock  # noqa: E402
import league  # noqa: E402
import publish as pub  # noqa: E402


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout


class AtomicWrites(unittest.TestCase):
    def test_failed_write_leaves_old_file(self):
        d = Path(tempfile.mkdtemp())
        p = d / "x.csv"
        rows.write(p, [{"a": 1}, {"a": 2}])
        before = p.read_text(encoding="utf-8")

        class Bad:
            def __str__(self):
                raise RuntimeError("disk gone")
        with self.assertRaises(RuntimeError):
            rows.write(p, [{"a": 3}, {"a": Bad()}])
        self.assertEqual(p.read_text(encoding="utf-8"), before)
        self.assertFalse((d / "x.csv.tmp").exists() and (d / "x.csv.tmp").read_text() == before)

    def test_round_trip(self):
        p = Path(tempfile.mkdtemp()) / "y.csv"
        self.assertEqual(rows.write(p, [{"name": "Luka Dončić", "pts": 41}]), 1)
        self.assertEqual(rows.read(p), [{"name": "Luka Dončić", "pts": "41"}])


class Lock(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_second_run_is_refused(self):
        a, b = RunLock("t", 3600, self.d), RunLock("t", 3600, self.d)
        self.assertTrue(a.acquire())
        self.assertFalse(b.acquire())
        a.release()
        self.assertTrue(b.acquire())
        b.release()

    def test_dead_process_lock_is_taken_over(self):
        import socket
        (self.d / ".t.lock").write_text(json.dumps({"pid": 999999, "host": socket.gethostname(),
                                                    "started": dt.datetime.now(dt.timezone.utc).isoformat()}))
        self.assertTrue(RunLock("t", 3600, self.d).acquire())

    def test_old_lock_is_taken_over(self):
        import socket
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).isoformat()
        (self.d / ".t.lock").write_text(json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "started": old}))
        self.assertTrue(RunLock("t", 3 * 3600, self.d).acquire())

    def test_two_processes_race(self):
        code = ("import sys,time; sys.path.insert(0, %r); from lock import RunLock; "
                "l = RunLock('race', 3600, %r); ok = l.acquire(); print('got' if ok else 'no', flush=True); time.sleep(2); l.release()") % (str(HERE.parent), str(self.d))
        ps = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True) for _ in range(4)]
        outs = sorted(p.communicate()[0].strip() for p in ps)
        self.assertEqual(outs.count("got"), 1, outs)


def res(gid, away, home, a_pts="", h_pts="", date="2026-11-12"):
    return {"date": date, "away": away, "home": home, "away_pts": a_pts, "home_pts": h_pts, "ot": 0 if a_pts != "" else "",
            "game_id": gid, "cup": "", "neutral": 0}


def bx(gid, team, opp, ha, pts, date="2026-11-12", mp=240, tov=12):
    return {"date": date, "team": team, "opp": opp, "home_away": ha, **{k: 0 for k in league.BOX}, "pts": pts, "tov": tov,
            "mp": mp, "game_id": gid}


def ln(gid, team, player, pts, date="2026-11-12"):
    return {"date": date, "team": team, "opp": "", "player": player, "min": 30.0, **{k: 0 for k in league.LINE}, "pts": pts,
            "game_id": gid, "player_id": 1}


def as_file(rows_):
    """Rows as they come back from a CSV (strings)."""
    return [{k: "" if v is None else str(v) for k, v in r.items()} for r in rows_]


class AcceptFinals(unittest.TestCase):
    def test_complete_game_is_published(self):
        r = [res("1", "TOR", "PHI", 112, 123)]
        b = [bx("1", "PHI", "TOR", "H", 123), bx("1", "TOR", "PHI", "A", 112)]
        l_ = [ln("1", "PHI", "A", 100), ln("1", "PHI", "B", 23), ln("1", "TOR", "C", 112)]
        out, ob, ol, rep = league.accept_finals(r, b, l_, [], [], [])
        self.assertEqual((out[0]["home_pts"], len(ob), len(ol)), (123, 2, 3))
        self.assertEqual(rep, {"pending": [], "kept": [], "lines": []})

    def test_final_without_box_score_waits(self):
        # Nov 12, CHA@PHX: final on NBA.com's schedule, box score not posted yet
        r = [res("230", "CHA", "PHX", 96, 101)]
        out, ob, ol, rep = league.accept_finals(r, [], [], [], [], [])
        self.assertEqual((out[0]["home_pts"], ob, ol), ("", [], []))
        self.assertIn("box score not posted", rep["pending"][0])

    def test_box_score_caught_mid_overtime_keeps_good_version(self):
        # Nov 4, MIA@LAC: stored once as 112-112 with 240 minutes, final was 121-118 after overtime
        r = [res("9", "MIA", "LAC", 118, 121)]
        b = [bx("9", "LAC", "MIA", "H", 112), bx("9", "MIA", "LAC", "A", 112)]
        old_r = as_file([dict(res("9", "MIA", "LAC", 118, 121), ot=1)])
        old_b = as_file([dict(bx("9", "LAC", "MIA", "H", 121, mp=265, tov=14), tov_players=12, tov_team=2),
                         dict(bx("9", "MIA", "LAC", "A", 118, mp=265, tov=10), tov_players=10, tov_team="")])
        out, ob, ol, rep = league.accept_finals(r, b, [], old_r, old_b, [])
        self.assertEqual((out[0]["away_pts"], out[0]["home_pts"], out[0]["ot"]), (118, 121, 1))
        self.assertEqual(sorted(x["pts"] for x in ob), [118, 121])
        lac = next(x for x in ob if x["team"] == "LAC")
        self.assertEqual((lac["tov"], lac["mp"]), (12, 265))  # player turnovers only; team turnovers are added back later
        self.assertIn("differs from the final score", rep["kept"][0])
        # and add_team_turnovers() still works on the restored rows
        d = Path(tempfile.mkdtemp())
        rows.write(d / "league_extras.csv", [{"game_id": "9", "team": "LAC", "tov_team": 2}])
        league.add_team_turnovers(ob, lambda data, name, cols=None: rows.write(d / name, data, cols), d)
        self.assertEqual(next(x for x in ob if x["team"] == "LAC")["tov"], 14)

    def test_tied_box_without_good_version_waits(self):
        r = [res("9", "MIA", "LAC", 112, 112)]
        b = [bx("9", "LAC", "MIA", "H", 112), bx("9", "MIA", "LAC", "A", 112)]
        out, _, _, rep = league.accept_finals(r, b, [], [], [], [])
        self.assertEqual(out[0]["home_pts"], "")
        self.assertIn("tied", rep["pending"][0])

    def test_missing_player_lines_do_not_hold_back_the_score(self):
        r = [res("5", "PHX", "GSW", 104, 110)]
        b = [bx("5", "GSW", "PHX", "H", 110), bx("5", "PHX", "GSW", "A", 104)]
        l_ = [ln("5", "GSW", "A", 110)]
        out, ob, ol, rep = league.accept_finals(r, b, l_, [], [], [])
        self.assertEqual(out[0]["home_pts"], 110)
        self.assertIn("no player lines for PHX", rep["lines"][0])
        # with a complete earlier set on file, that set is kept
        old_l = as_file([ln("5", "GSW", "A", 110), ln("5", "PHX", "B", 104)])
        out, ob, ol, rep = league.accept_finals(r, b, l_, [], [], old_l)
        self.assertEqual(sorted((x["team"], x["pts"]) for x in ol), [("GSW", 110), ("PHX", 104)])
        self.assertIn("kept the complete lines", rep["lines"][0])

    def test_game_no_longer_final_keeps_version_on_file(self):
        r = [res("7", "DAL", "MIN")]  # NBA.com briefly shows it unplayed
        old_r = as_file([res("7", "DAL", "MIN", 94, 133)])
        old_b = as_file([bx("7", "MIN", "DAL", "H", 133), bx("7", "DAL", "MIN", "A", 94)])
        out, ob, _, rep = league.accept_finals(r, [], [], old_r, old_b, [])
        self.assertEqual(out[0]["home_pts"], 133)
        self.assertEqual(len(ob), 2)
        self.assertIn("no longer final", rep["kept"][0])

    def test_short_schedule_is_not_saved(self):
        old = as_file([res(str(i), "A", "B") for i in range(100)])
        with self.assertRaises(ValueError):
            league.accept_finals([res("1", "A", "B")], [], [], old, [], [])


class Publish(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", "--bare", str(self.t / "remote.git")], check=True)
        subprocess.run(["git", "-C", str(self.t / "remote.git"), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
        seed = self.t / "seed"
        subprocess.run(["git", "clone", "-q", str(self.t / "remote.git"), str(seed)], check=True, capture_output=True)
        git(seed, "checkout", "-q", "-b", "main")
        (seed / "data" / "nba").mkdir(parents=True)
        (seed / "data" / "nba" / "live.json").write_text('{"v": 0}\n')
        (seed / "data" / "nba" / "league_results.csv").write_text("a\n0\n")
        git(seed, "add", "-A")
        git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed")
        git(seed, "push", "-q", "origin", "main")
        self.a, self.b = self.t / "pc", self.t / "other"
        for c in (self.a, self.b):
            subprocess.run(["git", "clone", "-q", str(self.t / "remote.git"), str(c)], check=True, capture_output=True)
        self.logs = []
        pub.FAILURES = self.t / "git_failures.log"

    def p(self, paths, msg):
        return pub.publish(paths, msg, log=self.logs.append, root=self.a, lock_folder=self.t, retry_wait=0)

    def test_publish_with_a_competing_push(self):
        (self.b / "data/nba/live.json").write_text('{"v": "other"}\n')
        git(self.b, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "live")
        git(self.b, "push", "-q")
        (self.a / "data/nba/live.json").write_text('{"v": "mine"}\n')
        (self.a / "data/nba/league_results.csv").write_text("a\n1\n")
        self.assertEqual(self.p(["data/nba"], "update"), "pushed")
        git(self.b, "pull", "-q")
        self.assertEqual((self.b / "data/nba/live.json").read_text(), '{"v": "mine"}\n')  # the newer local file wins
        self.assertEqual(git(self.a, "log", "-1", "--format=%an <%ae>").strip(), "Stots714 <Stots714@users.noreply.github.com>")
        self.assertEqual(self.p(["data/nba"], "again"), "nothing")

    def test_half_done_rebase_is_undone(self):
        (self.b / "data/nba/live.json").write_text('{"v": "other"}\n')
        git(self.b, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "live")
        git(self.b, "push", "-q")
        (self.a / "data/nba/live.json").write_text('{"v": "mine"}\n')
        git(self.a, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "mine")
        subprocess.run(["git", "-C", str(self.a), "-c", "user.name=t", "-c", "user.email=t@t", "pull", "-q", "--rebase"], capture_output=True)
        self.assertTrue((self.a / ".git" / "rebase-merge").exists() or (self.a / ".git" / "rebase-apply").exists())
        (self.a / "data/nba/league_results.csv").write_text("a\n2\n")
        self.assertEqual(self.p(["data/nba"], "update"), "pushed")
        self.assertTrue(any("undoing a rebase" in m for m in self.logs))

    def test_failed_push_is_logged(self):
        git(self.a, "remote", "set-url", "origin", str(self.t / "missing.git"))
        (self.a / "data/nba/league_results.csv").write_text("a\n3\n")
        self.assertEqual(self.p(["data/nba"], "update"), "failed")
        self.assertTrue(pub.FAILURES.exists() and "git" in pub.FAILURES.read_text())


class CatchUp(unittest.TestCase):
    def test_reruns_until_the_late_game_is_on_file(self):
        try:
            import live
        except ImportError as e:
            self.skipTest(f"nba_api not importable here: {e}")
        d = Path(tempfile.mkdtemp())
        live.OUT = d / "live.json"
        live.log = lambda m: logs.append(m)
        logs = []
        games = [{"game_id": "229", "status": 3, "away": {"team": "DAL", "score": 94}, "home": {"team": "MIN", "score": 133}},
                 {"game_id": "230", "status": 3, "away": {"team": "CHA", "score": 96}, "home": {"team": "PHX", "score": 101}}]
        runs = []

        def fake_update():
            runs.append(1)
            r = [res("229", "DAL", "MIN", 94, 133), res("230", "CHA", "PHX", *((96, 101) if len(runs) >= 2 else ("", "")))]
            rows.write(d / "league_results.csv", r, league.RESULT_COLS)
            return 0
        ok = live.after_the_games(games, run=fake_update, wait=0, tries=3, sleep=lambda s: None)
        self.assertTrue(ok)
        self.assertEqual(len(runs), 2)
        self.assertTrue(any("CHA@PHX: no final score" in m for m in logs))

    def test_gives_up_after_the_tries(self):
        try:
            import live
        except ImportError as e:
            self.skipTest(f"nba_api not importable here: {e}")
        d = Path(tempfile.mkdtemp())
        live.OUT = d / "live.json"
        logs = []
        live.log = lambda m: logs.append(m)
        rows.write(d / "league_results.csv", [res("230", "CHA", "PHX", 96, 100)], league.RESULT_COLS)
        games = [{"game_id": "230", "status": 3, "away": {"team": "CHA", "score": 96}, "home": {"team": "PHX", "score": 101}}]
        n = []
        self.assertFalse(live.after_the_games(games, run=lambda: n.append(1), wait=0, tries=2, sleep=lambda s: None))
        self.assertEqual(len(n), 3)
        self.assertTrue(any("stats say 96-100, live feed 96-101" in m for m in logs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
