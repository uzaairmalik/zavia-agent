"""SEO checks for Zavia.

- check_sitemap(): fetch /sitemap.xml, count URLs, find duplicate slugs,
  and fetch a small sample of URLs to catch 404s.
- check_article(slug): fetch one article page, confirm it loads, that a
  <title> is present, that a canonical link is present, and that it has
  a meta description + Open Graph tags (warnings, not failures).
  Also warns when the canonical points at a different page/site, or
  when the meta description is too short (< 50 chars) or too long
  (> 160 chars, Google truncates it).
- check_articles(slugs): same checks for several slugs at once.
- check_sitemap_seo(): same checks for EVERY article in the sitemap
  (a weekly sweep — no slug list to type).
- find_duplicate_meta(results): titles / meta descriptions shared by
  more than one article — Google shows the same title/snippet for
  both, so each article needs its own. Used by the bulk check and the
  full audit; costs no extra requests (it reuses the fetched pages).
- check_links(slug=None): fetch one article page (or the homepage when
  no slug is given), find its internal links, and report any that
  return a non-200 status — broken internal links hurt SEO.

Nothing here needs a token — these are plain public-page checks.
"""

import html
import re
from urllib.parse import urljoin, urlparse

from .client import ZaviaClient, ZaviaError

# Internal hosts for Zavia (custom domain + the Vercel URL the API uses).
INTERNAL_HOSTS = ("zaviamagazine.dev", "zavia-magazine.vercel.app")
# Link targets that are not content pages — checking them would cry wolf
# (API routes need a POST, static chunks move between deploys, assets are
# not SEO links). The scan checks internal PAGE links only.
SKIP_PREFIXES = ("/api/", "/_next/")
SKIP_EXTENSIONS = (".js", ".css", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".pdf", ".xml", ".woff", ".woff2")
LINK_HREF_RE = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\']', re.IGNORECASE)

