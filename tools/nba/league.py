"""League-wide NBA.com data in the tracker's own file formats (all 30 teams).

Writes to data/nba/:
  league_results.csv        every regular-season game: date, away, home, scores once final, overtime periods
  league_box.csv            one row per team per game: shooting, rebounds, turnovers, fouls, minutes
  league_player_games.csv   every player's line in every game

A final game is published only once its box score is complete and matches the final score (accept_finals);
until then the version already on file is kept, or the game waits for the next run.
  league_quarters.csv       points per quarter for each team in each game (fetched once per game)
  league_inactive.csv       players NBA.com listed inactive for each game (fetched once per game)
  league_extras.csv         per team per game: team turnovers, paint, second-chance, fast-break, off-turnover and bench points, lead changes

Team turnovers: NBA.com's team game logs count only player turnovers. Box-score analysis (Basketball-Reference,
the four factors) counts team turnovers too, so league_box.csv adds them from the game summary: tov = players + team.

The page merges these over the hand-checked Lakers files; NBA.com wins where both exist.
Plain Python only (see rows.py for why there is no pandas).
"""
import datetime as _dt

from nba_api.stats.endpoints import boxscoresummaryv3, leaguegamefinder, playergamelogs, scheduleleaguev2
from nba_api.stats.static import teams as static_teams

from rows import from_dataset, read

TRICODE = {t["id"]: t["abbreviation"] for t in static_teams.get_teams()}
REGULAR = "002"  # NBA game ids: 001 preseason, 002 regular season (NBA Cup group and knockout games included), 004 playoffs, 005 play-in
BOX = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "tov", "pf"]
LINE = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf"]
EXTRAS = {"turnoversTeam": "tov_team", "turnoversTotal": "tov_total", "reboundsTeam": "reb_team", "pointsInThePaint": "pts_paint",
          "pointsSecondChance": "pts_second_chance", "pointsFastBreak": "pts_fast_break", "pointsFromTurnovers": "pts_off_tov",
          "benchPoints": "pts_bench", "biggestLead": "biggest_lead", "leadChanges": "lead_changes", "timesTied": "times_tied",
          "biggestScoringRun": "biggest_run"}


def _date(v):
    return str(v)[:10]


def _min(v):
    """Minutes as a number: 240, 240.0 or '240:00' all become 240.0; empty becomes None."""
    if v is None or v == "":
        return None
    s = str(v)
    if ":" in s:
        m, sec = s.split(":")[:2]
        return int(m) + int(sec) / 60
    return float(s)


def _int(v):
    return 0 if v is None or v == "" else int(float(v))


def _opp(matchup):
    # "LAL vs. GSW" (home) or "LAL @ GSW" (away)
    return str(matchup).split()[-1]


def _sides(sched):
    """game id -> (home, away) from the schedule. NBA.com's MATCHUP text is occasionally wrong
    (on 2026-11-07 it gave Indiana as its own opponent), so the schedule decides where it can."""
    return {str(g["gameId"]): (g.get("homeTeam_teamTricode"), g.get("awayTeam_teamTricode")) for g in (sched or [])}


def _opp_ha(r, sides):
    """(opponent, 'H' or 'A') for a team or player row."""
    team, gid = r["TEAM_ABBREVIATION"], str(r["GAME_ID"])
    home, away = sides.get(gid, (None, None))
    if team in (home, away) and home and away:
        return (away, "H") if team == home else (home, "A")
    return _opp(r["MATCHUP"]), ("A" if "@" in str(r["MATCHUP"]) else "H")


def _regular(rows, key):
    return [r for r in rows if str(r.get(key, "")).startswith(REGULAR)]


def schedule(fetch, season):
    ep = fetch(scheduleleaguev2.ScheduleLeagueV2, season=season, league_id="00")
    games, cols = from_dataset(ep.season_games)
    date_col = "gameDateEst" if "gameDateEst" in cols else "gameDate"
    games = _regular(games, "gameId")
    for g in games:
        g["date"] = _date(g[date_col])
    return games


def team_games(fetch, season):
    ep = fetch(leaguegamefinder.LeagueGameFinder, player_or_team_abbreviation="T", season_nullable=season,
               season_type_nullable="Regular Season", league_id_nullable="00")
    return _regular(from_dataset(ep.league_game_finder_results)[0], "GAME_ID")


def player_games(fetch, season):
    ep = fetch(playergamelogs.PlayerGameLogs, season_nullable=season, season_type_nullable="Regular Season",
               measure_type_player_game_logs_nullable="Base")
    return _regular(from_dataset(ep.player_game_logs)[0], "GAME_ID")


