"""Site health checks for Zavia.

Checks: homepage returns 200 with article cards, sitemap is reachable
and lists URLs, and the API answers at all.

Zavia's database (Heroku Postgres) drops for ~5-15 minutes at a time and
then recovers, so a single failed request does NOT mean the site is down.
Every check retries up to 3 times, 20 seconds apart, and reports
"DB possibly down, retry later" instead of failing hard when the site
stays unreachable.
"""

import time

from .client import ZaviaClient, ZaviaError

HEALTH_RETRIES = 3
HEALTH_DELAY = 20  # seconds between attempts (DB flaps last minutes)


def _try(fn, retries=HEALTH_RETRIES, delay=HEALTH_DELAY):
    """Run fn(); retry on ZaviaError up to `retries` times.

    Returns (result_dict, error_message_or_None).
    """
    last_error = None
    for _ in range(retries):
        try:
            return fn(), None
        except ZaviaError as exc:
            last_error = str(exc)
            time.sleep(delay)
    return None, last_error


def check_site(client=None, retries=HEALTH_RETRIES, delay=HEALTH_DELAY):
    """Run all health checks.

    Returns a dict:
    {
      "homepage": {"ok": bool, "status": int|None, "detail": str},
      "sitemap":  {"ok": bool, "count": int, "detail": str},
      "api":      {"ok": bool, "status": int|None, "detail": str},
      "ok": bool,            # True only when every check passed
      "verdict": str,        # one-line human summary
    }
    """
    client = client or ZaviaClient()
    result = {"homepage": {}, "sitemap": {}, "api": {}, "ok": False, "verdict": ""}

    def _homepage():
        info = client.get_homepage_status()
        if info["status"] != 200:
            raise ZaviaError(f"Homepage returned HTTP {info['status']}.")
        if not info["has_articles"]:
            raise ZaviaError("Homepage loaded but no article cards were found.")
        return info

    info, err = _try(_homepage, retries, delay)
    if info:
        result["homepage"] = {"ok": True, "status": info["status"], "detail": f"Homepage OK ({info['size_bytes']} bytes, article cards present)."}
    else:
        result["homepage"] = {"ok": False, "status": None, "detail": err or "Homepage unreachable."}

    def _sitemap():
        urls = client.get_sitemap_urls()
        if not urls:
            raise ZaviaError("Sitemap loaded but listed no URLs.")
        return urls

    urls, err = _try(_sitemap, retries, delay)
    if urls is not None:
        result["sitemap"] = {"ok": True, "count": len(urls), "detail": f"Sitemap OK, {len(urls)} URLs listed."}
    else:
        result["sitemap"] = {"ok": False, "count": 0, "detail": err or "Sitemap unreachable."}

    def _api():
        # Any HTTP answer (even 401/405) means the API server is up.
        # A connection error means it is not reachable at all.
        resp = client.session.get(client.base_url + "/api/cron/publish", timeout=client.timeout)
        return resp.status_code

    status, err = _try(_api, retries, delay)
    if status is not None:
        result["api"] = {"ok": True, "status": status, "detail": f"API answered (HTTP {status}; auth is expected without a token)."}
    else:
        result["api"] = {"ok": False, "status": None, "detail": err or "API unreachable."}

    failed = [name for name in ("homepage", "sitemap", "api") if not result[name]["ok"]]
    if not failed:
        result["ok"] = True
        result["verdict"] = "All good — Zavia is up: homepage, sitemap and API all answered."
    elif len(failed) == 3:
        result["verdict"] = (
            "Zavia looks down right now (homepage, sitemap and API all failed "
            f"after {retries} tries). The database drops for a few minutes at a "
            "time — wait 10-15 minutes and run the check again before worrying."
        )
    else:
        result["verdict"] = (
            "Partly up — failed: " + ", ".join(failed) + ". "
            "The database may be having one of its short drops; retry in a few minutes."
        )
    return result


def verdict_line(result):
    """One-line summary for the terminal."""
    return result["verdict"]
