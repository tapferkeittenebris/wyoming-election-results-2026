# Wyoming 2026 Election Night Dashboard

This repository powers the election-night data feed for the Wyoming 2026 legislative primary dashboard.

## Architecture

- **GitHub Actions** runs the county-clerk scraper every 10 minutes during the election-night window.
- **Official county-clerk result pages** are the source data.
- The updater preserves each county's last good snapshot and aggregates House and Senate races across county lines.
- `results.json` is committed back to this repository and acts as the public data feed.
- The **Vercel Hobby** site is static and only reads `results.json`; no Vercel Cron or always-on computer is required.

## Election-night window

The scheduled workflow runs at 10-minute intervals covering **7:00 PM MDT August 18 through 2:00 AM MDT August 19, 2026**. The Python updater also enforces the date/time window so the county sites are not scraped outside election night.

## Display classifications

Candidates identified on Better Wyoming Action + Research's Freedom Caucus page are flagged in the feed with `is_fc: true`. The dashboard displays those candidates in **deep red**; all other candidates are black. The FC-vs-Other meter counts races with reported votes and a sole current leader; tied races are excluded.

House District 29 appears in normal numerical order with the rest of the House races.

## Data status

All election-night totals are unofficial until canvassed/certified by the appropriate election authorities.