BOX_COLS = ["date", "team", "opp", "home_away"] + BOX + ["mp", "game_id"]
RESULT_COLS = ["date", "away", "home", "away_pts", "home_pts", "ot", "game_id", "cup", "neutral"]
LINE_COLS = ["date", "team", "opp", "player", "min"] + LINE + ["game_id", "player_id"]


def box_rows(tg, sched=None):
    out, sides = [], _sides(sched)
    for r in tg:
        opp, ha = _opp_ha(r, sides)
        mp = _min(r.get("MIN"))
        if mp is not None:
            mp = 240 + 25 * max(0, int(round((mp - 240) / 25)))
        out.append({
            "date": _date(r["GAME_DATE"]), "team": r["TEAM_ABBREVIATION"], "opp": opp, "home_away": ha,
            **{k: _int(r[k.upper()]) for k in BOX},
            "mp": int(round(mp)) if mp is not None else "", "game_id": str(r["GAME_ID"]),
        })
    out.sort(key=lambda x: (x["date"], x["game_id"], x["team"]))
    return out


def result_rows(sched, box):
    """Schedule rows, with scores for final games (status 3) and overtime periods from team minutes."""
    ot = {r["game_id"]: max(0, int(round((int(r["mp"]) - 240) / 25))) for r in box if r["mp"] != ""}
    out = []
    for g in sched:
        final = _int(g.get("gameStatus")) == 3
        gid = str(g["gameId"])
        # NBA Cup group and knockout games count in the standings (the final has its own 006 id and is left out);
        # the schedule labels them, e.g. "Emirates NBA Cup" / "West Group B", or game subtype "in-season"
        label = " ".join(str(g.get(k) or "").strip() for k in ("gameLabel", "gameSubLabel")).strip()
        cup = label if ("cup" in label.lower() or "in-season" in str(g.get("gameSubtype") or "").lower()) else ""
        out.append({
            "date": g["date"], "away": g["awayTeam_teamTricode"], "home": g["homeTeam_teamTricode"],
            "away_pts": _int(g.get("awayTeam_score")) if final else "", "home_pts": _int(g.get("homeTeam_score")) if final else "",
            "ot": ot.get(gid, 0) if final else "", "game_id": gid,
            "cup": cup or ("NBA Cup" if "in-season" in str(g.get("gameSubtype") or "").lower() else ""),
            "neutral": 1 if str(g.get("isNeutral")).lower() in ("1", "true") else 0,
        })
    out.sort(key=lambda x: (x["date"], x["game_id"]))
    return out


def player_rows(pg, sched=None):
    out, sides = [], _sides(sched)
    for r in pg:
        out.append({
            "date": _date(r["GAME_DATE"]), "team": r["TEAM_ABBREVIATION"], "opp": _opp_ha(r, sides)[0], "player": r["PLAYER_NAME"],
            "min": round(_min(r.get("MIN")) or 0, 2),
            **{k: _int(r[k.upper()]) for k in LINE},
            "game_id": str(r["GAME_ID"]), "player_id": r["PLAYER_ID"],
        })
    out.sort(key=lambda x: (x["date"], x["team"], x["player"]))
    return out


# ---------------- which final games to publish ----------------
def _group(rows):
    out = {}
    for r in rows:
        out.setdefault(str(r["game_id"]), []).append(r)
    return out


def _restored_box(r):
    """A box row read back from league_box.csv, as box_rows() makes it (numbers, player turnovers only)."""
    out = {k: r.get(k, "") for k in ["date", "team", "opp", "home_away", "game_id"]}
    out.update({k: _int(r.get(k)) for k in BOX})
    if r.get("tov_players") not in (None, ""):
        out["tov"] = _int(r["tov_players"])  # team turnovers are added again by add_team_turnovers()
    out["mp"] = _int(r["mp"]) if r.get("mp") not in (None, "") else ""
    return out


def _restored_line(r):
    out = {k: r.get(k, "") for k in ["date", "team", "opp", "player", "game_id", "player_id"]}
    out.update({k: _int(r.get(k)) for k in LINE})
    out["min"] = float(r["min"]) if r.get("min") not in (None, "") else 0
    return out


