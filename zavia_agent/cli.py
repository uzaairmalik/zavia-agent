"""Interactive command-line agent for Zavia.

Run it and pick a number:
    python -m zavia_agent.cli

Or run one job directly (no menu):
    python -m zavia_agent.cli --health
    python -m zavia_agent.cli --seo
    python -m zavia_agent.cli --seo --articles slug-one,slug-two
    python -m zavia_agent.cli --seo --all
    python -m zavia_agent.cli --seo --all --report        (save the audit as a Markdown file)
    python -m zavia_agent.cli --seo --articles a,b --report my-report.md
    python -m zavia_agent.cli --seo-report   (full audit + dated report, compared with the last run)
    python -m zavia_agent.cli --links my-article-slug
    python -m zavia_agent.cli --links   (scan the homepage's links)
    python -m zavia_agent.cli --sitemap-check
    python -m zavia_agent.cli --crawl-check
    python -m zavia_agent.cli --pages-check
    python -m zavia_agent.cli --list
    python -m zavia_agent.cli --publish examples/sample-article.md
    python -m zavia_agent.cli --update update.json
    python -m zavia_agent.cli --config

Nothing here crashes on bad input — every error is caught, explained in
plain words, and you are sent back to the menu.
"""

import argparse
import json
import os
import sys

from .client import ZaviaClient, ZaviaError, load_dotenv
from .health import check_site
from .report import (compare_reports, default_report_path, load_previous_state,
                     run_audit, write_report, write_seo_report)
from .seo import (check_article, check_articles, check_crawl_files,
                   check_links, check_sitemap, check_sitemap_live,
                   check_sitemap_pages, check_sitemap_seo,
                   find_junk_articles)


# ---------------------------------------------------------------- front-matter
def parse_markdown_article(path):
    """Read a Markdown file with a small front-matter block.

    Example:
        ---
        title: My Article
        slug: my-article
        excerpt: One short line.
        category: guides
        language: en
        tags: freelancing, students
        ---
        # My Article
        ...the article body in Markdown...

    Returns a dict ready for client.publish_article().
    """
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if not text.lstrip().startswith("---"):
        raise ZaviaError("That file has no front-matter (it must start with a line of ---). See examples/sample-article.md.")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ZaviaError("Front-matter block is not closed (need a second --- line).")
    meta_block, body = parts[1], parts[2]
    article = {}
    for line in meta_block.strip().splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        if key == "tags":
            value = value.strip("[]")
            article["tags"] = [t.strip() for t in value.split(",") if t.strip()]
        else:
            article[key] = value
    article["content"] = body.strip()
    return article


