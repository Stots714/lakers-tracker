"""The NBA's official injury report (a PDF the league publishes every 15 minutes) to data/nba/injuries.csv.

Teams must file a status for each injured player by 5 p.m. local time the day before a game, again on
game day, and update it as things change (official.nba.com/nba-injury-report). The report is the source
news outlets cite ("according to the NBA's injury report"), with five statuses: Out, Doubtful,
Questionable, Probable and Available.

injuries.csv holds only the newest report, one row per player listed:
  report       when the league published it, Eastern time, e.g. "Nov 8, 2:30 PM ET"
  game_date    YYYY-MM-DD (Eastern)
  game_time    as printed, e.g. "06:00 (ET)"
  matchup      e.g. "LAL@DET"
  team         three-letter code
  player       "First Last" (the report prints "Last, First")
  status       Out / Doubtful / Questionable / Probable / Available, or "Not submitted" for a team that
               has not filed yet (no player)
  reason       e.g. "Injury/Illness - Low Back; Tightness"

Plain Python plus pypdf, which is pure Python (Windows Smart App Control blocks unsigned compiled modules).
Reading is defensive: text is placed into columns by its position on the page, every row is checked, and
a report that does not parse cleanly is rejected so the last good file stays in place.
"""
import csv
import datetime as dt
import io
import os
import re
from pathlib import Path

import requests
from pypdf import PdfReader

INDEX = "https://official.nba.com/nba-injury-report/"
PDF = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{stamp}.pdf"
NAME = re.compile(r"Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})_(\d{2})(AM|PM)\.pdf")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0",
    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
    "Referer": "https://official.nba.com/",
}
COLS = ["Game Date", "Game Time", "Matchup", "Team", "Player Name", "Current Status", "Reason"]
# Left edge of each column in points, for pages where the header row cannot be read (2023-24 layout onward)
DEFAULT_STARTS = [18.3, 108.8, 183.5, 255.1, 371.9, 543.5, 655.0]
STATUSES = ["Out", "Doubtful", "Questionable", "Probable", "Available"]
TEAMS = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}
FIELDS = ["report", "game_date", "game_time", "matchup", "team", "player", "status", "reason"]


class ReportError(Exception):
    """The report could not be found or did not parse cleanly."""


# ---------------- time ----------------
def eastern(now_utc=None):
    """Current US Eastern time, without tzdata: daylight time runs from 2 a.m. on the second Sunday in
    March to 2 a.m. on the first Sunday in November."""
    now = now_utc or dt.datetime.now(dt.timezone.utc)

    def sunday(month, n):
        d = dt.date(now.year, month, 1)
        return d + dt.timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))
    start = dt.datetime.combine(sunday(3, 2), dt.time(7), dt.timezone.utc)
    end = dt.datetime.combine(sunday(11, 1), dt.time(6), dt.timezone.utc)
    return now.astimezone(dt.timezone(dt.timedelta(hours=-4 if start <= now < end else -5)))


def stamp_of(when):
    """Eastern datetime -> the report's file stamp, e.g. 2026-11-08_02_30PM."""
    return when.strftime("%Y-%m-%d_%I_%M%p")


def when_of(name_match):
    d, h, m, ap = name_match.groups()
    hour = int(h) % 12 + (12 if ap == "PM" else 0)
    return dt.datetime.fromisoformat(d).replace(hour=hour, minute=int(m))


# ---------------- finding the newest report ----------------
def newest_url(session, now_utc=None, log=print):
    """The newest report: from the league's index page, else by trying the last few 15-minute slots."""
    try:
        r = session.get(INDEX, headers=HEADERS, timeout=30)
        if r.ok:
            found = {m.group(0): when_of(m) for m in NAME.finditer(r.text)}
            if found:
                name = max(found, key=found.get)
                return PDF.format(stamp=name[len("Injury-Report_"):-len(".pdf")]), found[name]
    except requests.RequestException as e:
        log(f"  injury report index not available: {type(e).__name__}")
    et = eastern(now_utc)
    slot = et.replace(minute=et.minute - et.minute % 15, second=0, microsecond=0, tzinfo=None)
    for i in range(12):  # 3 hours back
        when = slot - dt.timedelta(minutes=15 * i)
        url = PDF.format(stamp=stamp_of(when))
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException:
            continue
        if r.ok and r.content[:4] == b"%PDF":
            return url, when
    raise ReportError("no injury report found on the index page or in the last 3 hours")


# ---------------- reading the PDF ----------------
def _mult(a, b):
    return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5]]


def _fragments(page):
    """(x, y, text) for every piece of text on the page, in page coordinates (y grows upward)."""
    out = []

    def visit(text, cm, tm, font, size):
        if text and text.strip():
            m = _mult(tm, cm)
            for k, line in enumerate(text.split("\n")):
                if line.strip():
                    out.append((m[4], m[5] - k * (size or 8) * 1.15, line.strip()))
    page.extract_text(visitor_text=visit)
    return out


