# Springfield Food Inspections

A public website that makes Sangamon County food inspection reports easy to find and read, with a short food safety course and classroom kit built on real, current local reports.

- **Look up a place:** search every inspection on file by name, street or ZIP.
- **Report decoder:** each finding in plain words, why it matters for getting sick, and whether it is a risk factor or upkeep item.
- **Learn:** the five CDC risk factors with local counts, five course modules, a weekly practice report, and how Illinois food handler training works.
- **Classroom kit:** lesson plan, printable worksheet, and slides that update themselves.

Everything runs in the cloud. Nothing needs to be installed on a work computer.

## How it works

```
GitHub Actions (nightly, 2 AM Central)          GitHub Pages (static site)
  helper/scrape.py  ──► data/*.json, *.csv  ──►  index.html, place.html, learn.html ...
  reads the county's public portal               reads the JSON files in the browser
```

- `helper/scrape.py` searches the county portal one month (or the last 14 days) at a time, posts each row's **Details** button to learn its inspection id, then downloads the violation page once. Rows already on file are matched without extra requests, so nightly runs take a few minutes.
- `helper/build_site.py` writes small files for the site: `data/site/recent.json` (first paint), `data/site/index.json` (full history search), one file per place in `data/site/est/`, weekly practice report, examples and 12-month stats.
- `data/inspections.json`, `data/violations.json` and the CSV copies hold everything. `data/meta.json` logs each run. `data/state.json` tracks backfill progress.
- The site is plain HTML, CSS and JavaScript. No build step, no server, no login.

## Setup (once, all in the browser)

1. Create a **public** GitHub repository and upload the contents of this folder (or let Claude push it).
2. In `helper/config.json`, set `user_agent` to include a real contact email.
3. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
4. **Settings → Actions → General → Workflow permissions: Read and write.**
5. **Actions → Update inspection data → Run workflow.** The first runs backfill history (about 24,500 inspections since 2010). Each run works for up to about 5.5 hours, saves progress, and the next run picks up where it stopped. Expect 5 to 7 runs; you can start them by hand back to back instead of waiting for nightly runs.
6. The site appears at `https://<owner>.github.io/<repo>/`.

## Running an update

- **Automatic:** every night at 08:17 UTC (about 2 AM Central in winter, 3 AM in summer). GitHub can delay scheduled runs at busy times.
- **By hand:** Actions → Update inspection data → Run workflow. Choose `recent-only` to skip the backfill.
- **Refresh now** on the site just re-reads the JSON files. It never contacts the county and costs nothing.
- **Run update now** appears when data is more than 24 hours old. It opens the workflow page; someone with write access presses Run workflow, and the page polls `meta.json` until the new data arrives.
- GitHub pauses scheduled workflows in repositories with no activity for 60 days. The nightly data commits count as activity, but if the schedule ever stops, re-enable it on the Actions tab.

If the county changes its portal, the helper stops, records `stopped: portal layout changed` in `meta.json`, and the workflow shows a red X. The site keeps showing the last good data.

## Adding a county

Other counties on the same Custom Data Processing portal have their own code in the portal link (`ESTTST_CTY=...`).

1. Open that county's inspection page and copy the `ESTTST_CTY` value.
2. Add an entry under `counties` in `helper/config.json` and set `"county"` to it (or run with `COUNTY=<key>` in the environment).
3. Use a separate repository or a separate `data/` folder per county. Update the county name, source link and complaint link in the HTML pages.

## Editing the plain-language text

- The form items (1 to 56 from the FDA model form, plus Illinois items 57 food handler training and 58 allergen awareness training): `assets/items.js`. These lines are written by hand on purpose; edit them there. Check them against `data/site/items_seen.json`, which lists every item number and title exactly as the county prints them.
- The five CDC risk factor groupings are in the same file. Verify item ranges against the official Illinois inspection form before publishing.
- Course text: `learn.html`. Lesson plan and worksheet: `kit.html`. Slides: `slides.html`.

## Settings in `helper/config.json`

| Key | Meaning |
|---|---|
| `delay_seconds` | Random pause between requests (default 1 to 2 seconds) |
| `recent_days` | How far back each nightly run re-checks (default 14) |
| `page_size` | Rows per portal page (500 works) |
| `time_budget_minutes` | Stop and save before GitHub's 6 hour job limit |
| `store_inspector_names` | `false` keeps inspector names out of the data files and site |

## Older reports (before 2019)

Before 2019 Sangamon County used an older inspection form with different item numbers. On those reports the portal's list page shows 0 risk factor and 0 upkeep items even when the report has findings, so the helper always opens their detail pages, and the site shows them as written (label "Older report form") instead of decoding them. The cutoff is `form_change_date` in `helper/config.json`.

## Labels

- **Needs attention:** 3 or more risk factor findings (items 1 to 29), or any repeat violation.
- **Minor issues:** any other finding.
- **No issues written up:** no findings recorded.

These are this site's labels, not county ratings.

## Acceptance checks

Run `python helper/check.py` to compare 10 random stored inspections and 3 detail pages against the live portal, and the stored total against the portal's count for a date range. Also: run the helper twice in a row (the second run should add nothing), confirm Refresh now changes the "Updated" time after a run, and have three people outside public health decode one report using only the site.

## Data source

Sangamon County Department of Public Health public inspection portal, run by Custom Data Processing: https://public.cdpehs.com/ILEnvPbl/ESTABLISHMENT/ShowESTABLISHMENTTablePage.aspx?ESTTST_CTY=sJRNEgWYzzs%3d. The county's records are the official source. The helper only reads public pages and never touches internal IDPH systems.
