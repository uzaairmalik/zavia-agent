"""Tests for the SEO audit report logic — no network, no token needed.

Covers the parts that must never lie: the new vs fixed vs worse
classification in compare_reports(), the count deltas, and the
report round-trip (write a report, read the state back, compare a
second run against it).

Run it:
    python test_report.py

Exits 0 when every check passes, 1 otherwise.
"""

import os
import sys
import tempfile

sys.path.insert(0, ".")  # so `zavia_agent` imports from this folder

from zavia_agent.report import (
    compare_reports,
    latest_report,
    list_reports,
    load_previous_state,
    render_html,
    render_markdown,
    write_report,
)


def _issue(key, severity="fail"):
    return {
        "key": key,
        "severity": severity,
        "kind": key.split(":")[0],
        "subject": key,
        "detail": f"Detail for {key}.",
    }


def _audit(issues, counts, date="2026-11-17"):
    return {
        "ran_at": date + "T12:00:00+00:00",
        "date": date,
        "counts": counts,
        "issues": issues,
        "checks": {"sitemap": "sitemap ok", "articles": "articles ok", "junk": "junk ok"},
        "ok": all(i["severity"] != "fail" for i in issues),
    }


def _counts(**overrides):
    base = {
        "total_urls": 180,
        "article_urls": 66,
        "articles_checked": 66,
        "articles_failed": 0,
        "articles_warned": 0,
        "broken_links": 0,
        "duplicate_slugs": 0,
        "duplicate_titles": 0,
        "duplicate_descriptions": 0,
        "thin_articles": 0,
        "h1_issues": 0,
        "junk_articles": 0,
        "total_issues": 0,
        "fail_issues": 0,
        "warn_issues": 0,
    }
    base.update(overrides)
    return base


def test_first_run_has_no_comparison():
    current = _audit([], _counts())
    result = compare_reports(None, current)
    assert result["first_run"] is True
    assert result["new"] == [] and result["fixed"] == []
    assert result["ongoing"] == [] and result["worse"] == []
    assert result["improved"] == [] and result["counts"] == {}
    text = render_markdown(current, result)
    assert "first audit run" in text.lower()


def test_new_fixed_ongoing_classification():
    previous = _audit(
        [_issue("a:1"), _issue("a:2"), _issue("a:3")],
        _counts(total_issues=3, fail_issues=3),
        date="2026-11-10",
    )
    current = _audit(
        [_issue("a:2"), _issue("a:3"), _issue("a:4")],
        _counts(total_issues=3, fail_issues=3),
        date="2026-11-17",
    )
    result = compare_reports(previous, current)
    assert result["first_run"] is False
    assert [i["key"] for i in result["new"]] == ["a:4"]
    assert [i["key"] for i in result["fixed"]] == ["a:1"]
    assert [i["key"] for i in result["ongoing"]] == ["a:2", "a:3"]
    assert result["previous_date"] == "2026-11-10"


def test_worse_is_new_failures_only():
    previous = _audit([_issue("a:1")], _counts(total_issues=1, fail_issues=1))
    current = _audit(
        [_issue("a:1"), _issue("b:new-fail", "fail"), _issue("c:new-warn", "warn")],
        _counts(total_issues=3, fail_issues=2, warn_issues=1),
    )
    result = compare_reports(previous, current)
    # Both new issues appear under "new"...
    assert sorted(i["key"] for i in result["new"]) == ["b:new-fail", "c:new-warn"]
    # ...but only the new FAIL counts as "worse".
    assert [i["key"] for i in result["worse"]] == ["b:new-fail"]
    # Nothing was fixed, so nothing improved.
    assert result["improved"] == []


def test_improved_is_fixed_issues():
    previous = _audit(
        [_issue("a:1"), _issue("a:2")],
        _counts(total_issues=2, fail_issues=2),
    )
    current = _audit([_issue("a:2")], _counts(total_issues=1, fail_issues=1))
    result = compare_reports(previous, current)
    assert [i["key"] for i in result["improved"]] == ["a:1"]
    assert [i["key"] for i in result["fixed"]] == ["a:1"]
    assert result["worse"] == [] and result["new"] == []


