# Zavia Agent

A small, friendly, ready-to-use agent for the **Zavia blog**
([zaviamagazine.dev](https://zaviamagazine.dev)).

It does six useful jobs, and nothing else:

1. **Health check** — is the site up? (homepage, sitemap, API)
2. **Publish / update articles** — from a Markdown file or a JSON file
3. **SEO check** — sitemap, duplicate slugs, broken links, one article's title + canonical + meta description + Open Graph tags, or several articles at once
4. **Find junk / test articles** — test slugs still live in the sitemap, so you can delete them in the admin panel
5. **SEO audit report** — a repeatable full-site audit (Lesson 14 pattern) that writes a dated Markdown + HTML report and compares it with the previous run: what improved, what got worse, what is new
6. **Web dashboard** — the same jobs in your browser, plus the latest SEO audit report

Built for Uzair (BCS student, Pakistan). Simple English, a little Roman
Urdu where natural, and it never crashes on a bad input — it explains
the problem in plain words and sends you back to the menu.

---

## Setup (one time, ~2 minutes)

```bash
git clone https://github.com/uzaairmalik/zavia-agent.git
cd zavia-agent

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Open .env and paste your Zavia publish token after ZAVIA_PUBLISH_TOKEN=
# (the token is secret — never share it, never commit .env)
```

No token? The health check, SEO check and article listing work **without**
a token. You only need the token to publish or update articles.

---

## Run it

### Command line (interactive menu)

```bash
python -m zavia_agent.cli
```

```
================ ZAVIA AGENT ================
  1  Health check (is the site up?)
  2  List recent articles (from sitemap)
  3  Publish an article (from a Markdown file)
  4  Update an article (from a JSON file)
  5  SEO check (sitemap + one article)
  6  Show config / status
  7  Find junk / test articles (to delete)
  8  Bulk SEO check (several article slugs at once)
  9  SEO audit report (full audit + dated report file)
  0  Exit
=============================================
```

### Command line (one job, no menu)

```bash
python -m zavia_agent.cli --health
python -m zavia_agent.cli --seo
python -m zavia_agent.cli --seo --article my-article-slug
python -m zavia_agent.cli --seo --articles slug-one,slug-two,slug-three
python -m zavia_agent.cli --seo-report
python -m zavia_agent.cli --junk
python -m zavia_agent.cli --list
python -m zavia_agent.cli --publish examples/sample-article.md
python -m zavia_agent.cli --update update.json
python -m zavia_agent.cli --config
```

### Bulk SEO check (several articles at once)

```bash
python -m zavia_agent.cli --seo --articles slug-one,slug-two,slug-three
# [OK  ] slug-one
# [WARN] slug-two — no meta description (Google will write its own snippet)
# [FAIL] slug-three — Article 'slug-three' is a 404 (not found).
```

Each article is checked for: a `<title>`, a canonical link, a meta
description, and Open Graph title/description (for Facebook/WhatsApp
link previews). A missing description or OG tag is a **warning**, not a
failure — the article still loads, it just looks worse in search
results and link previews. A missing title, canonical, or a 404 is a
failure (exit code 1).

### SEO audit reports (scheduled, Lesson 14 pattern)

Hisham's Lesson 14 (Learn Claude AI in 30 Days, Module 7) runs an SEO
audit on a schedule — his re-audits every Thursday 11:00 AM, with a
progress report Mon/Wed/Fri — and tracks what changed since the last
run. This agent does Zavia's version of that:

```bash
python -m zavia_agent.cli --seo-report
```

One run audits the whole live site (sitemap reachability + URL count,
duplicate slugs, a sample of URLs for broken links, **every** article
for title/canonical/meta-description/OG tags, and the junk-article
finder), then writes a dated report — Markdown + HTML — under
`reports/` (e.g. `seo-report-2026-11-17-1206.md`) and stores the
result in `state/last-audit.json` (gitignored) for the next
comparison. The report shows what was checked, every issue found
(with slugs/URLs), and — from the second run on — what improved
(fixed), what got worse (new failures), what is new, and how the
counts moved. The first run has no previous report, and says so
plainly. Exit code is 1 when a FAIL issue is found, 0 otherwise.

Suggested schedule (run the same command each time; every run audits
fresh and compares with the previous one):

```cron
# Thursday 11:00 — the weekly re-audit
0 11 * * 4  cd /path/to/zavia-agent && .venv/bin/python -m zavia_agent.cli --seo-report
# Mon/Wed/Fri 09:00 — progress reports
0 9 * * 1,3,5  cd /path/to/zavia-agent && .venv/bin/python -m zavia_agent.cli --seo-report
```

No cron job is created by this agent — add the lines above yourself
if you want them. Every number in a report comes from a real check:
no invented scores, and no keyword-rank data (Zavia has no Search
Console connection here, so ranks are not claimed).

**Email delivery is a documented next step, not built yet.** Hisham's
version emails the report; this agent only writes the report files
and shows the latest one on the dashboard. Sending it by email needs
Uzair's Gmail approval flow, so it stays manual until that is set up.

### Web dashboard

```bash
python -m zavia_agent.web
# open http://127.0.0.1:5000 in your browser
```

Home page shows site health + article count, a form to publish an
article, a button to run the SEO check, and the latest SEO audit
report with a link to the full HTML report. `/reports` lists all
saved reports (newest first).

### Smoke test (no token needed)

```bash
python test_smoke.py
# [PASS] Homepage returns 200 with article cards
# [PASS] Sitemap reachable and lists URLs

python test_report.py
# [PASS] New vs fixed vs ongoing classification
# ... (8 checks, no network needed)
```

---

## Publishing an article

Write a Markdown file with a small front-matter block at the top
(see `examples/sample-article.md`):

```markdown
---
title: "My Article Title"
slug: "my-article-slug"
excerpt: "One short line about the article."
category: "students"
language: "en"
tags: "my-new-tag, another-new-tag"
---

# My Article Title

...the article body in Markdown...
```

Then publish it:

```bash
python -m zavia_agent.cli --publish my-article.md
```

### The tags bug — read this before you publish

Zavia's publish API returns **500** if your `tags` list contains a tag
that **already exists** on Zavia. So:

- Use **brand-new tags** (ones never used before), **or**
- Use an **empty tags list** (`tags: ""`).

The agent warns you about this before publishing, and if the API does
return 500 it tells you this is the likely cause instead of crashing.

---

## Updating an article

Write a JSON file (the `slug` is required, everything else is optional):

```json
{
  "slug": "my-article-slug",
  "title": "New Title",
  "excerpt": "New short description.",
  "faq": [
    {"question": "What is this?", "answer": "A short answer."}
  ]
}
```

Then run:

```bash
python -m zavia_agent.cli --update update.json
```

---

## Zavia API notes (for the curious)

- Base URL used for API calls: `https://zavia-magazine.vercel.app`
  (the custom domain `zaviamagazine.dev` sometimes returns HTTP 000 from
  some networks, so the API calls use the vercel.app URL).
- Publish: `POST /api/cron/publish`, multipart form-data, field
  `article` = JSON string. Auth: `Authorization: Bearer <token>`.
- Update: `POST /api/cron/update`, multipart form-data, field
  `update` = JSON string. Same Bearer auth.
- Sitemap: `GET /sitemap.xml`. Homepage: `GET /`.
- Vercel deploys take ~30 minutes — a new article may not show up
  instantly. Sabar karo, aa jayega.
- Heroku Postgres (the database) drops for ~5–15 minutes at a time and
  then recovers. Health checks retry 3 times, 20 seconds apart, and say
  "DB possibly down, retry later" instead of failing hard.

## What this agent does NOT do

**Deleting articles is a manual admin-panel step.** There is no public
delete endpoint that works without the admin login cookie, so this agent
has no delete feature on purpose. To delete an article, open
[zaviamagazine.dev/admin](https://zaviamagazine.dev/admin), log in with
the admin password, go to the **Articles** tab, find the article, and
delete it there.

## Files

```
zavia-agent/
├── zavia_agent/
│   ├── __init__.py     # package version
│   ├── client.py       # Zavia API client (publish/update/sitemap/homepage)
│   ├── health.py       # site health checks (with retries for DB flaps)
│   ├── seo.py          # sitemap + article SEO checks
│   ├── report.py       # repeatable SEO audit + dated reports (Lesson 14 pattern)
│   ├── cli.py          # interactive menu + --flags
│   └── web.py          # tiny Flask dashboard (port 5000)
├── examples/
│   └── sample-article.md
├── reports/            # dated SEO audit reports (Markdown + HTML; gitignored, local only)
├── state/              # last-audit.json for report comparisons (gitignored, local only)
├── test_smoke.py       # dry-run health check, no token needed
├── test_report.py      # report comparison logic tests, no network needed
├── IMPROVEMENTS.md     # one line per daily improvement (date + commit)
├── requirements.txt    # requests + flask only
├── .env.example        # copy to .env, add your token
├── .gitignore          # keeps .env, reports/ and state/ out of git
└── README.md
```

## Dependencies

Only two: `requests` and `flask` (see `requirements.txt`).
