"""League-wide NBA.com data in the tracker's own file formats (all 30 teams).

Writes to data/nba/:
  league_results.csv        every regular-season game: date, away, home, scores once final, overtime periods
  league_box.csv            one row per team per game: shooting, rebounds, turnovers, fouls, minutes
  league_player_games.csv   every player's line in every game
  league_quarters.csv       points per quarter for each team in each game (fetched once per game)
  league_inactive.csv       players NBA.com listed inactive for each game (fetched once per game)
  league_extras.csv         per team per game: team turnovers, paint, second-chance, fast-break, off-turnover and bench points, lead changes

Team turnovers: NBA.com's team game logs count only player turnovers. Box-score analysis (Basketball-Reference,
the four factors) counts team turnovers too, so league_box.csv adds them from the game summary: tov = players + team.

The page merges these over the hand-checked Lakers files; NBA.com wins where both exist.
Plain Python only (see rows.py for why there is no pandas).
"""
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


def write_box(tg, save):
    out = []
    for r in tg:
        mp = _min(r.get("MIN"))
        if mp is not None:
            mp = 240 + 25 * max(0, int(round((mp - 240) / 25)))
        out.append({
            "date": _date(r["GAME_DATE"]), "team": r["TEAM_ABBREVIATION"], "opp": _opp(r["MATCHUP"]),
            "home_away": "A" if "@" in str(r["MATCHUP"]) else "H",
            **{k: _int(r[k.upper()]) for k in BOX},
            "mp": int(round(mp)) if mp is not None else "", "game_id": str(r["GAME_ID"]),
        })
    out.sort(key=lambda x: (x["date"], x["game_id"], x["team"]))
    cols = ["date", "team", "opp", "home_away"] + BOX + ["mp", "game_id"]
    return save(out, "league_box.csv", cols), out


def write_results(sched, box, save):
    """Schedule rows, with scores for final games (status 3) and overtime periods from team minutes."""
    ot = {r["game_id"]: max(0, int(round((int(r["mp"]) - 240) / 25))) for r in box if r["mp"] != ""}
    out = []
    for g in sched:
        final = _int(g.get("gameStatus")) == 3
        gid = str(g["gameId"])
        out.append({
            "date": g["date"], "away": g["awayTeam_teamTricode"], "home": g["homeTeam_teamTricode"],
            "away_pts": _int(g.get("awayTeam_score")) if final else "", "home_pts": _int(g.get("homeTeam_score")) if final else "",
            "ot": ot.get(gid, 0) if final else "", "game_id": gid,
        })
    out.sort(key=lambda x: (x["date"], x["game_id"]))
    return save(out, "league_results.csv", ["date", "away", "home", "away_pts", "home_pts", "ot", "game_id"]), out


def write_player_games(pg, save):
    out = []
    for r in pg:
        out.append({
            "date": _date(r["GAME_DATE"]), "team": r["TEAM_ABBREVIATION"], "opp": _opp(r["MATCHUP"]), "player": r["PLAYER_NAME"],
            "min": round(_min(r.get("MIN")) or 0, 2),
            **{k: _int(r[k.upper()]) for k in LINE},
            "game_id": str(r["GAME_ID"]), "player_id": r["PLAYER_ID"],
        })
    out.sort(key=lambda x: (x["date"], x["team"], x["player"]))
    cols = ["date", "team", "opp", "player", "min"] + LINE + ["game_id", "player_id"]
    return save(out, "league_player_games.csv", cols), out


def summaries(fetch, save, out_dir, results, refresh=False):
    """Quarter scores and inactive players, one request per final game not already on file."""
    q_old = [] if refresh else read(out_dir / "league_quarters.csv")
    i_old = [] if refresh else read(out_dir / "league_inactive.csv")
    x_old = [] if refresh else read(out_dir / "league_extras.csv")
    done = {r["game_id"] for r in q_old} & {r["game_id"] for r in x_old}
    info = {r["game_id"]: r for r in results if r["home_pts"] != ""}
    todo = [g for g in info if g not in done]
    redo = set(todo)  # drop any partial rows for games fetched again
    q_old = [r for r in q_old if r["game_id"] not in redo]
    i_old = [r for r in i_old if r["game_id"] not in redo]
    x_old = [r for r in x_old if r["game_id"] not in redo]
    q_new, i_new, x_new = [], [], []
    for gid in todo:
        g = info[gid]
        ep = fetch(boxscoresummaryv3.BoxScoreSummaryV3, game_id=gid)
        ls = from_dataset(ep.line_score)[0]
        if len(ls) != 2 or any(r.get("period%dScore" % k) is None for r in ls for k in range(1, 5)):
            continue  # not final in the summary yet; try again next run
        for r in ls:
            team = TRICODE.get(_int(r.get("teamId")), r.get("teamTricode"))
            q = [_int(r["period%dScore" % k]) for k in range(1, 5)]
            q_new.append({"date": g["date"], "team": team, "opp": g["away"] if team == g["home"] else g["home"],
                          "q1": q[0], "q2": q[1], "q3": q[2], "q4": q[3], "ot": _int(r.get("score")) - sum(q), "game_id": gid})
        for r in from_dataset(ep.other_stats)[0]:
            team = TRICODE.get(_int(r.get("teamId")), r.get("teamTricode"))
            x_new.append({"date": g["date"], "team": team, "opp": g["away"] if team == g["home"] else g["home"],
                          **{v: r.get(k) for k, v in EXTRAS.items()}, "game_id": gid})
        for r in from_dataset(ep.inactive_players)[0]:
            i_new.append({"date": g["date"], "team": TRICODE.get(_int(r.get("teamId")), ""),
                          "player": f'{r.get("firstName") or ""} {r.get("familyName") or ""}'.strip(), "game_id": gid, "person_id": r.get("personId")})
    q_all = sorted(q_old + q_new, key=lambda x: (x["date"], x["game_id"], x["team"]))
    i_all = sorted(i_old + i_new, key=lambda x: (x["date"], x["game_id"], x["team"]))
    save(q_all, "league_quarters.csv", ["date", "team", "opp", "q1", "q2", "q3", "q4", "ot", "game_id"])
    save(i_all, "league_inactive.csv", ["date", "team", "player", "game_id", "person_id"])
    x_all = sorted(x_old + x_new, key=lambda x: (x["date"], x["game_id"], x["team"]))
    save(x_all, "league_extras.csv", ["date", "team", "opp"] + list(EXTRAS.values()) + ["game_id"])
    return len(q_new) // 2


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
    return notes
