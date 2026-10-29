# Lakers Tracker

A fan-made, schedule-adjusted NBA rating tracker with a Lakers focus. It shows the league table (adjusted rating or raw margins), each team's game log and next five projections, offense four factors, Lakers game-by-game four factors and three-point luck, known absences, and how the rating works.

Everything is calculated in the browser from the CSV files in `data/`. There is no server, account or tracking. Not affiliated with the NBA or the Los Angeles Lakers.

## Publish on GitHub Pages

1. Create a **public** repository named `lakers-tracker`. GitHub Pages is free for public repositories.
2. Upload everything in this folder to the repository root, including `.nojekyll` and the `data/` and `icons/` folders.
3. In the repository go to **Settings → Pages**. Under **Build and deployment**, set **Source** to **Deploy from a branch**, choose **main** and **/ (root)**, and save.
4. After a minute or two the site is live at `https://<your-username>.github.io/lakers-tracker/`.

## Add it to an iPhone home screen

Open the link in Safari, tap **Share**, then **Add to Home Screen**. It opens full screen like an app.

## Updating after a game night

The data files in `data/` are replaced after each game night. The page recalculates on every load; in the home-screen app, tap Refresh (it also reloads by itself when reopened after 5 minutes away).

## Sharing a view

Settings live in the address bar, so a link can open a specific view. For example, `#view=raw` opens raw margins and `#team=HOU` selects the Rockets.

## Files

| Path | What it is |
|---|---|
| `index.html` | The page and its calculations |
| `dash-shim.js` | Loads the CSV files, runs the calculations, keeps view settings in the address bar |
| `data/*.csv` | Results and schedule, team box totals, Lakers box scores, absences, Lakers schedule, teams |
| `data/sources.json` | Where each file comes from and when it was last updated |
| `manifest.webmanifest`, `icons/` | Home-screen name and icon |