CANONICAL_RE = re.compile(r'<link[^>]+rel=["\']canonical["\'][^>]*>', re.IGNORECASE)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
# Meta description + Open Graph tags (Google + social previews use these).
DESCRIPTION_RE = re.compile(r'<meta[^>]+name=["\']description["\'][^>]*>', re.IGNORECASE)
OG_TITLE_RE = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]*>', re.IGNORECASE)
OG_DESC_RE = re.compile(r'<meta[^>]+property=["\']og:description["\'][^>]*>', re.IGNORECASE)
CONTENT_RE = re.compile(r'content=["\']([^"\']*)["\']', re.IGNORECASE)


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

    Besides title + canonical, this also checks the meta description
    (Google shows it in search results — a missing one means Google
    writes its own snippet) and the Open Graph title/description
    (used for Facebook/WhatsApp link previews).

    Two sharper warnings (Day 6) — these are warnings, not failures,
    because the article still loads and ranks:
    - A canonical that points at a DIFFERENT page (or a different
      site) than this article. Google then credits the wrong page.
      Either Zavia host (zaviamagazine.dev / zavia-magazine.vercel.app)
      is fine — only the path has to match this article.
    - A meta description that is too short (< 50 chars, Google has
      almost nothing to show) or too long (> 160 chars, Google cuts
      it off). Length is counted after unescaping HTML entities, so
      &#x27; counts as one character, not six.

    Returns a dict:
    {
      "slug": str, "url": str, "status": int|None,
      "title": str, "title_found": bool,
      "canonical": str|None, "canonical_found": bool,
      "canonical_matches": bool,   # canonical path == this article's path
      "canonical_host_ok": bool,   # canonical host is a Zavia host
      "description": str, "description_found": bool,
      "description_full": str,   # full description, entities unescaped
      "description_length": int,   # chars, after unescaping entities
      "og_title_found": bool, "og_description_found": bool,
      "warnings": [str, ...],   # missing description / OG tags / bad canonical / odd description length (not fatal)
      "ok": bool, "summary": str,
    }
    """
    info = client.get_article(slug)
    html_text = info.get("html") or ""
    title_found = bool(info.get("title"))
    canonical = None
    match = CANONICAL_RE.search(html_text)
    if match:
        href = HREF_RE.search(match.group(0))
        if href:
            canonical = href.group(1)
    # Canonical check: the path must be THIS article's path. The host
    # may be either Zavia host (the agent fetches via vercel.app while
    # the canonical uses zaviamagazine.dev) — a host-only difference
    # is fine, a different path or a stranger host is not.
    canonical_matches = False
    canonical_host_ok = True
    if canonical:
        parsed_canon = urlparse(canonical if "://" in canonical else urljoin(info["url"], canonical))
        article_path = urlparse(info["url"]).path.rstrip("/")
        canonical_matches = parsed_canon.path.rstrip("/") == article_path
        canonical_host_ok = not parsed_canon.netloc or parsed_canon.netloc in INTERNAL_HOSTS
    description = ""
    description_found = False
    match = DESCRIPTION_RE.search(html_text)
    if match:
        content = CONTENT_RE.search(match.group(0))
        if content:
            description = content.group(1).strip()
            description_found = bool(description)
    description_length = len(html.unescape(description)) if description_found else 0
    og_title_found = bool(OG_TITLE_RE.search(html_text))
    og_description_found = bool(OG_DESC_RE.search(html_text))
    warnings = []
    if info["status"] == 200:
        if canonical is not None:
            if not canonical_host_ok:
                warnings.append(f"canonical points to a different site ({urlparse(canonical).netloc}) — it should be zaviamagazine.dev")
            elif not canonical_matches:
                warnings.append(f"canonical points to a different page ({canonical}) — it should be this article's own URL")
        if not description_found:
            warnings.append("no meta description (Google will write its own snippet)")
        elif description_length < 50:
            warnings.append(f"meta description is very short ({description_length} chars) — aim for 50-160")
        elif description_length > 160:
            warnings.append(f"meta description is long ({description_length} chars) — Google truncates it after ~160")
        if not og_title_found or not og_description_found:
            warnings.append("missing Open Graph tags (link previews on Facebook/WhatsApp will look plain)")
    ok = info["status"] == 200 and title_found and canonical is not None
    if info["status"] == 404:
        summary = f"Article '{slug}' is a 404 (not found)."
    elif not title_found:
        summary = f"Article '{slug}' loaded (HTTP {info['status']}) but has no <title>."
    elif canonical is None:
        summary = f"Article '{slug}' loaded but has no canonical link."
    else:
        summary = f"Article '{slug}' OK — title present, canonical: {canonical}."
    if warnings:
        summary += " Warning: " + "; ".join(warnings) + "."
    return {
        "slug": slug,
        "url": info["url"],
        "status": info["status"],
        "title": info.get("title", ""),
        "title_found": title_found,
        "canonical": canonical,
        "canonical_found": canonical is not None,
        "canonical_matches": canonical_matches,
        "canonical_host_ok": canonical_host_ok,
        "description": description[:160],
        "description_full": html.unescape(description).strip() if description_found else "",
        "description_found": description_found,
        "description_length": description_length,
        "og_title_found": og_title_found,
        "og_description_found": og_description_found,
        "warnings": warnings,
        "ok": ok,
        "summary": summary,
    }


def _normalise_meta(value):
    """One comparable form of a title / description: entities unescaped,
    whitespace collapsed, lowercased. So 'Foo  Bar' and 'foo bar' count
    as the same text, while genuinely different texts stay different."""
    return " ".join(html.unescape(value or "").split()).lower()


def find_duplicate_meta(results):
    """Find titles / meta descriptions shared by more than one article.

    Google treats a shared title or description as a sign of thin or
    duplicate content: two articles get the same title/snippet in
    search results, so neither stands out. Each article needs its own.

    results: check_article() dicts (from check_articles or
    check_sitemap_seo — no extra requests, the pages are already
    fetched). Empty titles / descriptions are skipped — a missing
    description is already warned about per article, it is not a
    duplicate. Comparison is on the FULL description
    (description_full), not the 160-char display cut, so two long
    descriptions that only share their first 160 chars are not
    flagged.

    Returns a dict:
    {
      "duplicate_titles": [{"key": str, "value": str, "slugs": [str, ...]}, ...],
      "duplicate_descriptions": [{"key": str, "value": str, "slugs": [str, ...]}, ...],
    }
    `key` is the normalised text (stable across runs — the audit
    report uses it for its issue keys); `value` is the text as first
    seen, for display; `slugs` lists every article sharing it.
    """
    titles = {}
    descriptions = {}
    for result in results:
        slug = result.get("slug") or ""
        if not slug:
            continue
        title_key = _normalise_meta(result.get("title") or "")
        if title_key:
            group = titles.setdefault(title_key, {"key": title_key, "value": (result.get("title") or "").strip(), "slugs": []})
            group["slugs"].append(slug)
        desc_key = _normalise_meta(result.get("description_full") or result.get("description") or "")
        if desc_key:
            group = descriptions.setdefault(desc_key, {"key": desc_key, "value": (result.get("description_full") or result.get("description") or "").strip(), "slugs": []})
            group["slugs"].append(slug)
    # Most-shared first, then alphabetically — deterministic order.
    dup_titles = sorted(
        (g for g in titles.values() if len(g["slugs"]) > 1),
        key=lambda g: (-len(g["slugs"]), g["key"]),
    )
    dup_descs = sorted(
        (g for g in descriptions.values() if len(g["slugs"]) > 1),
        key=lambda g: (-len(g["slugs"]), g["key"]),
    )
    return {"duplicate_titles": dup_titles, "duplicate_descriptions": dup_descs}


def _failed_result(slug, exc):
    """A check_article-shaped dict for an article that could not be checked."""
    return {
        "slug": slug, "url": "", "status": None, "title": "",
        "title_found": False, "canonical": None,
        "canonical_found": False, "canonical_matches": False,
        "canonical_host_ok": True, "description": "",
        "description_full": "",
        "description_found": False, "description_length": 0,
        "og_title_found": False,
        "og_description_found": False,
        "warnings": [], "ok": False,
        "summary": f"Article '{slug}' could not be checked: {exc}",
    }


def check_articles(client, slugs):
    """Check several article slugs at once (bulk SEO check).

    slugs: list of slug strings (empty entries ignored).
    Returns a dict:
    {
      "results": [check_article(...) dict, ...],
      "checked": int, "failed": int, "warned": int,
      "duplicate_titles": [{"key", "value", "slugs"}, ...],
      "duplicate_descriptions": [{"key", "value", "slugs"}, ...],
      "ok": bool,   # True only when every article passed title+canonical
      "summary": str,
    }
    A missing meta description or OG tag is a warning, not a failure —
    the article still loads and ranks, it just looks worse in search
    results and link previews. Duplicate titles / descriptions are
    reported as groups (see find_duplicate_meta) — they are warnings
    too, and do not change `ok`.
    """
    results = []
    for slug in slugs:
        slug = (slug or "").strip().strip("/")
        if not slug:
            continue
        try:
            results.append(check_article(client, slug))
        except ZaviaError as exc:
            results.append(_failed_result(slug, exc))
    failed = [r for r in results if not r["ok"]]
    warned = [r for r in results if r["ok"] and r["warnings"]]
    duplicates = find_duplicate_meta(results)
    ok = not failed
    parts = [f"Checked {len(results)} article(s)"]
    if failed:
        parts.append(f"{len(failed)} failed: " + ", ".join(r["slug"] for r in failed))
    if warned:
        parts.append(f"{len(warned)} with warnings: " + ", ".join(r["slug"] for r in warned))
    if duplicates["duplicate_titles"]:
        parts.append(f"{len(duplicates['duplicate_titles'])} duplicate title(s)")
    if duplicates["duplicate_descriptions"]:
        parts.append(f"{len(duplicates['duplicate_descriptions'])} duplicate meta description(s)")
    if not failed and not warned and not duplicates["duplicate_titles"] and not duplicates["duplicate_descriptions"]:
        parts.append("all OK — titles, canonicals, meta descriptions and OG tags present")
    return {
        "results": results,
        "checked": len(results),
        "failed": len(failed),
        "warned": len(warned),
        "duplicate_titles": duplicates["duplicate_titles"],
        "duplicate_descriptions": duplicates["duplicate_descriptions"],
        "ok": ok,
        "summary": "; ".join(parts) + ".",
    }


def check_sitemap_seo(client=None, limit=150, progress=None):
    """Check SEO tags on EVERY article in the sitemap.

    Same per-article checks as check_articles (title, canonical, meta
    description, OG tags), but the slugs come from the sitemap itself —
    no list to type. A weekly sweep: it catches the articles Uzair
    forgot to check one by one.

    limit: max articles to check (the sitemap holds ~70 articles; the
    cap keeps a bad day quick). progress: optional callback called as
    progress(done, total) after each article.

    Returns a dict:
    {
      "results": [check_article(...) dict, ...],
      "checked": int, "failed": int, "warned": int,
      "failed_slugs": [slug, ...], "warned_slugs": [slug, ...],
      "duplicate_titles": [{"key", "value", "slugs"}, ...],
      "duplicate_descriptions": [{"key", "value", "slugs"}, ...],
      "ok": bool,   # True only when no article failed title/canonical
      "summary": str,
    }
    """
    client = client or ZaviaClient()
    urls = client.get_sitemap_urls()
    article_urls = [u for u in urls if "/article/" in urlparse(u).path]
    slugs = []
    seen = set()
    for url in article_urls:
        slug = _slug_of(url)
        if slug and slug not in seen:
            seen.add(slug)
            slugs.append(slug)
    slugs = slugs[:limit]
    results = []
    for i, slug in enumerate(slugs, 1):
        try:
            results.append(check_article(client, slug))
        except ZaviaError as exc:
            results.append(_failed_result(slug, exc))
        if progress:
            progress(i, len(slugs))
    failed = [r for r in results if not r["ok"]]
    warned = [r for r in results if r["ok"] and r["warnings"]]
    duplicates = find_duplicate_meta(results)
    ok = not failed
    parts = [f"Checked {len(results)} article(s) from the sitemap"]
    if failed:
        parts.append(f"{len(failed)} failed: " + ", ".join(r["slug"] for r in failed))
    if warned:
        parts.append(f"{len(warned)} with warnings: " + ", ".join(r["slug"] for r in warned))
    if duplicates["duplicate_titles"]:
        parts.append(f"{len(duplicates['duplicate_titles'])} duplicate title(s)")
    if duplicates["duplicate_descriptions"]:
        parts.append(f"{len(duplicates['duplicate_descriptions'])} duplicate meta description(s)")
    if not failed and not warned and not duplicates["duplicate_titles"] and not duplicates["duplicate_descriptions"]:
        parts.append("all OK — titles, canonicals, meta descriptions and OG tags present")
    return {
        "results": results,
        "checked": len(results),
        "failed": len(failed),
        "warned": len(warned),
        "failed_slugs": [r["slug"] for r in failed],
        "warned_slugs": [r["slug"] for r in warned],
        "duplicate_titles": duplicates["duplicate_titles"],
        "duplicate_descriptions": duplicates["duplicate_descriptions"],
        "ok": ok,
        "summary": "; ".join(parts) + ".",
    }


def find_junk_articles(client=None):
    """Find test / junk articles that need deleting.

    Two sources:
    (a) Sitemap scan — a slug looks like junk when it contains one of these
        markers: "please-ignore", "test-article", "lorem", "asdf", "xxx",
        "delete-me". NOTE: a bare "test" or "draft" is deliberately NOT a
        marker — it matches real content (cricket "Test" matches, sports
        drafts, "latest", "contest"), so it would cry wolf.
    (b) Known test slugs — the two test articles are live (HTTP 200) but no
        longer in the sitemap, so a sitemap-only scan misses them. These are
        checked directly by URL: 200 = still live, 404 = cleaned up.
    Test articles hurt SEO (thin pages Google can index) and look
    unprofessional, so this reports them for cleanup. Deleting is a manual
    admin-panel step — this function only FINDS them, it never deletes anything.

    Returns a dict:
    {
      "total_articles": int,
      "junk": [{"slug": str, "url": str, "marker": str, "source": str}, ...],
      "ok": bool,   # True when no junk found
      "summary": str,
    }
    """
    client = client or ZaviaClient()
    urls = client.get_sitemap_urls()
    article_urls = [u for u in urls if "/article/" in urlparse(u).path]
    markers = ("please-ignore", "test-article", "lorem", "asdf", "xxx", "delete-me")
    junk = []
    seen = set()
    for url in article_urls:
        slug = _slug_of(url)
        lowered = slug.lower()
        for marker in markers:
            if marker in lowered:
                junk.append({"slug": slug, "url": url, "marker": marker, "source": "sitemap"})
                seen.add(slug)
                break
    # (b) Known test slugs, checked live (they are not in the sitemap any more).
    for slug in KNOWN_JUNK_SLUGS:
        if slug in seen:
            continue
        info = client.get_article(slug)
        if info["status"] == 200:
            junk.append({"slug": slug, "url": info["url"], "marker": "known test article", "source": "live check"})
            seen.add(slug)
    if junk:
        summary = (
            f"Found {len(junk)} junk/test article(s) still live — "
            "delete them in the admin panel (zaviamagazine.dev/admin → Articles tab)."
        )
    else:
        summary = "No junk/test articles found. Clean!"
    return {
        "total_articles": len(article_urls),
        "junk": junk,
        "ok": not junk,
        "summary": summary,
    }


# Test articles that are live but no longer appear in the sitemap, so the
# sitemap scan alone would miss them. Remove a slug here once it is deleted
# (it will then 404 and the live check will stop reporting it).
KNOWN_JUNK_SLUGS = (
    "test-article-please-ignore-two",
    "test-article-please-ignore-three",
)


def check_article_by_slug(slug, client=None):
    """Convenience wrapper that builds a client when none is given."""
    return check_article(client or ZaviaClient(), slug)


def _internal_page_links(html, page_url):
    """Pull the internal PAGE links out of a page's HTML.

    Keeps: relative links ("/article/...", "/guides/...") and absolute
    links to Zavia's own hosts. Drops: anchors (#...), mailto:/tel:/
    javascript:, links to other sites, API routes, /_next/ chunks and
    asset files (images, scripts, stylesheets, PDFs). Returns a list of
    unique absolute URLs, in the order they first appear.
    """
    seen = []
    seen_set = set()
    for href in LINK_HREF_RE.findall(html or ""):
        href = (href or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc and parsed.netloc not in INTERNAL_HOSTS:
            continue  # external link — not this scan's job
        path = parsed.path or "/"
        lowered = path.lower()
        if lowered.startswith(SKIP_PREFIXES):
            continue
        if any(lowered.endswith(ext) for ext in SKIP_EXTENSIONS):
            continue
        # Drop the #fragment — two links to the same page are one check.
        clean = f"{parsed.scheme}://{parsed.netloc}{path}"
        if parsed.query:
            clean += "?" + parsed.query
        if clean not in seen_set:
            seen_set.add(clean)
            seen.append(clean)
    return seen


def check_links(client=None, slug=None, max_links=60, timeout=10):
    """Scan one page's internal links and report the broken ones.

    slug: an article slug to scan, or None to scan the homepage.
    Only internal page links are checked (see _internal_page_links);
    at most max_links are fetched so a link-heavy page stays quick.

    Returns a dict:
    {
      "page_url": str, "page_status": int|None,
      "links": [{"url": str, "status": int|None, "error": str|None}, ...],
      "broken": [{"url": str, "status": int|None, "error": str|None}, ...],
      "checked": int, "skipped": int,   # skipped = found but over max_links
      "ok": bool,   # True when the page loaded AND no link is broken
      "summary": str,
    }
    Broken means a non-200 status or a request that failed outright.
    """
    client = client or ZaviaClient()
    if slug:
        info = client.get_article(slug)
        page_url, page_status, html = info["url"], info["status"], info.get("html") or ""
    else:
        page_url = client.base_url + "/"
        try:
            resp = client.session.get(page_url, timeout=timeout)
            page_status, html = resp.status_code, resp.text
        except Exception as exc:  # network error, timeout, DNS, ...
            return {
                "page_url": page_url, "page_status": None, "links": [],
                "broken": [], "checked": 0, "skipped": 0, "ok": False,
                "summary": f"Homepage could not be loaded: {str(exc)[:120]}",
            }
    if page_status != 200:
        return {
            "page_url": page_url, "page_status": page_status, "links": [],
            "broken": [], "checked": 0, "skipped": 0, "ok": False,
            "summary": f"Page {page_url} returned HTTP {page_status} — cannot scan its links.",
        }
    urls = _internal_page_links(html, page_url)
    to_check, skipped = urls[:max_links], max(0, len(urls) - max_links)
    links = []
    broken = []
    for url in to_check:
        try:
            # Plain session GET (no client retries — a link that needs
            # three attempts is slow enough to count as broken anyway).
            resp = client.session.get(url, timeout=timeout)
            status, error = resp.status_code, None
        except Exception as exc:
            status, error = None, str(exc)[:120]
        links.append({"url": url, "status": status, "error": error})
        if status != 200:
            broken.append({"url": url, "status": status, "error": error})
    ok = not broken
    parts = [f"Scanned {len(links)} internal link(s) on {page_url}"]
    if broken:
        parts.append(f"{len(broken)} broken: " + ", ".join(b["url"] for b in broken))
    else:
        parts.append("all returned 200")
    if skipped:
        parts.append(f"{skipped} more link(s) skipped (over the {max_links} cap)")
    return {
        "page_url": page_url, "page_status": page_status, "links": links,
        "broken": broken, "checked": len(links), "skipped": skipped,
        "ok": ok, "summary": "; ".join(parts) + ".",
    }
