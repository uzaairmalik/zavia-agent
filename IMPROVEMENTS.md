# Zavia Agent — Improvements log

One line per daily improvement (date + what changed + commit). Newest first.

- 2026-11-19 — Day 5: save SEO reports to a file (menu 11, `--seo --all/--articles --report`) — full audit or bulk check written as a dated Markdown file (summary, failed, warnings, clean list); default name `zavia-seo-report-<today>.md`. Commit 4cc6bf22.
- 2026-11-18 — Day 4: full sitemap-wide SEO audit (menu 10, `--seo --all`) — checks every article in the sitemap for title, canonical, meta description and OG tags, no slug list to type; only problem articles listed one by one; exit 1 on failure. Commit e59f1cdd.
- 2026-11-17 — Day 3: broken internal-link scan (menu 9, `--links [slug]`) — scans one article's (or the homepage's) internal page links and reports any non-200; external links, images/scripts and /api/ routes skipped; exit 1 when a link is broken. Commit 4c8171e2.
- 2026-11-16 — Day 2: bulk article SEO check (`--seo --articles slug1,slug2` + menu 8) — checks several slugs at once for title, canonical, meta description and Open Graph tags; missing description/OG is a warning, missing title/canonical or a 404 is a failure (exit 1). Commit 5bc79e7e.
- 2026-11-15 — Day 1: junk/test-article finder (menu 7, `--junk`) — sitemap scan with safe markers + live check of known test slugs; avoids false positives on cricket Test-match articles. Commit 2db1a79d.
