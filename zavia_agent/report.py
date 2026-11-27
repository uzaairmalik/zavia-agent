"""Repeatable SEO audit + progress report for Zavia.

This is Hisham Sarwar's Lesson 14 pattern (Learn Claude AI in 30 Days,
Module 7) applied to Zavia: an SEO audit that runs on a schedule, tracks
what changed since the last run, and writes a progress report. His setup
re-audits every Thursday 11:00 AM and emails a progress report
Mon/Wed/Fri; this module is Zavia's version of it:

- run_audit(): one audit run. It reuses the checks in seo.py (sitemap
  reachability + URL count, duplicate slugs, a sample of URLs for
  broken links, per-article title/canonical/meta-description/OG/
  preview-image/H1/word-count/structured-data/title-length/
  image-alt-text/broken-image checks, plus the junk-article finder) and returns one
  structured result.
- compare_reports(previous, current): new vs fixed vs ongoing issues,
  plus per-count deltas labelled better/worse/same.
- write_report(): saves a dated Markdown + HTML report under reports/
  and stores the audit as JSON in state/last-audit.json, so the next
  run can compare against it. The first run has no previous state —
  the report says so plainly instead of pretending to compare.

Every number in a report comes from a real check — no invented scores,
no rank data (Zavia has no Search Console connection here, so ranks
are not claimed). Email delivery is deliberately OUT of scope: sending
the report by email needs Uzair's Gmail approval flow (see README).

Nothing here needs a token — these are plain public-page checks.

Day 5 added a second, simpler exporter alongside the audit reports:
render_seo_report() / write_seo_report() turn one check_articles /
check_sitemap_seo result into a single Markdown file (used by
`--seo --all --report`). The two live side by side — the audit
reports compare runs over time under reports/, the exporter writes
one standalone file wherever you ask.
"""

import html
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from .client import ZaviaClient
from .seo import (TITLE_MAX_CHARS, TITLE_MIN_CHARS, THIN_CONTENT_WORDS,
                  check_articles, check_sitemap, find_junk_articles)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(REPO_ROOT, "reports")
STATE_DIR = os.path.join(REPO_ROOT, "state")
STATE_FILE = os.path.join(STATE_DIR, "last-audit.json")

# Counts where a LOWER number is better (used for better/worse labels).
LOWER_IS_BETTER = (
    "total_issues",
    "fail_issues",
    "warn_issues",
    "broken_links",
    "duplicate_slugs",
    "duplicate_titles",
    "duplicate_descriptions",
    "thin_articles",
    "h1_issues",
    "og_image_issues",
    "structured_data_issues",
    "title_issues",
    "image_alt_issues",
    "broken_image_issues",
    "junk_articles",
    "articles_failed",
    "articles_warned",
)
# Counts that are just reported (a change is neither good nor bad).
INFO_COUNTS = ("total_urls", "article_urls", "articles_checked")


def _slug_of(url):
    """Last path segment of a URL (the article slug for /article/<slug>)."""
    path = urlparse(url).path.strip("/")
    return path.split("/")[-1] if path else ""


def _issue(key, severity, kind, subject, detail):
    """One audit issue. `key` is stable across runs so two reports can
    be compared: the same problem next week has the same key."""
    return {
        "key": key,
        "severity": severity,  # "fail" (must fix) or "warn" (should fix)
        "kind": kind,
        "subject": subject,  # a slug or a URL
        "detail": detail,
    }


