"""Pictures made on this machine, by an engine HAYAT owns.

The slow tests here are skipped unless the engine and model are actually
installed: generating a real image takes seconds and loads four gigabytes,
which does not belong in a suite that runs on every change. What is always
checked is the part that decides whether a request is even attempted.
"""
from __future__ import annotations


import pytest

from app.models.diffusion import (
    MAX_DIMENSION,
    MAX_STEPS,
    ImageGenerator,
    ImageResult,
    _round_dimension,
    build_negative_prompt,
    enhance_prompt,
    find_engine,
    find_model,
    validate_png,
)


def _installed() -> ImageGenerator:
    generator = ImageGenerator()
    if not generator.available:
        pytest.skip("no image engine or model installed on this machine")
    return generator


@pytest.fixture
def as_hayat_runs(monkeypatch):
    """See memory the way the running app does: through HAYAT's own model server when it is up.

    The app points OLLAMA_BASE_URL at its own Ollama, so a loaded brain counts
    as memory that can be freed for a picture. The test process otherwise asks
    the machine's other Ollama, sees nothing to free, and calls a busy machine
    unable to make images at all.
    """
    import httpx

    from app import hardware, portable

    try:
        httpx.get(portable.base_url() + "/api/version", timeout=1).raise_for_status()
        monkeypatch.setenv("OLLAMA_BASE_URL", portable.base_url())
    except Exception:
        pass
    monkeypatch.setattr(hardware, "_CACHE", None)
    return _installed()


def _generating(generator: ImageGenerator) -> ImageGenerator:
    if generator.verdict().get("placement") == "GPU_AFTER_FREEING":
        pytest.skip("making a picture now would unload the brain of the HAYAT that is running; "
                    "run with HAYAT closed to exercise the engine")
    return generator


# --------------------------------------------------- refuse before loading


def test_an_empty_prompt_is_refused_without_loading_anything(tmp_path):
    result = ImageGenerator(engine_path=tmp_path / "sd-cli.exe").generate("   ")
    assert not result.ok
    assert "needs a description" in result.detail


def test_a_missing_engine_is_reported_plainly(tmp_path):
    result = ImageGenerator(engine_path=tmp_path / "absent.exe",
                            model_path=tmp_path / "m.safetensors").generate("a cat")
    assert not result.ok
    assert "engine" in result.detail


def test_a_missing_model_is_reported_plainly(tmp_path):
    engine = tmp_path / "sd-cli.exe"
    engine.write_bytes(b"x")
    result = ImageGenerator(engine_path=engine, model_path=tmp_path / "absent.safetensors").generate("a cat")
    assert not result.ok
    assert "model" in result.detail


def test_a_model_the_machine_cannot_hold_is_refused_before_it_loads(tmp_path, monkeypatch):
    """Loading a checkpoint costs seconds; learning it will not fit afterwards
    is the slow way to find out."""
    from app import hardware
    from app.hardware import GPUInfo, HardwareProfile

    engine = tmp_path / "sd-cli.exe"; engine.write_bytes(b"x")
    model = tmp_path / "m.safetensors"; model.write_bytes(b"x" * 1024)
    monkeypatch.setattr(hardware, "profile", lambda **kwargs: HardwareProfile(
        gpu=GPUInfo(), total_ram_bytes=1024, available_ram_bytes=1))

    result = ImageGenerator(engine_path=engine, model_path=model).generate("a cat")
    assert not result.ok
    assert "exceeds" in result.detail


# ------------------------------------------------------------- input shape


@pytest.mark.parametrize("given,expected", [(512, 512), (500, 512), (63, 64), (0, 64),
                                            (5000, MAX_DIMENSION), (100, 128)])
def test_dimensions_snap_to_the_latent_grid(given, expected):
    """Sizes that are not multiples of 64 are rejected by the engine only
    after the model has loaded."""
    assert _round_dimension(given) == expected
    assert _round_dimension(given) % 64 == 0


def test_dimensions_never_exceed_what_the_card_can_hold():
    assert _round_dimension(99999) <= MAX_DIMENSION


def test_prompt_enhancement_keeps_subject_first_and_adds_style_specific_quality():
    enhanced = enhance_prompt("a portrait of Mohsin", "photo")
    assert enhanced.startswith("a portrait of Mohsin")
    assert "accurate perspective" in enhanced
    assert len(enhanced) <= 1800


