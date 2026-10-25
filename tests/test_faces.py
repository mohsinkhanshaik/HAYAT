"""Enrolled-face recognition: persists across a restart, matches by signature, never searches.

The OpenCV detector/embedder is replaced by a fake that returns fixed signatures,
so the gallery, the matching threshold, persistence and the tools are all tested
without a model download or a camera.
"""
from __future__ import annotations

from app.perception.faces import DetectedFace, FaceGallery, FaceRecognizer, MATCH_THRESHOLD
from app.safety.permissions import PermissionManager
from app.tools.faces import register_face_tools
from app.tools.registry import ToolRegistry


class _FakeEngine:
    """Maps an image path to the faces it 'contains'. Recognition is always available."""

    def __init__(self, by_path):
        self._by_path = by_path

    def recognition_available(self):
        return True

    def status(self):
        return {"detection": True, "recognition": True, "reason": ""}

    def read_faces(self, path):
        from pathlib import Path

        key = Path(path).name
        if key not in self._by_path:
            raise FileNotFoundError(str(path))
        return self._by_path[key]


def _face(vec):
    return DetectedFace((0, 0, 10, 10), 0.99, list(vec))


# -- gallery: matching + persistence ----------------------------------------

def test_match_returns_the_closest_enrolled_person_above_threshold(tmp_path):
    gallery = FaceGallery(tmp_path / "g.json")
    gallery.enroll("Sam", [1.0, 0.0, 0.0])
    gallery.enroll("Ada", [0.0, 1.0, 0.0])
    name, score = gallery.match([0.95, 0.05, 0.0])
    assert name == "Sam" and score >= MATCH_THRESHOLD


def test_an_unlike_face_is_unknown_not_forced_to_a_name(tmp_path):
    gallery = FaceGallery(tmp_path / "g.json")
    gallery.enroll("Sam", [1.0, 0.0, 0.0])
    name, score = gallery.match([0.0, 0.0, 1.0])
    assert name is None and score < MATCH_THRESHOLD


def test_enrolment_survives_a_restart(tmp_path):
    path = tmp_path / "g.json"
    FaceGallery(path).enroll("Sam", [1.0, 0.0, 0.0])
    reopened = FaceGallery(path)  # a fresh process reading the same file
    assert [p["name"] for p in reopened.people()] == ["Sam"]
    assert reopened.match([1.0, 0.0, 0.0])[0] == "Sam"


def test_clear_empties_the_gallery_and_persists(tmp_path):
    path = tmp_path / "g.json"
    gallery = FaceGallery(path)
    gallery.enroll("Sam", [1.0, 0.0, 0.0])
    assert gallery.clear() == 1
    assert FaceGallery(path).people() == []


def test_a_person_keeps_only_the_most_recent_signatures(tmp_path):
    gallery = FaceGallery(tmp_path / "g.json")
    for i in range(8):
        gallery.enroll("Sam", [float(i + 1), 1.0, 0.0])
    assert gallery.people()[0]["signatures"] == 5  # MAX_PER_PERSON


# -- recognizer: enrol one, recognise enrolled vs unknown --------------------

def _recognizer(tmp_path, by_path):
    return FaceRecognizer(engine=_FakeEngine(by_path), gallery=FaceGallery(tmp_path / "g.json"))


def test_enroll_then_recognize_names_the_enrolled_and_flags_the_unknown(tmp_path):
    by_path = {
        "sam.jpg": [_face([1.0, 0.0, 0.0])],
        "scene.jpg": [_face([0.98, 0.02, 0.0]), _face([0.0, 0.0, 1.0])],
    }
    rec = _recognizer(tmp_path, by_path)
    assert rec.enroll("Sam", "sam.jpg", consent=True)["enrolled"] is True
    out = rec.recognize("scene.jpg")
    assert out["available"] is True and out["count"] == 2
    names = {(r["name"], r["match"]) for r in out["faces"]}
    assert ("Sam", "enrolled") in names
    assert (None, "unknown") in names


