"""SEO checks for Zavia.

- check_sitemap(): fetch /sitemap.xml, count URLs, find duplicate slugs,
  and fetch a small sample of URLs to catch 404s.
- check_sitemap_live(): fetch EVERY URL in the sitemap and report any
  that does not return 200 — the full sweep that catches dead pages
  the 10-URL sample misses (e.g. articles lost from the database
  that Google still links to).
- check_article(slug): fetch one article page, confirm it loads, that a
  <title> is present, that a canonical link is present, and that it has
  a meta description + Open Graph tags (warnings, not failures).
  Also warns when the canonical points at a different page/site, or
  when the meta description is too short (< 50 chars) or too long
  (> 160 chars, Google truncates it). It also checks the article's
  own content: exactly one <h1> heading, and a word count of the
  article body (under 300 words counts as thin content — Google may
  treat it as low value). Both are warnings, not failures. And it
  checks the social preview image: an og:image tag, an og:image:alt
  tag and a twitter:card tag — and it actually fetches the og:image
  URL, because a preview picture that 404s (a stale Vercel Blob link,
  for example) leaves Facebook/WhatsApp/X previews picture-less.
  A missing or broken preview image is a warning, not a failure.
  And it checks the structured data: the page should carry JSON-LD
  with an Article node (Article / NewsArticle / BlogPosting — top
  level or inside an @graph) holding headline, datePublished, author
  and image, so Google can show the article as a rich result. A
  JSON-LD block that does not parse as JSON is broken structured
  data — Google ignores it. Missing or broken structured data is a
  warning, not a failure.
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
import json
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
# Article content checks (Day 8): one <h1> per page, and a real body of
# words. The <article> tag holds the body; nav, footer and "related
# articles" sit outside it, so counting words inside it measures the
# article itself, not the page chrome.
H1_RE = re.compile(r'<h1[\s>]', re.IGNORECASE)
ARTICLE_RE = re.compile(r'<article\b[^>]*>(.*?)</article>', re.IGNORECASE | re.DOTALL)
SCRIPT_RE = re.compile(r'<script.*?</script>', re.IGNORECASE | re.DOTALL)
STYLE_RE = re.compile(r'<style.*?</style>', re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r'<[^>]+>')
# Under this many words the article is "thin content" — Google may
# treat a very short page as low value, so it ranks worse.
THIN_CONTENT_WORDS = 300
# Meta description + Open Graph tags (Google + social previews use these).
DESCRIPTION_RE = re.compile(r'<meta[^>]+name=["\']description["\'][^>]*>', re.IGNORECASE)
OG_TITLE_RE = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]*>', re.IGNORECASE)
OG_DESC_RE = re.compile(r'<meta[^>]+property=["\']og:description["\'][^>]*>', re.IGNORECASE)
# Social preview image (Day 10): the picture Facebook/WhatsApp/X show
# for the link. og:image:alt describes it for screen readers, and
# twitter:card picks the large-image layout on X.
OG_IMAGE_RE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]*>', re.IGNORECASE)
OG_IMAGE_ALT_RE = re.compile(r'<meta[^>]+property=["\']og:image:alt["\'][^>]*>', re.IGNORECASE)
TWITTER_CARD_RE = re.compile(r'<meta[^>]+name=["\']twitter:card["\'][^>]*>', re.IGNORECASE)
CONTENT_RE = re.compile(r'content=["\']([^"\']*)["\']', re.IGNORECASE)
# Structured data (Day 11): JSON-LD blocks in <script> tags. Zavia
# writes one block per page (BlogPosting, BreadcrumbList, and a
# @graph with Organization/WebSite), so a page can hold several.
JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
# @type values that mark the article itself (Google's article
# rich-result types). A node with one of these is "the article" in
# the structured data, wherever it sits in the page.
ARTICLE_SCHEMA_TYPES = ("Article", "NewsArticle", "BlogPosting")


def _slug_of(url):
    """Last path segment of a URL (the article slug for /article/<slug>)."""
    path = urlparse(url).path.strip("/")
    return path.split("/")[-1] if path else ""


def _article_word_count(html_text):
    """Word count of the article body, or None when it cannot be found.

    Takes the text inside the page's <article> tag, strips scripts,
    styles and all other tags, unescapes HTML entities, and counts the
    words left. Returns None when the page has no <article> tag (some
    pages render differently) — then no thin-content warning is given
    rather than a wrong one.
    """
    match = ARTICLE_RE.search(html_text or "")
    if not match:
        return None
    body = SCRIPT_RE.sub(" ", match.group(1))
    body = STYLE_RE.sub(" ", body)
    body = TAG_RE.sub(" ", body)
    body = html.unescape(body)
    return len([word for word in body.split() if word.strip()])


def _h1_count(html_text):
    """How many <h1> headings the page has (a page should have exactly one)."""
    return len(H1_RE.findall(html_text or ""))


def _schema_nodes(data):
    """Yield every dict node in one parsed JSON-LD block.

    A block is usually a single object, but Zavia also uses @graph
    (a list of nodes under one block) — walk both, so an Article
    node sitting inside a @graph is still found.
    """
    if isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if isinstance(graph, list):
            for node in graph:
                if isinstance(node, dict):
                    yield node
    elif isinstance(data, list):
        for node in data:
            if isinstance(node, dict):
                yield node


def _node_types(node):
    """The @type value(s) of a JSON-LD node, as a list of strings."""
    value = node.get("@type")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _structured_data(html_text):
    """Structured data (JSON-LD) on a page.

    Finds every <script type="application/ld+json"> block, parses it,
    and looks for an article node (Article / NewsArticle /
    BlogPosting — top level or inside an @graph). For that node it
    checks the fields Google needs for an article rich result:
    headline, datePublished, author and image.

    Returns a dict:
    {
      "jsonld_found": bool,   # any ld+json block at all
      "jsonld_blocks": int,   # blocks found
      "jsonld_invalid": int,  # blocks that did not parse as JSON
      "article_schema_found": bool,
      "article_schema_type": str|None,   # e.g. "BlogPosting"
      "schema_headline_found": bool,
      "schema_date_published_found": bool,
      "schema_author_found": bool,
      "schema_image_found": bool,
    }
    A block that does not parse is broken structured data — Google
    ignores it, so it counts as invalid, not as a missing article.
    """
    blocks = JSONLD_RE.findall(html_text or "")
    invalid = 0
    article = None
    article_type = None
    for raw in blocks:
        try:
            data = json.loads(raw)
        except ValueError:
            invalid += 1
            continue
        for node in _schema_nodes(data):
            if article is not None:
                break
            for type_name in _node_types(node):
                if type_name in ARTICLE_SCHEMA_TYPES:
                    article = node
                    article_type = type_name
                    break

    def _has(key):
        return bool(article and article.get(key))

    return {
        "jsonld_found": len(blocks) > 0,
        "jsonld_blocks": len(blocks),
        "jsonld_invalid": invalid,
        "article_schema_found": article is not None,
        "article_schema_type": article_type,
        "schema_headline_found": _has("headline"),
        "schema_date_published_found": _has("datePublished"),
        "schema_author_found": _has("author"),
        "schema_image_found": _has("image"),
    }


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


def check_sitemap_live(client=None, timeout=10, progress=None):
    """Check EVERY URL in the sitemap, not just a sample.

    check_sitemap() above only fetches the first 10 URLs — fine as a
    quick sniff, but it misses the dead pages further down the list.
    This one fetches all of them (articles, tools, guides, Quran
    pages, everything the sitemap lists) and reports every URL that
    does not return 200.

    Why it matters: when an article is lost from the database it 404s
    live, but Google still has it indexed and still sends clicks to
    it — so the sitemap quietly fills up with dead links. This sweep
    finds them all in one run, so they can be fixed (re-publish the
    article at the same slug) or removed from the sitemap.

    A 5xx here can also be a temporary database drop (Heroku Postgres
    drops for ~5-15 minutes at a time) rather than a dead page — if
    MANY URLs fail at once with 500s, wait a few minutes and run it
    again before treating them as dead.

    progress: optional callback called as progress(done, total) after
    each URL, so a long run can show it is still working.

    Returns a dict:
    {
      "total_urls": int,
      "checked": [{"url": str, "status": int|None, "error": str|None}, ...],
      "broken": [{"url": str, "status": int|None, "error": str|None}, ...],
      "ok": bool,   # True when every sitemap URL returned 200
      "summary": str,
    }
    Broken means a non-200 status or a request that failed outright.
    """
    client = client or ZaviaClient()
    urls = client.get_sitemap_urls()
    checked = []
    broken = []
    for i, url in enumerate(urls, 1):
        try:
            # Plain session GET (no client retries — a URL that needs
            # three attempts is slow enough to count as broken anyway,
            # and a retry would hide a flapping database).
            resp = client.session.get(url, timeout=timeout)
            status, error = resp.status_code, None
        except Exception as exc:  # network error, timeout, DNS, ...
            status, error = None, str(exc)[:120]
        checked.append({"url": url, "status": status, "error": error})
        if status != 200:
            broken.append({"url": url, "status": status, "error": error})
        if progress:
            progress(i, len(urls))
    ok = not broken
    if broken:
        parts = [f"Checked {len(urls)} URL(s) in the sitemap; {len(broken)} broken"]
    else:
        parts = [f"Checked {len(urls)} URL(s) in the sitemap; all returned 200"]
    return {
        "total_urls": len(urls),
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

    Two content warnings (Day 8) — also warnings, not failures:
    - No <h1> heading, or more than one. Google uses the H1 to
      understand what the page is about, and a page should have
      exactly one.
    - Thin content: fewer than 300 words in the article body (the
      text inside the page's <article> tag). A very short page may
      rank as low value. When the page has no <article> tag the word
      count cannot be measured, so no thin-content warning is given.

    Social preview image warnings (Day 10) — also warnings, not
    failures:
    - No og:image tag — Facebook/WhatsApp show the link with no
      picture. The og:image URL is also fetched: a non-200 status
      (or a request that fails outright) means the preview picture
      is broken — Zavia's images live on Vercel Blob storage, so a
      stale link 404s silently. A relative og:image URL is resolved
      against the article's own URL first.
    - No og:image:alt — screen readers cannot describe the picture.
    - No twitter:card — X falls back to a small summary card
      instead of the large-image layout.

    Structured data warnings (Day 11) — also warnings, not
    failures:
    - No JSON-LD at all — Google cannot show rich results for the
      article.
    - A JSON-LD block that does not parse as JSON — broken
      structured data, which Google ignores.
    - JSON-LD present but no Article node (Article / NewsArticle /
      BlogPosting, top level or inside an @graph) — Google cannot
      show the article as a rich result.
    - An Article node missing headline, datePublished, author or
      image — Google's article rich result needs those fields.

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
      "og_image": str,   # the og:image URL as written in the tag ("" when missing)
      "og_image_found": bool,
      "og_image_ok": bool|None,   # True when the image URL returned 200,
                                  # False when it returned something else,
                                  # None when there was no image to check,
                                  # no session to fetch it with, or the
                                  # fetch failed (unknown, not broken)
      "og_image_status": int|None,   # HTTP status of the image fetch
      "og_image_alt_found": bool,
      "twitter_card_found": bool,
      "h1_count": int,   # <h1> headings found (should be exactly 1)
      "word_count": int|None,   # words in the <article> body (None when no <article> tag)
      "jsonld_found": bool,   # any <script type="application/ld+json"> block
      "jsonld_blocks": int,   # JSON-LD blocks found
      "jsonld_invalid": int,   # blocks that did not parse as JSON
      "article_schema_found": bool,   # an Article / NewsArticle / BlogPosting node
      "article_schema_type": str|None,   # e.g. "BlogPosting"
      "schema_headline_found": bool,
      "schema_date_published_found": bool,
      "schema_author_found": bool,
      "schema_image_found": bool,
      "warnings": [str, ...],   # missing description / OG tags / bad canonical / odd description length / H1 / thin content / preview image / structured data (not fatal)
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
    # Social preview image (Day 10): the tag, its alt text, the X card,
    # and — when the tag is there — whether the picture URL loads.
    og_image = ""
    og_image_found = False
    match = OG_IMAGE_RE.search(html_text)
    if match:
        content = CONTENT_RE.search(match.group(0))
        if content:
            og_image = content.group(1).strip()
            og_image_found = bool(og_image)
    og_image_alt_found = bool(OG_IMAGE_ALT_RE.search(html_text))
    twitter_card_found = bool(TWITTER_CARD_RE.search(html_text))
    og_image_ok = None
    og_image_status = None
    og_image_error = None
    if og_image_found:
        # A fake/test client has no session — then the image cannot be
        # fetched, so no claim is made either way (og_image_ok stays
        # None and no warning is given). A real client always has one.
        session = getattr(client, "session", None)
        if session is not None:
            image_url = urljoin(info["url"], og_image)
            try:
                # stream=True: only the headers are read — the picture
                # itself is never downloaded, this is just a liveness
                # check, and the response is closed straight away.
                # 10s, not 5: the blob host is sometimes slow from
                # here, and a slow reply is not a broken picture.
                resp = session.get(image_url, timeout=10, stream=True)
                try:
                    og_image_status = resp.status_code
                    og_image_ok = resp.status_code == 200
                finally:
                    resp.close()
            except Exception as exc:  # network error, timeout, DNS, ...
                # Unknown, NOT broken: a timeout says the picture
                # could not be checked, not that it is gone. So
                # og_image_ok stays None (no false "broken" flag),
                # and the warning says so plainly.
                og_image_ok = None
                og_image_error = str(exc)[:120]
    h1_count = _h1_count(html_text)
    word_count = _article_word_count(html_text)
    schema = _structured_data(html_text)
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
        if not og_image_found:
            warnings.append("no og:image (Facebook/WhatsApp link previews will show no picture)")
        elif og_image_ok is False:
            warnings.append(f"og:image does not load (HTTP {og_image_status}) — link previews will show no picture")
        elif og_image_ok is None and og_image_error is not None:
            # Checked but unreachable (timeout, DNS, ...). og_image_error
            # is only set when a fetch was attempted, so a fake/test
            # client with no session gives no warning here.
            warnings.append(f"og:image could not be checked ({og_image_error}) — could not confirm the preview picture loads")
        if og_image_found and not og_image_alt_found:
            warnings.append("no og:image:alt (screen readers cannot describe the preview picture)")
        if not twitter_card_found:
            warnings.append("no twitter:card (X link previews fall back to a small summary card)")
        if h1_count == 0:
            warnings.append("no <h1> heading — Google uses the H1 to understand what the page is about")
        elif h1_count > 1:
            warnings.append(f"{h1_count} <h1> headings — a page should have exactly one")
        if word_count is not None and word_count < THIN_CONTENT_WORDS:
            warnings.append(f"thin content — only {word_count} words in the article body (aim for 300+)")
        # Structured data (Day 11): JSON-LD with an Article node.
        if not schema["jsonld_found"]:
            warnings.append("no structured data (JSON-LD) — Google cannot show rich results for this article")
        else:
            if schema["jsonld_invalid"]:
                warnings.append(f"structured data is broken ({schema['jsonld_invalid']} invalid JSON-LD block(s)) — Google ignores broken structured data")
            if not schema["article_schema_found"]:
                warnings.append("no Article structured data (Article / NewsArticle / BlogPosting) — Google cannot show this article as a rich result")
            else:
                missing_fields = []
                if not schema["schema_headline_found"]:
                    missing_fields.append("headline")
                if not schema["schema_date_published_found"]:
                    missing_fields.append("datePublished")
                if not schema["schema_author_found"]:
                    missing_fields.append("author")
                if not schema["schema_image_found"]:
                    missing_fields.append("image")
                if missing_fields:
                    warnings.append(f"structured data is missing {', '.join(missing_fields)} — Google's article rich result needs these")
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
        "og_image": og_image,
        "og_image_found": og_image_found,
        "og_image_ok": og_image_ok,
        "og_image_status": og_image_status,
        "og_image_alt_found": og_image_alt_found,
        "twitter_card_found": twitter_card_found,
        "h1_count": h1_count,
        "word_count": word_count,
        "jsonld_found": schema["jsonld_found"],
        "jsonld_blocks": schema["jsonld_blocks"],
        "jsonld_invalid": schema["jsonld_invalid"],
        "article_schema_found": schema["article_schema_found"],
        "article_schema_type": schema["article_schema_type"],
        "schema_headline_found": schema["schema_headline_found"],
        "schema_date_published_found": schema["schema_date_published_found"],
        "schema_author_found": schema["schema_author_found"],
        "schema_image_found": schema["schema_image_found"],
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
        "og_image": "",
        "og_image_found": False,
        "og_image_ok": None,
        "og_image_status": None,
        "og_image_alt_found": False,
        "twitter_card_found": False,
        "h1_count": 0,
        "word_count": None,
        "jsonld_found": False,
        "jsonld_blocks": 0,
        "jsonld_invalid": 0,
        "article_schema_found": False,
        "article_schema_type": None,
        "schema_headline_found": False,
        "schema_date_published_found": False,
        "schema_author_found": False,
        "schema_image_found": False,
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
    description, OG tags, preview image, H1 count, article word count,
    structured data), but the slugs come from the sitemap itself — no list to type.
    A weekly sweep: it catches the articles Uzair forgot to check one by one.
    catches the articles Uzair forgot to check one by one.

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
