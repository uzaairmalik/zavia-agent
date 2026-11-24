# Zavia Agent

A small, friendly, ready-to-use agent for the **Zavia blog**
([zaviamagazine.dev](https://zaviamagazine.dev)).

It does nine useful jobs, and nothing else:

1. **Health check** — is the site up? (homepage, sitemap, API)
2. **Publish / update articles** — from a Markdown file or a JSON file
3. **SEO check** — sitemap, duplicate slugs, broken links, one article's title + canonical + meta description + Open Graph tags, or several articles at once. It also warns when a canonical points at the wrong page (or a different site), when a meta description is too short (< 50 chars) or too long (> 160 chars — Google cuts it off), when two or more articles share the same title or meta description (Google shows the same title/snippet for both), when a page has no `<h1>` heading or more than one, when an article is thin content (under 300 words in the body), and when the social preview image is missing or broken — no `og:image`, no `og:image:alt`, no `twitter:card`, or an `og:image` URL that does not load (Zavia's pictures live on Vercel Blob storage, so a stale link 404s and Facebook/WhatsApp/X previews come out picture-less).
4. **Full SEO audit** — the same title/canonical/description/OG check on *every* article in the sitemap, so nothing gets forgotten
5. **Broken-link scan** — one article's (or the homepage's) internal links, so dead links get fixed before Google finds them
6. **Find junk / test articles** — test slugs still live in the sitemap, so you can delete them in the admin panel
7. **Save an SEO report** — a bulk check or full audit saved as a dated Markdown file, so you have a record to look back at
8. **Web dashboard** — the same jobs in your browser
9. **Sitemap liveness check** — every URL in the sitemap fetched, so dead pages (articles lost from the database that Google still links to) get found in one run

Built for Uzair (BCS student, Pakistan). Simple English, a little Roman
Urdu where natural, and it never crashes on a bad input — it explains
the problem in plain words and sends you back to the menu.

---

## Setup (one time, ~2 minutes)

```bash
git clone <your-new-repo-url> zavia-agent
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
  9  Broken-link scan (one article's internal links)
 10  Full SEO audit (every article in the sitemap)
 11  Full SEO audit + save report to a file
 12  SEO audit report (full audit + dated report, compared with last run)
 13  Sitemap liveness check (every URL in the sitemap — find dead pages)
  0  Exit
=============================================
```

### Command line (one job, no menu)

```bash
python -m zavia_agent.cli --health
python -m zavia_agent.cli --seo
python -m zavia_agent.cli --seo --article my-article-slug
python -m zavia_agent.cli --seo --articles slug-one,slug-two,slug-three
python -m zavia_agent.cli --seo --all   # full audit: every article in the sitemap
python -m zavia_agent.cli --seo --all --report        # same audit, saved as zavia-seo-report-<today>.md
python -m zavia_agent.cli --seo-report   # full audit + dated report under reports/, compared with the last run
python -m zavia_agent.cli --seo --articles slug-one,slug-two --report my-report.md
python -m zavia_agent.cli --links my-article-slug
python -m zavia_agent.cli --links   # scan the homepage's links
python -m zavia_agent.cli --sitemap-check   # every URL in the sitemap, find the dead ones
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
failure (exit code 1). Two more warnings: a canonical that points at a
*different* page than the article (Google then credits the wrong page —
either Zavia host is fine, only the path has to match), and a meta
description shorter than 50 chars or longer than 160 chars (Google
truncates it after ~160). The bulk check and the full audit also list
duplicate titles and duplicate meta descriptions shared by two or more
of the checked articles (a warning, not a failure — fix it by giving
each article its own title and description). Two content warnings as
well: a page with no `<h1>` heading or more than one (a page should
have exactly one — Google uses it to understand what the page is
about), and an article with under 300 words in its body ("thin
content" — Google may treat a very short page as low value). And a
social preview image check: a missing `og:image`, a missing
`og:image:alt`, a missing `twitter:card`, or an `og:image` URL that
does not return 200 (the agent fetches the picture URL — headers only,
the picture itself is never downloaded). All warnings, not failures —
the article loads, but its link preview on Facebook, WhatsApp or X
comes out picture-less or small.

### Full SEO audit (every article in the sitemap)

```bash
python -m zavia_agent.cli --seo --all
# Full SEO audit — checking every article in the sitemap...
#   ... 10/68 checked
#   ... 20/68 checked
# Checked 68 article(s) from the sitemap; 2 with warnings: slug-one, slug-two.
```

Same checks as the bulk check, but the slugs come from the sitemap —
you type nothing. Run it once a week as a sweep: it catches the
articles you forgot to check one by one. Only the articles with a
problem are listed one by one; the clean ones just count towards the
summary. The audit also reports **duplicate titles and duplicate meta
descriptions** — two or more articles sharing the same title or the
same description (compared on the full text, case- and spacing-insensitive,
so a near-identical copy counts too). That is a warning, not a failure:
the articles still load, but Google shows the same title/snippet for
both, so give each article its own. The `--seo-report` audit tracks
these as issues too, so a fixed duplicate shows up as "improved" in
the next report. The audit also tracks social preview image problems
(no `og:image`, a broken `og:image` URL, no `og:image:alt`, no
`twitter:card`) as issues, so a fixed preview shows up as "improved"
the same way. Exit code is 1 when any article fails (missing title,
canonical, or a 404), so you can run it in a script. It takes about a
minute or two (one quick request per article, plus one more per article
to check its preview picture actually loads).

### Save an SEO report to a file

```bash
python -m zavia_agent.cli --seo --all --report
# Report saved to: /home/uzair/zavia-agent/zavia-seo-report-2026-11-19.md

