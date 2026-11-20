"""Smoke test for the Zavia agent — no token needed.

Checks the LIVE site with plain public requests only:
  1. Homepage returns 200 and shows article cards.
  2. Sitemap is reachable and lists URLs.
  3. Full SEO audit runs (sample of 3 articles) and returns counts.

Run it:
    python test_smoke.py

Exits 0 when every check passes, 1 otherwise.
"""

import sys
import tempfile

sys.path.insert(0, ".")  # so `zavia_agent` imports from this folder

from zavia_agent.client import ZaviaClient, ZaviaError
from zavia_agent.report import write_seo_report
from zavia_agent.seo import check_article, check_sitemap_seo


def main():
    client = ZaviaClient()
    results = []

    # 1. Homepage
    try:
        info = client.get_homepage_status()
        ok = info["status"] == 200 and info["has_articles"]
        detail = f"HTTP {info['status']}, {info['size_bytes']} bytes, article cards: {'yes' if info['has_articles'] else 'no'}"
    except ZaviaError as exc:
        ok, detail = False, str(exc)
    results.append(("Homepage returns 200 with article cards", ok, detail))

    # 2. Sitemap
    first_slug = None
    try:
        urls = client.get_sitemap_urls()
        ok = len(urls) > 0
        detail = f"{len(urls)} URLs listed"
        article_urls = [u for u in urls if "/article/" in u]
        if article_urls:
            first_slug = article_urls[0].rstrip("/").split("/")[-1]
    except ZaviaError as exc:
        ok, detail = False, str(exc)
    results.append(("Sitemap reachable and lists URLs", ok, detail))

    # 3. Full SEO audit (sample of 3 — the real audit checks all of them)
    try:
        report = check_sitemap_seo(client, limit=3)
        ok = report["checked"] == 3 and "failed" in report and "warned" in report
        detail = f"{report['checked']} articles checked, {report['failed']} failed, {report['warned']} with warnings"
    except ZaviaError as exc:
        ok, detail = False, str(exc)
    results.append(("Full SEO audit runs (sample of 3 articles)", ok, detail))

    # 4. SEO report export (offline — formats a sample report, no network)
    try:
        sample = {
            "results": [
                {"slug": "good-article", "url": "https://zaviamagazine.dev/article/good-article",
                 "status": 200, "title": "Good Article", "canonical": "https://zaviamagazine.dev/article/good-article",
                 "description": "A fine description.", "warnings": [], "ok": True, "summary": "OK."},
                {"slug": "plain-article", "url": "https://zaviamagazine.dev/article/plain-article",
                 "status": 200, "title": "Plain Article", "canonical": "https://zaviamagazine.dev/article/plain-article",
                 "description": "", "warnings": ["no meta description (Google will write its own snippet)"],
                 "ok": True, "summary": "OK."},
            ],
            "checked": 2, "failed": 0, "warned": 1, "ok": True,
            "summary": "Checked 2 article(s); 1 with warnings: plain-article.",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = write_seo_report(sample, tmp + "/report.md")
            text = open(path, encoding="utf-8").read()
            ok = ("good-article" in text and "plain-article" in text
                  and "no meta description" in text and "## Warnings (1)" in text)
            detail = f"wrote {len(text)} bytes to {path}"
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    results.append(("SEO report export writes a Markdown file", ok, detail))

    # 5. Single-article check returns the sharper warning fields
    # (canonical match + description length, added Day 6).
    try:
        if not first_slug:
            raise ZaviaError("No article slug from the sitemap to check.")
        one = check_article(client, first_slug)
        ok = ("canonical_matches" in one and "canonical_host_ok" in one
              and isinstance(one.get("description_length"), int))
        detail = (f"{one['slug']}: canonical_matches={one['canonical_matches']}, "
                  f"description {one['description_length']} chars, "
                  f"{len(one['warnings'])} warning(s)")
    except ZaviaError as exc:
        ok, detail = False, str(exc)
    results.append(("Single-article check reports canonical + description length", ok, detail))

    print("Zavia agent — smoke test (no token needed)")
    print("-" * 55)
    all_ok = True
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"[{mark}] {name} — {detail}")
    print("-" * 55)
    print("ALL PASS" if all_ok else "SOME CHECKS FAILED — see above.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
