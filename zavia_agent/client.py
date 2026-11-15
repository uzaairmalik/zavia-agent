"""HTTP client for the Zavia blog API.

Real Zavia endpoints (do not invent others):
- Publish article: POST multipart/form-data /api/cron/publish
  field "article" = JSON string {title, slug, excerpt, content, category,
  language ("en"|"ur"), tags, coverImage?, publishedAt?}
  Optional file field "cover" for a cover-image upload.
- Update article: POST multipart/form-data /api/cron/update
  field "update" = JSON string {slug, title?, excerpt?, content?,
  category?, language?, tags?, faq?}
- Both need: Authorization: Bearer <ZAVIA_PUBLISH_TOKEN>

Known Zavia quirks (handled here):
- The publish API returns 500 if `tags` contains an EXISTING tag
  (connectOrCreate bug). Use brand-new tags or an empty list. The error
  message below says so plainly instead of crashing.
- Heroku Postgres drops for ~5-15 minutes at a time, so every request
  retries up to 3 times on connection errors / 5xx responses.
- Always send the JSON as a multipart part (not url-encoded, not pasted
  into a shell command). requests only sends multipart when a `files`
  argument is present, so the JSON field is passed as a file-like part
  with no filename: files={"article": (None, json_string)}.
"""

import json
import os
import time
import xml.etree.ElementTree as ET

import requests

DEFAULT_BASE_URL = "https://zavia-magazine.vercel.app"
MAX_ATTEMPTS = 3
# Back-off between retries (seconds). Health checks use a longer delay
# (see health.py) because the DB can be down for minutes.
RETRY_BACKOFF = (2, 5)


class ZaviaError(Exception):
    """Anything that goes wrong talking to Zavia, in plain words."""


def load_dotenv(path=None):
    """Load KEY=VALUE lines from a .env file into os.environ.

    Only sets a variable if it is not already set. Silently does nothing
    when the file is missing. No third-party dependency needed.
    """
    candidates = []
    if path:
        candidates.append(path)
    candidates.append(os.path.join(os.getcwd(), ".env"))
    candidates.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    for candidate in candidates:
        if not candidate or not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError:
            pass
        return True
    return False