python -m zavia_agent.cli --seo --articles slug-one,slug-two --report my-report.md
```

Works with the full audit (`--seo --all`, menu 10/11) and the bulk
check (`--seo --articles`, menu 8). With no file name after `--report`
it saves as `zavia-seo-report-<today's date>.md` in the folder you run
it from. The file lists the summary, every failed article with its
problem, every article with a warning, and the clean ones as a short
list — so you can keep one report per day and compare them over time.
The report file is yours to keep; it is not part of the repo, and
nothing is uploaded anywhere.

### SEO audit report (full audit, saved + compared with the last run)

```bash
python -m zavia_agent.cli --seo-report
```

One run audits the whole live site (sitemap reachability + URL count,
duplicate slugs, a sample of URLs for broken links, **every** article
for title/canonical/meta-description/OG tags/preview image/H1/word count, and the
junk-article finder), then writes a dated report — Markdown + HTML — under
`reports/` (e.g. `seo-report-2026-11-20-0005.md`) and stores the
result in `state/last-audit.json` (gitignored) for the next
comparison. The report shows what was checked, every issue found
(with slugs/URLs), and — from the second run on — what improved
(fixed), what got worse (new failures), what is new, and how the
counts moved. The first run has no previous report, and says so
plainly. Exit code is 1 when a FAIL issue is found, 0 otherwise.
The dashboard shows the latest report on its home page and lists all
of them at `/reports`. This is Hisham Sarwar's Lesson 14 pattern
(weekly re-audit + progress report).

Suggested schedule (run the same command each time; every run audits
fresh and compares with the previous one):

```cron
# Thursday 11:00 — the weekly re-audit
0 11 * * 4  cd /path/to/zavia-agent && .venv/bin/python -m zavia_agent.cli --seo-report
```

### Broken-link scan (one article's internal links)

```bash
python -m zavia_agent.cli --links my-article-slug
python -m zavia_agent.cli --links        # scan the homepage instead
# Scanned 14 internal link(s) on https://zavia-magazine.vercel.app/article/my-article-slug; all returned 200.
```

It fetches the page, finds its internal links (same-site pages only —
external links, images, scripts, stylesheets and `/api/` routes are
skipped), and reports any that return a non-200 status. Exit code is 1
when a broken link is found, so you can run it in a script. Fix broken
links by updating the article (menu 4 / `--update`) — a dead internal
link tells Google the site is not looked after.

### Sitemap liveness check (every URL in the sitemap)

```bash
python -m zavia_agent.cli --sitemap-check
# Sitemap liveness check — fetching every URL in the sitemap...
#   ... 25/174 checked
# Checked 174 URL(s) in the sitemap; 3 broken.
# Dead URLs (fix or remove these — Google still sends clicks to them):
#   - https://zaviamagazine.dev/article/my-old-article  (HTTP 404)
```

The plain SEO check only fetches the first 10 URLs in the sitemap — a
quick sniff. This one fetches **all** of them (articles, tools, guides,
Quran pages, everything the sitemap lists) and lists every URL that
does not return 200. That is the check to run after a database problem:
an article lost from the database 404s live, but Google still has it
indexed and still sends clicks to it, so the sitemap quietly fills up
with dead links. Fix a dead article by re-publishing it at the same
slug (menu 3 / `--publish`), or remove the page from the sitemap. Exit
code is 1 when any URL is broken, so you can run it in a script. It
takes about a minute (one quick request per URL). One caution: if many
pages fail at once with 500/502/503, the database is probably having
one of its short drops — wait a few minutes and run it again before
treating those pages as dead. The dashboard has the same check as a
button in its SEO card.

### Web dashboard

```bash
python -m zavia_agent.web
# open http://127.0.0.1:5000 in your browser
```

Home page shows site health + article count, a form to publish an
article, and a button to run the SEO check.

### Smoke test (no token needed)

```bash
python test_smoke.py
# [PASS] Homepage returns 200 with article cards
# [PASS] Sitemap reachable and lists URLs
# [PASS] Full SEO audit runs (sample of 3 articles)
# [PASS] SEO report export writes a Markdown file
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
│   ├── report.py       # save an SEO check as a Markdown report file
│   ├── cli.py          # interactive menu + --flags
│   └── web.py          # tiny Flask dashboard (port 5000)
├── examples/
│   └── sample-article.md
├── test_smoke.py       # dry-run health check, no token needed
├── IMPROVEMENTS.md     # one line per daily improvement (date + commit)
├── requirements.txt    # requests + flask only
├── .env.example        # copy to .env, add your token
├── .gitignore          # keeps .env out of git
└── README.md
```

## Dependencies

Only two: `requests` and `flask` (see `requirements.txt`).
