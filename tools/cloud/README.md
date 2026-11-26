# GitHub jobs

Jobs that run on GitHub's machines (`.github/workflows/`), not on the PC. Most use standard-library Python only;
the NBA data jobs install the same three pinned packages as the PC (requests, pypdf, nba_api without pandas). Each
job runs on a fresh machine and gets only the permissions it needs. None can be started by a pull request, which
keeps forks away from the secrets.

| Job | When | What it does | Writes |
|---|---|---|---|
| Reach test | by hand | Shows which data sources GitHub's machines can reach | nothing |
| Watchdog | daily 11:23 Pacific (12:23 in summer) | Fails, so GitHub emails you, when yesterday's scores, a recent update or the live watcher's game-night updates did not publish, or today's odds read failed | nothing |
| Odds | daily 09:47 Pacific, and before each group of tip-offs (started by the live watcher) | One consensus line per game from The Odds API | `data/markets/` |
| Trade reports | main feed every 2 hours; all 30 team feeds daily at 07:33 Pacific | Trade reports with a named source from Hoops Rumors' feeds: date, team, players, status, reporter, link | `data/trades/` |
| NBA data (PC-free) | every 3 hours, and by hand | The NBA's public data files (cdn.nba.com), then `compare.py` against the PC's files | `data/nba_cloud/` |
| **Tick** | every 5 minutes, started by cron-job.org | Whatever is due: live scores, injury report, game data, betting lines, trade reports (`tick.py`) | see below |

GitHub's own schedule has started jobs 5 to 10 hours late, so the tick is started from outside, on time. While the
tick runs, the scheduled runs of Odds, Trade reports and NBA data stand down (they check when the tick last
started) and take over again if it stops. The Watchdog stays on GitHub's schedule and fails when the tick stops.

`data/pipeline.json` says who writes what:
- `data_writer`: `cloud` (since Nov 26, 2026) = the tick writes `data/nba/` and the PC's tasks stand down (they
  read this file at each start); `pc` = the PC writes `data/nba/` and the tick writes `data/nba_cloud/` for a
  side-by-side check.
- `odds_pretip`: `tick` (since Nov 25, 2026) = the tick starts the pre-tip odds read; `pc` = the PC's live watcher.

Not carried over from the PC: NBA.com's daily roster comparison (signings and waivers before a player's first game)
and the per-team files the site does not load (shots, lineups, on/off, hustle, play-by-play). stats.nba.com, their
source, does not answer GitHub's machines.

stats.nba.com does not answer GitHub's machines; the tick reads the same numbers from cdn.nba.com.

## Setting up the 5-minute tick (once)

1. A GitHub token that can only start jobs in this repo: fine-grained, repository `lakers-tracker` only,
   permission **Actions: Read and write**, nothing else.
2. A free cron-job.org account holding one job: `POST https://api.github.com/repos/Stots714/lakers-tracker/actions/workflows/tick.yml/dispatches`
   every 5 minutes, headers `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`,
   `X-GitHub-Api-Version: 2026-03-10`, `Content-Type: application/json`, body `{"ref":"main"}`, failure emails on.
   The token is kept at cron-job.org, never in this repo. If it expires, cron-job.org emails a failure (HTTP 401)
   and the watchdog fails; make a new token and paste it into the job's Authorization header.

## Setting up the odds job (once)

1. Get a free key at https://the-odds-api.com (500 credits a month; each read costs 2).
2. Save it as a repo secret. On the PC, where you are signed in to GitHub, run this and paste the key when asked:
   `gh secret set ODDS_API_KEY --repo Stots714/lakers-tracker`
3. First read now (optional): `gh workflow run odds.yml --repo Stots714/lakers-tracker`

Until the key is set, the job notes that and changes nothing, and the watchdog reports the odds as "not set up".

## What is kept from the odds, and why so little

`data/markets/odds_lines.csv` holds, per game and read (`morning` at 09:47 Pacific, `pretip` about 10 minutes
before tip-off): the market's expected home margin (median of US sportsbooks' spreads, to the half point), the
home team's win chance (median of their moneylines, each book's margin removed) and how many books offered both.
No book names, prices or links. The Odds API's terms allow showing odds in an app but not redistributing their
data as files, and this repo is public. `odds_status.json` records the last read, its result and credits left.

A pre-tip read is skipped when fewer than 30 credits are left, so the morning reads last the month.

## Trade reports: what is kept, and how reliable it is

`data/trades/reports.csv` holds one row per team per paragraph that talks about a trade and names its reporter
("according to Shams Charania of ESPN"): date, team, that team's players named, other teams named, status, the
reporter(s), the link to the original source, and the Hoops Rumors post and headline. Never article text.
Paragraphs with no named reporter are left out: unsourced talk, opinion and trade ideas never reach the site.

Status comes from the paragraph's wording (Official, Agreed, Denied, Talks, Interest, Mention), so it is automatic and
can be wrong; the link is always there to check. The site scores each report against NBA.com's rosters
(`data/nba/roster_moves.csv`, written by the PC): a player who changes teams after a report was about him makes it
"happened"; at the trade deadline the rest become "did not happen". Over a season that gives each reporter a record.
The job reads robots.txt and skips anything it disallows, waits 1.5 seconds between requests, and names itself.

## Tests

    python -m unittest discover -s tools/cloud/tests -v