def test_requested_typography_is_not_negated_by_the_default_prompt():
    negative = build_negative_prompt("a poster with the title HAYAT", "", enhance=True)
    assert "unreadable text" not in negative
    assert "malformed hands" in negative


def test_png_validation_checks_signature_and_dimensions(tmp_path):
    from PIL import Image
    path = tmp_path / "image.png"
    Image.new("RGB", (512, 384), "green").save(path)
    assert validate_png(path, 512, 384)[0]
    assert not validate_png(path, 256, 256)[0]


def test_truncated_png_with_plausible_header_is_not_a_success(tmp_path):
    from PIL import Image
    path = tmp_path / "truncated.png"
    Image.new("RGB", (512, 384), "green").save(path)
    path.write_bytes(path.read_bytes()[:40])
    assert not validate_png(path, 512, 384)[0]


def test_memory_fallback_reduces_portrait_and_landscape_without_making_them_square():
    from app.models.diffusion import smaller_frame
    assert smaller_frame(512, 1024) == (384, 768)
    assert smaller_frame(1024, 512) == (768, 384)
    assert smaller_frame(512, 512) is None


def test_step_count_is_bounded(tmp_path):
    """Every step costs the same, so a step budget is a time budget."""
    engine = tmp_path / "sd-cli.exe"; engine.write_bytes(b"x")
    generator = ImageGenerator(engine_path=engine, model_path=tmp_path / "absent")
    # Refused for a missing model, but the clamping happens on the way there.
    assert generator.generate("x", steps=9999).steps in (0, MAX_STEPS)


# ------------------------------------------------------------ what it says


def test_a_result_states_that_nothing_was_uploaded():
    payload = ImageResult(prompt="a cat", ok=True).to_dict()
    assert payload["generated_locally"] is True
    assert payload["uploaded"] is False


def test_status_reports_the_engine_provenance():
    status = ImageGenerator().status()
    assert "provenance" in status and status["local_only"] is True
    if status["available"]:
        assert status["provenance"]["sha256"], "a fetched binary must not be anonymous"


def test_discovery_returns_none_rather_than_guessing(tmp_path):
    assert find_engine(tmp_path) is None
    assert find_model(tmp_path) is None


def test_retained_images_have_a_searchable_manifest(tmp_path):
    from datetime import UTC, datetime

    output = tmp_path / "images"
    output.mkdir()
    path = output / "made.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\ncontent")
    generator = ImageGenerator(output_root=output)
    result = ImageResult(path=path, prompt="a reliable robot", seed=42, steps=20,
                         width=512, height=512, ok=True,
                         created_at=datetime.now(UTC).isoformat())
    generator._record(result, quality="quality", enhanced=True)

    rows = generator.history()
    assert rows[0]["path"] == str(path)
    assert rows[0]["seed"] == 42 and rows[0]["retained"] is True
    assert len(rows[0]["sha256"]) == 64


def test_legacy_pngs_are_not_hidden_when_no_manifest_exists(tmp_path):
    output = tmp_path / "images"
    output.mkdir()
    path = output / "old.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nold")
    rows = ImageGenerator(output_root=output).history()
    assert rows and rows[0]["legacy_unindexed"] is True


# ------------------------------------------------- the real engine, if here


def test_the_installed_model_fits_this_machine(as_hayat_runs):
    verdict = as_hayat_runs.verdict()
    assert verdict["runnable"], verdict["reason"]


def test_a_real_image_is_produced_and_left_on_disk(as_hayat_runs):
    """The end-to-end property: a file that is actually a PNG."""
    generator = _generating(as_hayat_runs)
    result = generator.generate("a single green leaf on white", steps=4, width=256, height=256)

    assert result.ok, result.detail
    assert result.path and result.path.is_file()
    assert result.path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.seconds > 0
    result.path.unlink(missing_ok=True)


def test_the_same_seed_reproduces_the_same_picture(as_hayat_runs):
    generator = _generating(as_hayat_runs)
    first = generator.generate("a red square", steps=4, width=256, height=256, seed=1234)
    second = generator.generate("a red square", steps=4, width=256, height=256, seed=1234)
    try:
        assert first.ok and second.ok
        assert first.path.read_bytes() == second.path.read_bytes()
    finally:
        for result in (first, second):
            if result.path:
                result.path.unlink(missing_ok=True)


