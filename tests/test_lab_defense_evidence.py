"""Cyber defence: findings leave as standard SARIF, and saved scan/patch evidence reopens without re-running anything."""
from __future__ import annotations

import json
import queue
from types import SimpleNamespace

import pytest

from app.code_defense import LIMITS, CodeDefense
from tests.test_research_lab import _repository


def _fix(root):
    return ("--- a/app/keys.py\n+++ b/app/keys.py\n@@ -2,4 +2,4 @@\n \n \n def key(text):\n"
            "-    return hashlib.md5(text.encode()).hexdigest()\n"
            "+    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()\n")


def test_recorded_findings_export_as_sarif_without_a_scan_or_this_computer_s_folders(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    defense = CodeDefense(tmp_path / "cd.db", root=root)
    assert defense.sarif()["runs"][0]["results"] == []           # nothing recorded yet: an empty, valid log
    defense.scan()
    finding = defense.findings()[0]
    monkeypatch.setattr("app.code_defense.bandit_findings", lambda *args: pytest.fail("an export must not scan"))
    log = defense.sarif()
    assert log["version"] == "2.1.0" and log["$schema"].endswith("sarif-2.1.0.json")
    run = log["runs"][0]
    assert run["tool"]["driver"]["name"].startswith("HAYAT code defence")
    assert [rule["id"] for rule in run["tool"]["driver"]["rules"]] == [finding["rule"]]
    result, = run["results"]
    assert result["ruleId"] == finding["rule"] and result["level"] == "error"      # HIGH
    where = result["locations"][0]["physicalLocation"]
    assert where["artifactLocation"] == {"uri": "app/keys.py", "uriBaseId": "SRCROOT"}
    assert where["region"]["startLine"] == finding["line"] and "md5" in where["region"]["snippet"]["text"]
    assert result["partialFingerprints"] == {"hayatFinding/v1": finding["fingerprint"]}
    assert run["properties"]["scope"] == LIMITS and "no scan was run" in run["properties"]["source"]
    # The file can be shared: no absolute path of this computer is in it.
    text = json.dumps(log)
    assert str(root) not in text and str(tmp_path) not in text and root.as_posix() not in text
    # A finding marked as deliberate is left out, or carried as a suppression with the owner's note.
    defense.set_status(finding["fingerprint"], "accepted", "cache key, not security")
    assert defense.sarif()["runs"][0]["results"] == []
    kept, = defense.sarif(include_accepted=True)["runs"][0]["results"]
    assert kept["suppressions"] == [{"kind": "external", "status": "accepted", "justification": "cache key, not security"}]


def test_severities_map_to_sarif_levels_and_a_missing_line_is_still_a_valid_region(tmp_path):
    defense = CodeDefense(tmp_path / "cd.db", root=tmp_path)
    from contextlib import closing

    with closing(defense._db()) as db, db:
        for index, severity in enumerate(("HIGH", "MEDIUM", "LOW", "ODD")):
            db.execute("INSERT INTO findings(fingerprint,project,tool,rule,title,path,line,severity,confidence,priority,"
                       "evidence,more,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (f"f{index}", str(tmp_path), "bandit", "B101", "Use of assert", f"app/m{index}.py", 0, severity,
                        "HIGH", 9 - index, "assert x", "https://bandit.readthedocs.io/b101", "2026-11-10", "2026-11-10"))
    run = defense.sarif()["runs"][0]
    assert [result["level"] for result in run["results"]] == ["error", "warning", "note", "note"]
    assert all(result["locations"][0]["physicalLocation"]["region"]["startLine"] == 1 for result in run["results"])
    rule, = run["tool"]["driver"]["rules"]                         # one rule entry however many findings share it
    assert rule == {"id": "B101", "shortDescription": {"text": "Use of assert"},
                    "helpUri": "https://bandit.readthedocs.io/b101"}
    assert defense.sarif(project="another project")["runs"][0]["results"] == []


