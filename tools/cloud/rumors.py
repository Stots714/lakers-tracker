"""Trade reports with a named source, from Hoops Rumors' public feeds, kept as short records with links.

Runs on GitHub's machines (.github/workflows/rumors.yml): the main feed every two hours, every team's feed once a
day as a backstop. Hoops Rumors names and links the original reporter in its posts ("according to Shams Charania
of ESPN"), which is what makes a report checkable.

What is kept (data/trades/reports.csv), one row per team per paragraph that talks about a trade:
  date, team, that team's players named, other teams named, status, reporter(s), the link to the original source
  (the first outside link in the paragraph), the Hoops Rumors post and its headline.
Never the article text. Paragraphs with no named reporter (unsourced talk, opinion, trade ideas) are left out.

Status comes from the paragraph's wording, strongest first (automatic, so the link is always there to check):
  Official  the trade is done or announced
  Agreed    the teams have agreed
  Denied    the team says it is not trading the player, or he is not available
  Talks     discussions, offers, negotiations
  Interest  interest, monitoring, inquiries, shopping a player
  Mention   a trade is mentioned with none of the above
The site checks each report against NBA.com's rosters (roster_moves.csv) to see whether it happened.

  python tools/cloud/rumors.py              main feed
  python tools/cloud/rumors.py --all-teams  main feed and all 30 team feeds
Standard library only. Reads robots.txt and skips anything it disallows.
"""
import argparse
import csv
import datetime as dt
import email.utils
import hashlib
import html
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "data/trades/reports.csv"
STATUS = ROOT / "data/trades/status.json"
SITE = "https://www.hoopsrumors.com"
UA = "lakers-tracker-rumors/1.0 (+https://github.com/Stots714/lakers-tracker)"
EASTERN = ZoneInfo("America/New_York")
SEASON_START = "2026-07-01"
PAUSE = 1.5  # seconds between requests
COLS = ["id", "date", "team", "players", "other_teams", "status", "reporters", "source_url", "post_url", "headline", "seen_at"]

TEAMS = {  # abbreviation: (feed slug, names used in posts)
    "ATL": ("atlanta-hawks", ["Hawks"]), "BOS": ("boston-celtics", ["Celtics"]), "BKN": ("brooklyn-nets", ["Nets"]),
    "CHA": ("charlotte-hornets", ["Hornets"]), "CHI": ("chicago-bulls", ["Bulls"]), "CLE": ("cleveland-cavaliers", ["Cavaliers", "Cavs"]),
    "DAL": ("dallas-mavericks", ["Mavericks", "Mavs"]), "DEN": ("denver-nuggets", ["Nuggets"]), "DET": ("detroit-pistons", ["Pistons"]),
    "GSW": ("golden-state-warriors", ["Warriors"]), "HOU": ("houston-rockets", ["Rockets"]), "IND": ("indiana-pacers", ["Pacers"]),
    "LAC": ("los-angeles-clippers", ["Clippers"]), "LAL": ("los-angeles-lakers", ["Lakers"]), "MEM": ("memphis-grizzlies", ["Grizzlies"]),
    "MIA": ("miami-heat", ["Heat"]), "MIL": ("milwaukee-bucks", ["Bucks"]), "MIN": ("minnesota-timberwolves", ["Timberwolves", "Wolves"]),
    "NOP": ("new-orleans-pelicans", ["Pelicans"]), "NYK": ("new-york-knicks", ["Knicks"]), "OKC": ("oklahoma-city-thunder", ["Thunder"]),
    "ORL": ("orlando-magic", ["Magic"]), "PHI": ("philadelphia-76ers", ["76ers", "Sixers"]), "PHX": ("phoenix-suns", ["Suns"]),
    "POR": ("portland-trail-blazers", ["Trail Blazers", "Blazers"]), "SAC": ("sacramento-kings", ["Kings"]),
    "SAS": ("san-antonio-spurs", ["Spurs"]), "TOR": ("toronto-raptors", ["Raptors"]), "UTA": ("utah-jazz", ["Jazz"]),
    "WAS": ("washington-wizards", ["Wizards"]),
}

