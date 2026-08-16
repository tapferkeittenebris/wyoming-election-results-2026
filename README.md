# Wyoming 2026 Election Night Dashboard

Static Vercel Hobby dashboard with a GitHub Actions election-night updater. The updater checks official Wyoming county-clerk result sources every 10 minutes during the configured August 18–19, 2026 election-night window, aggregates legislative races across counties, and writes `results.json`.

Freedom Caucus classifications are displayed with a rainbow treatment. Searching a race or candidate pins the matching race(s) at the top while keeping all other races visible.