def test_saved_scan_and_patch_evidence_reopens_without_scanning_or_touching_files(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    defense = CodeDefense(tmp_path / "cd.db", root=root)
    assert defense.history() == {"scans": [], "patches": []} and defense.patch_detail(1) is None
    scan = defense.scan()
    record = scan["evaluation"]
    assert record["protocol"] == "hayat-local-source-review-v2" and len(record["findings_sha256"]) == 64
    assert "not a fingerprint of all source files or proof of security" in record["scope"]
    before = (root / "app" / "keys.py").read_text(encoding="utf-8")
    tested = defense.validate_patch(_fix(root), ["tests/test_keys.py"])
    assert tested["validated"] and tested["baseline_tests_passed"] is True and tested["tests_passed"] is True
    evidence = tested["evaluation"]
    assert evidence["protocol"] == "hayat-local-patch-validation-v2" and len(evidence["base_commit"]) == 40
    assert len(evidence["patch_sha256"]) == 64 and evidence["tests"] == ["tests/test_keys.py"]
    assert (root / "app" / "keys.py").read_text(encoding="utf-8") == before          # never applied
    # Reading the saved evidence runs nothing.
    monkeypatch.setattr("app.code_defense.bandit_findings", lambda *args: pytest.fail("history must not scan"))
    monkeypatch.setattr("app.code_defense._run", lambda *args: pytest.fail("history must not start a process"))
    history = defense.history()
    assert [row["kind"] for row in history["scans"]] == ["source"]
    assert history["scans"][0]["summary"]["evaluation"] == record
    assert history["patches"][0]["verdict"] == tested["verdict"] and history["patches"][0]["tests_passed"] is True
    reopened = defense.patch_detail(tested["patch_id"])
    assert reopened["evaluation"] == evidence and reopened["verdict"] == tested["verdict"]
    assert defense.patch_detail(999) is None
    assert len(defense.history(limit=0)["scans"]) == 1 and len(defense.history(limit=10_000)["patches"]) == 1   # clamped


def test_the_lab_window_shows_the_new_actions_at_its_smallest_size_and_saves_sarif_where_chosen(shared_tk, tmp_path, monkeypatch):
    from tkinter import ttk

    from app.gui_research_lab import ResearchLab
    from app.gui_theme import Palette, style_controls
    from tests.conftest import close_window, open_window

    pending: queue.Queue = queue.Queue()
    owner = open_window(shared_tk, '900x700+10+10')
    defense = CodeDefense(tmp_path / "cd.db", root=_repository(tmp_path))
    defense.scan()
    poisoned = {"suite": "poisoning", "headline": "linear: balanced accuracy changes -0.031 when 10% of training labels are wrong",
                "recommendation": "Little change at these shares.", "clean_score": 0.98, "tool": "scikit-learn refit",
                "levels": [{"corrupted_share": 0.05, "score": 0.97, "refits": 2, "cost": 0.01},
                           {"corrupted_share": 0.10, "score": 0.949, "refits": 2, "cost": 0.031}],
                "not_measured_shares": [0.20], "limits": "Random label corruption.", "source": "labels.csv",
                "evaluation": {"protocol": "hayat-label-corruption-v1", "score": "balanced accuracy"}}
    robustness = SimpleNamespace(
        history=lambda limit=30: [{"id": 4, "headline": poisoned["headline"], "suite": "poisoning", "at": "2026-11-10T09:00:00"}],
        report=lambda: {"not_run_yet": [], "failure_categories": [], "recommendations": {}},
        detail=lambda run_id: poisoned if run_id == 4 else None, status=lambda: {})

    class GUI(Palette):
        root = owner
        runtime = SimpleNamespace(code_defense=defense, robustness=robustness)

        def _post(self, function, *args):
            pending.put((function, args))

        def _open_reverse_engineer(self):
            pass

    monkeypatch.setattr('app.research_lab.ghidra_status', lambda *args: {'installed': False})
    ttk.Style(shared_tk).theme_use('clam')
    style_controls(shared_tk, Palette)
    window = ResearchLab(GUI())

    def buttons(page):
        found = []
        for child in page.winfo_children():
            found += [child] if isinstance(child, ttk.Button) else buttons(child)
        return found

    def settle(done=None, quiet=0.5):
        """Hand queued background results to the window until `done()` is true; with no test, until it goes quiet."""
        import time
        deadline, last = time.monotonic() + 15, time.monotonic()
        while time.monotonic() < deadline:
            shared_tk.update()
            try:
                function, arguments = pending.get_nowait()
            except queue.Empty:
                if done() if done else time.monotonic() - last > quiet:
                    return
                time.sleep(.01)
                continue
            function(*arguments)
            last = time.monotonic()
        pytest.fail('the background work did not report back')

    try:
        window.geometry('840x620+20+20')
        settle()                                    # the window's own opening reads

        def right():
            return window.winfo_rootx() + window.winfo_width()
        # Cyber defence holds two inner tabs (findings and fuzzing; patch validation and evidence): each is checked
        # while it is the one showing.
        for name, inner, expected in (
                ("Robustness", None, {"Run all suites", "Wrong training labels", "Attack the last model"}),
                ("Cyber defence", 0, {"Export findings (SARIF)…", "Mark as deliberate", "Fuzz text handlers"}),
                ("Cyber defence", 1, {"Saved scan / patch evidence", "Test this patch",
                                      "Suggest a patch for the chosen finding"})):
            window.tabs.select(window.pages[name])
            if inner is not None:
                window.defense_views.select(inner)
            shared_tk.update()
            shown = {str(button.cget('text')): button for button in buttons(window.pages[name])}
            assert expected <= set(shown)
            for label in expected:                 # every action is whole and inside the smallest window
                assert shown[label].winfo_viewable(), label
                assert shown[label].winfo_rootx() + shown[label].winfo_width() <= right(), label
        # A saved label-corruption run reads as shares and scores, with what could not be measured named.
        window.tabs.select(window.pages["Robustness"])
        window.refresh_robustness()
        assert "All suites have run." in window.robust_text.get('1.0', 'end')
        window.robust_tree.selection_set('4')
        window._show_robust()
        words = window.robust_text.get('1.0', 'end')
        assert "No wrong labels: balanced accuracy 0.98" in words
        assert "10% of training labels wrong: balanced accuracy 0.949 (average of 2 refits)" in words
        assert "Not measured (time budget): 20%" in words and "Evaluation: hayat-label-corruption-v1" in words
        # Export: cancelled means nothing is written; a chosen path gets the SARIF file and the window says what is in it.
        target = tmp_path / 'out' / 'findings.sarif'
        target.parent.mkdir()
        monkeypatch.setattr('app.gui_research_lab.filedialog.asksaveasfilename', lambda **options: '')
        window.export_findings()
        assert not target.exists() and pending.empty()
        monkeypatch.setattr('app.gui_research_lab.filedialog.asksaveasfilename', lambda **options: str(target))
        window.export_findings()
        settle(lambda: 'saved to' in window.finding_text.get('1.0', 'end'))
        saved = json.loads(target.read_text(encoding='utf-8'))
        assert saved["version"] == "2.1.0" and len(saved["runs"][0]["results"]) == 1
        note = window.finding_text.get('1.0', 'end')
        assert f"1 open findings saved to {target}" in note and "No scan was run for this export" in note
        # A finding in another project is not covered by the review of HAYAT's own code, and the window says so.
        key = next(iter(window.findings))
        window.finding_tree.selection_set(key)
        window._show_finding()
        assert "No recorded review covers this finding" in window.finding_text.get('1.0', 'end')
        # Fuzzing from the window: a planted defect is found in a child process and shown with its smallest input.
        from app.fuzz_lab import FuzzLab, Target

        window.runtime.fuzz_lab = FuzzLab(tmp_path / "fuzz.db", {"crash": Target(
            "crash", "tests.fuzz_fixture:planted_crash", "planted", ("system: hello there", "plain words"), allowed=("ValueError",))})
        window.fuzz_handlers()
        settle(lambda: 'smallest input kept' in window.finding_text.get('1.0', 'end'))
        fuzzed = window.finding_text.get('1.0', 'end')
        assert "crash (finding 1): KeyError" in fuzzed and "at tests/fuzz_fixture.py:" in fuzzed
        assert "does not show there are none" in fuzzed                      # the limits travel with the result
        assert "fuzzing open findings: 1" in window.overview_nodes['Cyber defence']['tip']
        # Saved evidence is listed without running anything.
        monkeypatch.setattr("app.code_defense.bandit_findings", lambda *args: pytest.fail("history must not scan"))
        window.show_defense_history()
        settle(lambda: 'Saved local security evidence' in window.finding_text.get('1.0', 'end'))
        listed =window.finding_text.get('1.0', 'end')
        assert "Saved local security evidence (no scan or patch was run)" in listed and "Scan 1 · source" in listed
    finally:
        window.close()
        close_window(owner)