def test_enroll_refuses_an_image_with_more_than_one_face(tmp_path):
    rec = _recognizer(tmp_path, {"two.jpg": [_face([1, 0, 0]), _face([0, 1, 0])]})
    result = rec.enroll("Sam", "two.jpg", consent=True)
    assert result["enrolled"] is False and "one person" in result["reason"]


def test_forget_removes_only_the_named_person(tmp_path):
    rec = _recognizer(tmp_path, {"a.jpg": [_face([1, 0, 0])], "b.jpg": [_face([0, 1, 0])]})
    rec.enroll("Sam", "a.jpg", consent=True)
    rec.enroll("Ada", "b.jpg", consent=True)
    assert rec.forget("Sam")["forgotten"] is True
    assert [p["name"] for p in rec.people()["enrolled"]] == ["Ada"]


# -- tools: registered, scoped, and read/write as expected -------------------

def test_tools_are_registered_and_run(tmp_path):
    registry = ToolRegistry(PermissionManager(
        {"default": "DENY", "permissions": {"filesystem_read": "ALLOW", "filesystem_write_project": "ALLOW", "system_read": "ALLOW"}}, tmp_path))
    rec = FaceRecognizer(engine=_FakeEngine({"sam.jpg": [_face([1.0, 0.0, 0.0])]}),
                         gallery=FaceGallery(tmp_path / "g.json"))
    register_face_tools(registry, rec)
    names = set(registry.names())
    assert {"face_enroll", "face_recognize", "face_list_enrolled", "face_forget", "face_clear_all",
            "face_recognition_status"} <= names
    assert registry.execute("face_enroll", {"name": "Sam", "path": "sam.jpg", "consent": True}).output["enrolled"] is True
    assert registry.execute("face_list_enrolled", {}).output["enrolled"][0]["name"] == "Sam"


def test_recognition_without_models_still_counts_faces_but_names_no_one(tmp_path):
    class _NoModels(_FakeEngine):
        def recognition_available(self):
            return False

        def status(self):
            return {"detection": True, "recognition": False, "reason": "Install the models."}

    rec = FaceRecognizer(engine=_NoModels({"x.jpg": [_face([1, 0, 0]), _face([0, 1, 0])]}),
                         gallery=FaceGallery(tmp_path / "g.json"))
    out = rec.recognize("x.jpg")
    assert out["available"] is False and out["faces_detected"] == 2
    assert rec.enroll("Sam", "x.jpg", consent=True)["enrolled"] is False  # cannot enrol without the recognition model


def test_the_real_engine_reports_detection_even_with_no_models(tmp_path):
    """OpenCV's bundled detector means detection is available offline; recognition waits for the models."""
    from app.perception.faces import FaceEngine

    status = FaceEngine(models_dir=tmp_path / "absent").status()
    assert status["detection"] is True
    assert status["recognition"] is False and "OpenCV Zoo" in status["reason"]
    assert "set up face recognition" in status["reason"]


# -- installing the models ----------------------------------------------------

def _stand_in_sources(monkeypatch):
    """Point both models at fixed bytes whose SHA-256 is known, so no real download happens."""
    import hashlib

    from app.perception import faces

    payloads = {faces.YUNET: b"yunet-bytes", faces.SFACE: b"sface-bytes"}
    monkeypatch.setattr(faces, "FACE_MODEL_SOURCES", {
        name: (f"https://example.test/{name}", hashlib.sha256(data).hexdigest()) for name, data in payloads.items()})
    return {f"https://example.test/{name}": data for name, data in payloads.items()}


def test_install_keeps_models_whose_hash_matches_and_skips_them_next_time(tmp_path, monkeypatch):
    from app.perception.faces import FaceEngine

    by_url = _stand_in_sources(monkeypatch)
    fetched = []

    def fetch(url):
        fetched.append(url)
        return by_url[url]

    engine = FaceEngine(models_dir=tmp_path / "faces")
    out = engine.install(fetch)
    assert out["installed"] is True and out["recognition"] is True
    assert [f["status"] for f in out["files"]] == ["downloaded", "downloaded"]
    assert engine.recognition_available() and len(fetched) == 2

    again = engine.install(fetch)
    assert [f["status"] for f in again["files"]] == ["already installed", "already installed"]
    assert len(fetched) == 2  # nothing fetched the second time


