"""Tiny Flask dashboard for Zavia.

Run it:
    python -m zavia_agent.web
Then open http://127.0.0.1:5000 in your browser.

Home page shows: site health, article count, a form to publish an
article, and a button to run the SEO check. Results appear on the page.

No build step, no template files — the HTML lives in this one file.
Publishing needs ZAVIA_PUBLISH_TOKEN in your .env file.
"""

import os

from flask import Flask, render_template_string, request

from .client import ZaviaClient, ZaviaError
from .health import check_site
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
        publish_msg="", seo_msg="",
    )


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
    )


def main():
    port = int(os.environ.get("PORT", "5000"))
    print("Zavia Agent dashboard — open http://127.0.0.1:5000 in your browser.")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
