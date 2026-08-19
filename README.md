# Wyoming 2026 Election Night Dashboard

This repository powers the election-night data feed for the Wyoming 2026 legislative primary dashboard.

## Architecture

- **GitHub Actions** runs the county-clerk scraper every 10 minutes during the election-night window.
- **Official county-clerk result pages** are the primary source data, with Wyoming Secretary of State county summaries available as a fallback.
- The updater preserves each county's last good snapshot and aggregates House and Senate races across county lines.
- `results.json` is committed back to this repository as the canonical public data feed.
- Browsers **do not poll GitHub Raw directly**. Requests to `/results.json` are rewritten by Vercel to the cached `/api/results` function.
- The Vercel results proxy fetches the canonical GitHub feed, validates the JSON, and serves it through Vercel's CDN with a 60-second edge TTL and a five-minute stale-while-revalidate window.
- Dashboard clients refresh on a randomized **50–70 second interval**, stop routine polling while the tab is hidden, refresh when the tab becomes visible again, and preserve the last good data if a refresh fails.
- Election-data commits do **not** need to trigger a new Vercel deployment. Vercel deployments are only required when the site or proxy code changes.

### Traffic-safety rule

Do not restore per-browser cache-busting such as `?t=${Date.now()}` on the dashboard's results request, and do not point the browser directly at `raw.githubusercontent.com`. Those patterns bypass shared caching and multiply upstream traffic by the number of viewers.

## Election-night window

The scheduled workflow runs at 10-minute intervals covering **3:00 PM MDT August 18 through 2:00 AM MDT August 19, 2026**. This early-monitoring window allows county result pages to be detected before polls close while preserving the last good snapshot throughout election night. The workflow syncs the latest `main` branch before scraping so queued runs do not publish from stale checkouts.

## Display classifications

Candidates identified on Better Wyoming Action + Research's Freedom Caucus page are flagged in the feed with `is_fc: true`.

For the chamber outlook and district maps:

- **Freedom Caucus:** cyan-teal
- **Non-Freedom Caucus:** Republican red
- **Pending:** gray

The FC-vs-Non-FC meter uses uncontested seats, applicable incumbents, and Republican primary outcomes under the dashboard's seat-outlook rules.

House District 29 appears in normal numerical order with the rest of the House races.

## Data status

All election-night totals are unofficial until canvassed/certified by the appropriate election authorities.
