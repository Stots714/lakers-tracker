# NBA Tracker

A fan-made, schedule-adjusted NBA rating tracker for any team, with the Lakers as the default. Pick a **Focus team** at the top and the whole page follows it: title, team color, next game and spotlight (win chance, hot hands, with and without), players, quarters and game-by-game four factors. **Compare with** picks a second team for the tiles, game log, scorecard and next meeting. It also shows the league table (adjusted rating or raw margins), efficiency and defense, offense four factors, absences and how the rating works.

Everything is calculated in the browser from the CSV files in `data/`. There is no server, account or tracking. Not affiliated with the NBA or the Los Angeles Lakers.

## Publish on GitHub Pages

1. Create a **public** repository named `lakers-tracker`. GitHub Pages is free for public repositories.
2. Upload everything in this folder to the repository root, including `.nojekyll` and the `data/` and `icons/` folders.
3. In the repository go to **Settings → Pages**. Under **Build and deployment**, set **Source** to **Deploy from a branch**, choose **main** and **/ (root)**, and save.
4. After a minute or two the site is live at `https://<your-username>.github.io/lakers-tracker/`.

## Add it to a phone's home screen

- **iPhone:** open the link in Safari, tap **Share**, then **Add to Home Screen**.
- **Android:** open the link in Chrome, tap the **⋮** menu, then **Install app** (some versions say **Add to Home screen**, then **Install**). Samsung Internet: tap the menu, then **Add page to**, then **Home screen**.

Either way it opens full screen like an app, in dark mode, with a Refresh button at the top.

## Updating after a game night

`data/nba/` is written by `tools/nba` on a PC (daily if scheduled) and covers all 30 teams; the other files in `data/` are hand-checked and cover the Lakers in detail. NBA.com data takes precedence wherever both exist. The page recalculates on every load; in the home-screen app, tap Refresh (it also reloads by itself when reopened after 5 minutes away).

## Sharing a view

Settings live in the address bar, so a link can open a specific view. For example, `#focus=CHI` opens the Bulls tracker, `#focus=CHI&team=LAL` compares them with the Lakers, and `#view=raw` opens raw margins. The page remembers the last focus team on each device.

## Files

| Path | What it is |
|---|---|
| `index.html` | The page and its calculations |
| `dash-shim.js` | Loads the CSV files, runs the calculations, keeps view settings in the address bar |
| `data/*.csv` | Hand-checked files: results and schedule, team totals, Lakers box scores, players and quarters, absences, storyline notes, teams |
| `data/sources.json` | Where each file comes from and when it was last updated |
| `manifest.webmanifest`, `icons/` | Home-screen name and icons (iPhone, Android, and a cropped-safe Android version) |
| `tools/nba/` | Script that downloads NBA.com player data (on/off, lineups, shots, matchups, hustle) on your PC; see its README |
| `data/nba/` | NBA.com files for all 30 teams (and detail for chosen teams), written by `tools/nba` |