def test_count_deltas_and_directions():
    previous = _audit([], _counts(total_issues=5, fail_issues=3, warn_issues=2, broken_links=1,
                                  duplicate_titles=1))
    current = _audit([], _counts(total_issues=4, fail_issues=3, warn_issues=1, broken_links=2,
                                 total_urls=182, duplicate_titles=0))
    result = compare_reports(previous, current)
    counts = result["counts"]
    assert counts["total_issues"] == {"previous": 5, "current": 4, "delta": -1, "direction": "better"}
    assert counts["fail_issues"]["direction"] == "same"
    assert counts["warn_issues"]["direction"] == "better"
    assert counts["broken_links"]["direction"] == "worse"
    # Duplicate titles are lower-is-better: fixing one is an improvement.
    assert counts["duplicate_titles"] == {"previous": 1, "current": 0, "delta": -1, "direction": "better"}
    # URL totals are info only — a change is neither better nor worse.
    assert counts["total_urls"]["direction"] == "info"
    assert counts["total_urls"]["delta"] == 2


def test_report_round_trip_and_second_run_comparison():
    with tempfile.TemporaryDirectory() as tmp:
        reports_dir = os.path.join(tmp, "reports")
        state_dir = os.path.join(tmp, "state")
        first = _audit(
            [_issue("a:1"), _issue("a:2")],
            _counts(total_issues=2, fail_issues=2),
            date="2026-11-10",
        )
        paths = write_report(first, compare_reports(None, first), reports_dir, state_dir)
        assert os.path.isfile(paths["md"]) and os.path.isfile(paths["html"])
        assert os.path.isfile(paths["state"])
        assert load_previous_state(state_dir)["date"] == "2026-11-10"

        second = _audit(
            [_issue("a:2"), _issue("a:3")],
            _counts(total_issues=2, fail_issues=2),
            date="2026-11-17",
        )
        comparison = compare_reports(load_previous_state(state_dir), second)
        assert [i["key"] for i in comparison["fixed"]] == ["a:1"]
        assert [i["key"] for i in comparison["new"]] == ["a:3"]
        write_report(second, comparison, reports_dir, state_dir)

        reports = list_reports(reports_dir)
        assert len(reports) == 2
        assert reports[0]["date"] == "2026-11-17"  # newest first
        assert latest_report(reports_dir)["date"] == "2026-11-17"
        md = render_markdown(second, comparison)
        assert "a:3" in md or "Detail for a:3" in md
        assert "2026-11-10" in md  # previous report date shown
        html = render_html(second, comparison)
        assert "Detail for a:3" in html
        assert "2026-11-10" in html


def test_empty_reports_dir():
    with tempfile.TemporaryDirectory() as tmp:
        assert list_reports(tmp) == []
        assert latest_report(tmp) is None
        assert load_previous_state(tmp) is None


def test_corrupt_state_is_first_run():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "last-audit.json"), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        assert load_previous_state(tmp) is None


TESTS = [
    ("First run has no comparison (and says so)", test_first_run_has_no_comparison),
    ("New vs fixed vs ongoing classification", test_new_fixed_ongoing_classification),
    ("Worse = new failures only", test_worse_is_new_failures_only),
    ("Improved = fixed issues", test_improved_is_fixed_issues),
    ("Count deltas and better/worse directions", test_count_deltas_and_directions),
    ("Report round-trip + second-run comparison", test_report_round_trip_and_second_run_comparison),
    ("Empty reports dir / no state", test_empty_reports_dir),
    ("Corrupt state file treated as first run", test_corrupt_state_is_first_run),
]


def main():
    print("Zavia agent — SEO report tests (no network, no token needed)")
    print("-" * 55)
    all_ok = True
    for name, fn in TESTS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — report and continue
            all_ok = False
            print(f"[FAIL] {name} — {type(exc).__name__}: {exc}")
        else:
            print(f"[PASS] {name}")
    print("-" * 55)
    print("ALL PASS" if all_ok else "SOME CHECKS FAILED — see above.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