def run_audit(client=None, link_sample=10):
    """Run one full SEO audit of the live site.

    Returns a dict:
    {
      "ran_at": str (ISO timestamp, UTC),
      "date": str (YYYY-MM-DD, UTC),
      "counts": {total_urls, article_urls, articles_checked,
                 articles_failed, articles_warned, broken_links,
                 duplicate_slugs, duplicate_titles,
                 duplicate_descriptions, junk_articles, og_image_issues,
                 structured_data_issues, title_issues, image_alt_issues,
                 broken_image_issues,
                 total_issues, fail_issues, warn_issues},
      "issues": [issue dict, ...],   # fails first, then warnings
      "checks": {"sitemap": str, "articles": str, "junk": str},
      "ok": bool,  # True when no FAIL issue was found
    }
    """
    client = client or ZaviaClient()
    ran_at = datetime.now(timezone.utc)

    sitemap = check_sitemap(client, sample=link_sample)
    urls = client.get_sitemap_urls()
    article_urls = [u for u in urls if "/article/" in urlparse(u).path]
    slugs = [_slug_of(u) for u in article_urls if _slug_of(u)]
    articles = check_articles(client, slugs)
    junk = find_junk_articles(client)

    issues = []
    for slug in sitemap["duplicate_slugs"]:
        issues.append(_issue(
            f"duplicate-slug:{slug}", "fail", "duplicate-slug", slug,
            f"Slug '{slug}' appears more than once in the sitemap — Google gets confused by duplicates.",
        ))
    for url in sitemap["broken"]:
        issues.append(_issue(
            f"broken-link:{url}", "fail", "broken-link", url,
            f"URL did not return 200 in the sitemap sample: {url}",
        ))
    for result in articles["results"]:
        slug = result["slug"]
        if result["status"] == 404:
            issues.append(_issue(
                f"article:{slug}:not-found", "fail", "article-404", slug,
                f"Article '{slug}' is a 404 (in the sitemap but not found).",
            ))
            continue
        if not result["title_found"]:
            issues.append(_issue(
                f"article:{slug}:missing-title", "fail", "missing-title", slug,
                f"Article '{slug}' loaded but has no <title>.",
            ))
        if not result["canonical_found"]:
            issues.append(_issue(
                f"article:{slug}:missing-canonical", "fail", "missing-canonical", slug,
                f"Article '{slug}' loaded but has no canonical link.",
            ))
        if not result["description_found"]:
            issues.append(_issue(
                f"article:{slug}:missing-description", "warn", "missing-description", slug,
                f"Article '{slug}' has no meta description (Google will write its own snippet).",
            ))
        if not result["og_title_found"] or not result["og_description_found"]:
            issues.append(_issue(
                f"article:{slug}:missing-og", "warn", "missing-og", slug,
                f"Article '{slug}' is missing Open Graph tags (link previews will look plain).",
            ))
        # Social preview image (Day 10): no og:image, a broken og:image
        # URL, no alt text, or no twitter:card. All warnings — the
        # article loads, but its link preview is picture-less or small.
        if not result.get("og_image_found"):
            issues.append(_issue(
                f"article:{slug}:missing-og-image", "warn", "missing-og-image", slug,
                f"Article '{slug}' has no og:image (Facebook/WhatsApp link previews will show no picture).",
            ))
        elif result.get("og_image_ok") is False:
            # A real non-200 status (og_image_ok is None, not False,
            # when the fetch itself failed — an unknown, not a broken
            # picture — so that case makes no issue here).
            status = result.get("og_image_status")
            issues.append(_issue(
                f"article:{slug}:broken-og-image", "warn", "broken-og-image", slug,
                f"Article '{slug}' has an og:image that does not load (HTTP {status}) — link previews will show no picture.",
            ))
        if result.get("og_image_found") and not result.get("og_image_alt_found"):
            issues.append(_issue(
                f"article:{slug}:missing-og-image-alt", "warn", "missing-og-image-alt", slug,
                f"Article '{slug}' has an og:image but no og:image:alt (screen readers cannot describe the preview picture).",
            ))
        if not result.get("twitter_card_found"):
            issues.append(_issue(
                f"article:{slug}:missing-twitter-card", "warn", "missing-twitter-card", slug,
                f"Article '{slug}' has no twitter:card (X link previews fall back to a small summary card).",
            ))
        # Structured data (Day 11): no JSON-LD at all, a broken
        # JSON-LD block, no Article node, or an Article node missing
        # the fields Google needs for a rich result. All warnings —
        # the article loads and ranks, it just cannot appear as a
        # rich result.
        if not result.get("jsonld_found"):
            issues.append(_issue(
                f"article:{slug}:missing-jsonld", "warn", "missing-jsonld", slug,
                f"Article '{slug}' has no structured data (JSON-LD) — Google cannot show rich results for it.",
            ))
        else:
            if result.get("jsonld_invalid"):
                issues.append(_issue(
                    f"article:{slug}:broken-jsonld", "warn", "broken-jsonld", slug,
                    f"Article '{slug}' has {result['jsonld_invalid']} invalid JSON-LD block(s) — Google ignores broken structured data.",
                ))
            if not result.get("article_schema_found"):
                issues.append(_issue(
                    f"article:{slug}:missing-article-schema", "warn", "missing-article-schema", slug,
                    f"Article '{slug}' has JSON-LD but no Article structured data (Article / NewsArticle / BlogPosting) — Google cannot show it as a rich result.",
                ))
            else:
                missing_fields = [label for label, key in (
                    ("headline", "schema_headline_found"),
                    ("datePublished", "schema_date_published_found"),
                    ("author", "schema_author_found"),
                    ("image", "schema_image_found"),
                ) if not result.get(key)]
                if missing_fields:
                    issues.append(_issue(
                        f"article:{slug}:incomplete-article-schema", "warn", "incomplete-article-schema", slug,
                        f"Article '{slug}' structured data is missing {', '.join(missing_fields)} — Google's article rich result needs these.",
                    ))
        # Title length (Day 12): Google cuts titles off after about
        # 60 characters in search results; a title under 30 says
        # almost nothing about the article. Both are warnings — the
        # article loads, its title just shows cut off (or says too
        # little) in search results.
        title_length = result.get("title_length")
        if result.get("title_found") and isinstance(title_length, int):
            if title_length > TITLE_MAX_CHARS:
                issues.append(_issue(
                    f"article:{slug}:title-too-long", "warn", "title-too-long", slug,
                    f"Article '{slug}' has a long title ({title_length} chars) — Google cuts titles off after ~{TITLE_MAX_CHARS}.",
                ))
            elif title_length < TITLE_MIN_CHARS:
                issues.append(_issue(
                    f"article:{slug}:title-too-short", "warn", "title-too-short", slug,
                    f"Article '{slug}' has a very short title ({title_length} chars) — aim for {TITLE_MIN_CHARS}-{TITLE_MAX_CHARS}.",
                ))
        # Image alt text (Day 12): pictures in the article body with
        # no alt text. A warning — the article loads, but Google and
        # screen readers cannot tell what the pictures show.
        missing_alt = result.get("images_missing_alt")
        if isinstance(missing_alt, int) and missing_alt > 0:
            plural = "image" if missing_alt == 1 else "images"
            issues.append(_issue(
                f"article:{slug}:missing-image-alt", "warn", "missing-image-alt", slug,
                f"Article '{slug}' has {missing_alt} {plural} with no alt text — Google and screen readers cannot tell what they show.",
            ))
        # Broken in-body images (Day 14): a picture inside the article
        # body that does not load — readers see a broken picture. One
        # issue per article (listing every broken URL), so fixing the
        # pictures shows as "improved" next run. Pictures that could
        # not be checked (timeout, DNS, ...) make no issue here —
        # unknown is not broken (same rule as the og:image check).
        broken_images = result.get("image_broken") or []
        if broken_images:
            shown = ", ".join(f"{b['url']} (HTTP {b['status']})" for b in broken_images)
            plural = "image" if len(broken_images) == 1 else "images"
            verb = "does" if len(broken_images) == 1 else "do"
            issues.append(_issue(
                f"article:{slug}:broken-images", "warn", "broken-image", slug,
                f"Article '{slug}' has {len(broken_images)} {plural} that {verb} not load: {shown} — readers see a broken picture.",
            ))
        # Content checks (Day 8): H1 count and thin content. Both are
        # warnings — the article loads and ranks, it just ranks worse.
        h1_count = result.get("h1_count")
        if h1_count is not None and h1_count != 1:
            detail = (
                f"Article '{slug}' has no <h1> heading — Google uses the H1 to understand what the page is about."
                if h1_count == 0 else
                f"Article '{slug}' has {h1_count} <h1> headings — a page should have exactly one."
            )
            issues.append(_issue(
                f"article:{slug}:h1", "warn", "h1", slug, detail,
            ))
        word_count = result.get("word_count")
        if word_count is not None and word_count < THIN_CONTENT_WORDS:
            issues.append(_issue(
                f"article:{slug}:thin-content", "warn", "thin-content", slug,
                f"Article '{slug}' is thin content — only {word_count} words in the article body (aim for {THIN_CONTENT_WORDS}+).",
            ))
    for item in junk["junk"]:
        issues.append(_issue(
            f"junk-article:{item['slug']}", "warn", "junk-article", item["slug"],
            f"Test/junk article still live: {item['url']} ({item['marker']}). Delete it in the admin panel.",
        ))
    # Duplicate titles / descriptions (Day 7): one WARN issue per shared
    # text, so the next run can show it as fixed once the articles get
    # their own title/description. The key is the normalised text, so
    # the same duplicate next week has the same key.
    for group in articles.get("duplicate_titles") or []:
        slugs = ", ".join(group["slugs"])
        issues.append(_issue(
            f"duplicate-title:{group['key']}", "warn", "duplicate-title", slugs,
            f"Same title on {len(group['slugs'])} articles ({slugs}): \"{group['value']}\" — "
            "Google shows the same title for both; give each article its own title.",
        ))
    for group in articles.get("duplicate_descriptions") or []:
        slugs = ", ".join(group["slugs"])
        shown = group["value"]
        if len(shown) > 120:
            shown = shown[:120] + "..."
        issues.append(_issue(
            f"duplicate-description:{group['key']}", "warn", "duplicate-description", slugs,
            f"Same meta description on {len(group['slugs'])} articles ({slugs}): \"{shown}\" — "
            "Google shows the same snippet for both; write a unique description for each.",
        ))

    issues.sort(key=lambda i: (0 if i["severity"] == "fail" else 1, i["key"]))
    fail_count = sum(1 for i in issues if i["severity"] == "fail")
    warn_count = len(issues) - fail_count
    return {
        "ran_at": ran_at.isoformat(timespec="seconds"),
        "date": ran_at.strftime("%Y-%m-%d"),
        "counts": {
            "total_urls": sitemap["total_urls"],
            "article_urls": sitemap["article_urls"],
            "articles_checked": articles["checked"],
            "articles_failed": articles["failed"],
            "articles_warned": articles["warned"],
            "broken_links": len(sitemap["broken"]),
            "duplicate_slugs": len(sitemap["duplicate_slugs"]),
            "duplicate_titles": len(articles.get("duplicate_titles") or []),
            "duplicate_descriptions": len(articles.get("duplicate_descriptions") or []),
            "thin_articles": sum(1 for i in issues if i["kind"] == "thin-content"),
            "h1_issues": sum(1 for i in issues if i["kind"] == "h1"),
            "og_image_issues": sum(
                1 for i in issues
                if i["kind"] in ("missing-og-image", "broken-og-image",
                                 "missing-og-image-alt", "missing-twitter-card")
            ),
            "structured_data_issues": sum(
                1 for i in issues
                if i["kind"] in ("missing-jsonld", "broken-jsonld",
                                 "missing-article-schema", "incomplete-article-schema")
            ),
            "title_issues": sum(
                1 for i in issues
                if i["kind"] in ("title-too-long", "title-too-short")
            ),
            "image_alt_issues": sum(
                1 for i in issues
                if i["kind"] == "missing-image-alt"
            ),
            "broken_image_issues": sum(
                1 for i in issues
                if i["kind"] == "broken-image"
            ),
            "junk_articles": len(junk["junk"]),
            "total_issues": len(issues),
            "fail_issues": fail_count,
            "warn_issues": warn_count,
        },
        "issues": issues,
        "checks": {
            "sitemap": sitemap["summary"],
            "articles": articles["summary"],
            "junk": junk["summary"],
        },
        "ok": fail_count == 0,
    }


