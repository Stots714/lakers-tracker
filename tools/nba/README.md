# NBA.com data for the Lakers tracker

This folder downloads the player-level data the tracker cannot get on its own: on/off, lineups, shot locations, who guarded whom, hustle stats and play-by-play. It runs on your PC because NBA.com's stats site often refuses or times out requests from cloud servers.

It uses [nba_api](https://github.com/swar/nba_api) 1.12.0 and writes CSV files to `data/nba/`.

## One-time setup (PowerShell)

```powershell
# 1. Python. Skip this if "py --version" already prints a version. Open a new PowerShell window afterwards.
winget install -e --id Python.Python.3.12

# 2. Let git push with your GitHub sign-in (needed for scheduled runs).
gh auth setup-git

# 3. Get the repository. If you already have it in this folder, run "git -C $HOME\lakers-tracker pull" instead.
gh repo clone Stots714/lakers-tracker $HOME\lakers-tracker
```

## Run it

```powershell
# Test the connection to NBA.com (one small request)
powershell -ExecutionPolicy Bypass -File $HOME\lakers-tracker\tools\nba\run_update.ps1 -Check

# Download everything and push it to GitHub
powershell -ExecutionPolicy Bypass -File $HOME\lakers-tracker\tools\nba\run_update.ps1 -Push

# Every day at 9:40 AM (runs when the PC wakes if it was asleep)
powershell -ExecutionPolicy Bypass -File $HOME\lakers-tracker\tools\nba\schedule_task.ps1
```

The first run creates a private Python environment in `tools\nba\.venv` and installs nba_api into it, which takes a minute. A full first run makes about 40 requests at 1.5 seconds apart. Later runs only fetch matchups and play-by-play for games not already downloaded.

By default it pulls the Lakers and their next opponent, read from `data/lakers_schedule.csv`. Use `-Teams LAL,GSW` to choose teams.

## What it writes (`data/nba/`)

| File | What it holds |
|---|---|
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
