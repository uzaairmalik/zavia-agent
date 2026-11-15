"""Interactive command-line agent for Zavia.

Run it and pick a number:
    python -m zavia_agent.cli

Or run one job directly (no menu):
    python -m zavia_agent.cli --health
    python -m zavia_agent.cli --seo
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
from .seo import check_article, check_sitemap


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


def do_config(client):
    print("\nConfig / status")
    print(f"  Site (base URL): {client.base_url}")
    print(f"  Publish token:   {'set — good, ready to publish' if client.token else 'NOT SET — copy .env.example to .env and add your token'}")
    print("  Delete articles: NOT in this agent — deleting is a manual step in the admin panel (zaviamagazine.dev/admin).")
    print("  Note: Vercel deploys take ~30 min, so a new article may not show up instantly. Sabar karo, aa jayega.")


# ---------------------------------------------------------------- menu
MENU = """
================ ZAVIA AGENT ================
  1  Health check (is the site up?)
  2  List recent articles (from sitemap)
  3  Publish an article (from a Markdown file)
  4  Update an article (from a JSON file)
  5  SEO check (sitemap + one article)
  6  Show config / status
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
            else:
                print(f"'{choice}' is not on the menu. Pick a number 0-6.")
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
    parser.add_argument("--list", action="store_true", help="List recent articles and exit.")
    parser.add_argument("--publish", metavar="FILE", help="Publish an article from a Markdown file and exit.")
    parser.add_argument("--update", metavar="FILE", help="Update an article from a JSON file and exit.")
    parser.add_argument("--config", action="store_true", help="Show config/status and exit.")
    parser.add_argument("--article", metavar="SLUG", help="With --seo: also check this one article slug in detail.")
    args = parser.parse_args(argv)

    client = ZaviaClient()
    try:
        if args.health:
            do_health(client)
            return 0
        if args.seo:
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
