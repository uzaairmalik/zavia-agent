"""SEO checks for Zavia.

- check_sitemap(): fetch /sitemap.xml, count URLs, find duplicate slugs,
  and fetch a small sample of URLs to catch 404s.
- check_article(slug): fetch one article page, confirm it loads, that a
  <title> is present, and that a canonical link is present.

Nothing here needs a token — these are plain public-page checks.
"""

import re
from urllib.parse import urlparse

from .client import ZaviaClient, ZaviaError

CANONICAL_RE = re.compile(r'<link[^>]+rel=["\']canonical["\'][^>]*>', re.IGNORECASE)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def _slug_of(url):
    """Last path segment of a URL (the article slug for /article/<slug>)."""
    path = urlparse(url).path.strip("/")
    return path.split("/")[-1] if path else ""


def check_sitemap(client=None, sample=10, timeout=15):
    """Check the sitemap and a sample of its URLs.

    Returns a dict:
    {
      "total_urls": int,
      "article_urls": int,
      "duplicate_slugs": [slug, ...],
      "checked": [{"url": str, "status": int|None, "error": str|None}, ...],
      "broken": [url, ...],   # URLs that did not return 200
      "ok": bool,
      "summary": str,
    }
    """
    client = client or ZaviaClient()
    urls = client.get_sitemap_urls()
    article_urls = [u for u in urls if "/article/" in urlparse(u).path]

    seen = {}
    duplicates = []
    for url in article_urls:
        slug = _slug_of(url)
        if slug in seen:
            if slug not in duplicates:
                duplicates.append(slug)
        else:
            seen[slug] = url

    checked = []
    broken = []
    for url in urls[:sample]:
        try:
            resp = client.session.get(url, timeout=timeout)
            status = resp.status_code
            error = None
        except Exception as exc:  # network error, timeout, DNS, ...
            status = None
            error = str(exc)[:120]
        checked.append({"url": url, "status": status, "error": error})
        if status != 200:
            broken.append(url)

    ok = not duplicates and not broken
    parts = [f"{len(urls)} URLs in sitemap ({len(article_urls)} articles)"]
    if duplicates:
        parts.append(f"{len(duplicates)} duplicate slug(s): {', '.join(duplicates)}")
    if broken:
        parts.append(f"{len(broken)} broken URL(s) in the {len(checked)} sampled")
    else:
        parts.append(f"all {len(checked)} sampled URLs returned 200")
    return {
        "total_urls": len(urls),
        "article_urls": len(article_urls),
        "duplicate_slugs": duplicates,
        "checked": checked,
        "broken": broken,
        "ok": ok,
        "summary": "; ".join(parts) + ".",
    }


def check_article(client, slug):
    """Check one article page.

    Returns a dict:
    {
      "slug": str, "url": str, "status": int|None,
      "title": str, "title_found": bool,
      "canonical": str|None, "canonical_found": bool,
      "ok": bool, "summary": str,
    }
    """
    info = client.get_article(slug)
    html = info.get("html") or ""
    title_found = bool(info.get("title"))
    canonical = None
    match = CANONICAL_RE.search(html)
    if match:
        href = HREF_RE.search(match.group(0))
        if href:
            canonical = href.group(1)
    ok = info["status"] == 200 and title_found and canonical is not None
    if info["status"] == 404:
        summary = f"Article '{slug}' is a 404 (not found)."
    elif not title_found:
        summary = f"Article '{slug}' loaded (HTTP {info['status']}) but has no <title>."
    elif canonical is None:
        summary = f"Article '{slug}' loaded but has no canonical link."
    else:
        summary = f"Article '{slug}' OK — title present, canonical: {canonical}."
    return {
        "slug": slug,
        "url": info["url"],
        "status": info["status"],
        "title": info.get("title", ""),
        "title_found": title_found,
        "canonical": canonical,
        "canonical_found": canonical is not None,
        "ok": ok,
        "summary": summary,
    }


def check_article_by_slug(slug, client=None):
    """Convenience wrapper that builds a client when none is given."""
    return check_article(client or ZaviaClient(), slug)