def game_problem(res, box):
    """Why a game's final score cannot be published yet, or None if it can.

    Needed: a final score, a box score row for each team, each team's box points equal to its final score,
    and no tie (the 112-112 seen once was a box score caught mid-overtime)."""
    if res["home_pts"] in ("", None) or res["away_pts"] in ("", None):
        return "no final score"
    by_team = {r["team"]: r for r in box}
    if len(box) != 2 or set(by_team) != {res["home"], res["away"]}:
        return "box score not posted for both teams"
    if _int(by_team[res["home"]]["pts"]) != _int(res["home_pts"]) or _int(by_team[res["away"]]["pts"]) != _int(res["away_pts"]):
        return (f"box score {by_team[res['away']]['pts']}-{by_team[res['home']]['pts']} differs from the final score "
                f"{res['away_pts']}-{res['home_pts']}")
    if _int(res["home_pts"]) == _int(res["away_pts"]):
        return "tied score"
    return None


def lines_problem(lines, box):
    """Why a game's player lines are incomplete, or None: each team needs lines whose points add up to its box points."""
    pts = {}
    for r in lines:
        pts[r["team"]] = pts.get(r["team"], 0) + _int(r["pts"])
    for b in box:
        if b["team"] not in pts:
            return f"no player lines for {b['team']}"
        if pts[b["team"]] != _int(b["pts"]):
            return f"{b['team']} player points add up to {pts[b['team']]}, not {b['pts']}"
    return None


def accept_finals(results, box, lines, old_results, old_box, old_lines, min_share=0.95):
    """Decide what to publish for each game. Returns (results, box, lines, report).

    A final game is published when game_problem() finds nothing. When it does, the version already on file is
    kept if that one was complete; otherwise the game waits (no score, no box score, no player lines) and the next
    run asks again. Incomplete player lines do not hold a final score back: a complete earlier set is kept if there
    is one, and the rest is reported. A schedule much shorter than the one on file means a bad download: nothing
    is written (ValueError)."""
    old_ids = {str(r["game_id"]) for r in old_results}
    if old_results and len(results) < min_share * len(old_results):
        raise ValueError(f"the schedule has {len(results)} games, against {len(old_results)} on file: not saved")
    gone = old_ids - {str(r["game_id"]) for r in results}
    if len(gone) > 10:
        raise ValueError(f"{len(gone)} games on file are missing from the new schedule: not saved")
    bx, ln = _group(box), _group(lines)
    obx, oln = _group(old_box), _group(old_lines)
    ores = {str(r["game_id"]): r for r in old_results}
    out_res, out_box, out_lines = [], [], []
    report = {"pending": [], "kept": [], "lines": []}

    def name(r):
        return f"{r['away']}@{r['home']} ({r['date']})"

    def old_ok(gid):
        o = ores.get(gid)
        return o is not None and game_problem(o, obx.get(gid, [])) is None

    for r in results:
        gid = str(r["game_id"])
        final = r["home_pts"] not in ("", None)
        why = game_problem(r, bx.get(gid, [])) if final else None
        if final and why is None:
            out_res.append(r)
            out_box += bx[gid]
            new_lines, old_lines_g = ln.get(gid, []), oln.get(gid, [])
            lw = lines_problem(new_lines, bx[gid])
            if lw is None:
                out_lines += new_lines
            elif old_lines_g and lines_problem(old_lines_g, bx[gid]) is None:
                out_lines += [_restored_line(x) for x in old_lines_g]
                report["lines"].append(f"{name(r)}: {lw}; kept the complete lines already on file")
            else:
                out_lines += new_lines
                report["lines"].append(f"{name(r)}: {lw}")
        elif old_ok(gid):
            # NBA.com shows this game as not final, or not complete, right now: keep the complete version on file
            o = ores[gid]
            out_res.append(dict(r, away_pts=_int(o["away_pts"]), home_pts=_int(o["home_pts"]), ot=_int(o["ot"])))
            out_box += [_restored_box(x) for x in obx[gid]]
            out_lines += [_restored_line(x) for x in oln.get(gid, [])]
            report["kept"].append(f"{name(r)}: {why or 'no longer final on NBA.com'}; kept the version on file")
        elif final:
            out_res.append(dict(r, away_pts="", home_pts="", ot=""))
            report["pending"].append(f"{name(r)}: {why}")
        else:
            out_res.append(r)
    key = lambda x: (x["date"], x["game_id"], x["team"])  # noqa: E731
    out_box.sort(key=key)
    out_lines.sort(key=lambda x: (x["date"], x["team"], x["player"]))
    return out_res, out_box, out_lines, report


def save_finals(save, results, box, lines):
    """Write the three files together, after accept_finals()."""
    save(results, "league_results.csv", RESULT_COLS)
    save(box, "league_box.csv", BOX_COLS)
    save(lines, "league_player_games.csv", LINE_COLS)
    return sum(1 for r in results if r["home_pts"] not in ("", None))


