"""Offline tests for the PC-free NBA data job and the side-by-side check (no network).
Run: python -m unittest discover -s tools/cloud/tests -v"""
import datetime as dt
import io
import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compare  # noqa: E402
import nba_data as nd  # noqa: E402

NOW = dt.datetime(2026, 11, 25, 18, 0, tzinfo=dt.timezone.utc)


def team(tri, quarters, players):
    pts = sum(quarters)
    return {"teamTricode": tri, "score": pts, "periods": [{"period": i + 1, "periodType": "REGULAR" if i < 4 else "OVERTIME", "score": q} for i, q in enumerate(quarters)],
            "statistics": {"points": pts, "fieldGoalsMade": 40, "fieldGoalsAttempted": 88, "threePointersMade": 12, "threePointersAttempted": 35,
                           "freeThrowsMade": 15, "freeThrowsAttempted": 20, "reboundsOffensive": 10, "reboundsDefensive": 33, "turnovers": 13,
                           "turnoversTeam": 1, "turnoversTotal": 14, "foulsPersonal": 19, "minutes": "PT%dM00.00S" % (240 + 25 * (len(quarters) - 4)),
                           "reboundsTeam": 7, "pointsInThePaint": 44, "pointsSecondChance": 12, "pointsFastBreak": 9, "pointsFromTurnovers": 17,
                           "benchPoints": 30, "biggestLead": 15, "leadChanges": 6, "timesTied": 4, "biggestScoringRun": 10},
            "players": players}


def player(pid, name, pts, played=True, status="ACTIVE"):
    return {"personId": pid, "name": name, "status": status, "played": "1" if played else "0",
            "statistics": {"points": pts if played else 0, "minutes": "PT31M30.00S" if played else "PT00M00.00S", "assists": 5, "steals": 1,
                           "blocks": 0, "turnovers": 2, "foulsPersonal": 3, "fieldGoalsMade": 7, "fieldGoalsAttempted": 15,
                           "threePointersMade": 2, "threePointersAttempted": 6, "freeThrowsMade": 3, "freeThrowsAttempted": 4,
                           "reboundsOffensive": 1, "reboundsDefensive": 5}}


def game(gid, home_q=(30, 25, 28, 27), away_q=(20, 22, 30, 25), status=3, bad_players=False):
    h, a = sum(home_q), sum(away_q)
    hp = [player(1, "Luka Dončić", h - 10), player(2, "Austin Reaves", 10), player(3, "Bench Guy", 0, played=False),
          player(4, "Hurt Guy", 0, played=False, status="INACTIVE")]
    ap = [player(5, "Jayson Tatum", a - 1 if bad_players else a)]
    return {"game": {"gameId": gid, "gameStatus": status, "homeTeam": team("LAL", home_q, hp), "awayTeam": team("BOS", away_q, ap)}}