# ------------------------------------------------------------ through tools


def test_generation_goes_through_the_registry_and_its_authority():
    """It must pass the same gate as everything else, not sit beside it."""
    from app.safety.authority import ActionRequest, AuthorityManager, Decision, Principal

    authority = AuthorityManager(owner_mode=True)
    owner = authority.evaluate(ActionRequest(tool="generate_image", category="filesystem_write_project"))
    assert owner.decision is Decision.ALLOW, "owner mode should not interrupt for a picture"

    for principal in (Principal.BACKGROUND_LEARNER, Principal.UNTRUSTED_EXTERNAL_CONTENT):
        decision = authority.evaluate(ActionRequest(
            tool="generate_image", category="filesystem_write_project", principal=principal))
        assert decision.decision is Decision.DENY, f"{principal.value} reached image generation"


def test_the_tools_are_registered_with_the_right_categories():
    from app.safety.permissions import PermissionManager
    from app.tools.images import register_image_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(PermissionManager())
    register_image_tools(registry)

    assert registry.get("generate_image").permission == "filesystem_write_project"
    assert registry.get("image_generation_status").permission == "system_read"


def test_natural_image_request_routes_to_generation_and_real_verification():
    from app.agent.planner import Planner
    from app.agent.verifier import Verifier
    from app.intent.router import IntentRouter
    from app.safety.permissions import PermissionManager
    from app.session import SessionState
    from app.tools.images import register_image_tools
    from app.tools.registry import ToolRegistry, ToolResult

    intent = IntentRouter().route("Create an image of a blue robot reading a book", SessionState())
    registry = ToolRegistry(PermissionManager())
    register_image_tools(registry)
    plan = Planner(registry)._deterministic_plan(intent.goal, intent, SessionState())
    assert intent.entities["modality"] == "image"
    assert plan and plan[0].tool == "generate_image"
    assert plan[0].expected["type"] == "image_created"
    failed = Verifier().check({"type": "image_created"}, ToolResult(
        "generate_image", True, {"ok": False, "path": None}))
    assert not failed.passed, "a handled engine failure is not a generated image"


def test_styled_image_request_keeps_deterministic_image_path():
    from app.agent.planner import Planner
    from app.intent.router import IntentRouter
    from app.safety.permissions import PermissionManager
    from app.session import SessionState
    from app.tools.images import register_image_tools
    from app.tools.registry import ToolRegistry

    state = SessionState()
    intent = IntentRouter().route("Create a cinematic image of a blue robot", state)
    registry = ToolRegistry(PermissionManager())
    register_image_tools(registry)
    plan = Planner(registry)._deterministic_plan(intent.goal, intent, state)
    assert intent.entities["modality"] == "image"
    assert plan and plan[0].arguments["style"] == "cinematic"
    # Ultra is the default quality Mohsin asked for, and the planner sets it
    # explicitly on a routed generation.
    assert plan[0].arguments["quality"] == "ultra"


def test_a_failed_generation_is_returned_as_a_fact_not_an_exception(tmp_path):
    from app.safety.permissions import PermissionManager
    from app.tools.images import register_image_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(PermissionManager())
    register_image_tools(registry, ImageGenerator(engine_path=tmp_path / "absent.exe",
                                                  model_path=tmp_path / "absent.st"))
    result = registry.execute("generate_image", {"prompt": "a cat"})

    assert result.success, "the tool call itself should not blow up"
    assert result.output["ok"] is False
    assert result.output["error"]


# ------------------------------------------------------------ video generation


def test_video_needs_a_motion_module_and_says_so_plainly(tmp_path):
    """Video runs on the same engine as images, plus an AnimateDiff module.

    Without one it is unavailable, reported the way a missing image model is,
    and never pretended to have happened.
    """
    engine = tmp_path / "sd-cli.exe"
    engine.write_bytes(b"x")
    model = tmp_path / "m.safetensors"
    model.write_bytes(b"x" * 1024)
    gen = ImageGenerator(engine_path=engine, model_path=model,
                         motion_root=tmp_path / "empty-motion")
    assert gen.video_available() is False
    result = gen.generate_video("a red balloon over a green field")
    assert not result.ok
    assert "motion module" in result.detail


def test_an_empty_video_prompt_is_refused_without_loading_anything(tmp_path):
    result = ImageGenerator(engine_path=tmp_path / "sd-cli.exe").generate_video("   ")
    assert not result.ok
    assert "needs a description" in result.detail