def compare_reports(previous, current):
    """Compare two audit results (as returned by run_audit).

    previous is None on the very first run — then there is nothing to
    compare with, and the report says so plainly.

    Returns a dict:
    {
      "first_run": bool,
      "previous_date": str|None,
      "new": [issue, ...],       # in current, not in previous
      "fixed": [issue, ...],     # in previous, not in current (improved)
      "ongoing": [issue, ...],   # in both
      "worse": [issue, ...],     # new FAIL issues (a new failure is worse)
      "improved": [issue, ...],  # fixed issues (a fix is an improvement)
      "counts": {metric: {"previous": int, "current": int, "delta": int,
                          "direction": "better"|"worse"|"same"|"info"}},
    }
    """
    if previous is None:
        return {
            "first_run": True,
            "previous_date": None,
            "new": [],
            "fixed": [],
            "ongoing": [],
            "worse": [],
            "improved": [],
            "counts": {},
        }
    prev_by_key = {i["key"]: i for i in previous.get("issues", [])}
    curr_by_key = {i["key"]: i for i in current.get("issues", [])}
    new = [curr_by_key[k] for k in sorted(set(curr_by_key) - set(prev_by_key))]
    fixed = [prev_by_key[k] for k in sorted(set(prev_by_key) - set(curr_by_key))]
    ongoing = [curr_by_key[k] for k in sorted(set(curr_by_key) & set(prev_by_key))]

    counts = {}
    prev_counts = previous.get("counts", {})
    curr_counts = current.get("counts", {})
    for metric in LOWER_IS_BETTER + INFO_COUNTS:
        before = prev_counts.get(metric, 0)
        now = curr_counts.get(metric, 0)
        delta = now - before
        if metric in INFO_COUNTS:
            direction = "info"
        elif delta == 0:
            direction = "same"
        elif (metric in LOWER_IS_BETTER and delta < 0):
            direction = "better"
        else:
            direction = "worse"
        counts[metric] = {
            "previous": before,
            "current": now,
            "delta": delta,
            "direction": direction,
        }
    return {
        "first_run": False,
        "previous_date": previous.get("date"),
        "new": new,
        "fixed": fixed,
        "ongoing": ongoing,
        "worse": [i for i in new if i["severity"] == "fail"],
        "improved": fixed,
        "counts": counts,
    }