def load_update_json(path):
    """Read an update payload from a JSON file (must contain `slug`)."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ZaviaError("Update file must be one JSON object, e.g. {\"slug\": \"...\", \"title\": \"...\"}.")
    return data


# ---------------------------------------------------------------- actions
def do_health(client):
    print("\nChecking Zavia health... (if the database is having one of its short drops, this retries a few times)")
    result = check_site(client)
    for name in ("homepage", "sitemap", "api"):
        check = result[name]
        mark = "OK " if check["ok"] else "FAIL"
        print(f"  [{mark}] {name}: {check['detail']}")
    print("\n" + result["verdict"])


def do_list(client, limit=10):
    print("\nFetching the sitemap...")
    urls = client.get_sitemap_urls()
    articles = [u for u in urls if "/article/" in u]
    print(f"Total URLs in sitemap: {len(urls)} ({len(articles)} articles)\n")
    print(f"Last {min(limit, len(articles))} articles in the sitemap:")
    for url in articles[-limit:]:
        print("  - " + url)


def do_publish(client, path):
    article = parse_markdown_article(path)
    print(f"\nReady to publish: \"{article.get('title', '(no title)')}\"  (slug: {article.get('slug', '(none)')})")
    tags = article.get("tags") or []
    if tags:
        print(
            "\nNOTE — Zavia tags bug: publishing FAILS (500) if any tag already "
            "exists on Zavia. These tags must all be brand-new, or use an empty "
            "tags list:\n  tags: " + ", ".join(tags)
        )
    answer = input("Publish this article? (y/n): ").strip().lower()
    if answer not in ("y", "yes"):
        print("OK, not published. Kuch nahi hua — wapas menu par chalte hain.")
        return
    result = client.publish_article(article)
    print("\nPublished! Zavia said:")
    print("  " + json.dumps(result, ensure_ascii=False)[:500])


def do_update(client, path):
    update = load_update_json(path)
    print(f"\nReady to update slug: {update.get('slug')}")
    print("Fields to change: " + ", ".join(k for k in update if k != "slug"))
    answer = input("Update this article? (y/n): ").strip().lower()
    if answer not in ("y", "yes"):
        print("OK, not updated.")
        return
    result = client.update_article(update)
    print("\nUpdated! Zavia said:")
    print("  " + json.dumps(result, ensure_ascii=False)[:500])


def do_seo(client):
    print("\nRunning SEO check on the sitemap (fetching a small sample of pages)...")
    report = check_sitemap(client)
    print("\n" + report["summary"])
    if report["duplicate_slugs"]:
        print("Duplicate slugs (fix these — Google gets confused by duplicates):")
        for slug in report["duplicate_slugs"]:
            print("  - " + slug)
    if report["broken"]:
        print("Broken URLs found:")
        for url in report["broken"]:
            print("  - " + url)
    slug = input("\nCheck one article in detail? Enter a slug (or press Enter to skip): ").strip()
    if slug:
        info = check_article(client, slug)
        print("\n" + info["summary"])


def _save_report(report, report_path, client):
    """Write an SEO report to a file and say where it went."""
    path = write_seo_report(report, report_path, site=client.base_url)
    print(f"\nReport saved to: {path}")


def _print_duplicates(report):
    """Print any duplicate titles / descriptions found in a bulk check
    or full audit — one line per shared text, with the slugs sharing it."""
    titles = report.get("duplicate_titles") or []
    descriptions = report.get("duplicate_descriptions") or []
    if not titles and not descriptions:
        return
    print("\nDuplicates (Google shows the same title/snippet for both — give each article its own):")
    for group in titles:
        print(f"  - Same title on: {', '.join(group['slugs'])}")
        print(f"    \"{group['value']}\"")
    for group in descriptions:
        shown = group["value"]
        if len(shown) > 160:
            shown = shown[:160] + "..."
        print(f"  - Same meta description on: {', '.join(group['slugs'])}")
        print(f"    \"{shown}\"")


def do_bulk_seo(client, slugs, report_path=None):
    """Check several article slugs at once; print one line per article.

    Returns the report dict (so --seo --articles can exit non-zero
    when an article fails its title/canonical check).
    """
    print(f"\nChecking {len(slugs)} article(s) — title, canonical, meta description, OG tags, preview image, structured data, title length, image alt text, broken images...")
    report = check_articles(client, slugs)
    for result in report["results"]:
        mark = "OK  " if result["ok"] and not result["warnings"] else ("WARN" if result["ok"] else "FAIL")
        line = f"  [{mark}] {result['slug']}"
        if result["warnings"]:
            line += " — " + "; ".join(result["warnings"])
        elif not result["ok"]:
            line += " — " + result["summary"]
        print(line)
    print("\n" + report["summary"])
    _print_duplicates(report)
    if report_path is not None:
        _save_report(report, report_path, client)
    return report


def do_seo_all(client, report_path=None):
    """Check SEO tags on every article in the sitemap.

    Only the articles with a problem are printed one by one — the
    clean ones just count towards the summary, so the output stays
    short even with 70+ articles.

    Returns the report dict (so --seo --all can exit non-zero when an
    article fails its title/canonical check).
    """
    print("\nFull SEO audit — checking every article in the sitemap (title, canonical, meta description, OG tags, preview image, structured data, title length, image alt text, broken images)...")
    print("This takes a minute or so — one quick request per article.")
    last_shown = [0]

    def _progress(done, total):
        # A short line every 10 articles, so you know it is still working.
        if done % 10 == 0 or done == total:
            print(f"  ... {done}/{total} checked")
            last_shown[0] = done

    report = check_sitemap_seo(client, progress=_progress)
    if report["failed"]:
        print("\nFailed (missing title/canonical, or a 404) — fix these first:")
        for result in report["results"]:
            if not result["ok"]:
                print(f"  - {result['slug']} — {result['summary']}")
    if report["warned"]:
        print("\nWarnings (article loads, but looks worse in search / link previews):")
        for result in report["results"]:
            if result["ok"] and result["warnings"]:
                print(f"  - {result['slug']} — " + "; ".join(result["warnings"]))
    print("\n" + report["summary"])
    _print_duplicates(report)
    if report_path is not None:
        _save_report(report, report_path, client)
    return report


def do_sitemap_check(client):
    """Check every URL in the sitemap; print each dead one.

    Returns the report dict (so --sitemap-check can exit non-zero
    when a URL is broken).
    """
    print("\nSitemap liveness check — fetching every URL in the sitemap...")
    print("This takes a minute or so — one quick request per URL.")

    def _progress(done, total):
        if done % 25 == 0 or done == total:
            print(f"  ... {done}/{total} checked")

    report = check_sitemap_live(client, progress=_progress)
    print("\n" + report["summary"])
    if report["broken"]:
        print("Dead URLs (fix or remove these — Google still sends clicks to them):")
        for item in report["broken"]:
            detail = f"HTTP {item['status']}" if item["status"] else f"error: {item['error']}"
            print(f"  - {item['url']}  ({detail})")
        statuses = {item["status"] for item in report["broken"]}
        if 500 in statuses or 502 in statuses or 503 in statuses:
            print("\nNote: several pages returned 500/502/503 — that can be a "
                  "temporary database drop, not dead pages. Wait a few minutes "
                  "and run this again before treating them as dead.")
    return report


def do_links(client, slug=None):
    """Scan one page's internal links; print each broken one.

    Returns the report dict (so --links can exit non-zero when a
    link is broken).
    """
    label = f"article '{slug}'" if slug else "the homepage"
    print(f"\nScanning the internal links on {label}...")
    report = check_links(client, slug)
    print("\n" + report["summary"])
    if report["broken"]:
        print("Broken links (fix or remove these — they hurt SEO):")
        for item in report["broken"]:
            detail = f"HTTP {item['status']}" if item["status"] else f"error: {item['error']}"
            print(f"  - {item['url']}  ({detail})")
    return report


def do_crawl_files(client):
    """Check /robots.txt and /llms.txt; print each failure and warning.

    Returns the report dict (so --crawl-check can exit non-zero when
    robots.txt is missing, unreachable, or blocks all crawlers).
    """
    print("\nChecking the crawl files (/robots.txt and /llms.txt) — the files crawlers read before anything else...")
    report = check_crawl_files(client)
    print("\n" + report["summary"])
    if report["failures"]:
        print("Failures (fix these — crawlers cannot read the site properly):")
        for item in report["failures"]:
            print("  - " + item)
    if report["warnings"]:
        print("Warnings:")
        for item in report["warnings"]:
            print("  - " + item)
    if report["robots_blocks_all"]:
        print("\nNote: a robots.txt that Disallows / for every crawler stops "
              "Google crawling the whole site. Remove that line (or narrow "
              "it to one path, e.g. Disallow: /admin/) and re-run this check.")
    return report


def do_pages_check(client):
    """Check SEO basics on every non-article page in the sitemap.

    Returns the report dict (so --pages-check can exit non-zero when
    a page fails its title/canonical check).
    """
    print("\nPages check — checking every non-article page in the sitemap (tools, games, guides, Quran pages, homepage)...")
    print("This takes a minute or so — one quick request per page.")

    def _progress(done, total):
        if done % 25 == 0 or done == total:
            print(f"  ... {done}/{total} checked")

    report = check_sitemap_pages(client, progress=_progress)
    if report["failed"]:
        print("\nFailed (missing title/canonical, or a 404) — fix these first:")
        for result in report["results"]:
            if not result["ok"]:
                print(f"  - {result['url']} — {result['summary']}")
    if report["warned"]:
        print("\nWarnings (page loads, but looks worse in search results):")
        for result in report["results"]:
            if result["ok"] and result["warnings"]:
                print(f"  - {result['url']} — " + "; ".join(result["warnings"]))
    print("\n" + report["summary"])
    return report


def do_config(client):
    print("\nConfig / status")
    print(f"  Site (base URL): {client.base_url}")
    print(f"  Publish token:   {'set — good, ready to publish' if client.token else 'NOT SET — copy .env.example to .env and add your token'}")
    print("  Delete articles: NOT in this agent — deleting is a manual step in the admin panel (zaviamagazine.dev/admin).")
    print("  Note: Vercel deploys take ~30 min, so a new article may not show up instantly. Sabar karo, aa jayega.")


def do_junk(client):
    print("\nChecking for test / junk articles (sitemap scan + live check of known test slugs)...")
    report = find_junk_articles(client)
    print("\n" + report["summary"])
    if report["junk"]:
        for item in report["junk"]:
            print(f"  - {item['slug']}  ({item['marker']}, via {item['source']})")
            print(f"    {item['url']}")
        print("\nDelete these in the admin panel: zaviamagazine.dev/admin → Articles tab.")
        print("After deleting, they 404 and drop out — that is expected, not a problem.")


def do_seo_report(client):
    """Full SEO audit + dated report (Lesson 14 pattern).

    Runs the audit, compares with the previous run (if any), writes a
    dated Markdown + HTML report under reports/, and saves the state
    for the next comparison. Non-interactive — safe to run from cron.
    Returns the audit dict.
    """
    print("\nRunning the full SEO audit (sitemap + every article + junk check)...")
    print("This takes a minute or two — one request per article.")
    previous = load_previous_state()
    audit = run_audit(client)
    comparison = compare_reports(previous, audit)
    paths = write_report(audit, comparison)
    counts = audit["counts"]
    print("\n" + audit["checks"]["sitemap"])
    print(audit["checks"]["articles"])
    print(audit["checks"]["junk"])
    print(f"\nIssues: {counts['total_issues']} total "
          f"({counts['fail_issues']} FAIL, {counts['warn_issues']} warnings).")
    if comparison["first_run"]:
        print("First audit run — no previous report to compare with yet.")
    else:
        print(f"Since the last report ({comparison['previous_date']}): "
              f"{len(comparison['improved'])} fixed, "
              f"{len(comparison['worse'])} new failures, "
              f"{len(comparison['new'])} new issues.")
    print("\nReport written:")
    print("  " + paths["md"])
    print("  " + paths["html"])
    return audit


# ---------------------------------------------------------------- menu
MENU = """
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
 14  Crawl-files check (/robots.txt + /llms.txt — what crawlers read first)
 15  Pages check (every non-article page in the sitemap — tools, games, guides, Quran)
  0  Exit