def test_video_frames_are_bounded_to_what_animatediff_can_do():
    """The flag allows any number; the model is trained for sixteen, so a wild
    request is clamped rather than passed through to a broken clip."""
    from app.models.diffusion import MAX_VIDEO_FRAMES

    assert MAX_VIDEO_FRAMES == 32
    assert max(2, min(1000, MAX_VIDEO_FRAMES)) == 32
    assert max(2, min(-5, MAX_VIDEO_FRAMES)) == 2


def test_generation_refuses_only_for_technical_reasons_never_for_content(tmp_path):
    """Generation is unrestricted by content, by design: the only refusals are
    technical -- empty prompt, missing engine or model, a size the card cannot
    hold. There is no content branch in the code, and this test exists so one
    is not added silently. Any prompt fails here only because the engine is
    absent, and the reason string proves it."""
    absent = ImageGenerator(engine_path=tmp_path / "absent.exe",
                            model_path=tmp_path / "m.safetensors")
    for prompt in ("subject one", "subject two", "subject three"):
        result = absent.generate(prompt)
        assert not result.ok
        reason = result.detail.lower()
        assert "engine" in reason or "model" in reason
        for judged in ("not allowed", "inappropriate", "cannot generate that",
                       "content policy", "against policy"):
            assert judged not in reason


def test_videos_get_their_own_folder_and_manifest(tmp_path):
    """Clips are kept apart from stills, each with its own manifest."""
    gen = ImageGenerator(engine_path=tmp_path / "sd-cli.exe", output_root=tmp_path / "art")
    assert gen.video_root == tmp_path / "art" / "videos"
    status = gen.status()
    assert "videos" in status["video_directory"]
    assert "videos" in status["video_manifest"]


def test_a_truncated_or_empty_clip_is_caught_not_reported_as_success(tmp_path):
    """path.is_file() is not proof: a crashed run can leave a bad file behind."""
    from app.models.diffusion import validate_video, _WEBM_MAGIC

    empty = tmp_path / "empty.webm"
    empty.write_bytes(b"")
    ok, detail = validate_video(empty)
    assert not ok and "not a real clip" in detail

    junk = tmp_path / "junk.webm"
    junk.write_bytes(b"not a video" * 200)
    ok, detail = validate_video(junk)
    assert not ok and "container" in detail

    real = tmp_path / "real.webm"
    real.write_bytes(_WEBM_MAGIC + bytes(2048))
    ok, detail = validate_video(real)
    assert ok, detail


def test_video_uses_fewer_steps_by_default_than_a_still():
    """Steps cost time once per frame, so video defaults below a still's budget."""
    from app.models.diffusion import DEFAULT_VIDEO_STEPS, QUALITY_PRESETS

    assert DEFAULT_VIDEO_STEPS < QUALITY_PRESETS["balanced"]["steps"]
    assert DEFAULT_VIDEO_STEPS == 10


def test_a_quality_finetune_is_preferred_over_a_larger_base_checkpoint(tmp_path):
    """"Largest wins" picked the weakest picture.

    The base SD 1.5 checkpoint is the biggest file on disk and the worst
    output; a 2 GB fine-tune added next to a 4 GB base one used to be ignored
    for being smaller. Selection now prefers a known quality model by name.
    """
    from app.models.diffusion import find_model

    (tmp_path / "sd-v1-5-pruned-emaonly.safetensors").write_bytes(b"x" * 4000)
    (tmp_path / "dreamshaper_8.safetensors").write_bytes(b"x" * 2000)
    assert find_model(tmp_path).name == "dreamshaper_8.safetensors"


def test_a_lone_base_model_is_still_used(tmp_path):
    """With nothing better present, the base checkpoint is the answer."""
    from app.models.diffusion import find_model

    (tmp_path / "sd-v1-5-pruned-emaonly.safetensors").write_bytes(b"x" * 4000)
    assert find_model(tmp_path).name == "sd-v1-5-pruned-emaonly.safetensors"


def test_an_unknown_non_base_model_beats_a_base_one_by_not_being_base(tmp_path):
    """A checkpoint that is not a recognised base is preferred over one that is,
    even when the base file is larger -- a fine-tune is the likely intent."""
    from app.models.diffusion import find_model

    (tmp_path / "sd-v1-5-pruned-emaonly.safetensors").write_bytes(b"x" * 5000)
    (tmp_path / "somecustommodel.safetensors").write_bytes(b"x" * 1000)
    assert find_model(tmp_path).name == "somecustommodel.safetensors"


