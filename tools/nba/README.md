# NBA.com data for the tracker

This folder downloads the data the tracker cannot get on its own. It runs on your PC because NBA.com's stats site often refuses or times out requests from cloud servers.

- **All 30 teams** (powers the Focus team picker): schedule and scores, team box scores, every player's game lines, quarter scores and inactive lists. Once pushed, the site uses these automatically for every team, and they take precedence over the hand-checked Lakers files.
- **Chosen teams** (Lakers and their next opponent by default): on/off, lineups, shot locations, who guarded whom, hustle stats and play-by-play.

It uses [nba_api](https://github.com/swar/nba_api) 1.12.0 and writes CSV files to `data/nba/`.

## Run it: one paste in PowerShell

```powershell
$r = "$HOME\nba-tracker"
if (-not (Test-Path "$r\.git")) { gh repo clone Stots714/lakers-tracker $r }
powershell -ExecutionPolicy Bypass -File "$r\tools\nba\run_update.ps1" -Push -Daily
```

That one run does everything: gets a copy of the repository (in its own folder, so it never clashes with other copies), installs Python for your user if it is missing, sets up nba_api, downloads the data for all 30 teams, checks it, pushes it to GitHub (the site updates a minute or two later), and sets it to run by itself every morning at 9:40 (when the PC wakes, if it was asleep). Leave off `-Daily` if you only want this one run. It prints what worked and the check results at the end.

To run it again by hand later, paste only the last line. Other switches: `-Check` tests the connection with one small request; `-Teams LAL,GSW` chooses teams for the detail steps.

The first run creates a private Python environment in `tools\nba\.venv` and installs nba_api into it, which takes a minute. The first download makes one request per game played so far (about 75 at the end of October) plus about 40 more, at 1.5 seconds apart: a few minutes. Later runs only fetch games not already on file, so a daily run takes a minute or two.

The detail steps cover the Lakers and their next opponent, read from the schedule files. Use `-Teams LAL,GSW` to choose teams for the detail steps; the league-wide files always cover all 30.

## What it writes (`data/nba/`)

| File | What it holds |
|---|---|
| `league_results.csv` | Every regular-season game: date, teams, final score, overtime periods |
| `league_box.csv` | One row per team per game: shooting, rebounds, turnovers, fouls, minutes |
| `league_player_games.csv` | Every player's line in every game |
| `league_quarters.csv` | Points per quarter for each team in each game |
| `league_inactive.csv` | Players NBA.com listed inactive for each game |
| `games_<TEAM>.csv` | Each game: NBA.com game id, date, matchup, result, team box totals |
| `player_games_<TEAM>.csv`, `player_games_adv_<TEAM>.csv` | Player box score per game, basic and advanced (usage, ratings) |
| `onoff_<TEAM>.csv` | Team results with each player on the court and off it, advanced (`COURT` = on or off) |
| `lineups5_<TEAM>.csv`, `lineups2_<TEAM>.csv` | Five-man lineups and two-man pairs, advanced |
| `shots_<TEAM>.csv`, `shots_against_<TEAM>.csv` | Every shot taken by the team and by its opponents: location, zone, distance, make or miss |
| `hustle_<TEAM>.csv` | Contests, deflections, loose balls, box-outs, charges, screen assists |
| `matchups/<GAME_ID>.csv` | Who guarded whom in each game |
| `pbp/<GAME_ID>.csv` | Play-by-play for each game |
| `manifest.json` | When it ran, which steps worked, row counts, check results |

## Checks it runs

- Every score NBA.com and the tracker's `data/results.csv` both have, compared; hand-checked games NBA.com doesn't list on the same date are named.
- The Lakers box scores and quarter scores against the hand-checked files, number by number.
- Player points adding up to team points in every game.
- Lakers scores from NBA.com against the tracker's own `data/results.csv`. Any mismatch is listed.
- The Oct 21 Dončić and Lendeborg matchup rows, against ABC7's report that Dončić shot 1 of 6 with Lendeborg guarding him. This shows whether the `Off` and `Def` column names point the way we read them.

## If something goes wrong

- Details are in `tools\nba\last_run.log` (Python) and `tools\nba\last_run_ps.log` (PowerShell). Neither is pushed.
- A step that fails is retried 3 times, then skipped; the other steps still run.
- "git pull failed": you have unsaved changes in the folder. Commit or discard them, then run again.
- "Access is denied" when scheduling: open PowerShell as administrator and run the schedule command again.
- Remove the daily task: add `-Remove` to the schedule command.

## Notes

- NBA.com's terms of use govern reuse of its stats. The repository is public, so `-Push` publishes these files; leave it off to keep them on your PC.
- Not affiliated with the NBA or the Los Angeles Lakers.
