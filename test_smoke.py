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

sys.path.insert(0, ".")  # so `zavia_agent` imports from this folder

from zavia_agent.client import ZaviaClient, ZaviaError
from zavia_agent.seo import check_sitemap_seo


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
    try:
        urls = client.get_sitemap_urls()
        ok = len(urls) > 0
        detail = f"{len(urls)} URLs listed"
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