TRADE = re.compile(r"\b(trades?|traded|trading|trade[- ]deadline|acquir\w+|acquisition|swap\w*|shopp\w+|on the (?:trade )?block|dangl\w+|"
                   r"deal (?:for|involving|that would send)|sen(?:d|ds|ding|t) \w+(?: \w+)? to)\b", re.I)
STATUS_RULES = [  # strongest first
    ("Official", re.compile(r"\b(officially|(?:have|has) (?:completed|finalized|announced) (?:a|the|their) (?:trade|deal)|trade (?:is|was) (?:now )?official|announced (?:that )?(?:they|the team) (?:have|has) (?:acquired|traded))", re.I)),
    ("Agreed", re.compile(r"\b(agreed to (?:a |terms on a )?(?:trade|deal)|(?:have|has) agreed to (?:send|trade|acquire)|are trading|will trade|(?:trade|deal) (?:is|has been) (?:agreed|struck|in place|done)|finaliz\w+ (?:a |the )?(?:trade|deal))", re.I)),
    ("Denied", re.compile(r"\b(not (?:actively )?(?:shopping|looking to (?:trade|move|deal))|no (?:plans|intention|interest) (?:to|in) (?:trade|trading|move|moving|deal|dealing)|(?:isn't|is not|aren't|are not|won't|will not|isn’t|aren’t|won’t) (?:expected to be |going to be |likely to be )?(?:traded|moved|available|dealt)|off the table|untouchable|not interested in (?:trading|moving|dealing))", re.I)),
    ("Talks", re.compile(r"\b(talks?|discussions?|discussed|negotiat\w+|engaged|conversations?|explor\w+|made (?:an |a )?(?:offer|pitch|proposal)|offered|offering|proposals?|proposed|framework)\b", re.I)),
    ("Interest", re.compile(r"\b(interest|interested|eye(?:ing|d)?|monitor\w*|keeping tabs|target\w*|covet\w*|inquir\w+|kick(?:ed|ing) the tires|checked in|calls? (?:on|about)|shopp\w+|on the (?:trade )?block|available|dangl\w+|pursu\w+|in the mix)\b", re.I)),
]
KNOWN_REPORTERS = [
    "Shams Charania", "Marc Stein", "Jake Fischer", "Chris Haynes", "Brian Windhorst", "Tim Bontemps", "Bobby Marks",
    "Michael Scotto", "Sam Amick", "Fred Katz", "Kelly Iko", "Dan Woike", "Jovan Buha", "Dave McMenamin", "Ramona Shelburne",
    "Zach Lowe", "Kevin O'Connor", "Evan Sidery", "Marc J. Spears", "Ian Begley", "Stefan Bondy", "Keith Smith",
    "John Hollinger", "Eric Pincus", "Jason Lloyd", "Joe Vardon", "Chris Mannix", "Anthony Slater", "Brett Siegel",
    "Grant Afseth", "Law Murray", "Tony Jones", "Ira Winderman", "Barry Jackson", "Anthony Chiang", "Jeff Zillgitt",
    "Mark Medina", "Bill Oram", "Darnell Mayberry", "K.C. Johnson", "Joe Cowley", "Kevin Chouinard", "Yossi Gozlan",
]
NAME = r"[A-Z][a-zA-Z'’.\-]+(?:\s+(?:[A-Z][a-zA-Z'’.\-]+|J\.)){1,2}"
VERB = r"(?:reports?|reported|hears?|heard|relays|relayed|tweets?|tweeted|confirms?|confirmed|writes|wrote|indicates|indicated|notes|noted)"
ATTRIBUTION = [
    re.compile(rf"(?:according to|per|via|reported by|sources? (?:tell|told|tells)|league sources tell)\s+({NAME})"),
    re.compile(rf"({NAME})\s+of\s+(?:The\s+|the\s+)?([A-Z][\w.&'’\-]+(?:\s+[A-Z][\w.&'’\-]+){{0,3}})"),
    re.compile(rf"[A-Z][\w.]+(?:'s|’s)\s+({NAME})\s+{VERB}\b"),
    re.compile(rf"({NAME})\s+and\s+({NAME})\s+(?:report|reported|write|wrote|hear|confirm|confirmed)\b"),
    re.compile(rf"({NAME})\s+{VERB}\b"),
]
NOT_PEOPLE = {"Hoops Rumors", "Trade Candidate", "Western Conference", "Eastern Conference", "Los Angeles", "New York", "San Antonio",
              "Golden State", "Oklahoma City", "New Orleans", "Salt Lake", "The Athletic", "The Stein Line", "Bleacher Report",
              "Free Agent", "Trade Deadline", "Second Round", "First Round", "Hall Of Fame", "Most Improved", "Sixth Man", "Player Of"}


