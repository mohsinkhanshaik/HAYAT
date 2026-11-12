"""Pictures come out in the shape the request names, and the chat card can open, copy, reveal and remake them."""
from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager

import pytest

import app.models.diffusion as d
from app.gui_image_card import generation_record, preview_box


@pytest.mark.parametrize("prompt, ratio", [
    ("a 16:9 desktop wallpaper of Lake Michigan", "16:9"),
    ("phone wallpaper of the Milky Way", "9:16"),
    ("portrait of an old fisherman", "2:3"),
    ("a panorama of the Alps", "21:9"),
    ("a logo for a coffee shop", "1:1"),
    ("a city at night, 1920x1080", "16:9"),
    ("a city at night, 1080 x 1920", "9:16"),
    ("a movie poster for a space film", "2:3"),
])
def test_the_shape_is_read_from_the_request(prompt, ratio):
    assert d.aspect_ratio(prompt) == ratio


def test_no_shape_word_keeps_the_square_default():
    assert d.aspect_ratio("a red apple on a table") is None


def test_shapes_use_trained_frames_scaled_to_the_tier():
    assert d.frame_for("16:9", 1024) == (1344, 768)
    assert d.frame_for("9:16", 1024) == (768, 1344)
    assert d.frame_for("21:9", 1024) == (1536, 640)
    # The fast tier's smaller budget keeps the shape.
    width, height = d.frame_for("16:9", 768)
    assert width > height and width % 64 == 0 and height % 64 == 0