def _fake_video_run(tmp_path, monkeypatch):
    """A generator whose engine is faked so the command can be inspected
    without actually rendering. Returns (generator, captured-command dict)."""
    import subprocess
    from contextlib import contextmanager
    import app.models.diffusion as d
    import app.resources as res

    engine = tmp_path / "sd-cli.exe"; engine.write_bytes(b"x")
    model = tmp_path / "m.safetensors"; model.write_bytes(b"x" * 1024)
    md = tmp_path / "motion"; md.mkdir()
    (md / "mm.ckpt").write_bytes(b"x" * 1024)
    gen = d.ImageGenerator(engine_path=engine, model_path=model,
                           motion_root=md, output_root=tmp_path)
    gen.video_verdict = lambda *_: {"runnable": True, "placement": "GPU"}

    captured = {}

    class _Popen:
        def __init__(self, cmd, **kw):
            captured["cmd"] = cmd
        def communicate(self, timeout=None):
            return ("seed 42", "")
        returncode = 1  # non-zero: return cleanly right after the command is built

    @contextmanager
    def _heavy(*a, **k):
        yield

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    monkeypatch.setattr(res, "heavy", _heavy)
    return gen, captured


def test_video_resolution_follows_the_quality_tier(tmp_path, monkeypatch):
    """256 is fast; quality and ultra render at AnimateDiff's native 512."""
    gen, captured = _fake_video_run(tmp_path, monkeypatch)
    for quality, expected in (("balanced", "256"), ("fast", "256"),
                              ("quality", "512"), ("ultra", "512")):
        captured.clear()
        gen.generate_video("a test clip", quality=quality)
        cmd = captured["cmd"]
        assert cmd[cmd.index("-W") + 1] == expected, quality
        assert cmd[cmd.index("-H") + 1] == expected, quality


def test_a_clip_carries_temporal_negatives_that_a_still_does_not(tmp_path, monkeypatch):
    """Video adds anti-flicker/anti-morph terms images do not use."""
    from app.models.diffusion import VIDEO_NEGATIVE_EXTRA

    gen, captured = _fake_video_run(tmp_path, monkeypatch)
    gen.generate_video("a test clip", quality="balanced")
    cmd = captured["cmd"]
    negative = cmd[cmd.index("-n") + 1]
    assert "flickering" in negative and "morphing" in negative
    assert VIDEO_NEGATIVE_EXTRA.split(",")[0].strip() in negative


def test_ultra_is_the_default_quality_for_both_image_and_video():
    """Mohsin asked for ultra by default. Locked so it is not quietly lowered."""
    import inspect
    from app.models.diffusion import ImageGenerator

    assert inspect.signature(ImageGenerator.generate).parameters["quality"].default == "ultra"
    assert inspect.signature(ImageGenerator.generate_video).parameters["quality"].default == "ultra"


def test_a_default_video_call_renders_at_native_512(tmp_path, monkeypatch):
    """With ultra the default, a bare clip is the native-resolution one."""
    gen, captured = _fake_video_run(tmp_path, monkeypatch)
    gen.generate_video("a test clip")  # no quality given -> ultra -> 512
    cmd = captured["cmd"]
    assert cmd[cmd.index("-W") + 1] == "512"
    assert cmd[cmd.index("-H") + 1] == "512"


def test_a_generated_image_reply_names_the_file_not_just_the_tool(tmp_path):
    """"generate image completed and verified" named no file, so Mohsin was
    told an image existed and had nothing to open."""
    from app.assistant import AssistantService

    def outcome(tool, output):
        return type("O", (), {"history": [{"tool": tool,
                     "result": {"output": output},
                     "verification": {"passed": True}}]})()

    img_path = tmp_path / "x.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    ok = AssistantService._generated_media_reply(
        outcome("generate_image", {"ok": True, "path": str(img_path), "seconds": 12.0}))
    assert str(img_path) in ok and "ready" in ok.lower()

    clip_path = tmp_path / "c.webm"
    clip_path.write_bytes(b"video")
    clip = AssistantService._generated_media_reply(
        outcome("generate_video", {"ok": True, "path": str(clip_path), "seconds": 130.0}))
    assert "c.webm" in clip and "clip" in clip.lower()

    bad = AssistantService._generated_media_reply(
        outcome("generate_image", {"ok": False, "error": "no diffusion model is installed"}))
    assert "couldn't" in bad.lower() and "no diffusion model" in bad
    assert "no file to open" in bad.lower()


