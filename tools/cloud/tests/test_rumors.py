"""Offline tests for the trade-report reader (no network). Run: python -m unittest discover -s tools/cloud/tests -v"""
import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rumors  # noqa: E402

PLAYERS = {"Jarrett Allen": "CLE", "Trae Young": "ATL", "Collin Sexton": "LAL", "Austin Reaves": "LAL", "Nique Clifford": "SAC", "LeBron James": "PHI"}


def feed(items):
    out = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>Hoops Rumors</title>']
    for title, link, date, body in items:
        out.append(f"<item><title>{title}</title><link>{link}</link><pubDate>{date}</pubDate><content:encoded><![CDATA[{body}]]></content:encoded></item>")
    return "".join(out) + "</channel></rss>"


POST = ("Lakers Notes: Allen, Young, Sexton", "https://www.hoopsrumors.com/2026/11/lakers-notes.html", "Tue, 17 Nov 2026 14:02:00 +0000",
        '<p>The Lakers have shown interest in Jarrett Allen, according to <a href="https://x.com/ShamsCharania/status/1">Shams Charania of ESPN</a>.</p>'
        '<ul><li>The Hawks are not shopping Trae Young before the trade deadline, <a href="https://www.thesteinline.com/p/x">Marc Stein</a> reports.</li>'
        '<li>The Lakers could look to trade for a center before the deadline.</li>'
        '<li>Austin Reaves is out with a heel bruise, Dave McMenamin of ESPN reports.</li></ul>'
        "<p>HoopsHype's Michael Scotto reports the Suns have discussed a deal for Collin Sexton with the Lakers.</p>")
AGREED = ("Kings Trade Nique Clifford To Thunder", "https://www.hoopsrumors.com/2026/11/kings-trade-clifford.html", "Sun, 08 Nov 2026 20:47:52 +0000",
          "<p>The Kings and Thunder have agreed to a trade sending Nique Clifford to Oklahoma City, sources tell Shams Charania of ESPN.</p>")
OLD = ("Offseason note", "https://www.hoopsrumors.com/2026/05/old.html", "Fri, 01 May 2026 12:00:00 +0000",
       "<p>The Lakers explored a trade for Trae Young, Marc Stein reports.</p>")


def rows_of(items):
    return rumors.parse_feed(feed(items), PLAYERS, "2026-11-17T22:00:00+00:00")


