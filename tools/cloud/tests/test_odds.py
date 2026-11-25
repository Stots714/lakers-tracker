"""Offline tests for the odds reader (no network). Run: python -m unittest discover -s tools/cloud/tests -v"""
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
import odds  # noqa: E402

NOW = dt.datetime(2026, 11, 17, 17, 47, tzinfo=dt.timezone.utc)   # 09:47 Pacific
HEAD = "date,away,home,away_pts,home_pts,ot,game_id,cup,neutral\n"
RESULTS = HEAD + "".join([
    "2026-11-17,CHA,LAL,,,0,0022600257,,0\n",
    "2026-11-17,NYK,TOR,,,0,0022600253,,0\n",
    "2026-11-18,UTA,LAL,,,0,0022600270,,0\n",
    "2026-11-17,DET,SAS,,,0,0022600255,,1\n",   # neutral site: The Odds API lists it the other way round below
])


def book(home, away, ph, pa, hpt):
    return {"key": "b", "title": "Book", "markets": [
        {"key": "h2h", "outcomes": [{"name": home, "price": ph}, {"name": away, "price": pa}]},
        {"key": "spreads", "outcomes": [{"name": home, "price": 1.91, "point": hpt}, {"name": away, "price": 1.91, "point": -hpt}]}]}


def event(home, away, tip, books):
    return {"id": "x", "sport_key": "basketball_nba", "commence_time": tip, "home_team": home, "away_team": away, "bookmakers": books}


LAL_CHA = event("Los Angeles Lakers", "Charlotte Hornets", "2026-11-18T03:30:00Z",
                [book("Los Angeles Lakers", "Charlotte Hornets", 1.25, 4.10, -9.5),
                 book("Los Angeles Lakers", "Charlotte Hornets", 1.27, 3.90, -9.0),
                 book("Los Angeles Lakers", "Charlotte Hornets", 1.26, 4.00, -9.5)])
TOR_NYK = event("Toronto Raptors", "New York Knicks", "2026-11-18T00:30:00Z",
                [book("Toronto Raptors", "New York Knicks", 2.40, 1.62, 3.5), book("Toronto Raptors", "New York Knicks", 2.35, 1.65, 3.5)])
SAS_DET_FLIPPED = event("Detroit Pistons", "San Antonio Spurs", "2026-11-18T01:00:00Z",
                        [book("Detroit Pistons", "San Antonio Spurs", 3.00, 1.42, 7.0), book("Detroit Pistons", "San Antonio Spurs", 3.10, 1.40, 7.0)])
TOMORROW = event("Los Angeles Lakers", "Utah Jazz", "2026-11-19T03:30:00Z",
                 [book("Los Angeles Lakers", "Utah Jazz", 1.20, 4.8, -10.5), book("Los Angeles Lakers", "Utah Jazz", 1.21, 4.7, -10.5)])
THIN = event("Toronto Raptors", "New York Knicks", "2026-11-18T00:30:00Z", [book("Toronto Raptors", "New York Knicks", 2.4, 1.62, 3.5)])
UNKNOWN = event("Seattle SuperSonics", "Los Angeles Lakers", "2026-11-18T03:30:00Z", LAL_CHA["bookmakers"])
STARTED = event("Toronto Raptors", "New York Knicks", "2026-11-17T17:00:00Z", TOR_NYK["bookmakers"])


class FakeResponse(io.BytesIO):
    def __init__(self, body, headers):
        super().__init__(json.dumps(body).encode())
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def opener_for(body, remaining="480"):
    return lambda req, timeout=30: FakeResponse(body, {"x-requests-remaining": remaining, "x-requests-used": "20", "x-requests-last": "2"})


def refusing(code):
    def opener(req, timeout=30):
        raise urllib.error.HTTPError(req.full_url, code, "no", {}, None)
    return opener


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "data/nba").mkdir(parents=True)
        (self.root / "data/nba/league_results.csv").write_text(RESULTS, encoding="utf-8")
        self.saved = (odds.RESULTS, odds.LINES, odds.STATUS)
        odds.RESULTS = self.root / "data/nba/league_results.csv"
        odds.LINES = self.root / "data/markets/odds_lines.csv"
        odds.STATUS = self.root / "data/markets/odds_status.json"
        self.addCleanup(self.restore)
        self.logs = []

    def restore(self):
        odds.RESULTS, odds.LINES, odds.STATUS = self.saved

    def run_odds(self, body=None, kind="morning", now=NOW, key="k", opener=None):
        return odds.run(kind, key, now=now, opener=opener or opener_for(body or []), log=self.logs.append)

    def lines(self):
        return odds.read_csv(odds.LINES)

    def status(self):
        return json.loads(odds.STATUS.read_text(encoding="utf-8"))


