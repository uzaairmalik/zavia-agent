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
  And it checks the title length: Google shows about 60 characters
  of a title in search results, so a longer title gets cut off
  mid-word, and a very short title (under 30 characters) says almost
  nothing about the article. And it checks the pictures inside the
  article body: every <img> should have alt text, so Google and
  screen readers can tell what the picture shows — and every picture's
  URL is actually fetched, because a picture that 404s shows readers
  a broken image. A picture whose fetch fails outright (timeout, DNS,
  ...) is "could not be checked", not broken — the same rule as the
  og:image check. Both are warnings, not failures.
- check_articles(slugs): same checks for several slugs at once.
- check_sitemap_seo(): same checks for EVERY article in the sitemap
  (a weekly sweep — no slug list to type).
- check_page(url): the same SEO basics (title, title length, meta
  description, H1 count, canonical) for one NON-article page — a
  tools, games, guides or Quran page. Every check so far was
  article-only, so a game page with no meta description or five H1s
  went unnoticed.
- check_sitemap_pages(): the same checks for EVERY non-article URL
  in the sitemap (no list to type).
- find_duplicate_meta(results): titles / meta descriptions shared by
  more than one article — Google shows the same title/snippet for
  both, so each article needs its own. Used by the bulk check and the
  full audit; costs no extra requests (it reuses the fetched pages).
- check_links(slug=None): fetch one article page (or the homepage when
  no slug is given), find its internal links, and report any that
  return a non-200 status — broken internal links hurt SEO.
