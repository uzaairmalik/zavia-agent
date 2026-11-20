"""Save an SEO check report to a Markdown file.

After a bulk SEO check or a full sitemap audit, Uzair often wants a
record he can look at later (or send to someone) instead of scrolling
back through terminal output. This turns a report dict from seo.py
(check_articles / check_sitemap_seo) into a readable Markdown file.

Nothing here touches the network — it only formats results that were
already collected. The file is written next to where the agent runs,
unless a path is given.
"""

import datetime
import os


def default_report_path():
    """Default file name, e.g. zavia-seo-report-2026-11-19.md (today's date)."""
    today = datetime.date.today().isoformat()
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
    today = datetime.date.today().isoformat()
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