class Consensus(unittest.TestCase):
    def test_median_spread_and_chance_without_the_books_margin(self):
        margin, p, books = odds.consensus(LAL_CHA)
        self.assertEqual(margin, 9.5)            # spreads -9.5, -9.0, -9.5: median 9.5
        self.assertEqual(books, 3)
        self.assertAlmostEqual(p, 0.760, places=3)  # median book 1.26 / 4.00 with its margin removed: (1/1.26) / ((1/1.26) + (1/4.00)) = 0.7605

    def test_one_sided_or_uneven_spreads_are_ignored(self):
        ev = event("Toronto Raptors", "New York Knicks", "2026-11-18T00:30:00Z", [{"key": "b", "markets": [
            {"key": "spreads", "outcomes": [{"name": "Toronto Raptors", "point": 3.5}, {"name": "New York Knicks", "point": -2.5}]}]}])
        self.assertEqual(odds.consensus(ev), (None, None, 0))


class Reading(Base):
    def test_no_key_writes_nothing(self):
        self.assertEqual(self.run_odds(key=""), 0)
        self.assertFalse(odds.STATUS.exists())

    def test_morning_keeps_todays_unstarted_games(self):
        self.assertEqual(self.run_odds([LAL_CHA, TOR_NYK, TOMORROW, STARTED]), 0)
        got = {r["game_id"]: r for r in self.lines()}
        self.assertEqual(sorted(got), ["0022600253", "0022600257"])   # not tomorrow's, not the started game
        lal = got["0022600257"]
        self.assertEqual((lal["away"], lal["home"], lal["kind"], lal["home_margin"], lal["p_home"], lal["books"]),
                         ("CHA", "LAL", "morning", "9.5", "0.760", "3"))
        self.assertEqual(self.status()["remaining"], 480)
        self.assertTrue(self.status()["ok"])

    def test_each_kinds_last_try_is_kept_across_reads(self):
        self.run_odds([LAL_CHA])
        first = self.status()["attempts"]["morning"]
        self.run_odds([], kind="pretip", now=NOW + dt.timedelta(hours=6))
        self.assertEqual(self.status()["attempts"], {"morning": first, "pretip": (NOW + dt.timedelta(hours=6)).isoformat(timespec="seconds")})
        self.run_odds(opener=refusing(401), now=NOW + dt.timedelta(hours=7))
        self.assertEqual(self.status()["attempts"]["morning"], (NOW + dt.timedelta(hours=7)).isoformat(timespec="seconds"))

    def test_no_book_names_or_prices_are_saved(self):
        self.run_odds([LAL_CHA])
        text = odds.LINES.read_text(encoding="utf-8") + odds.STATUS.read_text(encoding="utf-8")
        for word in ("Book", "price", "1.26", "4.0"):
            self.assertNotIn(word, text)

    def test_neutral_site_listed_the_other_way_is_turned_round(self):
        self.run_odds([SAS_DET_FLIPPED])
        r = self.lines()[0]
        self.assertEqual((r["away"], r["home"]), ("DET", "SAS"))
        self.assertEqual(r["home_margin"], "7")                 # Detroit +7 at "home" means San Antonio by 7
        self.assertGreater(float(r["p_home"]), 0.6)

    def test_unknown_team_and_thin_markets_are_skipped_and_noted(self):
        self.run_odds([UNKNOWN, THIN])
        self.assertEqual(self.lines(), [])
        self.assertIn("not matched", self.status()["message"])

    def test_pretip_records_only_games_within_the_hour(self):
        soon = dt.datetime(2026, 11, 18, 0, 5, tzinfo=dt.timezone.utc)   # 25 minutes before NYK@TOR
        self.run_odds([LAL_CHA, TOR_NYK], kind="pretip", now=soon)
        self.assertEqual([(r["game_id"], r["kind"]) for r in self.lines()], [("0022600253", "pretip")])

    def test_newer_read_replaces_older_of_same_kind(self):
        self.run_odds([TOR_NYK])
        moved = event("Toronto Raptors", "New York Knicks", "2026-11-18T00:30:00Z",
                      [book("Toronto Raptors", "New York Knicks", 2.6, 1.55, 4.5), book("Toronto Raptors", "New York Knicks", 2.6, 1.55, 4.5)])
        self.run_odds([moved], now=NOW + dt.timedelta(hours=1))
        rows = self.lines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["home_margin"], "-4.5")

    def test_refused_key_is_recorded_and_fails(self):
        self.assertEqual(self.run_odds(opener=refusing(401)), 1)
        st = self.status()
        self.assertFalse(st["ok"])
        self.assertIn("refused", st["message"])

    def test_pretip_skipped_when_credits_run_low(self):
        self.run_odds([TOR_NYK], opener=opener_for([TOR_NYK], remaining="25"))
        calls = []
        self.assertEqual(odds.run("pretip", "k", now=NOW, opener=lambda *a, **k: calls.append(1), log=self.logs.append), 0)
        self.assertEqual(calls, [])
        self.assertIn("Skipped", self.logs[-1])


if __name__ == "__main__":
    unittest.main()