def sched(n=1200, finals=()):
    games = []
    for i in range(1, n + 1):
        gid = f"00226{i:05d}"
        d = (dt.date(2026, 10, 20) + dt.timedelta(days=i // 8)).isoformat()
        games.append({"gameId": gid, "gameDateEst": d + "T00:00:00Z", "gameStatus": 3 if gid in finals else 1,
                      "homeTeam": {"teamTricode": "LAL", "score": 0}, "awayTeam": {"teamTricode": "BOS", "score": 0},
                      "isNeutral": gid == "0022600002", "gameLabel": "Emirates NBA Cup" if gid == "0022600001" else "",
                      "gameSubLabel": "West Group B" if gid == "0022600001" else "", "gameSubtype": "in-season" if gid == "0022600001" else ""})
    games.append({"gameId": "0012600001", "gameDateEst": "2026-10-05T00:00:00Z", "gameStatus": 3, "homeTeam": {"teamTricode": "LAL"}, "awayTeam": {"teamTricode": "BOS"}})
    return {"leagueSchedule": {"gameDates": [{"gameDate": "10/20/2026 00:00:00", "games": games}]}}


class Opener:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def __call__(self, req, timeout=30):
        self.urls.append(req.full_url)
        body = self.pages.get(req.full_url)
        if body is None:
            raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, None)

        class R(io.BytesIO):
            def __enter__(s): return s
            def __exit__(s, *a): s.close()
        return R(json.dumps(body).encode())


def pages(finals, games):
    p = {nd.SCHEDULE: sched(finals=finals)}
    for gid, g in games.items():
        p[nd.BOXSCORE.format(gid=gid)] = g
    return p


class NbaData(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out, True)
        self.logs = []

    def run_job(self, op):
        return nd.run(self.out, injuries=False, opener=op, sleep=lambda s: None, now=NOW, log=self.logs.append)

    def test_schedule_keeps_regular_season_with_cup_and_neutral(self):
        g = nd.schedule(sched(n=3))
        self.assertEqual([r["game_id"] for r in g], ["0022600001", "0022600002", "0022600003"])
        self.assertEqual(g[0]["cup"], "Emirates NBA Cup West Group B")
        self.assertEqual((g[1]["neutral"], g[0]["neutral"]), (1, 0))
        self.assertEqual(g[0]["date"], "2026-10-20")

    def test_final_game_writes_every_file(self):
        op = Opener(pages({"0022600001"}, {"0022600001": game("0022600001")}))
        self.assertEqual(self.run_job(op), 0)
        res = {r["game_id"]: r for r in nd.read(self.out / "league_results.csv")}
        self.assertEqual((res["0022600001"]["home_pts"], res["0022600001"]["away_pts"], res["0022600001"]["ot"]), ("110", "97", "0"))
        self.assertEqual(res["0022600005"]["home_pts"], "")                           # not final: no score
        box = {r["team"]: r for r in nd.read(self.out / "league_box.csv")}
        self.assertEqual((box["LAL"]["home_away"], box["LAL"]["tov"], box["LAL"]["tov_players"], box["LAL"]["tov_team"], box["LAL"]["mp"]),
                         ("H", "14", "13", "1", "240"))
        players = nd.read(self.out / "league_player_games.csv")
        self.assertEqual(sorted(p["player"] for p in players), ["Austin Reaves", "Jayson Tatum", "Luka Dončić"])  # not the DNP or inactive
        self.assertEqual(players[0]["min"], "31.5")
        self.assertEqual([r["player"] for r in nd.read(self.out / "league_inactive.csv")], ["Hurt Guy"])
        q = {r["team"]: r for r in nd.read(self.out / "league_quarters.csv")}
        self.assertEqual((q["LAL"]["q1"], q["LAL"]["ot"]), ("30", "0"))
        x = {r["team"]: r for r in nd.read(self.out / "league_extras.csv")}
        self.assertEqual((x["LAL"]["pts_paint"], x["LAL"]["biggest_run"]), ("44", "10"))

    def test_overtime(self):
        op = Opener(pages({"0022600001"}, {"0022600001": game("0022600001", home_q=(25, 25, 25, 25, 12), away_q=(25, 25, 25, 25, 8))}))
        self.run_job(op)
        res = {r["game_id"]: r for r in nd.read(self.out / "league_results.csv")}
        self.assertEqual(res["0022600001"]["ot"], "1")
        q = {r["team"]: r for r in nd.read(self.out / "league_quarters.csv")}
        self.assertEqual(q["LAL"]["ot"], "12")

    def test_inconsistent_box_score_is_held_back_and_old_rows_stay(self):
        night = dt.datetime(2026, 10, 21, 9, 0, tzinfo=dt.timezone.utc)   # the game (Oct 20) is recent, so it is read again
        op = Opener(pages({"0022600001"}, {"0022600001": game("0022600001")}))
        nd.run(self.out, injuries=False, opener=op, sleep=lambda s: None, now=night, log=self.logs.append)
        op2 = Opener(pages({"0022600001"}, {"0022600001": game("0022600001", bad_players=True)}))
        nd.run(self.out, injuries=False, opener=op2, sleep=lambda s: None, now=night + dt.timedelta(hours=12), log=self.logs.append)
        m = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(m["held_back"][0]["why"], "BOS: players' points do not add up to the final score")
        self.assertEqual(len(nd.read(self.out / "league_box.csv")), 2)                  # the earlier, checked rows stay

    def test_missing_box_score_is_held_back_without_failing(self):
        op = Opener(pages({"0022600001", "0022600002"}, {"0022600001": game("0022600001")}))
        self.assertEqual(self.run_job(op), 0)
        m = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual([h["game_id"] for h in m["held_back"]], ["0022600002"])

    def test_old_games_are_not_read_again(self):
        old = {f"00226{i:05d}" for i in range(1, 4)}  # dated Oct 20: older than two days
        op = Opener(pages(old, {g: game(g) for g in old}))
        self.run_job(op)
        op2 = Opener(pages(old, {g: game(g) for g in old}))
        self.run_job(op2)
        self.assertEqual([u for u in op2.urls if "boxscore" in u], [])

    def test_short_schedule_writes_nothing(self):
        op = Opener({nd.SCHEDULE: sched(n=50)})
        self.assertEqual(self.run_job(op), 1)
        self.assertFalse((self.out / "league_results.csv").exists())

    def test_compare_finds_no_differences_against_a_copy_and_finds_real_ones(self):
        op = Opener(pages({"0022600001"}, {"0022600001": game("0022600001")}))
        self.run_job(op)
        pc = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, pc, True)
        for f in nd.COLS:
            shutil.copy(self.out / f, pc / f)
        res = compare.compare(self.out, pc, {"0022600001"})
        self.assertTrue(all(not r["diffs"] and r["both"] for f, r in res.items() if f != "league_inactive.csv"))
        rows = nd.read(pc / "league_box.csv"); rows[0]["pf"] = "99"; rows[0]["pts"] = rows[0]["pts"] + ".0"
        nd.write(pc / "league_box.csv", rows, nd.COLS["league_box.csv"])
        res = compare.compare(self.out, pc, {"0022600001"})
        self.assertEqual(list(res["league_box.csv"]["diffs"]), ["pf"])                    # 97 = 97.0; 99 is a real difference
        self.assertIn("pf (1)", compare.report(res))


if __name__ == "__main__":
    unittest.main()
