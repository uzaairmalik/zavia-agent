"""Tiny Flask dashboard for Zavia.

Run it:
    python -m zavia_agent.web
Then open http://127.0.0.1:5000 in your browser.

Home page shows: site health, article count, a form to publish an
article, a button to run the SEO check, and the latest SEO audit
report (see report.py). Results appear on the page.

No build step, no template files — the HTML lives in this one file.
Publishing needs ZAVIA_PUBLISH_TOKEN in your .env file.
"""

import os

from flask import Flask, abort, render_template_string, request, send_from_directory

from .client import ZaviaClient, ZaviaError
from .health import check_site
from .report import REPORTS_DIR, latest_report_info, list_reports
from .seo import check_sitemap

app = Flask(__name__)

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zavia Agent — Dashboard</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 760px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; background: #fafaf7; }
  h1 { font-size: 1.6rem; } h2 { font-size: 1.2rem; margin-top: 2rem; }
  .card { background: #fff; border: 1px solid #ddd; border-radius: 12px; padding: 1rem 1.2rem; margin: 1rem 0; }
  .ok { color: #0a7d2c; font-weight: bold; } .bad { color: #b00020; font-weight: bold; }
  label { display: block; margin: .6rem 0 .2rem; font-weight: 600; }
  input, select, textarea { width: 100%; padding: .5rem; border: 1px solid #bbb; border-radius: 8px; font: inherit; }
  textarea { min-height: 160px; }
  button { margin-top: 1rem; padding: .6rem 1.2rem; border: 0; border-radius: 999px; background: #1a1a1a; color: #fff; font-weight: 700; cursor: pointer; }
  .note { color: #555; font-size: .9rem; } .msg { white-space: pre-wrap; }
  .row { display: flex; gap: 1rem; flex-wrap: wrap; } .row > div { flex: 1; min-width: 200px; }
</style>
</head>
<body>
<h1>Zavia Agent</h1>
<p class="note">Blog: <a href="https://zaviamagazine.dev">zaviamagazine.dev</a> &middot; Deleting articles is NOT here — that is a manual step in the admin panel.</p>

<div class="card">
<h2>Site health</h2>
{% if health %}
  <p>Homepage: <span class="{{ 'ok' if health.homepage.ok else 'bad' }}">{{ 'OK' if health.homepage.ok else 'FAIL' }}</span> — {{ health.homepage.detail }}</p>
  <p>Sitemap: <span class="{{ 'ok' if health.sitemap.ok else 'bad' }}">{{ 'OK' if health.sitemap.ok else 'FAIL' }}</span> — {{ health.sitemap.detail }}</p>
  <p>API: <span class="{{ 'ok' if health.api.ok else 'bad' }}">{{ 'OK' if health.api.ok else 'FAIL' }}</span> — {{ health.api.detail }}</p>
  <p><strong>{{ health.verdict }}</strong></p>
{% else %}
  <p>Health check did not run. {{ health_error or '' }}</p>
{% endif %}
<p>Articles in sitemap: <strong>{{ article_count if article_count is not none else '?' }}</strong></p>
<p class="note">Token: {{ 'set — ready to publish' if token_set else 'NOT SET — add ZAVIA_PUBLISH_TOKEN to your .env file' }}</p>
</div>

<div class="card">
<h2>Publish an article</h2>
<form method="post" action="/publish">
  <label>Title <input name="title" required></label>
  <label>Slug <input name="slug" required placeholder="my-article-slug"></label>
  <div class="row">
    <div><label>Category <input name="category" value="general"></label></div>
    <div><label>Language
      <select name="language"><option value="en">English</option><option value="ur">Urdu</option></select>
    </label></div>
  </div>
  <label>Excerpt <input name="excerpt"></label>
  <label>Tags (comma-separated, must be BRAND-NEW — old tags make publish fail)
    <input name="tags" placeholder="leave empty if unsure"></label>
  <label>Content (Markdown) <textarea name="content" required></textarea></label>
  <button type="submit">Publish article</button>
</form>
{% if publish_msg %}<p class="msg">{{ publish_msg }}</p>{% endif %}
</div>

<div class="card">
<h2>SEO check</h2>
<form method="post" action="/seo"><button type="submit">Run SEO check</button></form>
{% if seo_msg %}<p class="msg">{{ seo_msg }}</p>{% endif %}
</div>

<div class="card">
<h2>SEO audit reports</h2>
{% if reports %}
  <p>Latest report: <strong>{{ reports.date }}</strong>
  {% if reports.counts %}
    — {{ reports.counts.total_issues }} issues ({{ reports.counts.fail_issues }} FAIL, {{ reports.counts.warn_issues }} warnings), {{ reports.counts.articles_checked }} articles checked.
    <span class="{{ 'ok' if reports.ok else 'bad' }}">{{ 'No FAIL issues' if reports.ok else 'FAIL issues need fixing' }}</span>
  {% endif %}
  </p>
  {% if reports.html %}<p><a href="/reports/{{ reports.html }}">View the latest report (HTML)</a></p>{% endif %}
{% else %}
  <p>No SEO audit report yet. Run one from the command line:</p>
  <p><code>python -m zavia_agent.cli --seo-report</code></p>
{% endif %}
<p><a href="/reports">All reports</a></p>
</div>
</body>
</html>
"""

REPORTS_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zavia Agent — SEO audit reports</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 760px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; background: #fafaf7; }
  h1 { font-size: 1.6rem; }
  .card { background: #fff; border: 1px solid #ddd; border-radius: 12px; padding: 1rem 1.2rem; margin: 1rem 0; }
  .note { color: #555; font-size: .9rem; }
</style>
</head>
<body>
<h1>SEO audit reports</h1>
<p><a href="/">&larr; Back to the dashboard</a></p>
<div class="card">
{% if reports %}
  <ul>
  {% for report in reports %}
    <li><strong>{{ report.date }}</strong> —
      {% if report.html %}<a href="/reports/{{ report.html }}">HTML</a>{% endif %}{% if report.md and report.html %} &middot; {% endif %}{% if report.md %}<a href="/reports/{{ report.md }}">Markdown</a>{% endif %}
    </li>
  {% endfor %}
  </ul>
{% else %}
  <p>No reports yet. Run one from the command line:</p>
  <p><code>python -m zavia_agent.cli --seo-report</code></p>
{% endif %}
<p class="note">Reports are written by the scheduled-style audit (Lesson 14 pattern). Each run compares with the previous one — new, fixed and ongoing issues. Run it again any time to get a fresh report.</p>
</div>
</body>
</html>
"""


def _client():
    return ZaviaClient()


@app.route("/", methods=["GET"])
def home():
    client = _client()
    health = None
    health_error = ""
    article_count = None
    try:
        # Short retries here so the page stays responsive; the CLI uses longer ones.
        health = check_site(client, retries=2, delay=5)
        article_count = health["sitemap"].get("count")
    except ZaviaError as exc:
        health_error = str(exc)
    except Exception as exc:  # never crash the page
        health_error = f"{type(exc).__name__}: {exc}"
    return render_template_string(
        PAGE, health=health, health_error=health_error,
        article_count=article_count, token_set=bool(client.token),
        publish_msg="", seo_msg="", reports=latest_report_info(),
    )


@app.route("/reports", methods=["GET"])
def reports():
    """List all saved SEO audit reports, newest first."""
    return render_template_string(REPORTS_PAGE, reports=list_reports())


@app.route("/reports/<name>", methods=["GET"])
def report_file(name):
    """Serve one saved report file (HTML or Markdown).

    Only plain filenames inside reports/ are allowed — no subfolders,
    no other file types, no path tricks.
    """
    if "/" in name or "\\" in name or ".." in name:
        abort(404)
    if not (name.endswith(".html") or name.endswith(".md")):
        abort(404)
    path = os.path.join(REPORTS_DIR, name)
    if not os.path.isfile(path):
        abort(404)
    return send_from_directory(REPORTS_DIR, name)


@app.route("/publish", methods=["POST"])
def publish():
    client = _client()
    form = request.form
    tags = [t.strip() for t in (form.get("tags") or "").split(",") if t.strip()]
    article = {
        "title": (form.get("title") or "").strip(),
        "slug": (form.get("slug") or "").strip(),
        "excerpt": (form.get("excerpt") or "").strip(),
        "content": (form.get("content") or "").strip(),
        "category": (form.get("category") or "general").strip(),
        "language": form.get("language") or "en",
        "tags": tags,
    }
    try:
        result = client.publish_article(article)
        msg = "Published! Zavia said:\n" + str(result)[:500]
    except ZaviaError as exc:
        msg = "Could not publish:\n" + str(exc)
    except Exception as exc:
        msg = f"Unexpected error ({type(exc).__name__}): {exc}"
    # Re-render the home page with the message (fresh health, quick).
    try:
        health = check_site(client, retries=1, delay=0)
        article_count = health["sitemap"].get("count")
    except Exception:
        health, article_count = None, None
    return render_template_string(
        PAGE, health=health, health_error="", article_count=article_count,
        token_set=bool(client.token), publish_msg=msg, seo_msg="",
        reports=latest_report_info(),
    )


@app.route("/seo", methods=["POST"])
def seo():
    client = _client()
    try:
        report = check_sitemap(client)
        lines = [report["summary"]]
        if report["duplicate_slugs"]:
            lines.append("Duplicate slugs: " + ", ".join(report["duplicate_slugs"]))
        if report["broken"]:
            lines.append("Broken URLs:")
            lines.extend("  - " + url for url in report["broken"])
        msg = "\n".join(lines)
    except ZaviaError as exc:
        msg = "SEO check failed:\n" + str(exc)
    except Exception as exc:
        msg = f"Unexpected error ({type(exc).__name__}): {exc}"
    try:
        health = check_site(client, retries=1, delay=0)
        article_count = health["sitemap"].get("count")
    except Exception:
        health, article_count = None, None
    return render_template_string(
        PAGE, health=health, health_error="", article_count=article_count,
        token_set=bool(client.token), publish_msg="", seo_msg=msg,
        reports=latest_report_info(),
    )


def main():
    port = int(os.environ.get("PORT", "5000"))
    print(f"Zavia Agent dashboard — open http://127.0.0.1:{port} in your browser.")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
