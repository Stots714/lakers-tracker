# GitHub jobs

Jobs that run on GitHub's machines (`.github/workflows/`), not on the PC. Standard-library Python only: nothing is
installed, so there is no package to trust or keep up to date. Each job runs on a fresh machine and gets only the
permissions it needs. None can be started by a pull request, which keeps forks away from the secrets.

| Job | When | What it does | Writes |
|---|---|---|---|
| Reach test | by hand | Shows which data sources GitHub's machines can reach | nothing |
| Watchdog | daily 11:23 Pacific (12:23 in summer) | Fails, so GitHub emails you, when yesterday's scores, a recent update or the live watcher's game-night updates did not publish, or today's odds read failed | nothing |
| Odds | daily 09:47 Pacific, and before each group of tip-offs (started by the live watcher) | One consensus line per game from The Odds API | `data/markets/` |
| Trade reports | main feed every 2 hours; all 30 team feeds daily at 07:33 Pacific | Trade reports with a named source from Hoops Rumors' feeds: date, team, players, status, reporter, link | `data/trades/` |

The NBA.com jobs stay on the PC: stats.nba.com does not answer most cloud addresses.

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