def test_install_refuses_a_file_that_does_not_match_its_published_hash(tmp_path, monkeypatch):
    from app.perception.faces import FaceEngine

    _stand_in_sources(monkeypatch)
    folder = tmp_path / "faces"
    out = FaceEngine(models_dir=folder).install(lambda url: b"tampered")
    assert out["installed"] is False and "SHA-256" in out["reason"]
    assert list(folder.iterdir()) == []  # no model and no half-written file left behind


def test_install_reports_a_failed_download_in_words(tmp_path, monkeypatch):
    from app.perception.faces import FaceEngine

    _stand_in_sources(monkeypatch)

    def offline(url):
        raise ConnectionError("no network")

    out = FaceEngine(models_dir=tmp_path / "faces").install(offline)
    assert out["installed"] is False and "Could not download" in out["reason"] and "no network" in out["reason"]


def test_the_published_hashes_are_pinned_for_both_models():
    from app.perception.faces import FACE_MODEL_SOURCES, SFACE, YUNET

    assert set(FACE_MODEL_SOURCES) == {YUNET, SFACE}
    for url, digest in FACE_MODEL_SOURCES.values():
        assert url.startswith("https://github.com/opencv/opencv_zoo/") and len(digest) == 64


# -- asking for it in plain words ---------------------------------------------

import pytest  # noqa: E402


@pytest.mark.parametrize("text", ["set up face recognition", "Install the face models", "download the face models.",
                                  "enable face recognition", "download yunet and sface models"])
def test_setup_phrases_reach_the_model_download(text):
    from app.tools.faces import direct_face_setup_plan, face_setup_request

    assert face_setup_request(text)
    assert direct_face_setup_plan(text, ["face_models_setup"])[0]["tool"] == "face_models_setup"


@pytest.mark.parametrize("text", ["set up face unlock", "who is in this photo", "set up language tools",
                                  "enroll Sam's face for recognition", "set up face recognition for strangers"])
def test_other_requests_do_not_download_face_models(text):
    from app.tools.faces import face_setup_request

    assert not face_setup_request(text)


def test_the_assistant_runs_the_setup_for_set_up_face_recognition(monkeypatch):
    from app.perception import faces
    from app.runtime import build_runtime

    calls = []

    def stand_in(self, fetch=None):
        calls.append(self.models_dir)
        return {"installed": True, "recognition": True, "models_dir": str(self.models_dir),
                "files": [{"name": faces.YUNET, "status": "downloaded", "bytes": 232589},
                          {"name": faces.SFACE, "status": "downloaded", "bytes": 38696353}]}

    monkeypatch.setattr(faces.FaceEngine, "install", stand_in)
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        assert "face_models_setup" in runtime.registry.names()
        reply = runtime.assistant.handle("set up face recognition").display_response
        assert len(calls) == 1
        assert "Face recognition is set up" in reply and "returned nothing" not in reply
    finally:
        runtime.close()


# -- replies in words ---------------------------------------------------------

def test_face_tool_outputs_are_written_as_sentences():
    from app.observed_reply import render

    assert render({"installed": False, "files": [], "reason": "Could not download x: no network"}) == (
        "The face models were not installed. Could not download x: no network")
    status = render({"detection": True, "recognition": False, "reason": "Recognition needs the models.", "enrolled": []})
    assert "Face detection works" in status and "Nobody is enrolled yet." in status
    assert render({"enrolled": True, "name": "Sam", "signatures": 2}) == (
        "Enrolled Sam (2 signatures kept on this computer).")
    seen = render({"available": True, "count": 2, "enrolled_known": 1, "unknown": 1,
                   "faces": [{"name": "Sam", "match": "enrolled"}, {"name": None, "match": "unknown"}]})
    assert seen == "2 faces in the picture: Sam (enrolled); 1 unknown (not identified, and nobody is looked up)."
    assert render({"available": False, "faces_detected": 1, "reason": "Install the models."}).startswith("1 face detected")
