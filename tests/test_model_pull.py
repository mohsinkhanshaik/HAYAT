"""A model pull is planned, explicitly approved, and reported honestly."""
from __future__ import annotations

import contextlib
import threading

from app.intent.router import IntentRouter
from app.models.pull import ModelPulls


class FakeHTTP:
    def __init__(self) -> None:
        self.pulls = []

    def get(self, url, **kwargs):
        class Response:
            status_code = 200

            def json(self):
                if url.endswith("/api/tags"):
                    return {"models": []}
                return {"config": {"size": 100}, "layers": [{"size": 900}]}

        return Response()

    @contextlib.contextmanager
    def stream(self, method, url, *, json, timeout):
        self.pulls.append((method, url, json))

        class Response:
            status_code = 200

            def iter_lines(self):
                yield '{"digest":"sha256:one","completed":900,"total":900}'
                yield '{"status":"success"}'

        yield Response()


def test_installed_model_needs_no_registry_connection(tmp_path):
    pulls = ModelPulls("http://localhost:11434", store=tmp_path)
    pulls.installed = lambda: {"llama3:8b"}
    pulls._size = lambda name: (_ for _ in ()).throw(AssertionError("registry should not be queried"))
    plan = pulls.plan("llama3:8b")
    assert plan.installed and plan.name == "llama3:8b" and plan.size_bytes == 0


def test_registry_outage_is_not_reported_as_missing_model():
    class Offline:
        def get(self, url, **kwargs):
            if url.endswith("/api/tags"):
                return type("Tags", (), {"json": lambda self: {"models": []}})()
            raise OSError("offline")

    pulls = ModelPulls("http://localhost:11434", http=Offline())
    plan = pulls.plan("llama3:8b")
    assert not plan.found and "couldn't reach" in plan.text


def test_spoken_model_and_quantization_resolve_with_bounded_lookups():
    pulls = ModelPulls("http://localhost:11434")
    checked = []

    def size(name):
        checked.append(name)
        return 1000 if name == "dolphin-mixtral:8x7b-v2.7-q4_K_M" else None

    pulls._size = size
    assert pulls.resolve("Dolphin-Mixtral-8x7B-v2-Q4_K_M") == (
        "dolphin-mixtral:8x7b-v2.7-q4_K_M", 1000)
    assert len(checked) <= 8


def test_pull_requires_exact_approval_then_finishes_and_notifies():
    http = FakeHTTP()
    done = threading.Event()
    notices = []
    pulls = ModelPulls("http://localhost:11434", http=http,
                       notify=lambda job, text: (notices.append((job, text)), done.set()))
    plan = pulls.plan("llama3:8b")
    assert plan.found and plan.size_bytes == 1000
    assert not http.pulls
    try:
        pulls.start(plan.name, plan.size_bytes)
    except PermissionError:
        pass
    else:
        raise AssertionError("a model started without approval")
    pulls.approve(plan.name)
    job = pulls.start(plan.name, plan.size_bytes)
    assert done.wait(2)
    assert job.status == "done" and job.fraction == 1
    assert http.pulls == [("POST", "http://localhost:11434/api/pull", {"model": "llama3:8b", "stream": True})]
    assert "downloaded" in notices[0][1]
    try:
        pulls.start(plan.name)
    except PermissionError:
        pass
    else:
        raise AssertionError("approval was reusable")


def test_pull_phrases_do_not_route_program_installs_as_models():
    router = IntentRouter()
    assert router.route("install vlc").control == "SOFTWARE_INSTALL"
    intent = router.route("download model hf.co/user/repo:Q4_K_M now")
    assert intent.control == "MODEL_PULL" and intent.entities["model"] == "hf.co/user/repo:Q4_K_M"
    assert router.route("how is the download going?").control == "MODEL_PULL_STATUS"
    assert router.route("download it again").control == "MODEL_PULL_AGAIN"


def test_revocation_prevents_later_unapproved_pull():
    pulls = ModelPulls("http://localhost:11434", http=FakeHTTP())
    pulls.approve("llama3:8b")
    pulls.revoke("llama3:8b")
    try:
        pulls.start("llama3:8b")
    except PermissionError:
        pass
    else:
        raise AssertionError("a revoked approval was accepted")


def test_download_claim_detection_ignores_instructions_to_the_user():
    from app.assistant import AssistantService

    claims = AssistantService._CLAIMS_DOWNLOAD
    assert claims.search("Starting the pull now!")
    assert claims.search("I am downloading it now.")
    assert not claims.search("To start the download, approve the plan.")
    assert not claims.search("You can start the download when ready.")


def test_assistant_plans_then_starts_a_real_background_pull():
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, start_background_services=False)
    done = threading.Event()
    try:
        runtime.model_pulls._http = FakeHTTP()
        runtime.model_pulls.store = None
        runtime.model_pulls.notify = lambda job, text: done.set()
        plan = runtime.assistant.handle("download model llama3:8b").response.display_text
        assert "Shall I download it?" in plan
        assert runtime.model_pulls.jobs == []
        reply = runtime.assistant.handle("yes").response.display_text
        assert "llama3:8b" in reply
        assert done.wait(2)
        assert runtime.model_pulls.jobs[-1].status == "done"
        runtime.conversation.reply = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("YES must report the real job"))
        for _ in range(2):
            status = runtime.assistant.handle("YES").response.display_text
            assert "downloaded" in status and "Shall I" not in status
        assert len(runtime.model_pulls.jobs) == 1
    finally:
        runtime.close()


def test_missing_quantization_never_silently_downgrades_to_default():
    pulls = ModelPulls("http://localhost:11434")
    pulls._size = lambda name: 1000 if name in {"mixtral:latest", "mixtral:8x7b"} else None
    assert pulls.resolve("Mixtral-8x7b-q9_K_M") is None


def test_markdown_model_name_preserves_quantization():
    from app.models.pull import model_name_in

    assert model_name_in(r"Pulling Dolphin-Mixtral-8x7B-v2-Q4\_K\_M now") == "Dolphin-Mixtral-8x7B-v2-Q4_K_M"
    intent = IntentRouter().route(r"download model Dolphin-Mixtral-8x7B-v2-Q4\_K\_M")
    assert intent.control == "MODEL_PULL"
    assert intent.entities["model"] == "Dolphin-Mixtral-8x7B-v2-Q4_K_M"