def summaries(fetch, save, out_dir, results, refresh=False):
    """Quarter scores, inactive players and game extras, one request per final game not already on file.

    Returns (new games, missed): missed lists (game_id, date, error) for games that could not be read; they are
    left out of the files, so the next run asks for them again."""
    q_old = [] if refresh else read(out_dir / "league_quarters.csv")
    i_old = [] if refresh else read(out_dir / "league_inactive.csv")
    x_old = [] if refresh else read(out_dir / "league_extras.csv")
    info = {r["game_id"]: r for r in results if r["home_pts"] != ""}
    # A game counts as done once its quarters, extras and inactive list are all on file. NBA.com sometimes
    # fills the inactive list in late (39 early-season games had none), so a game without one is asked
    # for again, for up to 30 days.
    has_inactive = {r["game_id"] for r in i_old}
    today = _dt.date.today()

    def recent(gid):
        try:
            return (today - _dt.date.fromisoformat(info[gid]["date"])).days <= 30
        except (KeyError, ValueError):
            return False
    done = {g for g in {r["game_id"] for r in q_old} & {r["game_id"] for r in x_old} if g in has_inactive or not recent(g)}
    todo = [g for g in info if g not in done]
    redo = set(todo)  # drop any partial rows for games fetched again
    q_old = [r for r in q_old if r["game_id"] not in redo]
    i_old = [r for r in i_old if r["game_id"] not in redo]
    x_old = [r for r in x_old if r["game_id"] not in redo]
    q_new, i_new, x_new, missed = [], [], [], []
    for gid in todo:
        g = info[gid]
        try:
            ep = fetch(boxscoresummaryv3.BoxScoreSummaryV3, game_id=gid)
            ls = from_dataset(ep.line_score)[0]
            others, inactive = from_dataset(ep.other_stats)[0], from_dataset(ep.inactive_players)[0]
        except Exception as e:  # one unreadable game should not cost the rest; it is retried next run
            missed.append((gid, g["date"], f"{type(e).__name__}: {str(e)[:120]}"))
            continue
        if len(ls) != 2 or any(r.get("period%dScore" % k) is None for r in ls for k in range(1, 5)):
            continue  # not final in the summary yet; try again next run
        for r in ls:
            team = TRICODE.get(_int(r.get("teamId")), r.get("teamTricode"))
            q = [_int(r["period%dScore" % k]) for k in range(1, 5)]
            q_new.append({"date": g["date"], "team": team, "opp": g["away"] if team == g["home"] else g["home"],
                          "q1": q[0], "q2": q[1], "q3": q[2], "q4": q[3], "ot": _int(r.get("score")) - sum(q), "game_id": gid})
        for r in others:
            team = TRICODE.get(_int(r.get("teamId")), r.get("teamTricode"))
            x_new.append({"date": g["date"], "team": team, "opp": g["away"] if team == g["home"] else g["home"],
                          **{v: r.get(k) for k, v in EXTRAS.items()}, "game_id": gid})
        for r in inactive:
            i_new.append({"date": g["date"], "team": TRICODE.get(_int(r.get("teamId")), ""),
                          "player": f'{r.get("firstName") or ""} {r.get("familyName") or ""}'.strip(), "game_id": gid, "person_id": r.get("personId")})
    q_all = sorted(q_old + q_new, key=lambda x: (x["date"], x["game_id"], x["team"]))
    i_all = sorted(i_old + i_new, key=lambda x: (x["date"], x["game_id"], x["team"]))
    save(q_all, "league_quarters.csv", ["date", "team", "opp", "q1", "q2", "q3", "q4", "ot", "game_id"])
    save(i_all, "league_inactive.csv", ["date", "team", "player", "game_id", "person_id"])
    x_all = sorted(x_old + x_new, key=lambda x: (x["date"], x["game_id"], x["team"]))
    save(x_all, "league_extras.csv", ["date", "team", "opp"] + list(EXTRAS.values()) + ["game_id"])
    return len(q_new) // 2, missed


