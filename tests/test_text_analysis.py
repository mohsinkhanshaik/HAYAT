"""NLTK in HAYAT: its model-file sandbox bypass closed (PYSEC-2026-3740), and text analysis that uses it."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from app.config import PROJECT_ROOT
from app.security import nltk_guard
from app.text_analysis import PACKAGES, TextAnalyzer, report, text_request

SAMPLE = ("HAYAT reads the router and reports a strong 5G signal. The connection is fast and reliable, which is wonderful. "
          "However, the old firmware was terrible and caused frequent drops. Router updates fixed most problems. "
          "The router now works well, and the signal stays strong all day.")
REAL_DATA = PROJECT_ROOT / "data" / "nltk_data"


@pytest.fixture
def guarded():
    nltk_guard.install()
    import nltk

    allowed = Path(tempfile.mkdtemp(prefix="hayat-nltk-allowed-"))
    nltk.data.path.append(str(allowed))
    try:
        yield allowed
    finally:
        nltk.data.path.remove(str(allowed))


def outside(tmp_path: Path) -> Path:
    # tmp_path is under the project's .test-runs folder: not an NLTK data folder, not the user's temp.
    return tmp_path / "escape"


def test_every_vulnerable_function_refuses_a_path_outside_the_allowed_folders(guarded, tmp_path):
    from nltk.classify import maxent
    from nltk.parse.transitionparser import TransitionParser
    from nltk.tag.perceptron import AveragedPerceptron, PerceptronTagger

    target = outside(tmp_path)
    with pytest.raises(PermissionError, match="PYSEC-2026-3740 AveragedPerceptron.save"):
        AveragedPerceptron().save(str(target / "weights.json"))
    with pytest.raises(PermissionError):
        AveragedPerceptron().load(str(target / "weights.json"))
    with pytest.raises(PermissionError):
        PerceptronTagger(load=False).save_to_json(lang="xxx", loc=str(target))
    with pytest.raises(PermissionError):
        maxent.save_maxent_params(None, {}, [], {}, tab_dir=str(target / "tab"))
    with pytest.raises(PermissionError):
        TransitionParser("arc-standard").train([], str(target / "model"))
    with pytest.raises(PermissionError):
        TransitionParser("arc-standard").parse([], str(target / "model"))
    assert not target.exists()  # nothing was written


def test_inside_the_allowed_folders_they_still_work(guarded):
    from nltk.tag.perceptron import AveragedPerceptron

    model = AveragedPerceptron()
    model.weights = {"feature": {"NN": 1.0}}
    path = guarded / "weights.json"
    model.save(str(path))
    again = AveragedPerceptron()
    again.load(str(path))
    assert again.weights == {"feature": {"NN": 1.0}}


def test_the_guard_is_applied_on_import_in_a_fresh_process_and_reported():
    code = ("import sys; sys.path.insert(0, r'%s'); from app.security import nltk_guard; nltk_guard.install(); "
            "from nltk.tag.perceptron import AveragedPerceptron; print(getattr(AveragedPerceptron.save, '_hayat_pathsec_guarded', False)); "
            "print(nltk_guard.status()['functions_guarded'])" % PROJECT_ROOT)
    output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"}).stdout.split()
    assert output[0] == "True" and "loaded" in " ".join(output[1:])
    status = nltk_guard.status()
    assert status["installed"] and status["advisory"] == "PYSEC-2026-3740" and status["import_hook"]


def test_guarding_twice_changes_nothing(guarded):
    from nltk.tag import perceptron

    first = perceptron.AveragedPerceptron.save
    assert nltk_guard.harden_module(perceptron) == []
    assert perceptron.AveragedPerceptron.save is first


def test_analysis_without_the_language_data_is_approximate_and_says_so(tmp_path):
    result = TextAnalyzer(tmp_path / "empty").analyze(SAMPLE)
    assert result["words"] == 46 and result["sentences"] == 5 and "sentiment" not in result
    assert result["keywords"][0]["word"] == "router" and "parts of speech (needs the tagger)" in result["approximate"]
    text = report([{"tool": "text_analyze", "result": {"success": True, "output": result}}])
    assert "say 'set up language tools'" in text


@pytest.mark.skipif(not all((REAL_DATA / path).exists() for path in ("tokenizers/punkt_tab", "sentiment/vader_lexicon.zip",
                                                                      "corpora/stopwords", "taggers/averaged_perceptron_tagger_eng")),
                    reason="NLTK language data not set up on this machine")
def test_full_analysis_with_the_language_data():
    result = TextAnalyzer(REAL_DATA).analyze(SAMPLE)
    assert result["approximate"] == [] and result["sentiment"]["label"] == "positive"
    assert result["sentiment"]["most_negative"].startswith("However, the old firmware was terrible")
    assert set(result["parts_of_speech"]) == {"nouns_pct", "verbs_pct", "adjectives_pct", "adverbs_pct"}
    assert result["readability"]["meaning"] in {"easy", "plain English", "fairly difficult"}


def test_setup_downloads_only_what_is_missing_into_its_folder(tmp_path):
    fetched = []

    def download(name, folder):
        fetched.append(name)
        target = Path(folder) / {"punkt_tab": "tokenizers/punkt_tab", "stopwords": "corpora/stopwords",
                                 "vader_lexicon": "sentiment/vader_lexicon.zip",
                                 "averaged_perceptron_tagger_eng": "taggers/averaged_perceptron_tagger_eng"}[name]
        if target.suffix:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")
        else:
            target.mkdir(parents=True, exist_ok=True)
        return True
    analyzer = TextAnalyzer(tmp_path / "nltk_data", download=download)
    first = analyzer.setup()
    assert first["ready"] and sorted(fetched) == sorted(PACKAGES) and "Language tools are ready" in first["summary"]
    assert analyzer.setup()["results"] == {name: "already installed" for name in PACKAGES}


def test_files_are_read_and_odd_input_is_refused(tmp_path):
    note = tmp_path / "note.md"
    note.write_text(SAMPLE, encoding="utf-8")
    assert TextAnalyzer(tmp_path / "x").analyze(path=str(note))["source"] == str(note)
    with pytest.raises(ValueError, match="No file"):
        TextAnalyzer(tmp_path / "x").analyze(path=str(tmp_path / "missing.txt"))
    with pytest.raises(ValueError, match="at least a sentence"):
        TextAnalyzer(tmp_path / "x").analyze("hi")


@pytest.mark.parametrize("text, tool, arguments", [
    ("sentiment of: I love this but hate the price", "text_analyze", {"text": "I love this but hate the price"}),
    ("keywords in notes.txt", "text_analyze", {"path": "notes.txt"}),
    ("how readable is D:/AI/docs/network.md", "text_analyze", {"path": "D:/AI/docs/network.md"}),
    ("set up language tools", "text_tools_setup", {}),
])
def test_owner_phrases(text, tool, arguments):
    assert text_request(text) == {"tool": tool, "arguments": arguments}


@pytest.mark.parametrize("text", ["analyze the file report.pdf", "analyze the market", "check my router", "analyze this"])
def test_other_requests_stay_with_their_own_tools(text):
    assert text_request(text) is None


def test_the_assistant_answers_a_text_analysis_request_from_the_tool():
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        assert {"text_analyze", "text_tools_setup"} <= set(runtime.registry.names())
        reply = runtime.assistant.handle("sentiment of: I love this phone but the battery life is awful and slow").display_response
        assert "12 words in 1 sentence (" in reply and "readability" in reply
        assert nltk_guard.status()["import_hook"]  # installed by build_runtime
    finally:
        runtime.close()
