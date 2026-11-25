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
  Requested the player has asked to be traded
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
    # Trade words required: "agreed to a deal" and "officially signed" are usually contracts
    ("Official", re.compile(r"\b(officially (?:acquired|traded|completed|sent|announced (?:a|the) trade)|(?:have|has) (?:completed|finalized|announced) (?:a|the|their) trade|"
                            r"trade (?:is|was|has been) (?:now )?(?:official|completed|finalized)|announced (?:that )?(?:they|the team) (?:have|has) (?:acquired|traded))", re.I)),
    # "are trading X to the Y" is how insiders announce a deal; a bare "will trade" is usually a question ("whether they will trade him")
    ("Agreed", re.compile(r"\b(agreed (?:to|on) (?:a |the |terms on a )?trade|(?:have|has) agreed to (?:send|trade|acquire|move)|"
                          r"(?:are|is) (?:trading|sending) [\w .'’-]{2,40}? to the|is being traded to|"
                          r"trade (?:is|has been) (?:agreed|struck|in place|done)|finaliz\w+ (?:a |the )?trade)", re.I)),
    ("Denied", re.compile(r"\b(not (?:actively )?(?:shopping|looking to (?:trade|move|deal))|no (?:plans|intention|interest) (?:to|in) (?:trade|trading|move|moving|deal|dealing)|(?:isn't|is not|aren't|are not|won't|will not|isn’t|aren’t|won’t) (?:expected to be |going to be |likely to be )?(?:traded|moved|available|dealt)|off the table|untouchable|not interested in (?:trading|moving|dealing))", re.I)),
    ("Requested", re.compile(r"\b(request\w* (?:a |to be )?trade\w*|trade request|asked (?:to be traded|for a trade)|wants? out|wants? to be (?:traded|moved))", re.I)),
    ("Talks", re.compile(r"\b(talks?|discussions?|discussed|negotiat\w+|engaged|conversations?|explor\w+|made (?:an |a )?(?:offer|pitch|proposal)|offered|offering|proposals?|proposed|framework)\b", re.I)),
    ("Interest", re.compile(r"\b(interest(?:ed)? in|(?:have|has|had|showed|showing|registered|expressed) (?:some |real |serious )?interest|eyeing|eyed|keeping tabs|"
                            r"trade targets?|potential targets?|covet\w*|inquir\w+|kick(?:ed|ing) the tires|checked in (?:on|about|with)|fielded calls|"
                            r"calls? (?:on|about)|shopp\w+|on the (?:trade )?block|(?:made|make|makes|making) (?:\w+ )?available|available (?:in|via) (?:a )?trades?|"
                            r"dangl\w+|pursu\w+|in the mix)\b", re.I)),
]
# One team's move for another team's player, for paragraphs with no trade word ("the Lakers have interest in Jarrett Allen")
MOVE = re.compile(r"\b(acquir\w+|land(?:ing)?|pry(?:ing)?|deal|swap\w*|sen(?:d|ds|ding|t)|interest(?:ed)? in|eyeing|targets?|targeting|pursu\w+|inquir\w+|offer\w*|proposals?)\b", re.I)
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
# Words that start a sentence or name an outlet, stripped from the front of a captured name ("While Sam Amick", "ESPN Kris Rhim")
LEAD_WORDS = {"As", "While", "But", "And", "When", "After", "Before", "Per", "If", "Though", "Although", "Still", "Also", "Meanwhile",
              "However", "With", "Without", "Despite", "Since", "Then", "Now", "Yet", "So", "The", "A", "An", "In", "On", "At", "For",
              "From", "To", "By", "Of", "Via", "According", "ESPN", "NBA", "Yahoo", "HoopsHype", "Athletic", "SI", "CBS", "NBC", "Fox",
              "FOX", "TNT", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
              "November", "December"}
# A captured "name" with one of these words in it is an award, outlet or place, not a person
NOT_NAME_WORDS = {"Player", "Year", "Rookie", "Coach", "Executive", "Award", "Conference", "Team", "League", "Draft", "Cup", "Season",
                  "Game", "Post", "News", "Times", "Daily", "Tribune", "Herald", "Journal", "Globe", "Register", "Chronicle", "Press",
                  "Report", "Sports", "Network", "Radio", "Line", "Insider", "Notes", "Rumors", "Trade", "Free", "Agent", "Week",
                  "Month", "Defensive", "Offensive", "Most", "Valuable", "Improved", "Sixth", "Man", "Clutch", "Summer", "City",
                  "State", "York", "Angeles", "Orleans", "Antonio", "Bay", "Lake", "Center", "Arena", "Day", "Inquirer", "Gazette",
                  "Courier", "Observer", "Dispatch", "Bee", "Examiner", "Enquirer", "Sentinel", "Constitution", "Dealer", "Republic",
                  "Review", "Tribune-Review", "Star-Tribune", "Courant", "Advocate", "Oklahoman", "Statesman", "Telegraph"}
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


