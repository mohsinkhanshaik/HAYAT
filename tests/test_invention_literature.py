"""The workbench reads published work before proposing and goes back to it when stuck (app/invention_literature.py)."""
from __future__ import annotations

import json

from app.invention_literature import abstract_text, papers, stuck, stuck_question
from tests.test_executive_invention import ScriptedModel, accepted, bench, candidate

WORKS = {"results": [
    {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/abc", "display_name": "Large-amplitude pendulum",
     "publication_year": 2009, "cited_by_count": 41,
     "abstract_inverted_index": {"The": [0], "period": [1], "grows": [2], "with": [3], "amplitude": [4]}},
    {"id": "https://openalex.org/W2", "display_name": "No abstract here", "publication_year": 2001,
     "cited_by_count": 3, "abstract_inverted_index": None},
]}


def test_an_abstract_is_rebuilt_in_order_and_bounded():
    assert abstract_text({"world": [1], "Hello": [0]}) == "Hello world"
    assert abstract_text(None) == ""
    long = {f"w{index}": [index] for index in range(600)}
    assert len(abstract_text(long)) <= 900 and abstract_text(long).endswith("…")


def test_published_works_without_an_abstract_are_skipped():
    asked = []
    found = papers("pendulum period large amplitude", lambda url: asked.append(url) or WORKS)
    assert [paper["title"] for paper in found] == ["Large-amplitude pendulum"]
    assert found[0] == {"title": "Large-amplitude pendulum", "year": 2009, "citations": 41,
                        "url": "https://doi.org/10.1/abc", "abstract": "The period grows with amplitude"}
    assert asked[0].startswith("https://api.openalex.org/works?search=pendulum%20period%20large%20amplitude")


def _rounds(*formulas_passed, best_index=0):
    rounds = [{"round": index + 1, "method": "model", "candidate": {"formula": formula},
               "measurement": {"passed": passed, "total": 3, "cases": []}} for index, (formula, passed)
              in enumerate(formulas_passed)]
    return {"goal": "Doubling", "rounds": rounds, "best": rounds[best_index], "tests": []}


def test_stuck_means_two_proposals_in_a_row_did_not_beat_the_best():
    assert stuck(_rounds(("x*3", 1), ("x*4", 0), ("x*5", 0)))
    assert not stuck(_rounds(("x*3", 1), ("x*4", 0)))                    # only one since the best
    assert not stuck(_rounds(("x*4", 0), ("x*3", 1), best_index=1))       # the latest is the best
    finished = _rounds(("x*2", 3), ("x*4", 0), ("x*5", 0))
    assert not stuck(finished)                                            # everything passes: done, not stuck


def test_the_question_names_the_regime_where_the_best_formula_is_most_wrong():
    data = {"goal": "Pendulum period", "tests": [{"inputs": {"theta": "0.1", "L": 1}, "expected": 2.0},
                                                {"inputs": {"theta": "1.5", "L": 1}, "expected": 2.4}],
            "best": {"measurement": {"cases": [{"passed": True, "relative_error": 0.0},
                                               {"passed": False, "relative_error": 0.18}]}}}
    assert stuck_question(data) == "Pendulum period large theta correction beyond the simple model"
    data["best"]["measurement"]["cases"][1]["passed"] = True
    assert stuck_question(data) is None


def test_a_stuck_run_goes_back_to_the_literature_and_the_proposer_reads_it(tmp_path):
    looked_up = []
    model = ScriptedModel(candidate("x * 3"), accepted(), candidate("x * 4"), accepted(),
                          candidate("x * 5"), accepted(), candidate("x * 2"), accepted())

    def research(question):
        looked_up.append(question)
        return {"complete": True, "answer": f"Notes on {question}", "sources": []}

    workbench = bench(tmp_path, model, research)
    workbench.literature = lambda question: [{"title": f"Paper on {question}", "year": 2020, "citations": 5,
                                              "url": "https://doi.org/10.1/x", "abstract": "Doubling is exact."}]
    cases = [{"inputs": {"x": x}, "expected": 2 * x} for x in (2, 5, 9)]
    project = workbench.create("Doubling", tests=cases)
    result = workbench.run(project["id"], cycles=4, work_out=False)
    assert result["literature"]["question"] == "Doubling" and result["literature"]["papers"]
    followup = result["literature_followup"]
    assert followup["question"] == "Doubling large x correction beyond the simple model"
    assert looked_up == ["Doubling", "Doubling large x correction beyond the simple model"]
    assert followup["papers"][0]["title"].startswith("Paper on Doubling large x")
    # The fourth proposal was made with what the follow-up found.
    fourth = json.loads(model.calls[6][1].content)
    assert fourth["literature_followup"]["question"] == followup["question"]
    assert "never evidence that a formula works" in fourth["context_notes"]["external_content"]
    first = json.loads(model.calls[0][1].content)
    assert first["literature"]["papers"][0]["abstract"] == "Doubling is exact." and "literature_followup" not in first
    assert result["best"]["candidate"]["formula"] == "x * 2"
    report = workbench.report_markdown(project["id"])
    assert "## Published work read before proposing" in report and "## Back to the literature" in report


def test_without_research_there_is_no_literature_and_offline_papers_do_not_stop_a_run(tmp_path):
    model = ScriptedModel(candidate("x * 2"), accepted())
    workbench = bench(tmp_path, model, lambda question: {"complete": True, "sources": []})

    def offline(question):
        raise ConnectionError("no network")

    workbench.literature = offline
    project = workbench.create("Doubling", tests=[{"inputs": {"x": 2}, "expected": 4}])
    result = workbench.run(project["id"], cycles=1, work_out=False)
    assert result["literature"]["papers"] == [] and "ConnectionError" in result["literature"]["error"]
    assert result["state"] == "TESTED"
    quiet = bench(tmp_path / "quiet", ScriptedModel(candidate("x * 2"), accepted()))
    quiet.literature = lambda question: 1 / 0
    project = quiet.create("Doubling", tests=[{"inputs": {"x": 2}, "expected": 4}])
    assert "literature" not in quiet.run(project["id"], cycles=1, research=False, work_out=False)