def add_team_turnovers(box, save, out_dir):
    """Add team turnovers from the game summaries: tov = player turnovers + team turnovers. Returns rows updated."""
    extra = {(r["game_id"], r["team"]): r for r in read(out_dir / "league_extras.csv")}
    n = 0
    for r in box:
        x = extra.get((r["game_id"], r["team"]))
        r["tov_players"] = r.get("tov_players", r["tov"])
        if x and x.get("tov_team") not in (None, ""):
            r["tov_team"] = _int(x["tov_team"])
            r["tov"] = r["tov_players"] + r["tov_team"]
            n += 1
        else:
            r["tov_team"] = ""
    cols = ["date", "team", "opp", "home_away"] + BOX + ["mp", "game_id", "tov_players", "tov_team"]
    save(box, "league_box.csv", cols)
    return n


# ---------------- checks against the hand-checked files ----------------
def checks(root, results, box, pgames, out_dir):
    notes = []
    data = root / "data"
    cur = {(r["date"], r["away"], r["home"]): r for r in read(data / "results.csv") if r["home_pts"].strip()}
    nba = {(r["date"], r["away"], r["home"]): r for r in results if r["home_pts"] != ""}
    both = [k for k in cur if k in nba]
    bad = [k for k in both if (int(cur[k]["away_pts"]), int(cur[k]["home_pts"])) != (int(nba[k]["away_pts"]), int(nba[k]["home_pts"]))]
    missing = [k for k in cur if k not in nba]
    notes.append(f"Scores: {len(both)} games in both files, {len(bad)} differ{(' ' + str(bad[:5])) if bad else ''}; "
                 f"{len(missing)} hand-checked games not found on NBA.com by date and teams{(' ' + str(missing[:5])) if missing else ''}.")
    K = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "tov"]
    nb = {(r["date"], r["team"]): r for r in box}
    lb = read(data / "lakers_box.csv")
    diffs = [(r["date"], r["team"], k, r[k], nb[(r["date"], r["team"])][k]) for r in lb if (r["date"], r["team"]) in nb
             for k in K if int(r[k]) != int(nb[(r["date"], r["team"])][k])]
    notes.append(f"Lakers box scores: {sum((r['date'], r['team']) in nb for r in lb)} team-games compared, {len(diffs)} numbers differ{(' ' + str(diffs[:6])) if diffs else ''}.")
    sums = {}
    for r in pgames:
        sums[(r["date"], r["team"])] = sums.get((r["date"], r["team"]), 0) + int(r["pts"])
    off = [(d, t, s, int(nb[(d, t)]["pts"])) for (d, t), s in sums.items() if (d, t) in nb and s != int(nb[(d, t)]["pts"])]
    notes.append(f"Player points add up to team points in {len(sums) - len(off)} of {len(sums)} team-games{(': ' + str(off[:5])) if off else ''}.")
    xs = read(out_dir / "league_extras.csv")
    tov = {(b["game_id"], b["team"]): b["tov"] for b in box}
    tot_off = [(r["date"], r["team"]) for r in xs if r.get("tov_total") not in (None, "") and (r["game_id"], r["team"]) in tov
               and _int(r["tov_total"]) != _int(tov[(r["game_id"], r["team"])])]
    compared = [r for r in xs if r.get("tov_total") not in (None, "") and (r["game_id"], r["team"]) in tov]
    if compared:
        notes.append(f"Turnovers (players + team) match NBA.com's game totals in {len(compared) - len(tot_off)} of {len(compared)} team-games{(': ' + str(tot_off[:5])) if tot_off else ''}.")
    q = {(r["date"], r["team"]): r for r in read(out_dir / "league_quarters.csv")}
    if q:
        lq = read(data / "lakers_quarters.csv")
        qd = [(r["date"], r["team"]) for r in lq if (r["date"], r["team"]) in q and any(int(r[k]) != int(q[(r["date"], r["team"])][k]) for k in ["q1", "q2", "q3", "q4"])]
        notes.append(f"Lakers quarter scores: {sum((r['date'], r['team']) in q for r in lq)} compared, {len(qd)} differ{(' ' + str(qd)) if qd else ''}.")
    # NBA Cup group play uses point differential as a tiebreaker, so Cup games may run to bigger margins.
    # The ratings count every point; this line tracks whether that matters (compare at season end).
    fin = [r for r in results if r["home_pts"] != "" and r["away_pts"] != ""]
    cup = [abs(int(r["home_pts"]) - int(r["away_pts"])) for r in fin if r.get("cup")]
    other = [abs(int(r["home_pts"]) - int(r["away_pts"])) for r in fin if not r.get("cup")]
    if cup and other:
        notes.append(f"NBA Cup games: {len(cup)} played, average winning margin {sum(cup) / len(cup):.1f} points, "
                     f"against {sum(other) / len(other):.1f} in the other {len(other)} games.")
    return notes