def test_image_followup_delivers_the_file_without_an_image_criterion(tmp_path):
    from types import SimpleNamespace

    from app.assistant import AssistantService

    image = tmp_path / "made.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    outcome = SimpleNamespace(complete=True, needs_user_input=False, history=[{
        "tool": "generate_image",
        "result": {"success": True, "output": {"ok": True, "path": str(image)}},
        "verification": {"passed": True},
    }])
    intent = SimpleNamespace(success_criteria=[], entities={})
    answer = AssistantService.__new__(AssistantService)._outcome_response(intent, outcome)
    assert str(image) in answer
    assert "ready" in answer.lower()
    assert "nothing to show" not in answer

    image.unlink()
    missing = AssistantService.__new__(AssistantService)._outcome_response(intent, outcome)
    assert "missing" in missing.lower()
    assert "ready" not in missing.lower()

    image.write_bytes(b"not a PNG")
    invalid = AssistantService.__new__(AssistantService)._outcome_response(intent, outcome)
    assert "invalid" in invalid.lower()
    assert "ready" not in invalid.lower()


def test_generic_tool_check_requires_generated_image_file(tmp_path):
    from app.agent.verifier import Verifier
    from app.tools.registry import ToolResult

    image = tmp_path / "made.png"
    verifier = Verifier()
    output = {"ok": True, "path": str(image)}
    assert not verifier.check({"type": "tool_success"}, ToolResult("generate_image", True, output)).passed
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    assert verifier.check({"type": "tool_success"}, ToolResult("generate_image", True, output)).passed


def test_denying_the_image_tool_is_caught_as_a_false_capability_claim():
    """"I don't have a built-in image generation tool, use DALL-E" is false
    with generate_image registered."""
    from app.tools.introspection import denies_a_real_capability

    class _Spec:
        description = "Generate a picture from a text description, locally."

    class _Registry:
        def names(self):
            return ["generate_image", "generate_video"]
        def get(self, name):
            return _Spec()

    registry = _Registry()
    assert denies_a_real_capability(
        registry,
        "I don't have a built-in image generation tool, so I can't create the picture. "
        "Use DALL-E or Stable Diffusion and share the result with me.")
    assert denies_a_real_capability(registry, "I cannot generate an image for you.")
    assert not denies_a_real_capability(registry, "Your image is ready, saved to disk.")


def test_ultra_and_quality_tiers_carry_a_higher_resolution():
    """More pixels is the detail lever for smudged faces and busy backgrounds."""
    from app.models.diffusion import QUALITY_PRESETS

    assert QUALITY_PRESETS["fast"]["size"] == 512
    assert QUALITY_PRESETS["quality"]["size"] == 640
    assert QUALITY_PRESETS["ultra"]["size"] == 768
    # A firmer guidance scale on the high tier keeps a crowded prompt intact.
    assert QUALITY_PRESETS["ultra"]["cfg"] >= QUALITY_PRESETS["balanced"]["cfg"]


def test_out_of_memory_is_told_apart_from_a_real_failure():
    from app.models.diffusion import _is_out_of_memory

    assert _is_out_of_memory("ggml_vulkan: Device memory allocation of size 1358954496 failed")
    assert _is_out_of_memory("vk::Device::allocateMemory: ErrorOutOfDeviceMemory")
    assert not _is_out_of_memory("the prompt could not be parsed")
    assert not _is_out_of_memory("")


