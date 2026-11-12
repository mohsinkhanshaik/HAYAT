"""Every medium or high static-analysis finding in HAYAT's own code has a recorded review, or the suite fails."""
from __future__ import annotations

import pytest

from app.code_defense import bandit_findings
from app.code_review import REVIEWS, UNREVIEWED_RULES, Review, review_of, summary
from app.config import PROJECT_ROOT


@pytest.fixture(scope="module")
def found():
    pytest.importorskip("bandit")
    return bandit_findings(PROJECT_ROOT, [name for name in ("app", "scripts", "main.py") if (PROJECT_ROOT / name).exists()])


def test_no_medium_or_high_finding_is_unreviewed(found):
    serious = [item for item in found if item["severity"] in ("MEDIUM", "HIGH")]
    unreviewed = [f"{item['rule']} {item['path']}:{item['line']}  {item['evidence'].splitlines()[0][:100] if item['evidence'] else ''}"
                  for item in serious if review_of(item) is None]
    assert not unreviewed, ("New medium/high findings with no recorded review. Read each one, fix it or add an entry "
                            "with the reason to app/code_review.py:\n" + "\n".join(unreviewed))
    assert not [item for item in serious if item["severity"] == "HIGH"], "a high-severity finding is in the source"
    assert all(review_of(item).depth == "line" for item in serious)      # mediums are read, not waved through as a pattern


def test_the_summary_says_how_deep_the_review_went_and_what_is_left_for_a_person(found):
    report = summary(found)
    assert report["findings"] == len(found) == report["read_line_by_line"] + report["reviewed_as_a_pattern"] + report["unreviewed"]
    assert report["read_line_by_line"] >= 70 and report["reviewed_as_a_pattern"] >= 250
    # The asserts are deliberately left for a person, and the summary says why.
    assert set(report["unreviewed_by_rule"]) == {"B101"} == set(UNREVIEWED_RULES) and report["unreviewed"] >= 1
    assert "disappears under python -O" in report["needs_a_person"]["B101"]
    assert "not a security audit" in report["scope"]


def test_a_review_covers_only_what_it_names(found):
    sql = next(row for row in found if row["rule"] == "B608" and row["path"] == "app/memory/database.py")
    assert review_of(sql).verdict == "safe by construction"
    assert review_of({**sql, "path": "app/brand_new_module.py"}) is None        # a new file is not covered by old reading
    assert review_of({**sql, "rule": "B602"}) is None                           # nor is a different rule in a reviewed file
    assert review_of({"rule": "B603", "path": "app/anything/at_all.py"}).depth == "pattern"
    assert review_of({"rule": "B101", "path": "app/x.py"}) is None
    csi = next(row for row in found if row["rule"] == "B104" and row["path"] == "app/csi.py")
    assert review_of({**csi, "path": "app\\csi.py"}).verdict == "deliberate"   # Windows separators match too
    narrow = Review("B999", ("*",), "deliberate", "only this call", contains="specific_call(")
    assert narrow.contains and not ("other()" in narrow.contains)
    for review in REVIEWS:
        assert review.verdict in ("false positive", "deliberate", "safe by construction")
        assert len(review.reason) > 30 and review.depth in ("line", "pattern") and review.paths


def test_the_two_hardenings_the_review_led_to():
    from app.launcher import ollama_answers
    from app.network import _get_local_json, _get_local_text

    # A file: or custom scheme is refused before anything is opened.
    assert ollama_answers("file:///C:/Windows/win.ini") is False
    for read in (_get_local_json, _get_local_text):
        with pytest.raises(ValueError, match="http"):
            read("file:///C:/Windows/win.ini")
        with pytest.raises(ValueError, match="http"):
            read("ftp://192.168.1.1/status")


def test_a_column_name_that_is_not_an_identifier_is_refused(tmp_path):
    from app.intelligence.discovery import DiscoveryStore

    store = DiscoveryStore(tmp_path / "discovery.db")
    with store.db() as db:
        db.execute("INSERT INTO sources (url, title, domain, found_by, found_at) VALUES ('https://example.org/a','t','example.org','test','now')")
        identity = db.execute("SELECT id FROM sources").fetchone()[0]
    store.update(identity, status="rejected", reason="because")              # ordinary columns still work
    with store.db() as db:
        assert tuple(db.execute("SELECT status, reason FROM sources WHERE id=?", (identity,)).fetchone()) == ("rejected", "because")
    with pytest.raises(ValueError, match="column"):
        store.update(identity, **{"status='x', title": "injected"})
    with store.db() as db:                                                     # nothing was changed by the refused call
        assert db.execute("SELECT title FROM sources WHERE id=?", (identity,)).fetchone()[0] == "t"


def test_hayat_s_own_findings_carry_their_review_and_another_project_s_do_not(tmp_path, found):
    from contextlib import closing

    from app.code_defense import CodeDefense

    defense = CodeDefense(tmp_path / "cd.db")
    known = next(row for row in found if row["rule"] == "B608" and row["path"] == "app/memory/database.py")
    with closing(defense._db()) as db, db:
        for index, (project, rule, path) in enumerate(((str(PROJECT_ROOT), "B608", "app/memory/database.py"),
                                                       (str(PROJECT_ROOT), "B101", "app/x.py"),
                                                       (str(PROJECT_ROOT), "B603", "app/tools/git.py"),
                                                       (str(tmp_path), "B608", "app/memory/database.py"))):
            db.execute("INSERT INTO findings(fingerprint,project,tool,rule,title,path,line,severity,confidence,priority,"
                       "evidence,more,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (f"f{index}", project, "bandit", rule, "t", path, 1, "MEDIUM", "HIGH", 5,
                        known["evidence"] if index in (0, 3) else "code", "", "d", "d"))
    rows = {row["fingerprint"]: row for row in defense.findings()}
    assert rows["f0"]["review"]["verdict"] == "safe by construction" and rows["f0"]["review"]["depth"] == "line"
    assert rows["f2"]["review"]["depth"] == "pattern"
    assert "review" not in rows["f1"] and "review" not in rows["f3"]          # asserts need a person; the other project was never read
    report = defense.review_summary()
    assert (report["findings"], report["read_line_by_line"], report["reviewed_as_a_pattern"], report["unreviewed"]) == (3, 1, 1, 1)
    assert defense.findings()[0]["status"] == "open"                           # a review never changes a finding's status


def test_a_new_line_in_a_previously_reviewed_file_is_not_automatically_reviewed(found):
    known = next(row for row in found if row["rule"] == "B608" and row["path"] == "app/memory/database.py")
    assert review_of(known) is not None
    # Reusing a reported fingerprint cannot bypass recomputation from the actual evidence.
    changed = {**known, "evidence": "SELECT newly concatenated untrusted input"}
    assert review_of(changed) is None
    assert summary([changed])["unreviewed"] == 1


def test_changed_surrounding_source_invalidates_old_line_reviews(found, monkeypatch):
    import app.code_review as module

    known = next(row for row in found if row["rule"] == "B608" and row["path"] == "app/memory/database.py")
    assert review_of(known) is not None
    monkeypatch.setattr(module, "_source_digest", lambda path: "changed surrounding guards")
    assert review_of(known) is None


def test_missing_snapshot_cannot_establish_line_review_coverage(found, monkeypatch):
    import app.code_review as module

    known = next(row for row in found if row["rule"] == "B608" and row["path"] == "app/memory/database.py")
    monkeypatch.setattr(module, "_snapshot", lambda: {})
    assert review_of(known) is None