class Parsing(unittest.TestCase):
    def test_named_reports_become_rows_per_team(self):
        got = {(r["team"], r["status"], r["players"], r["reporters"]) for r in rows_of([POST])}
        self.assertIn(("LAL", "Interest", "", "Shams Charania"), got)
        self.assertIn(("CLE", "Interest", "Jarrett Allen", "Shams Charania"), got)
        self.assertIn(("ATL", "Denied", "Trae Young", "Marc Stein"), got)
        self.assertIn(("LAL", "Talks", "Collin Sexton", "Michael Scotto"), got)
        self.assertIn(("PHX", "Talks", "", "Michael Scotto"), got)

    def test_unsourced_and_non_trade_paragraphs_are_left_out(self):
        rows = rows_of([POST])
        self.assertFalse(any("Dave McMenamin" in r["reporters"] for r in rows))           # an injury note, not a trade
        self.assertEqual(len(rows), 5)                                                      # the unsourced center idea is gone

    def test_source_link_is_the_original_reporters(self):
        lal = next(r for r in rows_of([POST]) if r["team"] == "CLE")
        self.assertEqual(lal["source_url"], "https://x.com/ShamsCharania/status/1")
        self.assertEqual(lal["post_url"], POST[1])
        self.assertEqual(lal["date"], "2026-11-17")

    def test_agreed_trade(self):
        got = {(r["team"], r["status"]) for r in rows_of([AGREED])}
        self.assertEqual(got, {("SAC", "Agreed"), ("OKC", "Agreed")})

    def test_player_named_with_his_team_is_not_a_reporter(self):
        self.assertNotIn("Jarrett Allen", rumors.reporters_in("The Lakers want Jarrett Allen of the Cavaliers, Shams Charania reports.", PLAYERS))

    def test_names_are_cleaned_and_awards_outlets_refused(self):
        t = ("While Sam Amick of The Athletic reports the Celtics have talked trade, ESPN Kris Rhim reports the Knicks too. "
             "The Defensive Player of the Year is not available, New York Post reports. In September. Fischer said so.")
        got = rumors.reporters_in(t, PLAYERS)
        self.assertIn("Sam Amick", got)
        self.assertIn("Kris Rhim", got)
        for bad in ("While Sam Amick", "ESPN Kris Rhim", "Defensive Player", "New York Post", "September. Fischer", "York Post"):
            self.assertNotIn(bad, got)

    def test_injury_availability_is_not_trade_interest(self):
        body = ("<p>Austin Reaves is available to play against the Bucks after his heel bruise, Dave McMenamin of ESPN reports.</p>")
        self.assertEqual(rows_of([("Injury Notes", "https://www.hoopsrumors.com/x.html", "Tue, 17 Nov 2026 14:02:00 +0000", body)]), [])

    def test_injury_note_with_eye_and_agreed_is_not_a_trade(self):  # a real false positive from the first read
        body = ("<p>Guard Collin Sexton is day-to-day after getting stitches over his eye, and Austin Reaves is also considered "
                "day-to-day with a bruised left heel after coming down awkwardly in Wednesday's game against the Bucks. X-rays were "
                "negative, and he and the team agreed that more imaging wasn't needed, Dave McMenamin of ESPN reports.</p>")
        self.assertEqual(rows_of([("Injury Notes", "https://www.hoopsrumors.com/x.html", "Sat, 14 Nov 2026 05:01:06 +0000", body)]), [])

    def test_outlet_is_not_a_reporter(self):
        self.assertNotIn("Philadelphia Inquirer", rumors.reporters_in("The 76ers explored a trade, Keith Pompey of the Philadelphia Inquirer reports.", PLAYERS))

    def test_contract_wording_is_not_a_trade_agreement(self):
        self.assertNotEqual(rumors.status_of("Jamal Murray agreed to a four-year deal, which makes a trade less likely, Troy Renck reports."), "Agreed")
        self.assertNotEqual(rumors.status_of("The Knicks officially signed the guard, who was acquired in a trade last year, Jake Fischer reports."), "Official")
        self.assertEqual(rumors.status_of("The Kings have agreed to a trade sending Nique Clifford to the Thunder."), "Agreed")
        self.assertEqual(rumors.status_of("The Thunder officially acquired Nique Clifford from the Kings."), "Official")
        # a real false positive: a column asking whether a trade will happen
        self.assertNotEqual(rumors.status_of("Troy Renck considers whether the Nuggets will trade him before the February 11 deadline."), "Agreed")
        self.assertEqual(rumors.status_of("The Kings are trading Nique Clifford to the Thunder for two second-round picks."), "Agreed")

    def test_same_report_in_two_posts_is_one_row(self):
        a = ("Atlantic Notes", "https://www.hoopsrumors.com/a.html", "Mon, 16 Nov 2026 20:00:00 +0000",
             "<p>The Sixers have interest in Collin Sexton, Gina Mizell of the Philadelphia Inquirer reports.</p>")
        b = ("Sexton Rumors", "https://www.hoopsrumors.com/b.html", "Mon, 16 Nov 2026 22:00:00 +0000",
             "<p>As Gina Mizell of the Philadelphia Inquirer reports, the Sixers have interest in Collin Sexton.</p>")
        rows = rumors.merge([], rows_of([a, b]))
        self.assertEqual(sorted(r["team"] for r in rows), ["LAL", "PHI"])
        self.assertEqual({r["post_url"] for r in rows}, {"https://www.hoopsrumors.com/a.html"})

    def test_player_is_named_for_his_team_on_the_report_date(self):
        root = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, root, True)
        (root / "data/nba").mkdir(parents=True)
        (root / "data/nba/league_player_games.csv").write_text(
            "date,team,opp,player\n2026-11-02,SAC,LAL,Nique Clifford\n2026-11-06,SAC,DEN,Nique Clifford\n2026-11-10,OKC,SAC,Nique Clifford\n", encoding="utf-8")
        players = rumors.Players(root)
        item = ("Kings Trade Clifford", "https://www.hoopsrumors.com/c.html", "Sun, 08 Nov 2026 20:47:52 +0000",
                "<p>The Kings are trading Nique Clifford to the Thunder for two second-round picks, sources tell Shams Charania of ESPN.</p>")
        rows = rumors.parse_feed(feed([item]), players, "now")
        self.assertEqual({(r["team"], r["players"]) for r in rows}, {("SAC", "Nique Clifford"), ("OKC", "")})

    def test_trade_request(self):
        self.assertEqual(rumors.status_of("Dejounte Murray has requested a trade from the Pelicans, Shams Charania reports."), "Requested")
        self.assertEqual(rumors.status_of("The Pelicans confirmed Murray's trade request."), "Requested")

    def test_before_the_season_is_skipped(self):
        self.assertEqual(rows_of([OLD]), [])

    def test_no_article_text_is_kept(self):
        for r in rows_of([POST, AGREED]):
            for v in r.values():
                self.assertNotIn("shown interest", v)
                self.assertNotIn("heel bruise", v)