def plain(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace("’", "'").strip()


class Blocks(HTMLParser):
    """Splits post HTML into paragraphs and list items: (text, [(href, anchor text)])."""
    BLOCK = {"p", "li", "blockquote", "h2", "h3", "h4"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.text, self.links, self.href = [], [], [], None

    def flush(self):
        t = re.sub(r"\s+", " ", "".join(self.text)).strip()
        if t:
            self.blocks.append((t, self.links))
        self.text, self.links = [], []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.flush()
        elif tag == "a":
            self.href = dict(attrs).get("href")
            self.links.append([self.href, ""])
        elif tag == "br":
            self.text.append(" ")

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.flush()
        elif tag == "a":
            self.href = None

    def handle_data(self, data):
        self.text.append(data)
        if self.href is not None and self.links:
            self.links[-1][1] += data

    def close(self):
        super().close()
        self.flush()
        return self.blocks


def blocks_of(html_text):
    p = Blocks()
    p.feed(html_text or "")
    return p.close()


def player_index(root=ROOT):
    """Each player's current team: NBA.com's rosters when the PC has written them, else the latest game played."""
    idx = {}
    rost = root / "data/nba/league_rosters.csv"
    if rost.exists():
        with rost.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                idx[r["player"]] = r["team"]
        return idx
    games = root / "data/nba/league_player_games.csv"
    if games.exists():
        with games.open(encoding="utf-8", newline="") as f:
            latest = {}
            for r in csv.DictReader(f):
                if r["player"] not in latest or r["date"] >= latest[r["player"]][0]:
                    latest[r["player"]] = (r["date"], r["team"])
        idx = {p: t for p, (_, t) in latest.items()}
    return idx


def reporters_in(text, players):
    """Named reporters in a paragraph: known insiders, plus names in attribution phrases that are not players or teams."""
    found = [n for n in KNOWN_REPORTERS if n in text]
    nicknames = {w for _, names in TEAMS.values() for n in names for w in n.split()}
    pl = {plain(p) for p in players}
    for i, rx in enumerate(ATTRIBUTION):
        for m in rx.finditer(text):
            if i == 1:  # "X of Y": Y must not be a team ("Jarrett Allen of the Cavaliers")
                if any(w in nicknames for w in m.group(2).split()):
                    continue
                cands = [m.group(1)]
            else:
                cands = [g for g in m.groups() if g]
            for name in cands:
                name = re.sub(r"^\S+(?:'s|’s)\s+", "", name.strip(" ."))  # "HoopsHype's Michael Scotto": the outlet is not the name
                if len(name.split()) < 2:
                    continue
                if name in NOT_PEOPLE or plain(name) in pl or any(w in nicknames for w in name.split()) or name.split()[0] in ("The", "A", "An"):
                    continue
                if name not in found:
                    found.append(name)
    return found


def status_of(text):
    for label, rx in STATUS_RULES:
        if rx.search(text):
            return label
    return "Mention"


def teams_in(text, players):
    """{team: [its players named]} for every team named, or with a player named, in the text."""
    out = {}
    for abbr, (_, names) in TEAMS.items():
        if any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text) for n in names):
            out.setdefault(abbr, [])
    for name, team in players.items():
        if team in TEAMS and len(name) > 5 and name in text:
            out.setdefault(team, [])
            if name not in out[team]:
                out[team].append(name)
    return out