# ------------------------------------------------------------------ state
def load_previous_state(state_dir=STATE_DIR):
    """Load the previous audit from state/last-audit.json.

    Returns the audit dict, or None when there is no previous run (or
    the file is unreadable — a corrupt state file is treated as no
    previous run, never a crash).
    """
    path = os.path.join(state_dir, "last-audit.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and "issues" in data and "counts" in data:
            return data
    except (OSError, ValueError):
        pass
    return None


def save_state(audit, state_dir=STATE_DIR):
    """Store this audit as the previous run for the next comparison."""
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "last-audit.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(audit, handle, ensure_ascii=False, indent=2)
    return path


# ------------------------------------------------------------------ reports
def _report_stem(audit):
    """Filename stem for a report, e.g. seo-report-2026-11-17-1206."""
    stamp = audit.get("ran_at", "")[:16].replace("T", "-").replace(":", "")
    if len(stamp) != 15:  # unexpected timestamp shape — fall back to the date
        stamp = audit.get("date", "unknown-date") + "-0000"
    return f"seo-report-{stamp}"


def _count_rows(audit):
    """(label, value) rows for the 'what was checked' table."""
    counts = audit["counts"]
    return [
        ("URLs in sitemap", counts["total_urls"]),
        ("Article URLs in sitemap", counts["article_urls"]),
        ("Articles checked (title, canonical, meta description, OG tags, preview image, structured data, title length, image alt text, broken images)", counts["articles_checked"]),
        ("Articles failed (404 / no title / no canonical)", counts["articles_failed"]),
        ("Articles with warnings (missing description, OG tags, or preview image)", counts["articles_warned"]),
        ("Broken links in the sitemap sample", counts["broken_links"]),
        ("Duplicate slugs in the sitemap", counts["duplicate_slugs"]),
        ("Duplicate titles shared by 2+ articles", counts["duplicate_titles"]),
        ("Duplicate meta descriptions shared by 2+ articles", counts["duplicate_descriptions"]),
        ("Thin articles (under 300 words in the body)", counts["thin_articles"]),
        ("Articles with a wrong H1 count (not exactly one)", counts["h1_issues"]),
        ("Social preview image problems (no og:image / broken og:image / no og:image:alt / no twitter:card)", counts["og_image_issues"]),
        ("Structured data problems (no JSON-LD / broken JSON-LD / no Article schema / missing headline, datePublished, author or image)", counts["structured_data_issues"]),
        ("Title problems (over 60 chars — Google cuts them off — or under 30)", counts["title_issues"]),
        ("Articles with images missing alt text", counts["image_alt_issues"]),
        ("Articles with pictures that do not load (broken image in the body)", counts["broken_image_issues"]),
        ("Junk / test articles still live", counts["junk_articles"]),
        ("Total issues", counts["total_issues"]),
        ("— of which FAIL (must fix)", counts["fail_issues"]),
        ("— of which warnings (should fix)", counts["warn_issues"]),
    ]


def _delta_text(entry):
    """'+3', '-2' or '0' for a count comparison entry."""
    delta = entry["delta"]
    if delta > 0:
        return f"+{delta}"
    return str(delta)


def render_markdown(audit, comparison):
    """Render the audit + comparison as Markdown text."""
    counts = audit["counts"]
    lines = [
        f"# Zavia SEO audit report — {audit['date']}",
        "",
        f"Ran at {audit['ran_at']} (UTC). Site: https://zaviamagazine.dev",
        "",
        "Every number below comes from a real check of the live site — "
        "no invented scores. No rank data: Zavia has no Search Console "
        "connection here, so keyword ranks are not claimed.",
        "",
        "## What was checked",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for label, value in _count_rows(audit):
        lines.append(f"| {label} | {value} |")
    lines += [
        "",
        "Sitemap: " + audit["checks"]["sitemap"],
        "",
        "Articles: " + audit["checks"]["articles"],
        "",
        "Junk: " + audit["checks"]["junk"],
        "",
        "## Issues found",
        "",
    ]
    if not audit["issues"]:
        lines.append("No issues found. Clean!")
    else:
        for issue in audit["issues"]:
            mark = "FAIL" if issue["severity"] == "fail" else "WARN"
            lines.append(f"- **[{mark}]** {issue['detail']}")
    lines += ["", "## Changes since the last report", ""]
    if comparison["first_run"]:
        lines.append(
            "This is the first audit run — there is no previous report to "
            "compare with, so no 'improved / worse / new' section yet. "
            "Run the audit again later and this section will fill in."
        )
    else:
        lines.append(f"Previous report: {comparison['previous_date']}.")
        lines.append("")
        lines.append(f"### Improved (fixed since last time): {len(comparison['improved'])}")
        lines.append("")
        if comparison["improved"]:
            for issue in comparison["improved"]:
                lines.append(f"- {issue['detail']}")
        else:
            lines.append("Nothing fixed since the last report.")
        lines.append("")
        lines.append(f"### Worse (new failures): {len(comparison['worse'])}")
        lines.append("")
        if comparison["worse"]:
            for issue in comparison["worse"]:
                lines.append(f"- {issue['detail']}")
        else:
            lines.append("No new failures since the last report.")
        lines.append("")
        lines.append(f"### New issues: {len(comparison['new'])}")
        lines.append("")
        if comparison["new"]:
            for issue in comparison["new"]:
                mark = "FAIL" if issue["severity"] == "fail" else "WARN"
                lines.append(f"- **[{mark}]** {issue['detail']}")
        else:
            lines.append("No new issues since the last report.")
        lines.append("")
        lines.append(f"Ongoing issues (present in both reports): {len(comparison['ongoing'])}.")
        lines.append("")
        lines.append("### Counts vs last time")
        lines.append("")
        lines.append("| Count | Before | Now | Change |")
        lines.append("|---|---|---|---|")
        for metric in LOWER_IS_BETTER + INFO_COUNTS:
            entry = comparison["counts"][metric]
            label = metric.replace("_", " ")
            direction = entry["direction"]
            arrow = {"better": " (better)", "worse": " (worse)", "same": "", "info": ""}[direction]
            lines.append(f"| {label} | {entry['previous']} | {entry['current']} | {_delta_text(entry)}{arrow} |")
    lines.append("")
    if audit["ok"]:
        verdict = "no FAIL issues — good."
    else:
        verdict = f"{counts['fail_issues']} FAIL issue(s) need fixing."
    lines.append(f"Verdict: {verdict}")
    lines.append("")
    return "\n".join(lines)


def render_html(audit, comparison):
    """Render the audit + comparison as a small standalone HTML page."""
    counts = audit["counts"]
    esc = html.escape

    def issue_items(issues):
        if not issues:
            return "<p>None.</p>"
        items = []
        for issue in issues:
            cls = "fail" if issue["severity"] == "fail" else "warn"
            mark = "FAIL" if issue["severity"] == "fail" else "WARN"
            items.append(f'<li class="{cls}"><strong>[{mark}]</strong> {esc(issue["detail"])}</li>')
        return "<ul>" + "".join(items) + "</ul>"

    def count_table():
        rows = "".join(
            f"<tr><td>{esc(label)}</td><td>{value}</td></tr>"
            for label, value in _count_rows(audit)
        )
        return f"<table><tr><th>Check</th><th>Result</th></tr>{rows}</table>"

    if comparison["first_run"]:
        changes = (
            "<p>This is the first audit run — there is no previous report to "
            "compare with, so no 'improved / worse / new' section yet. Run "
            "the audit again later and this section will fill in.</p>"
        )
    else:
        delta_rows = ""
        for metric in LOWER_IS_BETTER + INFO_COUNTS:
            entry = comparison["counts"][metric]
            direction = entry["direction"]
            arrow = {"better": " (better)", "worse": " (worse)", "same": "", "info": ""}[direction]
            delta_rows += (
                f"<tr><td>{esc(metric.replace('_', ' '))}</td>"
                f"<td>{entry['previous']}</td><td>{entry['current']}</td>"
                f"<td>{_delta_text(entry)}{arrow}</td></tr>"
            )
        changes = f"""
<h2>Changes since the last report ({esc(str(comparison['previous_date']))})</h2>
<h3>Improved (fixed since last time): {len(comparison['improved'])}</h3>
{issue_items(comparison['improved']) if comparison['improved'] else '<p>Nothing fixed since the last report.</p>'}
<h3>Worse (new failures): {len(comparison['worse'])}</h3>
{issue_items(comparison['worse']) if comparison['worse'] else '<p>No new failures since the last report.</p>'}
<h3>New issues: {len(comparison['new'])}</h3>
{issue_items(comparison['new']) if comparison['new'] else '<p>No new issues since the last report.</p>'}
<p>Ongoing issues (present in both reports): {len(comparison['ongoing'])}.</p>
<h3>Counts vs last time</h3>
<table><tr><th>Count</th><th>Before</th><th>Now</th><th>Change</th></tr>{delta_rows}</table>
"""
    issues_html = "<p>No issues found. Clean!</p>" if not audit["issues"] else issue_items(audit["issues"])
    verdict = "no FAIL issues — good." if audit["ok"] else f"{counts['fail_issues']} FAIL issue(s) need fixing."
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zavia SEO audit report — {esc(audit['date'])}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 760px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; background: #fafaf7; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ border: 1px solid #ddd; padding: .4rem .6rem; text-align: left; }}
  th {{ background: #f0f0ea; }}
  .fail {{ color: #b00020; }} .warn {{ color: #8a6d00; }}
  .note {{ color: #555; font-size: .9rem; }}
</style>
</head>
<body>
<h1>Zavia SEO audit report — {esc(audit['date'])}</h1>
<p class="note">Ran at {esc(audit['ran_at'])} (UTC). Site: <a href="https://zaviamagazine.dev">zaviamagazine.dev</a></p>
<p class="note">Every number below comes from a real check of the live site — no invented scores. No rank data: Zavia has no Search Console connection here, so keyword ranks are not claimed.</p>
<h2>What was checked</h2>
{count_table()}
<p>Sitemap: {esc(audit['checks']['sitemap'])}</p>
<p>Articles: {esc(audit['checks']['articles'])}</p>
<p>Junk: {esc(audit['checks']['junk'])}</p>
<h2>Issues found</h2>
{issues_html}
{changes if not comparison['first_run'] else '<h2>Changes since the last report</h2>' + changes}
<p><strong>Verdict: {esc(verdict)}</strong></p>
</body>
</html>
"""


def write_report(audit, comparison, reports_dir=REPORTS_DIR, state_dir=STATE_DIR):
    """Write the dated Markdown + HTML reports and save the state.

    Returns {"md": path, "html": path, "state": path}.
    """
    os.makedirs(reports_dir, exist_ok=True)
    stem = _report_stem(audit)
    md_path = os.path.join(reports_dir, stem + ".md")
    html_path = os.path.join(reports_dir, stem + ".html")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(audit, comparison))
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write(render_html(audit, comparison))
    state_path = save_state(audit, state_dir)
    return {"md": md_path, "html": html_path, "state": state_path}


def list_reports(reports_dir=REPORTS_DIR):
    """List saved reports, newest first.

    Returns a list of {"stem", "date", "md", "html"} dicts (filenames,
    not full paths). An .md without a matching .html (or vice versa)
    is still listed with the missing side as None.
    """
    try:
        names = os.listdir(reports_dir)
    except OSError:
        return []
    stems = sorted(
        {name[:-3] for name in names if name.endswith(".md")}
        | {name[:-5] for name in names if name.endswith(".html")},
        reverse=True,
    )
    reports = []
    for stem in stems:
        md_name = stem + ".md"
        html_name = stem + ".html"
        date = stem.replace("seo-report-", "")[:10] if stem.startswith("seo-report-") else stem
        reports.append({
            "stem": stem,
            "date": date,
            "md": md_name if md_name in names else None,
            "html": html_name if html_name in names else None,
        })
    return reports


def latest_report(reports_dir=REPORTS_DIR):
    """The newest saved report (same shape as list_reports entries), or None."""
    reports = list_reports(reports_dir)
    return reports[0] if reports else None


def latest_report_info(reports_dir=REPORTS_DIR, state_dir=STATE_DIR):
    """What the dashboard shows about the latest report.

    Returns None when no report exists yet, else a dict with the
    report date, the HTML filename, and the headline counts (from the
    saved state, which always matches the latest report).
    """
    report = latest_report(reports_dir)
    if report is None:
        return None
    info = {"date": report["date"], "html": report["html"], "md": report["md"],
            "counts": None, "ok": None}
    state = load_previous_state(state_dir)
    if state is not None:
        info["counts"] = state.get("counts")
        info["ok"] = state.get("ok")
    return info


# ---------------------------------------------------------------------------
# Single-file SEO report export (Day 5)
#
# After a bulk SEO check or a full sitemap audit, Uzair often wants a
# record he can look at later (or send to someone) instead of scrolling
# back through terminal output. This turns a report dict from seo.py
# (check_articles / check_sitemap_seo) into a readable Markdown file.
# Nothing here touches the network — it only formats results that were
# already collected. The file is written next to where the agent runs,
# unless a path is given.
# ---------------------------------------------------------------------------


def default_report_path():
    """Default file name, e.g. zavia-seo-report-2026-11-19.md (today's date)."""
    today = datetime.now(timezone.utc).date().isoformat()
    return f"zavia-seo-report-{today}.md"


def _article_lines(result):
    """One bullet line + detail lines for a single article result."""
    lines = [f"- **{result['slug']}** — {result.get('url') or '(no URL)'}"]
    status = result.get("status")
    lines.append(f"  - Status: HTTP {status}" if status else "  - Status: could not be checked")
    if result.get("title"):
        lines.append(f"  - Title: {result['title']}")
    if result.get("canonical"):
        lines.append(f"  - Canonical: {result['canonical']}")
    if result.get("description"):
        length = result.get("description_length")
        suffix = f" ({length} chars)" if isinstance(length, int) and length else ""
        lines.append(f"  - Meta description{suffix}: {result['description']}")
    if result.get("warnings"):
        for warning in result["warnings"]:
            lines.append(f"  - Warning: {warning}")
    if not result["ok"]:
        lines.append(f"  - Problem: {result['summary']}")
    return lines


def render_seo_report(report, site="https://zaviamagazine.dev"):
    """Render a check_articles / check_sitemap_seo report dict as Markdown."""
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        f"# Zavia SEO report — {today}",
        "",
        f"Site: {site}",
        "",
        report.get("summary", ""),
        "",
    ]
    results = report.get("results") or []
    failed = [r for r in results if not r["ok"]]
    warned = [r for r in results if r["ok"] and r["warnings"]]
    clean = [r for r in results if r["ok"] and not r["warnings"]]
    duplicate_titles = report.get("duplicate_titles") or []
    duplicate_descriptions = report.get("duplicate_descriptions") or []

    if duplicate_titles or duplicate_descriptions:
        lines.append("## Duplicates")
        lines.append("")
        lines.append("Same title or meta description on more than one article — "
                     "Google shows the same title/snippet for both, so give each article its own.")
        lines.append("")
        for group in duplicate_titles:
            lines.append(f"- Same title on {', '.join(group['slugs'])}: \"{group['value']}\"")
        for group in duplicate_descriptions:
            shown = group["value"]
            if len(shown) > 160:
                shown = shown[:160] + "..."
            lines.append(f"- Same meta description on {', '.join(group['slugs'])}: \"{shown}\"")
        lines.append("")

    if failed:
        lines.append(f"## Failed ({len(failed)})")
        lines.append("")
        lines.append("Fix these first — a missing title, canonical, or a 404.")
        lines.append("")
        for result in failed:
            lines.extend(_article_lines(result))
        lines.append("")
    if warned:
        lines.append(f"## Warnings ({len(warned)})")
        lines.append("")
        lines.append("These articles load, but look worse in search results and link previews.")
        lines.append("")
        for result in warned:
            lines.extend(_article_lines(result))
        lines.append("")
    lines.append(f"## OK ({len(clean)})")
    lines.append("")
    if clean:
        # Compact list — one line per article would drown the report.
        lines.append(", ".join("`" + r["slug"] + "`" for r in clean))
    else:
        lines.append("None — every checked article had a problem.")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_seo_report(report, path=None, site="https://zaviamagazine.dev"):
    """Write the report to `path` (default: zavia-seo-report-<today>.md).

    Returns the path that was written.
    """
    path = path or default_report_path()
    text = render_seo_report(report, site=site)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return os.path.abspath(path)