class Running(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "data/nba").mkdir(parents=True)
        with (self.root / "data/nba/league_rosters.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["team", "player_id", "player", "num", "position", "how_acquired"])
            for i, (p, t) in enumerate(PLAYERS.items()):
                w.writerow([t, i, p, "", "", ""])
        self.urls = []

    def opener(self, pages):
        class R:
            def __init__(self, b): self.b = b
            def read(self): return self.b.encode()
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def op(req, timeout=30):
            self.urls.append(req.full_url)
            body = pages.get(req.full_url)
            if body is None:
                raise OSError("404")
            return R(body)
        return op

    class AllowAll:
        def can_fetch(self, ua, url): return True

    def test_merge_keeps_first_seen_and_counts_new(self):
        op = self.opener({rumors.SITE + "/feed": feed([POST])})
        logs = []
        self.assertEqual(rumors.run(opener=op, robots=self.AllowAll(), sleep=lambda s: None, log=logs.append, root=self.root), 0)
        self.assertEqual(rumors.run(opener=op, robots=self.AllowAll(), sleep=lambda s: None, log=logs.append, root=self.root), 0)
        rows = rumors.read_rows(self.root / "data/trades/reports.csv")
        self.assertEqual(len(rows), 5)
        st = json.loads((self.root / "data/trades/status.json").read_text())
        self.assertEqual(st["new_reports"], 0)
        self.assertIsNone(st["all_teams_updated"])

    def test_all_teams_time_survives_the_main_feed_reads(self):
        op = self.opener({rumors.SITE + "/feed": feed([POST])})
        kw = dict(opener=op, robots=self.AllowAll(), sleep=lambda s: None, log=lambda m: None, root=self.root)
        rumors.run(all_teams=True, **kw)
        at = json.loads((self.root / "data/trades/status.json").read_text())["all_teams_updated"]
        self.assertTrue(at)
        rumors.run(**kw)
        st = json.loads((self.root / "data/trades/status.json").read_text())
        self.assertEqual((st["feeds"], st["all_teams_updated"]), (1, at))

    def test_all_teams_reads_every_feed_and_survives_failures(self):
        op = self.opener({rumors.SITE + "/feed": feed([POST]), rumors.SITE + "/los-angeles-lakers/feed": feed([AGREED])})
        logs = []
        code = rumors.run(all_teams=True, opener=op, robots=self.AllowAll(), sleep=lambda s: None, log=logs.append, root=self.root)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.urls), 31)
        st = json.loads((self.root / "data/trades/status.json").read_text())
        self.assertEqual(len(st["failed"]), 29)
        self.assertEqual(st["reports"], 7)

    def test_robots_disallow_is_respected(self):
        class Deny:
            def can_fetch(self, ua, url): return False
        logs = []
        code = rumors.run(opener=self.opener({}), robots=Deny(), sleep=lambda s: None, log=logs.append, root=self.root)
        self.assertEqual(code, 1)
        self.assertEqual(self.urls, [])
        self.assertIn("robots.txt", logs[-1])


if __name__ == "__main__":
    unittest.main()