def outside_link(links):
    for href, _ in links:
        if href and href.startswith("http") and "hoopsrumors.com" not in href and "tradeRumors" not in href:
            return href
    return ""


def parse_feed(xml_text, players, seen_at):
    """Report rows from one RSS feed."""
    rows = []
    root = ET.fromstring(xml_text)
    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    for item in root.iter("item"):
        title = html.unescape(item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        when = item.findtext("pubDate")
        try:
            date = email.utils.parsedate_to_datetime(when).astimezone(EASTERN).date().isoformat()
        except (TypeError, ValueError):
            continue
        if date < SEASON_START:
            continue
        body = item.findtext("content:encoded", namespaces=ns) or item.findtext("description") or ""
        for k, (text, links) in enumerate(blocks_of(body)):
            named = teams_in(text, players)
            status = status_of(text)
            # A trade paragraph: trade wording, or one team's interest in or talks about another team's player
            cross = len(named) >= 2 and any(named.values()) and status in ("Interest", "Talks", "Agreed")
            if not (TRADE.search(text) or cross):
                continue
            reps = reporters_in(text, players)
            if not reps:
                continue  # no named source: left out
            for team, pl in named.items():
                others = [t for t in named if t != team]
                rows.append({
                    "id": hashlib.sha1(f"{link}#{k}|{team}".encode()).hexdigest()[:12], "date": date, "team": team,
                    "players": ";".join(pl), "other_teams": ";".join(others), "status": status, "reporters": ";".join(reps),
                    "source_url": outside_link(links) or link, "post_url": link, "headline": title, "seen_at": seen_at,
                })
    return rows


def get(url, robots, opener=urllib.request.urlopen):
    if robots is not None and not robots.can_fetch(UA, url):
        raise PermissionError("robots.txt disallows it")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.5"})
    with opener(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def read_rows(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def merge(old, new):
    """Keep every report already on file (first seen wins); add the new ones; newest first."""
    have = {r["id"] for r in old}
    rows = old + [r for r in new if r["id"] not in have and not have.add(r["id"])]
    return sorted(rows, key=lambda r: (r["date"], r["id"]), reverse=True)


def run(all_teams=False, opener=urllib.request.urlopen, robots=None, sleep=time.sleep, log=print, root=ROOT):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if robots is None:
        robots = urllib.robotparser.RobotFileParser(SITE + "/robots.txt")
        try:
            robots.parse(get(SITE + "/robots.txt", None, opener).splitlines())
        except Exception:
            robots = None  # no robots.txt answer: read only the feeds, as a reader's feed app would
    feeds = [SITE + "/feed"] + ([f"{SITE}/{slug}/feed" for slug, _ in TEAMS.values()] if all_teams else [])
    players = player_index(root)
    found, failed = [], []
    for i, url in enumerate(feeds):
        if i:
            sleep(PAUSE)
        try:
            found += parse_feed(get(url, robots, opener), players, now)
        except Exception as e:
            failed.append(f"{url.replace(SITE, '')}: {type(e).__name__}: {str(e)[:80]}")
    old = read_rows(root / REPORTS.relative_to(ROOT))
    rows = merge(old, found)
    new = len(rows) - len(old)
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=COLS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    write_atomic(root / REPORTS.relative_to(ROOT), buf.getvalue())
    ok = len(failed) < len(feeds)
    write_atomic(root / STATUS.relative_to(ROOT), json.dumps({"updated": now, "ok": ok, "feeds": len(feeds), "failed": failed,
                                                              "new_reports": new, "reports": len(rows)}, indent=1) + "\n")
    log(f"{len(feeds) - len(failed)} of {len(feeds)} feeds read; {new} new reports ({len(rows)} on file)")
    for f in failed:
        log("  not read: " + f)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all-teams", action="store_true", help="also read all 30 team feeds")
    a = ap.parse_args()
    return run(all_teams=a.all_teams)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