class Players:
    """Which team each player was on at a given date, so a report from before a trade names the team he was on then.
    From the season's box scores (the team of his latest game on or before the date, else his first game), with
    NBA.com's current rosters for players who have not played yet."""

    def __init__(self, root=ROOT):
        self.games, self.roster = {}, {}
        games = root / "data/nba/league_player_games.csv"
        if games.exists():
            with games.open(encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    self.games.setdefault(r["player"], set()).add((r["date"], r["team"]))
            self.games = {p: sorted(v) for p, v in self.games.items()}
        rost = root / "data/nba/league_rosters.csv"
        if rost.exists():
            with rost.open(encoding="utf-8", newline="") as f:
                self.roster = {r["player"]: r["team"] for r in csv.DictReader(f)}
        self._at = {}

    def at(self, date):
        """{player: team} as of a date (cached per date)."""
        if date not in self._at:
            out = dict(self.roster)
            for p, seq in self.games.items():
                before = [t for d, t in seq if d <= date]
                out[p] = before[-1] if before else seq[0][1]
            self._at[date] = out
        return self._at[date]


def player_index(root=ROOT):
    """Each player's team today (kept for callers that need no date)."""
    return Players(root).at("9999-12-31")


def clean_name(name):
    """A reporter's name from a captured phrase, or None: outlet prefixes and sentence starters removed, awards,
    outlets and places refused, and a word ending a sentence ("Amick.") or a possessive ("Acuff's") refused."""
    name = re.sub(r"^\S+(?:'s|’s)\s+", "", name.strip(" ."))
    toks = name.split()
    while toks and toks[0].rstrip(".") in LEAD_WORDS:
        toks = toks[1:]
    if len(toks) < 2:
        return None
    for t in toks:
        if t in NOT_NAME_WORDS or t.endswith(("'s", "’s")):
            return None
        if t.endswith(".") and not re.fullmatch(r"(?:[A-Z]\.)+|Jr\.|Sr\.", t):
            return None
    return " ".join(toks)


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
                name = clean_name(name)
                if not name:
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
    """Report rows from one RSS feed. players: a Players (team as of each post's date) or a plain {player: team}."""
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
        on = players.at(date) if hasattr(players, "at") else players
        for k, (text, links) in enumerate(blocks_of(body)):
            named = teams_in(text, on)
            status = status_of(text)
            # A trade paragraph: trade wording, or one team's interest in or talks about another team's player
            cross = len(named) >= 2 and any(named.values()) and status in ("Interest", "Talks", "Agreed") and MOVE.search(text)
            if not (TRADE.search(text) or cross):
                continue
            reps = reporters_in(text, on)
            if not reps:
                continue  # no named source: left out
            for team, pl in named.items():
                others = [t for t in named if t != team]
                # One report, however many posts repeat it: same day, team, players, status and reporters
                sig = "|".join([date, team, ";".join(sorted(pl)), status, ";".join(sorted(reps))])
                rows.append({
                    "id": hashlib.sha1(sig.encode()).hexdigest()[:12], "date": date, "team": team,
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
    """Reports already on file stay, with the first post that carried them and the time they were first seen;
    a report read again from another post is the same report. Newest first."""
    by = {r["id"]: r for r in old}
    for r in new:
        by.setdefault(r["id"], r)
    return sorted(by.values(), key=lambda r: (r["date"], r["id"]), reverse=True)


def run(all_teams=False, opener=urllib.request.urlopen, robots=None, sleep=time.sleep, log=print, root=ROOT):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if robots is None:
        robots = urllib.robotparser.RobotFileParser(SITE + "/robots.txt")
        try:
            robots.parse(get(SITE + "/robots.txt", None, opener).splitlines())
        except Exception:
            robots = None  # no robots.txt answer: read only the feeds, as a reader's feed app would
    feeds = [SITE + "/feed"] + ([f"{SITE}/{slug}/feed" for slug, _ in TEAMS.values()] if all_teams else [])
    players = Players(root)
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
    new = len({r["id"] for r in rows} - {r["id"] for r in old})
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=COLS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    write_atomic(root / REPORTS.relative_to(ROOT), buf.getvalue())
    ok = len(failed) < len(feeds)
    status_path = root / STATUS.relative_to(ROOT)
    try:  # when all 30 team feeds were last read, kept across the 2-hourly main-feed reads
        all_at = json.loads(status_path.read_text(encoding="utf-8")).get("all_teams_updated")
    except (OSError, ValueError):
        all_at = None
    write_atomic(status_path, json.dumps({"updated": now, "ok": ok, "feeds": len(feeds), "failed": failed,
                                          "new_reports": new, "reports": len(rows),
                                          "all_teams_updated": now if all_teams else all_at}, indent=1) + "\n")
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
