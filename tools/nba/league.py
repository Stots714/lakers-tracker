"""League-wide NBA.com data in the tracker's own file formats (all 30 teams).

Writes to data/nba/:
  league_results.csv        every regular-season game: date, away, home, scores once final, overtime periods
  league_box.csv            one row per team per game: shooting, rebounds, turnovers, fouls, minutes
  league_player_games.csv   every player's line in every game
  league_quarters.csv       points per quarter for each team in each game (fetched once per game)
  league_inactive.csv       players NBA.com listed inactive for each game (fetched once per game)

The page merges these over the hand-checked Lakers files; NBA.com wins where both exist.
"""
import csv
import math

import pandas as pd
from nba_api.stats.endpoints import boxscoresummaryv3, leaguegamefinder, playergamelogs, scheduleleaguev2
from nba_api.stats.static import teams as static_teams

TRICODE = {t["id"]: t["abbreviation"] for t in static_teams.get_teams()}
REGULAR = "002"  # NBA game ids: 001 preseason, 002 regular season (NBA Cup group and knockout games included), 004 playoffs, 005 play-in


def _date(v):
    return str(v)[:10]


def _min(v):
    """Minutes as a number: 240, 240.0 or '240:00' all become 240.0."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v)
    if ":" in s:
        m, sec = s.split(":")[:2]
        return int(m) + int(sec) / 60
    return float(s)


def _opp(matchup):
    # "LAL vs. GSW" (home) or "LAL @ GSW" (away)
    return str(matchup).split()[-1]


def schedule(fetch, season):
    ep = fetch(scheduleleaguev2.ScheduleLeagueV2, season=season, league_id="00")
    df = ep.season_games.get_data_frame()
    df = df[df["gameId"].astype(str).str.startswith(REGULAR)].copy()
    date_col = "gameDateEst" if "gameDateEst" in df.columns else "gameDate"
    df["date"] = df[date_col].map(_date)
    return df


def team_games(fetch, season):
    ep = fetch(leaguegamefinder.LeagueGameFinder, player_or_team_abbreviation="T", season_nullable=season,
               season_type_nullable="Regular Season", league_id_nullable="00")
    df = ep.league_game_finder_results.get_data_frame()
    return df[df["GAME_ID"].astype(str).str.startswith(REGULAR)].copy()


def player_games(fetch, season):
    ep = fetch(playergamelogs.PlayerGameLogs, season_nullable=season, season_type_nullable="Regular Season",
               measure_type_player_game_logs_nullable="Base")
    df = ep.player_game_logs.get_data_frame()
    return df[df["GAME_ID"].astype(str).str.startswith(REGULAR)].copy()


def write_box(tg, save):
    rows = []
    for _, r in tg.iterrows():
        mp = _min(r["MIN"])
        rows.append({
            "date": _date(r["GAME_DATE"]), "team": r["TEAM_ABBREVIATION"], "opp": _opp(r["MATCHUP"]),
            "home_away": "A" if "@" in str(r["MATCHUP"]) else "H",
            **{k.lower(): int(r[k]) for k in ["PTS", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB", "DREB", "TOV", "PF"]},
            "mp": int(round(mp)) if mp is not None else "", "game_id": r["GAME_ID"],
        })
    df = pd.DataFrame(rows).sort_values(["date", "game_id", "team"])
    return save(df, "league_box.csv"), df


def write_results(sched, box, save):
    """Schedule rows, with scores for final games (status 3) and overtime periods from team minutes."""
    ot = {}
    for _, r in box.iterrows():
        if r["mp"] != "":
            ot[r["game_id"]] = max(0, int(round((int(r["mp"]) - 240) / 25)))
    rows = []
    for _, g in sched.iterrows():
        final = int(g.get("gameStatus", 0) or 0) == 3
        hs, as_ = g.get("homeTeam_score"), g.get("awayTeam_score")
        rows.append({
            "date": g["date"], "away": g["awayTeam_teamTricode"], "home": g["homeTeam_teamTricode"],
            "away_pts": int(as_) if final else "", "home_pts": int(hs) if final else "",
            "ot": ot.get(g["gameId"], 0) if final else "", "game_id": g["gameId"],
        })
    df = pd.DataFrame(rows).sort_values(["date", "game_id"])
    return save(df, "league_results.csv"), df


def write_player_games(pg, save):
    rows = []
    for _, r in pg.iterrows():
        rows.append({
            "date": _date(r["GAME_DATE"]), "team": r["TEAM_ABBREVIATION"], "opp": _opp(r["MATCHUP"]), "player": r["PLAYER_NAME"],
            "min": round(_min(r["MIN"]) or 0, 2),
            **{k.lower(): int(r[k]) for k in ["PTS", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB", "DREB", "AST", "STL", "BLK", "TOV", "PF"]},
            "game_id": r["GAME_ID"], "player_id": r["PLAYER_ID"],
        })
    df = pd.DataFrame(rows).sort_values(["date", "team", "player"])
    return save(df, "league_player_games.csv"), df


def summaries(fetch, save, out_dir, results, refresh=False):
    """Quarter scores and inactive players, one request per final game not already on file."""
    qpath, ipath = out_dir / "league_quarters.csv", out_dir / "league_inactive.csv"
    q_old = pd.read_csv(qpath, dtype={"game_id": str}) if qpath.exists() and not refresh else pd.DataFrame()
    i_old = pd.read_csv(ipath, dtype={"game_id": str}) if ipath.exists() and not refresh else pd.DataFrame()
    done = set(q_old["game_id"]) if len(q_old) else set()
    final = results[results["home_pts"] != ""]
    info = {r["game_id"]: r for _, r in final.iterrows()}
    todo = [gid for gid in info if gid not in done]
    q_new, i_new = [], []
    for gid in todo:
        g = info[gid]
        ep = fetch(boxscoresummaryv3.BoxScoreSummaryV3, game_id=gid)
        ls = ep.line_score.get_data_frame()
        if len(ls) != 2 or ls[["period1Score", "period2Score", "period3Score", "period4Score"]].isna().any().any():
            continue  # not final in the summary yet; try again next run
        for _, r in ls.iterrows():
            team = TRICODE.get(int(r["teamId"]), r.get("teamTricode"))
            q = [int(r["period%dScore" % k]) for k in range(1, 5)]
            q_new.append({"date": g["date"], "team": team, "opp": g["away"] if team == g["home"] else g["home"],
                          "q1": q[0], "q2": q[1], "q3": q[2], "q4": q[3], "ot": int(r["score"]) - sum(q), "game_id": gid})
        for _, r in ep.inactive_players.get_data_frame().iterrows():
            i_new.append({"date": g["date"], "team": TRICODE.get(int(r["teamId"]), ""), "player": f'{r["firstName"]} {r["familyName"]}'.strip(),
                          "game_id": gid, "person_id": r["personId"]})
    q_all = pd.concat([q_old, pd.DataFrame(q_new)], ignore_index=True) if q_new else q_old
    i_all = pd.concat([i_old, pd.DataFrame(i_new)], ignore_index=True) if i_new else i_old
    if not len(i_all):
        i_all = pd.DataFrame(columns=["date", "team", "player", "game_id", "person_id"])
    if len(q_all):
        save(q_all.sort_values(["date", "game_id", "team"]), "league_quarters.csv")
    save(i_all.sort_values(["date", "game_id", "team"]) if len(i_all) else i_all, "league_inactive.csv")
    return len(q_new) // 2


# ---------------- checks against the hand-checked files ----------------
def checks(root, results, box, pgames, out_dir):
    notes = []
    data = root / "data"

    def read(name):
        p = data / name
        return list(csv.DictReader(p.open(encoding="utf-8"))) if p.exists() else []

    cur = {(r["date"], r["away"], r["home"]): r for r in read("results.csv") if r["home_pts"].strip()}
    nba = {(r["date"], r["away"], r["home"]): r for _, r in results.iterrows() if r["home_pts"] != ""}
    both = [k for k in cur if k in nba]
    bad = [k for k in both if (int(cur[k]["away_pts"]), int(cur[k]["home_pts"])) != (int(nba[k]["away_pts"]), int(nba[k]["home_pts"]))]
    missing = [k for k in cur if k not in nba]
    notes.append(f"Scores: {len(both)} games in both files, {len(bad)} differ{(' ' + str(bad[:5])) if bad else ''}; "
                 f"{len(missing)} hand-checked games not found on NBA.com by date and teams{(' ' + str(missing[:5])) if missing else ''}.")
    K = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "tov"]
    nb = {(r["date"], r["team"]): r for _, r in box.iterrows()}
    lb = read("lakers_box.csv")
    diffs = [(r["date"], r["team"], k, r[k], nb[(r["date"], r["team"])][k]) for r in lb if (r["date"], r["team"]) in nb
             for k in K if int(r[k]) != int(nb[(r["date"], r["team"])][k])]
    notes.append(f"Lakers box scores: {sum((r['date'], r['team']) in nb for r in lb)} team-games compared, {len(diffs)} numbers differ{(' ' + str(diffs[:6])) if diffs else ''}.")
    sums = pgames.groupby(["date", "team"])["pts"].sum()
    off = [(d, t, int(s), int(nb[(d, t)]["pts"])) for (d, t), s in sums.items() if (d, t) in nb and int(s) != int(nb[(d, t)]["pts"])]
    notes.append(f"Player points add up to team points in {len(sums) - len(off)} of {len(sums)} team-games{(': ' + str(off[:5])) if off else ''}.")
    qp = out_dir / "league_quarters.csv"
    if qp.exists():
        q = {(r["date"], r["team"]): r for r in csv.DictReader(qp.open(encoding="utf-8"))}
        lq = read("lakers_quarters.csv")
        qd = [(r["date"], r["team"]) for r in lq if (r["date"], r["team"]) in q and any(int(r[k]) != int(q[(r["date"], r["team"])][k]) for k in ["q1", "q2", "q3", "q4"])]
        notes.append(f"Lakers quarter scores: {sum((r['date'], r['team']) in q for r in lq)} compared, {len(qd)} differ{(' ' + str(qd)) if qd else ''}.")
    return notes