=============================================
"""


def interactive(client):
    while True:
        print(MENU)
        try:
            choice = input("Pick a number: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAllah Hafiz! Bye.")
            return 0
        try:
            if choice == "0":
                print("Allah Hafiz! Bye.")
                return 0
            if choice == "1":
                do_health(client)
            elif choice == "2":
                do_list(client)
            elif choice == "3":
                path = input("Markdown file path (e.g. examples/sample-article.md): ").strip()
                if not path:
                    print("No file given — wapas menu par.")
                else:
                    do_publish(client, path)
            elif choice == "4":
                path = input("JSON file path (e.g. update.json): ").strip()
                if not path:
                    print("No file given — wapas menu par.")
                else:
                    do_update(client, path)
            elif choice == "5":
                do_seo(client)
            elif choice == "6":
                do_config(client)
            elif choice == "7":
                do_junk(client)
            elif choice == "8":
                raw = input("Article slugs, comma-separated (e.g. slug-one, slug-two): ").strip()
                slugs = [s.strip() for s in raw.split(",") if s.strip()]
                if not slugs:
                    print("No slugs given — wapas menu par.")
                else:
                    do_bulk_seo(client, slugs)
            elif choice == "9":
                slug = input("Article slug to scan (or press Enter for the homepage): ").strip()
                do_links(client, slug or None)
            elif choice == "10":
                do_seo_all(client)
            elif choice == "11":
                do_seo_all(client, report_path="")
            elif choice == "12":
                do_seo_report(client)
            elif choice == "13":
                do_sitemap_check(client)
            elif choice == "14":
                do_crawl_files(client)
            elif choice == "15":
                do_pages_check(client)
            else:
                print(f"'{choice}' is not on the menu. Pick a number 0-15.")
        except FileNotFoundError as exc:
            print(f"\nFile not found: {exc.filename or exc}. Check the path and try again.")
        except ZaviaError as exc:
            print(f"\nSomething went wrong: {exc}")
        except json.JSONDecodeError as exc:
            print(f"\nThat JSON file is broken: {exc}. Fix the file and try again.")
        except Exception as exc:  # never crash the menu
            print(f"\nUnexpected error ({type(exc).__name__}): {exc}. Wapas menu par chalte hain.")
        input("\nPress Enter to go back to the menu...")


def main(argv=None):
    load_dotenv()
    parser = argparse.ArgumentParser(prog="zavia-agent", description="Interactive agent for the Zavia blog.")
    parser.add_argument("--health", action="store_true", help="Run the health check and exit.")
    parser.add_argument("--seo", action="store_true", help="Run the SEO check and exit.")
    parser.add_argument("--junk", action="store_true", help="Find test/junk articles in the sitemap and exit.")
    parser.add_argument("--list", action="store_true", help="List recent articles and exit.")
    parser.add_argument("--publish", metavar="FILE", help="Publish an article from a Markdown file and exit.")
    parser.add_argument("--update", metavar="FILE", help="Update an article from a JSON file and exit.")
    parser.add_argument("--config", action="store_true", help="Show config/status and exit.")
    parser.add_argument("--links", metavar="SLUG", nargs="?", const="",
                        help="Scan one article's internal links for broken ones (no SLUG = scan the homepage) and exit.")
    parser.add_argument("--article", metavar="SLUG", help="With --seo: also check this one article slug in detail.")
    parser.add_argument("--articles", metavar="SLUGS", help="With --seo: check several article slugs at once, comma-separated (title, canonical, meta description, OG tags).")
    parser.add_argument("--all", action="store_true", help="With --seo: check EVERY article in the sitemap (full SEO audit).")
    parser.add_argument("--report", metavar="FILE", nargs="?", const="", default=None,
                        help="With --seo --all or --seo --articles: also save the results as a Markdown report. "
                             "Give a file name, or leave it off for zavia-seo-report-<today>.md.")
    parser.add_argument("--seo-report", action="store_true",
                        help="Full SEO audit + dated report under reports/, compared with the previous run (Lesson 14 pattern), and exit.")
    parser.add_argument("--sitemap-check", action="store_true",
                        help="Check EVERY URL in the sitemap and list the dead ones (not just a 10-URL sample) and exit.")
    parser.add_argument("--crawl-check", action="store_true",
                        help="Check /robots.txt and /llms.txt (the files crawlers read first) and exit.")
    parser.add_argument("--pages-check", action="store_true",
                        help="Check SEO basics on every non-article page in the sitemap (tools, games, guides, Quran pages, homepage) and exit.")
    args = parser.parse_args(argv)

    client = ZaviaClient()
    try:
        if args.health:
            do_health(client)
            return 0
        if args.junk:
            do_junk(client)
            return 0
        if args.seo_report:
            audit = do_seo_report(client)
            return 0 if audit["ok"] else 1
        if args.sitemap_check:
            report = do_sitemap_check(client)
            return 0 if report["ok"] else 1
        if args.crawl_check:
            report = do_crawl_files(client)
            return 0 if report["ok"] else 1
        if args.pages_check:
            report = do_pages_check(client)
            return 0 if report["ok"] else 1
        if args.seo:
            if args.all:
                report = do_seo_all(client, report_path=args.report)
                return 0 if report["ok"] else 1
            if args.articles:
                slugs = [s.strip() for s in args.articles.split(",") if s.strip()]
                if not slugs:
                    print("Error: --articles needs at least one slug.", file=sys.stderr)
                    return 1
                report = do_bulk_seo(client, slugs, report_path=args.report)
                return 0 if report["ok"] else 1
            if args.report is not None:
                print("Note: --report only saves a file with --seo --all or --seo --articles.", file=sys.stderr)
            report = check_sitemap(client)
            print(report["summary"])
            if report["duplicate_slugs"]:
                print("Duplicate slugs: " + ", ".join(report["duplicate_slugs"]))
            if report["broken"]:
                print("Broken URLs:")
                for url in report["broken"]:
                    print("  - " + url)
            if args.article:
                print(check_article(client, args.article)["summary"])
            return 0 if report["ok"] else 1
        if args.links is not None:
            report = do_links(client, args.links or None)
            return 0 if report["ok"] else 1
        if args.list:
            do_list(client)
            return 0
        if args.publish:
            article = parse_markdown_article(args.publish)
            result = client.publish_article(article)
            print("Published! Zavia said:")
            print(json.dumps(result, ensure_ascii=False)[:500])
            return 0
        if args.update:
            result = client.update_article(load_update_json(args.update))
            print("Updated! Zavia said:")
            print(json.dumps(result, ensure_ascii=False)[:500])
            return 0
        if args.config:
            do_config(client)
            return 0
    except ZaviaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"Error: file not found: {exc.filename or exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"Error: that JSON file is broken: {exc}", file=sys.stderr)
        return 1

    return interactive(client)


if __name__ == "__main__":
    raise SystemExit(main())
