# Zavia Agent

A small, friendly, ready-to-use agent for the **Zavia blog**
([zaviamagazine.dev](https://zaviamagazine.dev)).

It does six useful jobs, and nothing else:

1. **Health check** — is the site up? (homepage, sitemap, API)
2. **Publish / update articles** — from a Markdown file or a JSON file
3. **SEO check** — sitemap, duplicate slugs, broken links, one article's title + canonical + meta description + Open Graph tags, or several articles at once
4. **Broken-link scan** — one article's (or the homepage's) internal links, so dead links get fixed before Google finds them
5. **Find junk / test articles** — test slugs still live in the sitemap, so you can delete them in the admin panel
6. **Web dashboard** — the same jobs in your browser

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
  0  Exit
=============================================
```

### Command line (one job, no menu)

```bash
python -m zavia_agent.cli --health
python -m zavia_agent.cli --seo
python -m zavia_agent.cli --seo --article my-article-slug
python -m zavia_agent.cli --seo --articles slug-one,slug-two,slug-three
python -m zavia_agent.cli --links my-article-slug
python -m zavia_agent.cli --links   # scan the homepage's links
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