- check_crawl_files(): fetch /robots.txt and /llms.txt — the two files
  crawlers read before anything else. A robots.txt that Disallows /
  for every crawler would stop Google crawling the whole site, so
  that is a failure; a missing robots.txt, a robots.txt with no
  Sitemap line, or a missing llms.txt are warnings.

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
# The <title> tag itself (Day 15): check_article() gets the title from
# the client, but a non-article page is fetched directly, so the title
# has to be pulled out of the HTML here.
TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# Title length (Day 12): Google shows about 60 characters of a title
# in search results — longer titles get cut off mid-word. A title
# under 30 characters says almost nothing about the article.
TITLE_MIN_CHARS = 30
TITLE_MAX_CHARS = 60
# Image alt text (Day 12): the <img> tags inside the article body.
# An image counts as missing alt text when it has no alt attribute
# at all, or an empty one (alt="" tells screen readers the picture
# is decorative — fine for a spacer, but an article picture should
# be described).
IMG_RE = re.compile(r'<img\b[^>]*>', re.IGNORECASE)
IMG_ALT_RE = re.compile(r'\balt\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)
IMG_SRC_RE = re.compile(r'\bsrc\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)
# Broken in-body images (Day 14): at most this many pictures per
# article are fetched — a picture-heavy article stays quick, and the
# rest are reported as skipped rather than silently ignored.
MAX_BODY_IMAGES = 10
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


def _image_alt_stats(html_text):
    """(images, missing_alt) for the <img> tags in the article body.

    Returns (None, None) when the page has no <article> tag — then no
    claim is made rather than a wrong one (same rule as the word
    count). An image counts as missing alt text when it has no alt
    attribute at all, or an empty one.
    """
    match = ARTICLE_RE.search(html_text or "")
    if not match:
        return None, None
    tags = IMG_RE.findall(match.group(1))
    missing = 0
    for tag in tags:
        alt = IMG_ALT_RE.search(tag)
        if alt is None or not alt.group(1).strip():
            missing += 1
    return len(tags), missing


def _image_srcs(html_text, page_url):
    """Unique picture URLs inside the article body.

    Returns a list of absolute URLs (a relative src is resolved
    against the article's own URL), in the order they first appear.
    data: URIs (embedded pictures) and empty srcs are skipped — they
    are not links to fetch, so they cannot be broken. Returns [] when
    the page has no <article> tag (same rule as the word count).
    """
    match = ARTICLE_RE.search(html_text or "")
    if not match:
        return []
    seen = []
    seen_set = set()
    for tag in IMG_RE.findall(match.group(1)):
        src = IMG_SRC_RE.search(tag)
        if src is None:
            continue
        # HTML entities in the attribute value (e.g. &amp; in a
        # Next.js /_next/image?url=...&w=...&q=... URL) must be
        # unescaped first — a browser decodes them before fetching,
        # and fetching the raw text returns HTTP 400 (a false
        # "broken" flag — caught live on Day 14).
        value = html.unescape((src.group(1) or "").strip())
        if not value or value.startswith("data:"):
            continue
        absolute = urljoin(page_url, value)
        if absolute not in seen_set:
            seen_set.add(absolute)
            seen.append(absolute)
    return seen


def _check_image_urls(session, urls, timeout=10):
    """Fetch picture URLs (headers only) and sort them into broken / unchecked.

    A non-200 status is broken — the picture is gone, so readers see
    a broken image. A fetch that fails outright (timeout, DNS, ...) is
    unchecked — unknown, not broken (the same rule as the og:image
    check, so a slow picture host does not cry wolf).

    Returns (broken, unchecked) — each a list of dicts:
    broken:    [{"url": str, "status": int}, ...]
    unchecked: [{"url": str, "error": str}, ...]
    """
    broken = []
    unchecked = []
    for url in urls:
        try:
            # stream=True: only the headers are read — the picture
            # itself is never downloaded, this is just a liveness
            # check, and the response is closed straight away.
            resp = session.get(url, timeout=timeout, stream=True)
            try:
                if resp.status_code != 200:
                    broken.append({"url": url, "status": resp.status_code})
            finally:
                resp.close()
        except Exception as exc:  # network error, timeout, DNS, ...
            unchecked.append({"url": url, "error": str(exc)[:120]})
    return broken, unchecked


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

    Title length warnings (Day 12) — also warnings, not failures:
    - A title longer than 60 characters — Google cuts titles off
      after about 60 characters in search results, so the end of
      the title never shows.
    - A title shorter than 30 characters — it says almost nothing
      about what the article is.
    Length is counted after unescaping HTML entities, the same as
    the meta description length.

    Image alt-text warnings (Day 12) — also warnings, not failures:
    - A picture inside the article body with no alt text (or an
      empty alt attribute) — Google and screen readers cannot tell
      what the picture shows. When the page has no <article> tag
      the pictures cannot be measured, so no warning is given.

    Broken in-body images (Day 14) — also warnings, not failures:
    - A picture inside the article body whose URL does not load
      (a non-200 status) — readers see a broken image, and a page
      full of broken pictures looks abandoned. The agent fetches
      each picture's URL (headers only, the picture itself is never
      downloaded), at most 10 pictures per article — the rest are
      reported as skipped, not silently ignored.
    - A picture whose URL could not be fetched at all (timeout, DNS,
      ...) is "could not be checked", not broken — a slow picture
      host is not a broken picture. A fake/test client with no
      session cannot fetch pictures at all, so no claim is made
      either way and no warning is given.

    Returns a dict:
    {
      "slug": str, "url": str, "status": int|None,
      "title": str, "title_found": bool,
      "title_length": int,   # chars, after unescaping entities (0 when no title)
      "image_count": int|None,   # <img> tags in the <article> body (None when no <article> tag)
      "images_missing_alt": int|None,   # of those, how many have no (or empty) alt text
      "image_broken": [{"url": str, "status": int}, ...],   # body pictures that do not load
      "image_unchecked": [{"url": str, "error": str}, ...],   # body pictures whose fetch failed (unknown, not broken)
      "images_checked": int,   # body pictures actually fetched (0 when no session or no <article> tag)
      "images_skipped": int,   # body pictures over the MAX_BODY_IMAGES cap (not fetched)
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
            # Unescape entities first (see _image_srcs): an og:image URL
            # with &amp; in it fetches as HTTP 400 when taken raw.
            image_url = urljoin(info["url"], html.unescape(og_image))
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
    # Title length (Day 12): counted after unescaping entities, so
    # &#x27; counts as one character, not six — same as the meta
    # description length. 0 when there is no title to measure.
    title_length = len(html.unescape(info.get("title") or "")) if title_found else 0
    image_count, images_missing_alt = _image_alt_stats(html_text)
    # Broken in-body images (Day 14): fetch each picture's URL in the
    # article body (headers only). A fake/test client has no session —
    # then the pictures cannot be fetched, so no claim is made either
    # way (image_broken / image_unchecked stay empty and no warning
    # is given). A real client always has one.
    image_srcs = _image_srcs(html_text, info["url"])
    images_skipped = max(0, len(image_srcs) - MAX_BODY_IMAGES)
    image_broken = []
    image_unchecked = []
    images_checked = 0
    session = getattr(client, "session", None)
    if session is not None and info["status"] == 200 and image_srcs:
        image_broken, image_unchecked = _check_image_urls(session, image_srcs[:MAX_BODY_IMAGES])
        images_checked = min(len(image_srcs), MAX_BODY_IMAGES)
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
        # Title length (Day 12): Google shows about 60 characters of
        # a title in search results; a longer title gets cut off.
        if title_found:
            if title_length > TITLE_MAX_CHARS:
                warnings.append(f"title is long ({title_length} chars) — Google cuts titles off after ~{TITLE_MAX_CHARS}")
            elif title_length < TITLE_MIN_CHARS:
                warnings.append(f"title is very short ({title_length} chars) — aim for {TITLE_MIN_CHARS}-{TITLE_MAX_CHARS}")
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
        # Image alt text (Day 12): pictures in the article body with
        # no alt text. image_count is None when the page has no
        # <article> tag — then nothing is claimed, so no warning.
        if image_count is not None and images_missing_alt:
            plural = "image" if images_missing_alt == 1 else "images"
            warnings.append(f"{images_missing_alt} {plural} in the article have no alt text — Google and screen readers cannot tell what they show")
        # Broken in-body images (Day 14): a picture that does not load
        # shows readers a broken image. Unchecked pictures (fetch
        # failed outright) warn that they could not be checked — no
        # false "broken" flag. Pictures over the cap are reported as
        # skipped, not silently ignored.
        if image_broken:
            count = len(image_broken)
            plural = "image" if count == 1 else "images"
            verb = "does" if count == 1 else "do"
            shown = ", ".join(b["url"] for b in image_broken[:3])
            extra = f" (+{count - 3} more)" if count > 3 else ""
            warnings.append(f"{count} {plural} in the article {verb} not load ({shown}{extra}) — readers see a broken picture")
        if image_unchecked:
            count = len(image_unchecked)
            plural = "image" if count == 1 else "images"
            warnings.append(f"{count} {plural} in the article could not be checked — could not confirm the picture loads")
        if images_skipped:
            plural = "image" if images_skipped == 1 else "images"
            warnings.append(f"{images_skipped} more {plural} in the article were not checked (over the {MAX_BODY_IMAGES}-picture cap)")
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
        "title_length": title_length,
        "image_count": image_count,
        "images_missing_alt": images_missing_alt,
        "image_broken": image_broken,
        "image_unchecked": image_unchecked,
        "images_checked": images_checked,
        "images_skipped": images_skipped,
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
        "title_found": False, "title_length": 0,
        "image_count": None, "images_missing_alt": None,
        "image_broken": [], "image_unchecked": [],
        "images_checked": 0, "images_skipped": 0,
        "canonical": None,
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


def check_page(client, url, timeout=10):
    """Check one NON-article page for the SEO basics.

    Articles are covered by check_article(); this one covers the rest
    of the site — the tools pages, the games, the guides, the Quran
    pages and the homepage. The 27 Nov SEO review found game pages
    with no meta description (7 of 11) and wrong H1 counts (Shadow
    Ops had 5, Tetris and Brick Breaker had 0) — none of that showed
    up in the article checks, because these pages are not articles.

    Same rules as check_article(), minus the article-only checks (no
    word count — these pages have no <article> body to measure — no
    structured-data Article node, no preview image, no body pictures):
    - Title present and 30-60 chars (Google cuts longer titles off).
    - Meta description present and 50-160 chars (Google truncates
      longer ones, and writes its own snippet when there is none).
    - Exactly one <h1> heading.
    - Canonical present, pointing at this page's own path on a Zavia
      host (either host is fine — only the path has to match).

    A non-200 status, a missing title or a missing canonical is a
    failure (ok False, exit 1); everything else is a warning — the
    page loads, it just looks worse in search results. A page that
    could not be fetched at all (timeout, DNS, ...) is a failure
    too, and says so plainly instead of pretending it was checked.

    Returns a dict:
    {
      "url": str, "path": str, "status": int|None,
      "title": str, "title_found": bool, "title_length": int,
      "canonical": str|None, "canonical_found": bool,
      "canonical_matches": bool, "canonical_host_ok": bool,
      "description": str, "description_full": str,
      "description_found": bool, "description_length": int,
      "h1_count": int,
      "warnings": [str, ...],
      "ok": bool, "summary": str,
    }
    """
    path = urlparse(url).path.rstrip("/") or "/"
    try:
        # Plain session GET (no client retries — a page that needs
        # three attempts is slow enough to count as broken, and a
        # retry would hide a flapping database).
        resp = client.session.get(url, timeout=timeout)
        status, html_text = resp.status_code, resp.text
    except Exception as exc:  # network error, timeout, DNS, ...
        return {
            "url": url, "path": path, "status": None,
            "title": "", "title_found": False, "title_length": 0,
            "canonical": None, "canonical_found": False,
            "canonical_matches": False, "canonical_host_ok": True,
            "description": "", "description_full": "",
            "description_found": False, "description_length": 0,
            "h1_count": 0, "warnings": [], "ok": False,
            "summary": f"Page {url} could not be checked: {str(exc)[:120]}",
        }
    title = ""
    title_found = False
    match = TITLE_TAG_RE.search(html_text or "")
    if match:
        # Strip any tags inside the title (there should not be any)
        # and unescape entities, so &#x27; counts as one character.
        title = html.unescape(TAG_RE.sub(" ", match.group(1))).strip()
        title_found = bool(title)
    title_length = len(title) if title_found else 0
    canonical = None
    match = CANONICAL_RE.search(html_text or "")
    if match:
        href = HREF_RE.search(match.group(0))
        if href:
            canonical = href.group(1)
    canonical_matches = False
    canonical_host_ok = True
    if canonical:
        parsed_canon = urlparse(canonical if "://" in canonical else urljoin(url, canonical))
        canonical_matches = parsed_canon.path.rstrip("/") == path
        canonical_host_ok = not parsed_canon.netloc or parsed_canon.netloc in INTERNAL_HOSTS
    description = ""
    description_found = False
    match = DESCRIPTION_RE.search(html_text or "")
    if match:
        content = CONTENT_RE.search(match.group(0))
        if content:
            description = content.group(1).strip()
            description_found = bool(description)
    description_length = len(html.unescape(description)) if description_found else 0
    h1_count = _h1_count(html_text or "")
    warnings = []
    if status == 200:
        if canonical is not None:
            if not canonical_host_ok:
                warnings.append(f"canonical points to a different site ({urlparse(canonical).netloc}) — it should be zaviamagazine.dev")
            elif not canonical_matches:
                warnings.append(f"canonical points to a different page ({canonical}) — it should be this page's own URL")
        if not description_found:
            warnings.append("no meta description (Google will write its own snippet)")
        elif description_length < 50:
            warnings.append(f"meta description is very short ({description_length} chars) — aim for 50-160")
        elif description_length > 160:
            warnings.append(f"meta description is long ({description_length} chars) — Google truncates it after ~160")
        if title_found:
            if title_length > TITLE_MAX_CHARS:
                warnings.append(f"title is long ({title_length} chars) — Google cuts titles off after ~{TITLE_MAX_CHARS}")
            elif title_length < TITLE_MIN_CHARS:
                warnings.append(f"title is very short ({title_length} chars) — aim for {TITLE_MIN_CHARS}-{TITLE_MAX_CHARS}")
        if h1_count == 0:
            warnings.append("no <h1> heading — Google uses the H1 to understand what the page is about")
        elif h1_count > 1:
            warnings.append(f"{h1_count} <h1> headings — a page should have exactly one")
    ok = status == 200 and title_found and canonical is not None
    if status == 404:
        summary = f"Page {url} is a 404 (not found)."
    elif status != 200:
        summary = f"Page {url} returned HTTP {status}."
    elif not title_found:
        summary = f"Page {url} loaded (HTTP {status}) but has no <title>."
    elif canonical is None:
        summary = f"Page {url} loaded but has no canonical link."
    else:
        summary = f"Page {url} OK — title present, canonical: {canonical}."
    if warnings:
        summary += " Warning: " + "; ".join(warnings) + "."
    return {
        "url": url,
        "path": path,
        "status": status,
        "title": title,
        "title_found": title_found,
        "title_length": title_length,
        "canonical": canonical,
        "canonical_found": canonical is not None,
        "canonical_matches": canonical_matches,
        "canonical_host_ok": canonical_host_ok,
        "description": description[:160],
        "description_full": html.unescape(description).strip() if description_found else "",
        "description_found": description_found,
        "description_length": description_length,
        "h1_count": h1_count,
        "warnings": warnings,
        "ok": ok,
        "summary": summary,
    }


def _failed_page_result(url, exc):
    """A check_page-shaped dict for a page that could not be checked."""
    path = urlparse(url).path.rstrip("/") or "/"
    return {
        "url": url, "path": path, "status": None,
        "title": "", "title_found": False, "title_length": 0,
        "canonical": None, "canonical_found": False,
        "canonical_matches": False, "canonical_host_ok": True,
        "description": "", "description_full": "",
        "description_found": False, "description_length": 0,
        "h1_count": 0, "warnings": [], "ok": False,
        "summary": f"Page {url} could not be checked: {exc}",
    }


def check_sitemap_pages(client=None, limit=250, progress=None):
    """Check SEO basics on EVERY non-article URL in the sitemap.

    Same per-page checks as check_page (title, title length, meta
    description, H1 count, canonical), but the URLs come from the
    sitemap itself — no list to type. Article URLs are skipped (they
    are check_article()'s job); everything else is checked: the
    tools pages, the games, the guides, the Quran pages, the hubs
    and the homepage.

    limit: max pages to check (the sitemap holds ~115 non-article
    URLs; the cap keeps a bad day quick). progress: optional
    callback called as progress(done, total) after each page.

    Returns a dict:
    {
      "results": [check_page(...) dict, ...],
      "checked": int, "failed": int, "warned": int,
      "failed_urls": [str, ...], "warned_urls": [str, ...],
      "ok": bool,   # True only when no page failed title/canonical
      "summary": str,
    }
    A missing meta description or a wrong H1 count is a warning, not
    a failure — the page still loads, it just looks worse in search
    results. A 404, a missing title or a missing canonical is a
    failure (exit 1).
    """
    client = client or ZaviaClient()
    urls = client.get_sitemap_urls()
    page_urls = []
    seen = set()
    for url in urls:
        if "/article/" in urlparse(url).path:
            continue  # articles are check_article()'s job
        if url not in seen:
            seen.add(url)
            page_urls.append(url)
    page_urls = page_urls[:limit]
    results = []
    for i, url in enumerate(page_urls, 1):
        try:
            results.append(check_page(client, url))
        except ZaviaError as exc:
            results.append(_failed_page_result(url, exc))
        if progress:
            progress(i, len(page_urls))
    failed = [r for r in results if not r["ok"]]
    warned = [r for r in results if r["ok"] and r["warnings"]]
    ok = not failed
    parts = [f"Checked {len(results)} non-article page(s) from the sitemap"]
    if failed:
        parts.append(f"{len(failed)} failed: " + ", ".join(r["url"] for r in failed))
    if warned:
        parts.append(f"{len(warned)} with warnings: " + ", ".join(r["url"] for r in warned))
    if not failed and not warned:
        parts.append("all OK — titles, canonicals, meta descriptions and H1 counts present")
    return {
        "results": results,
        "checked": len(results),
        "failed": len(failed),
        "warned": len(warned),
        "failed_urls": [r["url"] for r in failed],
        "warned_urls": [r["url"] for r in warned],
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


# ---------------------------------------------------------------- crawl files
# (Day 13): /robots.txt and /llms.txt — the two files crawlers read
# before anything else. A robots.txt that Disallows / for every
# crawler would stop Google crawling the whole site, so that is a
# failure, not a warning. Everything else here is a warning: the
# site still loads and ranks, it just tells crawlers less than it
# should.
ROBOTS_PATH = "/robots.txt"
LLMS_PATH = "/llms.txt"


def _parse_robots(text):
    """Read a robots.txt file.

    Returns a dict:
    {
      "sitemap_urls": [str, ...],   # every Sitemap: line, as written
      "blocks_all": bool,           # the User-agent: * block Disallows /
      "disallowed": [str, ...],     # Disallow paths in the * block
    }
    Only the User-agent: * block matters here — it applies to every
    crawler, including Googlebot. A bare "Disallow: /" (or "Disallow:
    /*") in that block blocks the whole site. An empty "Disallow:"
    means "allow everything" and is fine. Lines after a # are
    comments; field names are case-insensitive; Sitemap: lines are
    global (they belong to no group).
    """
    sitemap_urls = []
    disallowed = []
    blocks_all = False
    current_agents = []  # user-agents of the group being read
    # True once the current group has seen a rule (or a Sitemap line):
    # the next User-agent line then starts a NEW group. Two
    # User-agent lines in a row belong to the SAME group (the second
    # one just adds an agent), so they must not reset the group.
    group_open = False
    for raw_line in (text or "").splitlines():
        # A # starts a comment — everything after it is ignored.
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if group_open:
                current_agents = [value.lower()]
                group_open = False
            else:
                current_agents.append(value.lower())
        elif field == "disallow":
            group_open = True
            if "*" in current_agents:
                disallowed.append(value)
                if value in ("/", "/*"):
                    blocks_all = True
        elif field == "sitemap":
            # Global — belongs to no group — but it still closes the
            # run of User-agent lines, so a group after it is new.
            group_open = True
            if value:
                sitemap_urls.append(value)
        elif field in ("allow", "crawl-delay", "noindex"):
            # Any other rule line also closes the User-agent run.
            group_open = True
    return {"sitemap_urls": sitemap_urls, "blocks_all": blocks_all, "disallowed": disallowed}


def check_crawl_files(client=None, timeout=10):
    """Check /robots.txt and /llms.txt on the live site.

    - /robots.txt must return 200. It must name the sitemap (a
      Sitemap: line pointing at a Zavia host), and it must NOT
      Disallow / for every crawler — that would stop Google crawling
      the whole site. A missing or unreachable robots.txt, or one
      that blocks everything, is a failure (exit 1).
    - /llms.txt must return 200. AI crawlers (ChatGPT, Claude, ...)
      read it to learn what the site covers — the 22 Nov SEO roast
      found it missing, so it is worth checking it stays live. A
      missing llms.txt is a warning, not a failure: Google does not
      need it.

    A 5xx here can be a temporary database drop rather than a real
    problem — if both files fail at once, wait a few minutes and run
    it again before treating them as broken.

    Returns a dict:
    {
      "robots_status": int|None, "robots_found": bool,
      "robots_has_sitemap": bool,   # a Sitemap: line naming a Zavia host
      "robots_sitemap_url": str|None,   # the first Sitemap: line, if any
      "robots_blocks_all": bool,    # User-agent: * Disallows /
      "robots_disallowed": [str, ...],   # Disallow paths in the * block
      "llms_status": int|None, "llms_found": bool,
      "failures": [str, ...],   # missing/unreachable robots.txt, blocks-all
      "warnings": [str, ...],   # no Sitemap line, sitemap host, missing llms.txt
      "ok": bool,   # True only when there are no failures
      "summary": str,
    }
    """
    client = client or ZaviaClient()
    failures = []
    warnings = []

    def _fetch(path):
        # Plain session GET (no client retries — a file that needs
        # three attempts is slow enough to count as broken, and a
        # retry would hide a flapping database).
        url = client.base_url + path
        try:
            resp = client.session.get(url, timeout=timeout)
            return resp.status_code, resp.text, None
        except Exception as exc:  # network error, timeout, DNS, ...
            return None, None, str(exc)[:120]

    robots_status, robots_text, robots_error = _fetch(ROBOTS_PATH)
    robots_found = robots_status == 200
    robots = _parse_robots(robots_text) if robots_found else {"sitemap_urls": [], "blocks_all": False, "disallowed": []}
    robots_sitemap_url = robots["sitemap_urls"][0] if robots["sitemap_urls"] else None
    robots_has_sitemap = False
    if robots_sitemap_url:
        parsed_sitemap = urlparse(robots_sitemap_url)
        robots_has_sitemap = (
            parsed_sitemap.path.rstrip("/").endswith("/sitemap.xml")
            and (not parsed_sitemap.netloc or parsed_sitemap.netloc in INTERNAL_HOSTS)
        )
    if robots_status is None:
        failures.append(f"robots.txt could not be checked ({robots_error}) — could not confirm crawlers can read it")
    elif robots_status != 200:
        failures.append(f"robots.txt returned HTTP {robots_status} — crawlers cannot read it (Google assumes everything is allowed, but add the file so it finds the sitemap)")
    else:
        if robots["blocks_all"]:
            failures.append("robots.txt Disallows / for every crawler — Google would stop crawling the whole site")
        if not robots["sitemap_urls"]:
            warnings.append("robots.txt has no Sitemap: line — Google has to find the sitemap on its own")
        elif not robots_has_sitemap:
            warnings.append(f"robots.txt names a sitemap that is not Zavia's ({robots_sitemap_url}) — it should point at zaviamagazine.dev/sitemap.xml")

    llms_status, _llms_text, llms_error = _fetch(LLMS_PATH)
    llms_found = llms_status == 200
    if llms_status is None:
        warnings.append(f"llms.txt could not be checked ({llms_error}) — could not confirm AI crawlers can read it")
    elif llms_status != 200:
        warnings.append(f"llms.txt returned HTTP {llms_status} — AI crawlers (ChatGPT, Claude, ...) cannot read what the site covers")

    ok = not failures
    parts = []
    if robots_found:
        parts.append("robots.txt OK" if not failures else "robots.txt has problems")
        if robots_has_sitemap:
            parts.append("it names the sitemap")
    else:
        parts.append("robots.txt missing" if robots_status == 404 else "robots.txt could not be checked")
    parts.append("llms.txt OK" if llms_found else "llms.txt missing")
    if failures:
        parts.append("FAIL: " + "; ".join(failures))
    if warnings:
        parts.append("Warning: " + "; ".join(warnings))
    return {
        "robots_status": robots_status,
        "robots_found": robots_found,
        "robots_has_sitemap": robots_has_sitemap,
        "robots_sitemap_url": robots_sitemap_url,
        "robots_blocks_all": robots["blocks_all"],
        "robots_disallowed": robots["disallowed"],
        "llms_status": llms_status,
        "llms_found": llms_found,
        "failures": failures,
        "warnings": warnings,
        "ok": ok,
        "summary": "; ".join(parts) + ".",
    }