class ZaviaClient:
    """Talk to the Zavia site. Token comes from the environment only."""

    def __init__(self, base_url=None, token=None, timeout=30):
        load_dotenv()
        self.base_url = (base_url or os.environ.get("ZAVIA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.token = token or os.environ.get("ZAVIA_PUBLISH_TOKEN") or ""
        self.token = self.token.strip()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "zavia-agent/1.0"})

    # -- helpers ---------------------------------------------------------
    def _need_token(self):
        if not self.token:
            raise ZaviaError(
                "No publish token found. Copy .env.example to .env and set "
                "ZAVIA_PUBLISH_TOKEN to your Zavia publish token."
            )

    def _is_retryable(self, status):
        return status is None or status >= 500

    def _request(self, method, path, **kwargs):
        """One request with retries on connection errors and 5xx."""
        url = self.base_url + path
        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                last_error = ZaviaError(f"Could not reach {url} ({exc}). Zavia may be down; try again in a bit.")
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF[attempt - 1])
                    continue
                raise last_error
            if self._is_retryable(resp.status_code) and attempt < MAX_ATTEMPTS:
                last_error = ZaviaError(f"Zavia returned HTTP {resp.status_code} for {path}.")
                time.sleep(RETRY_BACKOFF[attempt - 1])
                continue
            return resp
        raise last_error or ZaviaError(f"Request to {path} failed.")

    def _post_multipart(self, path, field_name, payload, cover_path=None):
        """POST a JSON payload as a multipart form-data part.

        The JSON string is sent as a file-like part with no filename so
        requests uses multipart/form-data (a plain `data=` dict would be
        sent url-encoded, which the API does not accept).
        """
        self._need_token()
        files = {field_name: (None, json.dumps(payload, ensure_ascii=False))}
        open_file = None
        try:
            if cover_path:
                open_file = open(cover_path, "rb")
                files["cover"] = (os.path.basename(cover_path), open_file)
            resp = self._request(
                "POST",
                path,
                files=files,
                headers={"Authorization": f"Bearer {self.token}"},
            )
        finally:
            if open_file is not None:
                open_file.close()
        return self._check(resp, path, field_name)

    def _check(self, resp, path, field_name):
        """Turn an API response into a dict, or a clear error."""
        if resp.status_code in (200, 201):
            try:
                return resp.json()
            except ValueError:
                return {"ok": True, "status": resp.status_code, "raw": resp.text[:500]}
        detail = resp.text[:500].strip()
        if resp.status_code == 401:
            raise ZaviaError(
                "Unauthorized (401). Your ZAVIA_PUBLISH_TOKEN is wrong or "
                "expired. Check the token in your .env file."
            )
        if resp.status_code == 500 and field_name == "article":
            raise ZaviaError(
                "Zavia returned 500 while publishing. Known cause: the "
                "`tags` list contains a tag that already exists on Zavia "
                "(a bug in the publish API). Try again with brand-new tags "
                "or an empty tags list. Server said: " + (detail or "(no detail)")
            )
        raise ZaviaError(f"Zavia returned HTTP {resp.status_code} for {path}. Server said: {detail or '(no detail)'}")

    # -- public API ------------------------------------------------------
    def publish_article(self, article, cover_path=None):
        """Publish a new article.

        article: dict with title, slug, excerpt, content (markdown),
        category, language ("en" or "ur"), tags (list). Optional:
        coverImage (URL), publishedAt (ISO date).
        Returns the API response dict.
        """
        required = ("title", "slug", "content")
        missing = [key for key in required if not article.get(key)]
        if missing:
            raise ZaviaError(f"Cannot publish: missing {', '.join(missing)}.")
        if article.get("language") not in (None, "en", "ur"):
            raise ZaviaError('language must be "en" or "ur".')
        article = dict(article)
        article.setdefault("language", "en")
        article.setdefault("tags", [])
        article.setdefault("category", "general")
        article.setdefault("excerpt", "")
        return self._post_multipart("/api/cron/publish", "article", article, cover_path)

    def update_article(self, update):
        """Update an existing article.

        update: dict with slug (required) plus any of title, excerpt,
        content, category, language, tags, faq.
        Returns the API response dict.
        """
        if not update.get("slug"):
            raise ZaviaError("Cannot update: `slug` is required.")
        return self._post_multipart("/api/cron/update", "update", dict(update))

    def get_sitemap_urls(self):
        """Return every URL listed in /sitemap.xml (list of strings)."""
        resp = self._request("GET", "/sitemap.xml")
        if resp.status_code != 200:
            raise ZaviaError(f"Sitemap returned HTTP {resp.status_code}.")
        urls = []
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            raise ZaviaError(f"Could not parse sitemap.xml ({exc}).")
        for elem in root.iter():
            if elem.tag.endswith("}loc") or elem.tag == "loc":
                if elem.text:
                    urls.append(elem.text.strip())
        return urls

    def get_homepage_status(self):
        """Return {status, has_articles, size} for the homepage."""
        resp = self._request("GET", "/")
        has_articles = "/article/" in resp.text if resp.status_code == 200 else False
        return {
            "status": resp.status_code,
            "has_articles": has_articles,
            "size_bytes": len(resp.content),
        }

    def get_article(self, slug):
        """Fetch one article page. Returns {slug, url, status, title, html}."""
        slug = slug.strip().strip("/")
        url = f"{self.base_url}/article/{slug}"
        resp = self._request("GET", f"/article/{slug}")
        title = ""
        if resp.status_code == 200:
            start = resp.text.find("<title>")
            end = resp.text.find("</title>", start)
            if start != -1 and end != -1:
                title = resp.text[start + 7:end].strip()
        return {
            "slug": slug,
            "url": url,
            "status": resp.status_code,
            "title": title,
            "html": resp.text,
        }