def _rows(frags, starts):
    """Fragments -> table rows (top to bottom), each a dict column -> text."""
    lines = {}
    for x, y, t in frags:
        key = next((k for k in lines if abs(k - y) <= 2.0), None)
        lines.setdefault(key if key is not None else y, []).append((x, t))
    rows = []
    for y in sorted(lines, reverse=True):
        cells = {}
        for x, t in sorted(lines[y]):
            col = max((i for i, s in enumerate(starts) if x >= s - 3), default=0)
            cells[COLS[col]] = (cells.get(COLS[col], "") + " " + t).strip()
        rows.append((y, cells))
    return rows


def _name(last_first):
    """'Lively II, Dereck' -> 'Dereck Lively II'."""
    if "," not in last_first:
        return last_first.strip()
    last, first = last_first.split(",", 1)
    return f"{first.strip()} {last.strip()}"


def parse(pdf_bytes, report_label=""):
    """PDF bytes -> list of row dicts (FIELDS). Raises ReportError when the result does not check out."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    entries, carry, bad = [], {c: "" for c in COLS[:4]}, []
    for page in reader.pages:
        frags = _fragments(page)
        heads = {t: x for x, y, t in frags if t in COLS}
        head_y = max((y for x, y, t in frags if t in COLS), default=None)
        starts = [heads.get(c) for c in COLS]
        if any(s is None for s in starts):
            starts = DEFAULT_STARTS
        for y, cells in _rows(frags, starts):
            text = " ".join(cells.values())
            if head_y is not None and y >= head_y - 1:
                continue  # page title and the header row
            if re.fullmatch(r"Page \d+ of \d+", text) or text.startswith("Injury Report:") or all(v in COLS for v in cells.values()):
                continue
            for c in COLS[:4]:
                if cells.get(c):
                    carry[c] = cells[c]
                    if c == "Matchup":
                        carry["Team"] = carry["Team"] if cells.get("Team") else ""
            player, status, reason = cells.get("Player Name", ""), cells.get("Current Status", ""), cells.get("Reason", "")
            if player or status:
                entries.append({**carry, "Player Name": player, "Current Status": status, "Reason": reason})
            elif reason.upper().startswith("NOT YET SUBMITTED"):
                entries.append({**carry, "Player Name": "", "Current Status": "Not submitted", "Reason": ""})
            elif reason and entries:
                entries[-1]["Reason"] = (entries[-1]["Reason"] + " " + reason).strip()  # a wrapped reason
            elif text.strip():
                bad.append(text)
    rows = []
    for e in entries:
        problems = []
        try:
            date = dt.datetime.strptime(e["Game Date"], "%m/%d/%Y").date().isoformat()
        except ValueError:
            problems.append("date " + e["Game Date"])
            date = ""
        team = TEAMS.get(e["Team"])
        if not team:
            problems.append("team " + e["Team"])
        if e["Current Status"] not in STATUSES + ["Not submitted"]:
            problems.append("status " + e["Current Status"])
        if not re.fullmatch(r"[A-Z]{2,3}@[A-Z]{2,3}", e["Matchup"].replace(" ", "")):
            problems.append("matchup " + e["Matchup"])
        if problems:
            bad.append("; ".join(problems))
            continue
        rows.append({"report": report_label, "game_date": date, "game_time": e["Game Time"],
                     "matchup": e["Matchup"].replace(" ", ""), "team": team, "player": _name(e["Player Name"]),
                     "status": e["Current Status"], "reason": re.sub(r"\s+", " ", e["Reason"]).strip()})
    if not rows:
        raise ReportError("no rows read from the report" + (f" (first unreadable line: {bad[0][:80]})" if bad else ""))
    if len(bad) > max(2, len(rows) // 20):
        raise ReportError(f"{len(bad)} unreadable lines against {len(rows)} rows, e.g. {bad[0][:80]}")
    return rows


# ---------------- the file ----------------
def write(path, rows):
    """Write rows atomically: a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def current_label(path):
    """The report time of the file on disk, or ''."""
    try:
        with path.open(encoding="utf-8", newline="") as f:
            first = next(csv.DictReader(f), None)
        return first["report"] if first else ""
    except (OSError, KeyError, StopIteration):
        return ""


def update(out_dir, session=None, log=print):
    """Fetch the newest report and write injuries.csv if it is newer. Returns (rows, label, changed)."""
    session = session or requests.Session()
    path = Path(out_dir) / "injuries.csv"
    url, when = newest_url(session, log=log)
    label = f"{when:%b} {when.day}, {when.hour % 12 or 12}:{when:%M} {when:%p} ET"  # e.g. "Nov 8, 2:30 PM ET"
    if label == current_label(path):
        return None, label, False
    r = session.get(url, headers=HEADERS, timeout=60)
    if not r.ok or r.content[:4] != b"%PDF":
        raise ReportError(f"report download failed: HTTP {r.status_code}")
    rows = parse(r.content, label)
    write(path, rows)
    return rows, label, True


if __name__ == "__main__":  # python injuries.py [out_dir]: fetch once and print a summary
    import sys
    rows, label, changed = update(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "data" / "nba")
    print(label, "new" if changed else "unchanged", len(rows or []), "rows")
