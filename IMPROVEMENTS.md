# Zavia Agent — Improvements log

One line per daily improvement (date + what changed + commit). Newest first.

- 2026-11-17 — Day 3: repeatable SEO audit report (Hisham Lesson 14 pattern) — full-site audit (`--seo-report` + menu 9) reusing the seo.py checks, dated Markdown + HTML reports under `reports/`, comparison with the previous run (new / fixed / ongoing; worse = new failures) via gitignored `state/last-audit.json`, dashboard reports section + `/reports` page, `test_report.py` (8 checks, no network). First real audit: 180 URLs, 66 articles, 0 issues. Also fixed the dashboard startup message to show the real PORT. Commit 99bbab2.
- 2026-11-16 — Day 2: bulk article SEO check (`--seo --articles slug1,slug2` + menu 8) — checks several slugs at once for title, canonical, meta description and Open Graph tags; missing description/OG is a warning, missing title/canonical or a 404 is a failure (exit 1). Commit 5bc79e7e.
- 2026-11-15 — Day 1: junk/test-article finder (menu 7, `--junk`) — sitemap scan with safe markers + live check of known test slugs; avoids false positives on cricket Test-match articles. Commit 2db1a79d.