def _fake_engine(tmp_path, monkeypatch, sizes):
    import app.resources as res

    engine = tmp_path / "sd-cli.exe"
    engine.write_bytes(b"x")
    model = tmp_path / "realvisxl-v5.gguf"
    model.write_bytes(b"x" * 1024)
    gen = d.ImageGenerator(engine_path=engine, model_path=model, output_root=tmp_path)
    gen.verdict = lambda: {"runnable": True, "placement": "GPU"}

    class _Popen:
        returncode = 0

        def __init__(self, cmd, **_kw):
            sizes.append((int(cmd[cmd.index("-W") + 1]), int(cmd[cmd.index("-H") + 1])))
            open(cmd[cmd.index("-o") + 1], "wb").write(b"png")

        def communicate(self, timeout=None):
            return ("seed 3", "")

    @contextmanager
    def _heavy(*_a, **_k):
        yield

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    monkeypatch.setattr(res, "heavy", _heavy)
    monkeypatch.setattr(d, "validate_png", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(d, "is_sdxl_model", lambda path: True)
    return gen


def test_a_widescreen_request_renders_a_widescreen_frame(tmp_path, monkeypatch):
    sizes = []
    gen = _fake_engine(tmp_path, monkeypatch, sizes)
    assert gen.generate("a 16:9 wallpaper of a lighthouse", quality="balanced").ok
    assert gen.generate("a lighthouse", quality="balanced").ok
    assert gen.generate("a 16:9 wallpaper of a lighthouse", quality="balanced", width=512, height=512).ok
    # Shaped when asked, square otherwise, and an explicit size always wins.
    assert sizes == [(1344, 768), (1024, 1024), (512, 512)]


def test_preview_follows_the_conversation_width_within_bounds():
    assert preview_box(400) == (380, 260)
    assert preview_box(820) == (680, 430)
    assert preview_box(3000) == (760, 480)


def test_the_generation_record_is_found_for_a_generated_picture_only(tmp_path):
    picture = tmp_path / "lighthouse-1234.png"
    picture.write_bytes(b"png")
    (tmp_path / "manifest.jsonl").write_text(
        json.dumps({"path": str(picture), "prompt": "a lighthouse at dusk", "seed": 7}) + "\n", encoding="utf-8")
    assert generation_record(picture)["prompt"] == "a lighthouse at dusk"
    assert generation_record(tmp_path / "other.png") == {}
    assert generation_record(tmp_path / "missing" / "x.png") == {}


@pytest.mark.parametrize("prompt, wanted", [
    ("a wooden shop sign that says HAYAT CAFE", True),
    ('a poster with the text "Grand Opening"', True),
    ("a birthday cake with the words happy birthday", True),
    ("a lighthouse at dusk", False),
    ("a red sports car", False),
])
def test_a_request_for_lettering_is_recognised(prompt, wanted):
    assert d.asks_for_text(prompt) is wanted


def test_the_reply_warns_when_the_picture_was_asked_to_contain_words(tmp_path, monkeypatch):
    from app.assistant import AssistantService
    from app.agent import verifier

    png = tmp_path / "sign.png"
    png.write_bytes(b"png")
    monkeypatch.setattr(verifier.Verifier, "check", lambda self, *a, **k: type("V", (), {"passed": True})())
    reply = AssistantService._format_generated_media("generate_image", {
        "ok": True, "path": str(png), "seconds": 27, "prompt": "a shop sign that says HAYAT CAFE"})
    assert reply.startswith("Your image is ready in 27s. Note: the image model draws letters as shapes")
    plain = AssistantService._format_generated_media("generate_image", {
        "ok": True, "path": str(png), "seconds": 27, "prompt": "a lighthouse"})
    assert plain == f"Your image is ready in 27s. Saved to {png}"


# ------------------------------------------------------------------ Z-Image Turbo, once proven
def _z_files(folder, engine):
    for name in ("z_image_turbo-Q4_K.gguf", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "ae.safetensors"):
        (folder / name).write_bytes(b"x" * 64)
    (folder / "ready.json").write_text(json.dumps({"engine": str(engine)}), encoding="utf-8")


def test_z_image_is_only_found_after_its_setup_proved_it(tmp_path):
    engine = tmp_path / "sd-cli.exe"
    engine.write_bytes(b"x")
    folder = tmp_path / "z-image"
    folder.mkdir()
    for name in ("z_image_turbo-Q4_K.gguf", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "ae.safetensors"):
        (folder / name).write_bytes(b"x")
    assert d.find_z_image(folder) is None  # downloaded but not proven: no ready.json
    _z_files(folder, engine)
    found = d.find_z_image(folder)
    assert found["engine"] == engine and found["model"].name.startswith("z_image")
    (folder / "ae.safetensors").unlink()
    assert d.find_z_image(folder) is None  # a missing part disables it again


def test_a_proven_z_image_renders_with_its_documented_settings(tmp_path, monkeypatch):
    sizes = []
    gen = _fake_engine(tmp_path, monkeypatch, sizes)
    commands = []
    real_popen = subprocess.Popen

    def capture(cmd, **kw):
        commands.append(cmd)
        return real_popen(cmd, **kw)

    monkeypatch.setattr(subprocess, "Popen", capture)
    z_engine = tmp_path / "new" / "sd-cli.exe"
    z_engine.parent.mkdir()
    z_engine.write_bytes(b"x")
    folder = tmp_path / "z"
    folder.mkdir()
    _z_files(folder, z_engine)
    gen.z_image = d.find_z_image(folder)
    monkeypatch.delenv("HAYAT_IMAGE_MODEL", raising=False)
    assert gen.generate("a 16:9 sign that says HAYAT", quality="balanced").ok
    cmd = commands[-1]
    assert cmd[0] == str(z_engine) and "--llm" in cmd and "--diffusion-model" in cmd and "--offload-to-cpu" in cmd
    assert cmd[cmd.index("--cfg-scale") + 1] == "1.0" and cmd[cmd.index("--steps") + 1] == "8"
    assert cmd[cmd.index("-p") + 1] == "a 16:9 sign that says HAYAT"  # no SD tag list for its language model
    assert "-n" not in cmd and sizes[-1] == (1344, 768)
    monkeypatch.setenv("HAYAT_IMAGE_MODEL", "sdxl")
    assert gen.generate("a lighthouse", quality="balanced").ok
    assert commands[-1][0] != str(z_engine) and "--llm" not in commands[-1]


def test_sdxl_stays_on_its_recorded_engine_when_a_newer_build_sits_beside_it(tmp_path):
    old = tmp_path / "stable-diffusion.cpp-master-820-de298c2-vulkan-x64"
    new = tmp_path / "stable-diffusion.cpp-master-1030-0a06712-vulkan-x64"
    for folder in (old, new):
        folder.mkdir()
        (folder / "sd-cli.exe").write_bytes(b"x")
    (tmp_path / "provenance-sd.json").write_text(json.dumps({"extracted_to": str(old)}), encoding="utf-8")
    assert d.find_engine(tmp_path) == old / "sd-cli.exe"
    (tmp_path / "provenance-sd.json").unlink()
    assert d.find_engine(tmp_path).name == "sd-cli.exe"