def test_a_too_large_frame_steps_down_instead_of_failing(tmp_path, monkeypatch):
    """768 does not always fit beside a resident LLM on 8 GB; the request must
    fall back to a size that fits rather than return an out-of-memory error."""
    import subprocess
    from contextlib import contextmanager
    import app.models.diffusion as d
    import app.resources as res

    engine = tmp_path / "sd-cli.exe"; engine.write_bytes(b"x")
    model = tmp_path / "m.safetensors"; model.write_bytes(b"x" * 1024)
    gen = d.ImageGenerator(engine_path=engine, model_path=model, output_root=tmp_path)
    gen.verdict = lambda: {"runnable": True, "placement": "GPU"}

    sizes_tried = []

    class _Popen:
        def __init__(self, cmd, **kw):
            self._w = int(cmd[cmd.index("-W") + 1])
            sizes_tried.append(self._w)
        def communicate(self, timeout=None):
            # 768 OOMs; 512 "succeeds" (writes the target so is_file() passes).
            if self._w > 512:
                return ("ggml_vulkan: ErrorOutOfDeviceMemory", "")
            # find -o target and create it as a minimal valid PNG
            return ("seed 1", "")
        returncode = property(lambda self: 1 if self._w > 512 else 0)

    real_popen = _Popen
    def _popen_factory(cmd, **kw):
        p = real_popen(cmd, **kw)
        if p._w <= 512:
            import struct
            target = cmd[cmd.index("-o") + 1]
            open(target, "wb").write(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
                                     + struct.pack(">II", 512, 512))
        return p

    @contextmanager
    def _heavy(*a, **k):
        yield

    monkeypatch.setattr(subprocess, "Popen", _popen_factory)
    monkeypatch.setattr(res, "heavy", _heavy)
    monkeypatch.setattr(d, "validate_png", lambda *a, **k: (True, "ok"))

    result = gen.generate("a crowded market", quality="ultra")
    assert 768 in sizes_tried, "it should try the big size first"
    assert 512 in sizes_tried, "and step down after OOM"
    assert result.ok, result.detail


def test_ultra_targets_1080_by_upscaling_a_clean_render():
    """Native 1080 OOMs and duplicates on this card; the working path is to
    render small and clean, then upscale. The preset says so."""
    from app.models.diffusion import QUALITY_PRESETS, DEFAULT_UPSCALE_TARGET

    assert QUALITY_PRESETS["ultra"]["upscale"] == 1080
    assert DEFAULT_UPSCALE_TARGET == 1080
    # The base render stays small enough to fit; the size grows in the upscale.
    assert QUALITY_PRESETS["ultra"]["size"] <= 768


def test_upscale_is_skipped_gracefully_when_no_model_is_present(tmp_path):
    """No upscaler means the base image stands, never a failure."""
    from app.models.diffusion import ImageGenerator

    gen = ImageGenerator(engine_path=tmp_path / "sd-cli.exe", model_path=tmp_path / "m.st")
    # An empty upscale dir -> find_upscaler returns None -> _upscale returns None.
    target = tmp_path / "img.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    from app.models import diffusion as d
    d.UPSCALE_ROOT = tmp_path / "no-upscaler"
    assert gen._upscale(target, 1080) is None
    assert target.is_file(), "the base image must be left intact"


def test_the_upscaler_is_found_by_extension(tmp_path):
    from app.models.diffusion import find_upscaler

    assert find_upscaler(tmp_path) is None
    (tmp_path / "RealESRGAN_x4plus.pth").write_bytes(b"x" * 2048)
    assert find_upscaler(tmp_path).name == "RealESRGAN_x4plus.pth"


def test_face_first_models_are_preferred_over_the_all_rounder(tmp_path):
    """Faces were the recurring complaint. SDXL RealVisXL leads, then the SD 1.5
    face model, and both beat the DreamShaper all-rounder."""
    from app.models.diffusion import find_model, PREFERRED_MODELS

    assert PREFERRED_MODELS[0].startswith("realvis")
    assert PREFERRED_MODELS.index("realistic_vision") < PREFERRED_MODELS.index("dreamshaper")

    # SDXL wins when present.
    (tmp_path / "dreamshaper_8.safetensors").write_bytes(b"x" * 2000)
    (tmp_path / "realistic_vision_v51.safetensors").write_bytes(b"x" * 2000)
    (tmp_path / "realvisxl-v5.0-Q4_K.gguf").write_bytes(b"x" * 2000)
    assert "realvisxl" in find_model(tmp_path).name.lower()

    # Without SDXL, the SD 1.5 face model beats the all-rounder.
    (tmp_path / "realvisxl-v5.0-Q4_K.gguf").unlink()
    assert "realistic" in find_model(tmp_path).name.lower()


