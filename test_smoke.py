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
from zavia_agent.seo import (_article_word_count, _h1_count, check_article,
                             check_sitemap_seo, find_duplicate_meta)


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
        ok = (report["checked"] == 3 and "failed" in report and "warned" in report
              and "duplicate_titles" in report and "duplicate_descriptions" in report)
        detail = (f"{report['checked']} articles checked, {report['failed']} failed, "
                  f"{report['warned']} with warnings, "
                  f"{len(report['duplicate_titles'])} duplicate title(s), "
                  f"{len(report['duplicate_descriptions'])} duplicate description(s)")
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
    # (canonical match + description length, added Day 6) and the
    # content fields (H1 count + word count, added Day 8).
    try:
        if not first_slug:
            raise ZaviaError("No article slug from the sitemap to check.")
        one = check_article(client, first_slug)
        ok = ("canonical_matches" in one and "canonical_host_ok" in one
              and isinstance(one.get("description_length"), int)
              and one.get("h1_count") == 1
              and isinstance(one.get("word_count"), int) and one["word_count"] > 300)
        detail = (f"{one['slug']}: canonical_matches={one['canonical_matches']}, "
                  f"description {one['description_length']} chars, "
                  f"h1_count={one['h1_count']}, word_count={one['word_count']}, "
                  f"{len(one['warnings'])} warning(s)")
    except ZaviaError as exc:
        ok, detail = False, str(exc)
    results.append(("Single-article check reports canonical + description length + H1/word count", ok, detail))

    # 6. Every module imports (the Day 6 refactor broke cli.py + web.py
    # silently by gutting report.py, and had also deleted this guard —
    # never again: the smoke test must import every module the agent ships).
    try:
        import zavia_agent.cli  # noqa: F401 — the import itself is the check
        import zavia_agent.web  # noqa: F401 — the import itself is the check
        ok, detail = True, "cli + web modules import cleanly"
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    results.append(("CLI + dashboard modules import", ok, detail))

    # 7. Duplicate title / description detection (offline — synthetic
    # results, no network). Same title in different case/spacing counts
    # as one duplicate; a missing description is not a duplicate; a
    # unique article is not flagged.
    try:
        fake = [
            {"slug": "a-one", "title": "How to Learn Python", "description": "Learn Python fast.",
             "description_full": "Learn Python fast with this simple guide for students."},
            {"slug": "a-two", "title": "how  to learn  python", "description": "Learn Python fast.",
             "description_full": "learn python fast with this simple guide for students."},
            {"slug": "a-three", "title": "Something Else", "description": "",
             "description_full": ""},
            {"slug": "a-four", "title": "Something Else Entirely", "description": "A different description.",
             "description_full": "A different description, unique to this article."},
        ]
        dups = find_duplicate_meta(fake)
        ok = (len(dups["duplicate_titles"]) == 1
              and dups["duplicate_titles"][0]["slugs"] == ["a-one", "a-two"]
              and len(dups["duplicate_descriptions"]) == 1
              and dups["duplicate_descriptions"][0]["slugs"] == ["a-one", "a-two"])
        detail = (f"{len(dups['duplicate_titles'])} duplicate title group(s), "
                  f"{len(dups['duplicate_descriptions'])} duplicate description group(s)")
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    results.append(("Duplicate titles/descriptions detected", ok, detail))

    # 8. H1 + word-count checks (offline — synthetic pages, no network).
    # A short article with two H1s must warn about both; a long article
    # with one H1 must warn about neither; a page with no <article>
    # tag gives no word count and no thin-content warning.
    try:
        class _FakeClient:
            def __init__(self, html_text):
                self._html = html_text

            def get_article(self, slug):
                return {"slug": slug, "url": "https://zaviamagazine.dev/article/" + slug,
                        "status": 200, "title": "Fake Article", "html": self._html}

        long_body = "word " * 350
        long_page = ("<html><head><title>Fake Article</title>"
                     '<link rel="canonical" href="https://zaviamagazine.dev/article/fake">'
                     '<meta name="description" content="A fine description that is long enough to pass the fifty character minimum.">'
                     '<meta property="og:title" content="Fake Article">'
                     '<meta property="og:description" content="A fine description."></head>'
                     "<body><h1>Fake Article</h1><article><p>" + long_body + "</p></article></body></html>")
        short_body = "word " * 40
        short_page = ("<html><head><title>Fake Article</title>"
                      '<link rel="canonical" href="https://zaviamagazine.dev/article/fake">'
                      '<meta name="description" content="A fine description that is long enough to pass the fifty character minimum.">'
                      '<meta property="og:title" content="Fake Article">'
                      '<meta property="og:description" content="A fine description."></head>'
                      "<body><h1>Fake Article</h1><h1>Second Heading</h1><article><p>"
                      + short_body + "</p></article></body></html>")
        no_article_page = "<html><head><title>Fake</title></head><body><h1>Fake</h1><p>no article tag here</p></body></html>"
        good = check_article(_FakeClient(long_page), "fake")
        thin = check_article(_FakeClient(short_page), "fake")
        no_tag = check_article(_FakeClient(no_article_page), "fake")
        ok = (good["h1_count"] == 1 and good["word_count"] == 350 and not good["warnings"]
              and thin["h1_count"] == 2 and thin["word_count"] == 40
              and any("thin content" in w for w in thin["warnings"])
              and any("2 <h1>" in w for w in thin["warnings"])
              and no_tag["word_count"] is None
              and not any("thin content" in w for w in no_tag["warnings"])
              and _article_word_count("<article><p>one two three</p><script>var x = 1 2 3;</script></article>") == 3
              and _h1_count("<h1>A</h1><H1>B</H1>") == 2)
        detail = (f"good: h1={good['h1_count']}, words={good['word_count']}, warnings={len(good['warnings'])}; "
                  f"thin: h1={thin['h1_count']}, words={thin['word_count']}, warnings={len(thin['warnings'])}; "
                  f"no <article>: words={no_tag['word_count']}")
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    results.append(("H1 + thin-content checks (offline synthetic pages)", ok, detail))

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