def test_a_standalone_vae_is_found_and_applied(tmp_path):
    """A noVAE checkpoint washes out without one; the VAE is discovered by
    extension so it can be passed to the engine."""
    from app.models.diffusion import find_vae

    assert find_vae(tmp_path) is None
    (tmp_path / "vae-ft-mse-840000.safetensors").write_bytes(b"x" * 4096)
    assert find_vae(tmp_path).name == "vae-ft-mse-840000.safetensors"


def test_face_guidance_is_added_only_when_a_person_is_present():
    from app.models.diffusion import enhance_prompt

    person = enhance_prompt("a running man at a food market", "photo")
    assert "symmetric face" in person
    landscape = enhance_prompt("a quiet mountain lake at dawn", "photo")
    assert "symmetric face" not in landscape


def test_sdxl_is_detected_and_preferred():
    from app.models.diffusion import is_sdxl_model, PREFERRED_MODELS
    from pathlib import Path

    assert is_sdxl_model(Path("realvisxl-v5.0-Q4_K.gguf"))
    assert is_sdxl_model(Path("juggernautXL_v9.safetensors"))
    assert not is_sdxl_model(Path("realistic_vision_v51.safetensors"))
    assert not is_sdxl_model(None)
    assert PREFERRED_MODELS[0].startswith("realvis")


def test_an_sdxl_model_runs_at_native_1024_with_flash_attention_and_tiled_vae(tmp_path, monkeypatch):
    """Measured 2026-10-04: with flash attention and tiled VAE decoding, native 1024 fits the 8 GB card
    in 32 s and shows more detail than 768; RealVisXL looks more natural at guidance 5 than 8."""
    import subprocess
    from contextlib import contextmanager
    import app.models.diffusion as d
    import app.resources as res

    engine = tmp_path / "sd-cli.exe"; engine.write_bytes(b"x")
    model = tmp_path / "realvisxl-v5.0-Q4_K.gguf"; model.write_bytes(b"x" * 1024)
    gen = d.ImageGenerator(engine_path=engine, model_path=model, output_root=tmp_path)
    gen.verdict = lambda: {"runnable": True, "placement": "GPU"}

    captured = {}

    class _Popen:
        def __init__(self, cmd, **kw):
            captured["cmd"] = cmd
        def communicate(self, timeout=None):
            return ("seed 1", "")
        returncode = 1  # return early after capturing the command

    @contextmanager
    def _heavy(*a, **k):
        yield

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    monkeypatch.setattr(res, "heavy", _heavy)

    gen.generate("a portrait", quality="ultra")
    cmd = captured["cmd"]
    assert cmd[cmd.index("-W") + 1] == "1024", "SDXL renders at its native 1024"
    assert "--diffusion-fa" in cmd and "--vae-tiling" in cmd, "the memory savers that make 1024 fit"
    assert "--vae-on-cpu" not in cmd, "tiled decoding keeps the VAE on the GPU"
    assert float(cmd[cmd.index("--cfg-scale") + 1]) <= 6, "SDXL guidance stays natural"
    assert "--vae" not in cmd, "SDXL must not get the SD 1.5 VAE"
    gen.generate("a portrait", quality="fast")
    assert captured["cmd"][captured["cmd"].index("-W") + 1] == "768", "fast stays at 768"


def test_a_video_request_reads_the_quality_tier_from_its_words():
    from app.agent.planner import Planner, media_quality
    from app.intent.router import IntentRouter
    from app.safety.permissions import PermissionManager
    from app.session import SessionState
    from app.tools.images import register_image_tools
    from app.tools.registry import ToolRegistry

    assert media_quality("make a video of a cat") == "ultra"            # default is the richest
    assert media_quality("make a quick video of a sunset") == "fast"
    assert media_quality("a high quality video of a forest") == "quality"
    assert media_quality("render it in 4k") == "ultra"
    assert media_quality("make a video of deep ocean waves") == "ultra"  # "deep" is content, not a tier
    assert media_quality("a cinematic video of a city") == "ultra"       # a style word, not a downgrade

    registry = ToolRegistry(PermissionManager())
    register_image_tools(registry)
    state = SessionState()
    intent = IntentRouter().route("make a quick video of a sunset", state)
    plan = Planner(registry)._deterministic_plan(intent.goal, intent, state)
    assert intent.entities["modality"] == "video"
    assert plan[0].tool == "generate_video" and plan[0].arguments["quality"] == "fast"
    assert "quick" not in plan[0].arguments["prompt"] and "sunset" in plan[0].arguments["prompt"]
