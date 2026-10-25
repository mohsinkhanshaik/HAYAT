"""Making pictures locally, on an engine this runtime owns.

Same shape as the local language engine, for the same reasons: a prebuilt
binary fetched once with its checksum recorded, a model file on this disk, and
a child process started and stopped by HAYAT. Nothing is uploaded, no account
exists, and no service outside this machine is involved -- a prompt describing
something private stays private, which is the whole point of generating images
here rather than somewhere convenient.

Sizing comes from the measured machine rather than from constants. On this
8GB card SD 1.5 fits and SDXL does not, and the difference between those two
facts is the difference between a picture in twenty seconds and a failure
after a long wait. `HardwareProfile.can_run` already answers that question, so
this asks it instead of guessing.

Measured on this machine, Vulkan backend: 512x512 at 12 steps in 23.2s,
sampling at 3.3 iterations per second.
"""
from __future__ import annotations

import json
import hashlib
import logging
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.logging import log_swallowed
from app.models.video_generation import (
    check_video_cancelled, probe_generated_video, reserve_video_memory, run_video_process, wait_video_process,
)

_log = logging.getLogger(__name__)


ENGINE_ROOT = PROJECT_ROOT / "data" / "engines"
MODEL_ROOT = PROJECT_ROOT / "data" / "models" / "diffusion"
MOTION_ROOT = PROJECT_ROOT / "data" / "models" / "motion"
UPSCALE_ROOT = PROJECT_ROOT / "data" / "models" / "upscale"
VAE_ROOT = PROJECT_ROOT / "data" / "models" / "vae"
#: A short clip, not a film. AnimateDiff on SD 1.5 is trained for 16 frames;
#: past ~32 the motion falls apart and the VRAM cost climbs, so the ceiling is
#: honest about what the model can actually do rather than what the flag allows.
DEFAULT_VIDEO_FRAMES, MAX_VIDEO_FRAMES = 16, 32
DEFAULT_FPS = 8
IMAGE_ROOT = PROJECT_ROOT / "data" / "artifacts" / "images"
IMAGE_MANIFEST = IMAGE_ROOT / "manifest.jsonl"
#: Clips live apart from stills, so a folder of pictures is not diluted with
#: video and each has its own manifest to browse.
VIDEO_ROOT = PROJECT_ROOT / "data" / "artifacts" / "videos"
#: Preserve the existing quick per-frame budget. The installed engine's
#: reference-quality examples use 20-25 steps; callers can select that budget
#: explicitly. The short default is an execution-time choice, not a claim
#: that more steps cannot improve quality.
DEFAULT_VIDEO_STEPS = 10
#: Faults that only a moving picture has. AnimateDiff on SD 1.5 tends to
#: flicker, morph a subject between frames, and ghost edges; naming them in the
#: negative prompt measurably steadies the clip. Kept separate from the image
#: negatives because "motion blur" is a fault in a still and often wanted in a
#: clip, so the two lists must not be shared.
VIDEO_NEGATIVE_EXTRA = (
    "flickering, flicker, temporal artifacts, morphing, warping between frames, "
    "ghosting, frame jitter, inconsistent lighting, duplicate frames, "
    "shape shifting, unstable geometry, wobbling"
)

#: Generation is measured in iterations, and every one costs the same. A step
#: budget is therefore a time budget, and this is the range where more steps
#: still visibly improve the picture.
MIN_STEPS, MAX_STEPS, DEFAULT_STEPS = 1, 50, 20

# Each tier now also carries a default resolution. More pixels is the lever
# that actually fixes the reported faults -- a face or a background food stall
# rendered into a 512 frame gets too few pixels to hold together and smudges;
# the same subject at 768 has over twice the area to resolve into. The engine's
# own highres-fix flags are silently ignored by this build, so generating at
# the higher size directly is the reliable way to get the detail. cfg climbs
# with the tier too: a firmer guidance scale makes the model honour more of a
# crowded prompt, which is why the food stalls and the waving figure were
# dropping out of the busier scenes.
QUALITY_PRESETS = {
    "fast": {"steps": 12, "sampler": "euler_a", "scheduler": "discrete", "cfg": 7.0, "size": 512},
    "balanced": {"steps": 24, "sampler": "dpm++2m", "scheduler": "karras", "cfg": 7.0, "size": 512},
    "quality": {"steps": 36, "sampler": "dpm++2m", "scheduler": "karras", "cfg": 7.5, "size": 640},
    # The best this engine gives on a single image: full step budget, the
    # convergent sampler, 768 px for real detail, and a firmer cfg so a busy
    # prompt keeps all of its subjects. Slower, and worth it when the picture
    # matters more than the wait.
    "ultra": {"steps": 50, "sampler": "dpm++2m", "scheduler": "karras", "cfg": 8.0,
              "size": 768, "upscale": 1080},
}
#: The long edge an ultra picture is upscaled to after rendering. 1080, at
#: Mohsin's request. The base is rendered small and clean, then an ESRGAN pass
#: enlarges it and a final resize lands it exactly here.
DEFAULT_UPSCALE_TARGET = 1080
#: SDXL renders at its native 1024, so "ultra" enlarges it to 2048 instead.
SDXL_UPSCALE_TARGET = 2048
#: SDXL frame per tier: native 1024 except "fast". Measured on the 8 GB RTX 5060
#: on 2026-10-04 with flash attention and tiled VAE decoding: 1024 in 32 s,
#: 768 in 22 s, with visibly more detail at 1024.
SDXL_TIER_SIZE = {"fast": 768, "balanced": 1024, "quality": 1024, "ultra": 1024}
#: SDXL guidance per tier. At 7-8 RealVisXL overcooked skin and colour; 5 read
#: as more natural in a side-by-side on the same seeds.
SDXL_CFG = {"fast": 5.0, "balanced": 5.0, "quality": 5.5, "ultra": 5.5}
#: How much of the super-resolution pass to keep when enlarging. ESRGAN alone
#: sharpens edges but smooths skin to plastic; half of it over a Lanczos
#: enlargement keeps the edges and the texture.
UPSCALE_BLEND = 0.5
PROMPT_STYLES = {
    "auto": "coherent composition, clear subject, fine detail, natural lighting",
    "photo": "professional photography, realistic materials, natural light, sharp focus",
    "cinematic": "cinematic composition, motivated lighting, atmospheric depth, film still",
    "illustration": "polished illustration, confident shapes, harmonious color palette, fine detail",
    "concept": "production concept art, strong silhouette, environmental storytelling, detailed",
}
STYLE_QUALITY = {
    "auto": "balanced visual hierarchy, physically coherent details",
    "photo": "accurate perspective, natural skin and material texture, realistic depth of field",
    "cinematic": "intentional camera angle, controlled contrast, layered foreground and background",
    "illustration": "clean edges, deliberate linework, readable shapes",
    "concept": "functional design language, scale cues, cohesive environment",
}
#: Everything Stable Diffusion 1.5 gets wrong unprompted. Longer than it looks
#: because each entry buys something specific: the anatomy terms fix hands and
#: limbs, the frame terms stop borders and captions being drawn into the
#: picture, and the quality terms pull it away from the thumbnail-grade output
#: it settles on when told nothing.
DEFAULT_NEGATIVE = (
    "blurry, out of focus, low quality, worst quality, jpeg artifacts, noisy, grainy, "
    "distorted anatomy, deformed, disfigured, bad proportions, extra limbs, missing limbs, "
    "malformed hands, extra fingers, fused fingers, mutated hands, "
    # Faces get the most negative weight because they are where SD 1.5 fails
    # most visibly -- the smudged, melted, asymmetric face is the single
    # complaint that kept coming back.
    "poorly drawn face, deformed face, disfigured face, blurry face, melted face, "
    "asymmetric face, mutated face, extra faces, bad eyes, deformed eyes, crossed eyes, "
    "duplicate subject, cloned face, cropped, out of frame, border, frame, watermark, "
    "signature, username, text, caption, unreadable text, logo, "
    "oversaturated, washed out, flat lighting, ugly, tiling"
)

#: What a prompt is asking for, when it says so. Checked before the caller's
#: choice of "auto" is honoured: someone who wrote "a photo of" has already
#: said which style they want more clearly than any default can guess.
_STYLE_HINTS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("photo", re.compile(r"(?i)\b(?:photo|photograph|photorealistic|realistic|portrait|"
                         r"headshot|dslr|macro|candid|street photography)\b")),
    ("cinematic", re.compile(r"(?i)\b(?:cinematic|film still|movie|dramatic lighting|"
                             r"widescreen|anamorphic|noir|epic)\b")),
    ("illustration", re.compile(r"(?i)\b(?:illustration|drawing|painting|watercolou?r|"
                                r"anime|manga|cartoon|comic|sketch|vector|flat design)\b")),
    ("concept", re.compile(r"(?i)\b(?:concept art|character design|environment design|"
                           r"matte painting|key art|splash art)\b")),
)


def infer_style(prompt: str) -> str:
    """The style a prompt is plainly asking for, or "auto".

    "a photo of a rusted bridge at dawn" was getting the generic treatment,
    because the caller passed auto and auto means no opinion. The prompt had
    an opinion.
    """
    value = str(prompt or "")
    for style, pattern in _STYLE_HINTS:
        if pattern.search(value):
            return style
    return "auto"


#: A prompt that has a person in it, so the face guidance is worth adding.
#: Faces are where Stable Diffusion 1.5 fails most visibly, and a scene with a
#: small face -- a figure in a crowd, someone waving in the background -- is the
#: worst case, which is exactly what Mohsin kept seeing smudged.
_HAS_PERSON = re.compile(
    r"(?i)\b(?:person|people|man|men|woman|women|boy|girl|child|kid|guy|lady|"
    r"face|portrait|selfie|human|figure|crowd|player|worker|model|soldier|"
    r"king|queen|dancer|singer|actor|actress|she|running|walking|waving|"
    r"standing|sitting|smiling|posing|jogging)\b")
#: Extra face guidance, added only when a person is present. Kept short because
#: SD 1.5's text encoder has a small effective window.
_FACE_GUIDANCE = "detailed symmetric face, clear eyes, natural skin texture, sharp facial features"


def enhance_prompt(prompt: str, style: str) -> str:
    """Build a concise quality prompt without replacing the user's subject."""
    guidance = [PROMPT_STYLES[style], STYLE_QUALITY[style], "high quality"]
    if _HAS_PERSON.search(prompt):
        guidance.append(_FACE_GUIDANCE)
    folded = prompt.casefold()
    additions = [item for item in guidance if item.casefold() not in folded]
    # Stable Diffusion 1.5 has a short effective context. Keep the user's text
    # first and bound added guidance instead of drowning the subject in tags.
    return ", ".join([prompt, *additions])[:1800]


def build_negative_prompt(prompt: str, user_negative: str, *, enhance: bool) -> str:
    """Apply defaults, but do not forbid typography when typography was asked for."""
    pieces = [item.strip() for item in DEFAULT_NEGATIVE.split(",") if item.strip()] if enhance else []
    if re.search(r"(?i)\b(?:text|typography|poster|logo|wording|sign|label|title)\b", prompt):
        forbidden = {"text", "caption", "unreadable text", "logo"}
        pieces = [item for item in pieces if item.casefold() not in forbidden]
    if user_negative.strip():
        pieces = [*user_negative.split(","), *pieces]
    unique: list[str] = []
    seen: set[str] = set()
    for item in pieces:
        clean = item.strip()
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            unique.append(clean)
    return ", ".join(unique)


def validate_png(path: Path, expected_width: int, expected_height: int) -> tuple[bool, str]:
    """Verify the complete image, including compressed pixels, before reporting success."""
    try:
        with path.open("rb") as source:
            header = source.read(24)
    except OSError as exc:
        return False, f"generated file could not be read: {exc}"
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return False, "image engine output is not a valid PNG header"
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    if (width, height) != (expected_width, expected_height):
        return False, f"image dimensions are {width}x{height}, expected {expected_width}x{expected_height}"
    try:
        from PIL import Image
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
    except Exception as exc:
        return False, f"image engine output could not be decoded: {type(exc).__name__}"
    return True, "valid, decoded PNG"


def smaller_frame(width: int, height: int) -> tuple[int, int] | None:
    """Reduce OOM pixel demand while keeping portrait/landscape composition."""
    long_edge = max(width, height)
    if long_edge <= 512:
        return None
    target = 768 if long_edge > 768 else 640 if long_edge > 640 else 512
    scale = target / long_edge
    return _round_dimension(width * scale), _round_dimension(height * scale)


#: The two containers stable-diffusion.cpp writes video into. Each has an
#: unmistakable magic number at the start of the file, which is enough to tell
#: a real clip from an empty or truncated file the engine left behind when it
#: died mid-write -- the reliability failure this catches.
_WEBM_MAGIC = b"\x1a\x45\xdf\xa3"  # EBML / Matroska-WebM
_AVI_MAGIC = b"RIFF"


def _is_out_of_memory(output: str | None) -> bool:
    """Whether the engine died for want of VRAM rather than for a real fault.

    Distinguished so a too-large frame can be retried one size down instead of
    reported as a failure. Any other error is a real error and is reported.
    """
    text = (output or "").lower()
    return any(marker in text for marker in (
        "outofdevicememory", "out of device memory", "allocatememory",
        "device memory allocation of size", "failed to allocate", "out of memory",
    ))


def validate_video(path: Path, minimum_bytes: int = 1024) -> tuple[bool, str]:
    """Confirm a generated clip is a real, non-empty video container.

    A crashed or out-of-memory run can leave a zero-byte or half-written file
    that still exists on disk, so `path.is_file()` is not proof of success.
    Checking the magic number and a floor size turns a silent bad clip into a
    plain failure the caller can report.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as source:
            header = source.read(12)
    except OSError as exc:
        return False, f"generated video could not be read: {exc}"
    if size < minimum_bytes:
        return False, f"generated video is only {size} bytes, which is not a real clip"
    if header[:4] != _WEBM_MAGIC and header[:4] != _AVI_MAGIC and header[4:8] != b"ftyp":
        return False, "video engine output is not a recognised video container"
    return True, "valid video"

#: Dimensions must be multiples of 64 for the latent grid to divide evenly;
#: anything else is rejected by the engine after the model has loaded, which
#: is a slow way to learn about a typo.
SIZE_MULTIPLE = 64
MAX_DIMENSION = 1024


def find_engine(root: Path | None = None) -> Path | None:
    """Locate a fetched sd-cli binary, or say plainly that there is none."""
    base = root or ENGINE_ROOT
    if not base.is_dir():
        return None
    candidates = sorted(base.glob("stable-diffusion*/sd-cli.exe")) or sorted(base.glob("*/sd-cli"))
    return candidates[-1] if candidates else None


#: Community fine-tunes that are a clear step up from base SD 1.5, in
#: preference order. The base checkpoint (`sd-v1-5`, `emaonly`) is the largest
#: file on disk but the weakest picture, so "largest wins" picked exactly the
#: wrong one once a better, smaller model was added. A named preference fixes
#: that: a quality model is used when present, whatever its size, and the size
#: heuristic only decides between unknown checkpoints.
#: Realistic Vision leads because faces are where SD 1.5 fails most visibly and
#: it is the strongest of these at a real human face; DreamShaper stays as a
#: capable all-rounder behind it. Order is preference, not quality ranking for
#: every subject -- a fantasy scene may look better on DreamShaper -- but the
#: recurring complaint was smudged faces, and this is the answer to that.
PREFERRED_MODELS = ("realvisxl", "realvis", "juggernautxl", "juggernaut-xl",
                    "realistic_vision", "realistic-vision", "dreamshaper",
                    "epicrealism", "deliberate", "revanimated", "cyberrealistic")
#: The mark of a plain base checkpoint, which should never be preferred over a
#: fine-tune even though it is often the biggest file present.
_BASE_MODEL = ("sd-v1-5", "sd_v1-5", "v1-5-pruned", "emaonly", "sd-v1.5", "512-base")


def find_model(root: Path | None = None) -> Path | None:
    """The best diffusion checkpoint on this disk, not merely the biggest.

    Prefers a known quality fine-tune by name, then any non-base checkpoint,
    and only falls back to largest-wins among plain base models -- so adding a
    better 2 GB model actually replaces a weaker 4 GB base one instead of being
    ignored for being smaller.
    """
    base = root or MODEL_ROOT
    if not base.is_dir():
        return None
    models = [path for path in base.iterdir()
              if path.suffix.lower() in {".safetensors", ".ckpt", ".gguf"} and path.is_file()]
    if not models:
        return None
    for preferred in PREFERRED_MODELS:
        for path in models:
            if preferred in path.name.lower():
                return path
    non_base = [p for p in models if not any(tag in p.name.lower() for tag in _BASE_MODEL)]
    pool = non_base or models
    return max(pool, key=lambda path: path.stat().st_size)


#: Checkpoints AnimateDiff animates well, best first. Its motion modules are
#: trained on SD 1.5, so an SDXL checkpoint cannot be animated with them.
VIDEO_PREFERRED_MODELS = ("dreamshaper", "realistic_vision", "realistic-vision", "epicrealism", "revanimated",
                          "deliberate", "cyberrealistic")


def find_video_model(root: Path | None = None) -> Path | None:
    """The SD 1.5 checkpoint to animate, or None when only SDXL models are installed.

    Video used the image model. Once the image model became RealVisXL (SDXL),
    every clip paired an SDXL checkpoint with an SD 1.5 motion module, which
    cannot work; the last clip made was in August, with DreamShaper.
    """
    base = root or MODEL_ROOT
    if not base.is_dir():
        return None
    models = [path for path in base.iterdir() if path.is_file()
              and path.suffix.lower() in {".safetensors", ".ckpt", ".gguf"} and not is_sdxl_model(path)]
    for preferred in VIDEO_PREFERRED_MODELS:
        for path in models:
            if preferred in path.name.lower():
                return path
    non_base = [path for path in models if not any(tag in path.name.lower() for tag in _BASE_MODEL)]
    pool = non_base or models
    return max(pool, key=lambda path: path.stat().st_size) if pool else None


def smooth_video(source: Path, *, width: int = 512, fps: int = 24, min_width: int = 512,
                 target_width: int | None = None, sharpen: bool = False, timeout: float = 300.0) -> Path | None:
    """A smoother, sharper MP4 of a generated clip: in-between frames to ``fps`` and small clips enlarged.

    AnimateDiff makes 8 frames a second, which reads as stutter. Motion-
    compensated interpolation fills the gaps; a small clip is enlarged with
    Lanczos so it is not a postage stamp; H.264 MP4 plays everywhere. The
    original is kept. Returns None when ffmpeg is missing or fails, and the
    caller then uses the original clip unchanged.

    The deep tier passes a higher ``fps``, a larger ``target_width`` and
    ``sharpen`` so the clip is a crisper, higher-frame-rate, larger picture --
    all on the CPU in ffmpeg, so it costs seconds, not GPU time or VRAM.
    """
    try:
        from app.video_workflow import executable

        ffmpeg = executable("ffmpeg")
    except Exception:
        ffmpeg = None
    if not ffmpeg:
        return None
    target = source.with_name(source.stem + "-smooth.mp4")
    final_width = max(min_width, width, target_width or 0)
    filters = []
    if final_width > width:
        filters.append(f"scale={final_width}:-2:flags=lanczos")
    filters.append(f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
    if sharpen:
        # A gentle unsharp mask; AnimateDiff frames are soft and interpolation softens them further.
        filters.append("unsharp=5:5:0.8:3:3:0.4")
    command = [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(source), "-vf", ",".join(filters),
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18" if not sharpen else "16",
               "-preset", "medium", "-movflags", "+faststart", str(target)]
    try:
        completed = run_video_process(command, timeout)
    except Exception:
        target.unlink(missing_ok=True)
        check_video_cancelled()
        return None
    if completed.returncode != 0 or not validate_video(target)[0]:
        target.unlink(missing_ok=True)
        return None
    return target


def is_sdxl_model(path: Path | None) -> bool:
    """Whether a checkpoint is an SDXL model, which changes how it must run.

    SDXL is trained at 1024 and has its own latent space, so it wants a bigger
    frame and must not be handed the SD 1.5 VAE. Detected by name, since the
    quantised GGUF files carry "xl" in theirs. A false negative just means it
    is treated as SD 1.5; a false positive would ask an SD 1.5 model for 1024,
    which the OOM step-down would walk back anyway.
    """
    if path is None:
        return False
    name = path.name.lower()
    return "xl" in name or "sdxl" in name


def find_vae(root: Path | None = None) -> Path | None:
    """A standalone VAE, which is what gives a "noVAE" checkpoint real colour.

    Realistic Vision ships without a baked VAE, so on its own its output is
    washed out and grey. Pairing it with the standard SD 1.5 VAE fixes that.
    A checkpoint that already has its own VAE is unaffected by also being
    handed this one.
    """
    base = root or VAE_ROOT
    if not base.is_dir():
        return None
    models = [path for path in base.iterdir()
              if path.suffix.lower() in {".safetensors", ".ckpt", ".pt", ".gguf"} and path.is_file()]
    return max(models, key=lambda path: path.stat().st_size) if models else None


def find_upscaler(root: Path | None = None) -> Path | None:
    """An ESRGAN upscaler model, which is how a small clean render becomes 1080.

    SD 1.5 cannot draw a good native 1080 on an 8 GB card -- it runs out of
    memory and, past ~768, duplicates the subject. The working path to a big
    picture is to render it small and clean, then upscale it with a dedicated
    super-resolution model, which is cheap and adds detail rather than smearing
    it. Without a model here that second pass is simply skipped.
    """
    base = root or UPSCALE_ROOT
    if not base.is_dir():
        return None
    models = [path for path in base.iterdir()
              if path.suffix.lower() in {".pth", ".safetensors", ".gguf", ".bin"} and path.is_file()]
    return max(models, key=lambda path: path.stat().st_size) if models else None


#: Preserve selection among legacy top-level modules. New optional downloads
#: live in optional/ and require explicit selection; a version preference is
#: not a visual-quality benchmark.
MOTION_PREFERRED = ("v3_sd15_mm", "v3_sd15", "animatediffmotion_v3", "mm_sd_v15_v2", "mm-sd-v15-v2",
                    "mm_sd_v15", "mm-sd-v15", "mm_sd_v14", "improved")


def find_motion_module(root: Path | None = None) -> Path | None:
    """An AnimateDiff motion module, which is what turns still frames into video.

    The same stable-diffusion.cpp engine that makes images has a `vid_gen`
    mode; it needs a motion module alongside the SD 1.5 checkpoint and runs on
    the same Vulkan GPU, so no CUDA and no second engine are required. Legacy
    top-level modules retain their preference order. Optional modules are
    exposed by select_motion_module instead. Without any default module, video
    generation is simply unavailable, reported as plainly as a missing image model.
    """
    base = root or MOTION_ROOT
    if not base.is_dir():
        return None
    modules = [path for path in base.iterdir()
               if path.suffix.lower() in {".safetensors", ".ckpt", ".gguf"} and path.is_file()]
    for preferred in MOTION_PREFERRED:
        for path in modules:
            if preferred in path.name.lower().replace(" ", "_"):
                return path
    return max(modules, key=lambda path: path.stat().st_size) if modules else None


def installed_motion_modules(root: Path) -> dict[str, Path]:
    """Installed local choices, including explicit opt-in modules in optional/."""
    base = root.resolve()
    candidates = [*root.glob("*"), *root.glob("optional/*")]
    return {path.relative_to(root).as_posix(): path for path in sorted(candidates)
            if path.is_file() and path.suffix.lower() in {".ckpt", ".safetensors", ".gguf"}
            and path.resolve().is_relative_to(base)}


def select_motion_module(root: Path, selection: str = "auto") -> Path | None:
    if selection in {"", "auto"}:
        return find_motion_module(root)
    choices = installed_motion_modules(root)
    aliases = {"v2": "mm_sd_v15_v2.ckpt", "v3": "optional/v3_sd15_mm.ckpt"}
    selected = choices.get(aliases.get(selection, selection))
    if selected is None:
        raise ValueError("Choose an installed motion module from image_generation_status.video.motion_modules")
    return selected


def engine_provenance() -> dict[str, Any]:
    try:
        return json.loads((ENGINE_ROOT / "provenance-sd.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@dataclass
class ImageResult:
    """What was made, and what it cost."""

    path: Path | None = None
    prompt: str = ""
    seed: int | None = None
    steps: int = 0
    width: int = 0
    height: int = 0
    seconds: float = 0.0
    ok: bool = False
    detail: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    video: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "path": str(self.path) if self.path else None, "prompt": self.prompt[:500],
            "seed": self.seed, "steps": self.steps, "width": self.width,
            "height": self.height, "seconds": round(self.seconds, 1), "ok": self.ok,
            "detail": self.detail[:400], "created_at": self.created_at,
            "generated_locally": True, "uploaded": False,
        }
        if self.video is not None:
            payload["video"] = self.video
        return payload


def _round_dimension(value: int) -> int:
    """Snap to the latent grid, and keep it inside what the card can hold."""
    value = max(SIZE_MULTIPLE, min(int(value), MAX_DIMENSION))
    return int(round(value / SIZE_MULTIPLE) * SIZE_MULTIPLE)


class ImageGenerator:
    """Text to image, entirely on this machine."""

    def __init__(self, engine_path: Path | None = None, model_path: Path | None = None,
                 output_root: Path | None = None, timeout: float = 900.0,
                 motion_root: Path | None = None, video_root: Path | None = None) -> None:
        self.engine_path = engine_path or find_engine()
        self.model_path = model_path or find_model()
        self.output_root = output_root or IMAGE_ROOT
        self.motion_root = motion_root or MOTION_ROOT
        # Videos default to their own folder, but follow a custom output_root
        # when one is given so a test's tmp dir keeps images and clips together.
        self.video_root = video_root or (output_root / "videos" if output_root else VIDEO_ROOT)
        self.timeout = timeout
        self._process: subprocess.Popen | None = None

    # ----------------------------------------------------------- readiness

    @property
    def available(self) -> bool:
        return bool(self.engine_path and self.engine_path.is_file()
                    and self.model_path and self.model_path.is_file())

    def verdict(self) -> dict[str, Any]:
        """Whether this machine can actually run the model that is installed."""
        if not self.model_path or not self.model_path.is_file():
            return {"runnable": False, "reason": "no diffusion model is installed",
                    "placement": "NONE", "expect": "unavailable"}
        from app.hardware import profile

        return profile(refresh=True).can_run(self.model_path.stat().st_size)

    def status(self) -> dict[str, Any]:
        verdict = self.verdict()
        return {
            "available": self.available,
            "engine": str(self.engine_path) if self.engine_path else None,
            "model": self.model_path.name if self.model_path else None,
            "model_gb": round(self.model_path.stat().st_size / 1024**3, 2)
            if self.model_path and self.model_path.is_file() else None,
            "placement": verdict.get("placement"), "expect": verdict.get("expect"),
            "output_directory": str(self.output_root),
            "manifest": str(self.output_root / "manifest.jsonl"),
            "video_directory": str(self.video_root),
            "video_manifest": str(self.video_root / "manifest.jsonl"),
            "video_available": self.video_available(),
            "video": self.video_status(),
            "motion_module": (find_motion_module(self.motion_root).name
                              if find_motion_module(self.motion_root) else None),
            "upscaler": (find_upscaler(UPSCALE_ROOT).name if find_upscaler(UPSCALE_ROOT) else None),
            "ultra_upscales_to": (SDXL_UPSCALE_TARGET if is_sdxl_model(self.model_path)
                                  else DEFAULT_UPSCALE_TARGET),
            "quality_presets": QUALITY_PRESETS,
            "provenance": {key: engine_provenance().get(key) for key in ("tag", "sha256", "backend")},
            "local_only": True,
        }

    # ---------------------------------------------------------- generation

    def suggest_quality(self) -> str:
        """Pick a preset from what the GPU has free right now.

        Thirty-six steps on a card with a language model already holding most
        of its eight gigabytes means falling back to the CPU, where the same
        picture takes minutes instead of seconds. Asking for less when there
        is less is not a worse picture -- it is a picture that arrives.
        """
        try:
            from app.hardware import profile

            free = float(getattr(profile().gpu, "free_bytes", 0) or 0)
        except Exception:
            return "balanced"
        if free >= 6 * 1024**3:
            return "quality"
        if free >= 3.5 * 1024**3:
            return "balanced"
        return "fast"

    def generate(self, prompt: str, *, negative: str = "", steps: int | None = None,
                 width: int | None = None, height: int | None = None, seed: int | None = None,
                 name: str = "", quality: str = "ultra", enhance: bool = True,
                 style: str = "auto") -> ImageResult:
        """Make one image. Every refusal happens before the model is loaded.

        Loading a checkpoint costs seconds, so an unusable request is rejected
        first: an empty prompt, a machine that cannot hold the model, a size
        the latent grid cannot divide. Learning about a typo after a minute of
        loading is the failure mode this ordering exists to avoid.
        """
        prompt = " ".join(str(prompt).split()).strip()
        if not prompt:
            return ImageResult(prompt=prompt, detail="an image needs a description")
        if not self.engine_path or not self.engine_path.is_file():
            return ImageResult(prompt=prompt, detail="no image engine has been fetched into data/engines")
        if not self.model_path or not self.model_path.is_file():
            return ImageResult(prompt=prompt, detail="no diffusion model is installed in data/models/diffusion")

        verdict = self.verdict()
        released_models: list[str] = []
        if verdict.get("placement") == "GPU_AFTER_FREEING":
            released_models = self._release_idle_models(list(verdict.get("free_first") or []))
            if released_models:
                verdict = self.verdict()
        if not verdict["runnable"]:
            return ImageResult(prompt=prompt, detail=verdict["reason"])

        quality = quality.lower().strip()
        if quality in {"", "auto"}:
            capacity = self.suggest_quality()
            feedback = getattr(self, "feedback", None)
            learned = feedback.recommend("image") if feedback is not None else {}
            preferred = str(learned.get("quality") or "") if learned.get("trusted") else ""
            order = {"fast": 0, "balanced": 1, "quality": 2, "ultra": 3}
            quality = preferred if preferred in order and order[preferred] <= order.get(capacity, 1) else capacity
        if quality not in QUALITY_PRESETS:
            quality = "balanced"
        preset = QUALITY_PRESETS[quality]
        if steps is None:
            steps = int(preset["steps"])
        steps = max(MIN_STEPS, min(int(steps), MAX_STEPS))
        # Resolution follows the tier when the caller did not ask for one: 512
        # fast, 640 quality, 768 ultra. A higher frame is the detail lever, and
        # the tier the owner chose is the clearest signal of how much detail he
        # wants to wait for.
        # SDXL renders at 768 here, not its native 1024: on an 8 GB card the
        # 1024 diffusion buffer will not fit even with the language model
        # evicted, and the ESRGAN pass takes the finished 768 up to 1080
        # anyway. 768 SDXL already beats 512 SD 1.5 on faces, which is the whole
        # reason for it. SD 1.5 keeps its tier size.
        sdxl = is_sdxl_model(self.model_path)
        tier_size = SDXL_TIER_SIZE.get(quality, 1024) if sdxl else int(preset.get("size", 512))
        cfg = SDXL_CFG.get(quality, 5.0) if sdxl else float(preset["cfg"])
        width = tier_size if width is None else width
        height = tier_size if height is None else height
        width, height = _round_dimension(width), _round_dimension(height)
        style = style.lower().strip()
        if style in {"", "auto"}:
            # "auto" means no opinion, and the prompt usually has one.
            style = infer_style(prompt)
        if style not in PROMPT_STYLES:
            style = "auto"

        generation_prompt = enhance_prompt(prompt, style) if enhance else prompt
        negative = build_negative_prompt(prompt, negative, enhance=enhance)

        self.output_root.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", (name or prompt)[:48]).strip("-").lower() or "image"
        target = self.output_root / f"{stem}-{uuid.uuid4().hex[:8]}.png"

        command = [
            str(self.engine_path), "-M", "img_gen", "-m", str(self.model_path),
            "-p", generation_prompt, "--steps", str(steps), "-W", str(width), "-H", str(height),
            "--sampling-method", str(preset["sampler"]), "--scheduler", str(preset["scheduler"]),
            "--cfg-scale", str(cfg), "--auto-fit",
            "-o", str(target),
        ]
        if sdxl:
            # Flash attention shrinks the diffusion buffer and tiled decoding
            # lets the VAE run on the GPU instead of the CPU; together they fit
            # native 1024 on the 8 GB card. Its VAE is baked in, so the SD 1.5
            # one is not added.
            command += ["--diffusion-fa", "--vae-tiling"]
        else:
            vae = find_vae(VAE_ROOT)
            if vae is not None:
                command += ["--vae", str(vae)]
        if negative.strip():
            command += ["-n", negative.strip()]
        if seed is not None:
            command += ["-s", str(int(seed))]

        started = time.perf_counter()
        # Queue behind any other heavy job. A diffusion checkpoint and a
        # language model loading into the same few free gigabytes do not fail
        # politely; they take the machine with them.
        from app.resources import NotEnoughMemory, heavy

        model_bytes = self.model_path.stat().st_size
        try:
            with heavy(f"image: {prompt[:40]}", needs_bytes=model_bytes):
                self._process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                output, _ = self._process.communicate(timeout=self.timeout)
                code = self._process.returncode
        except NotEnoughMemory as exc:
            return ImageResult(prompt=prompt, steps=steps, width=width, height=height,
                               seconds=time.perf_counter() - started, detail=str(exc))
        except subprocess.TimeoutExpired:
            self._stop()
            return ImageResult(prompt=prompt, steps=steps, width=width, height=height,
                               seconds=time.perf_counter() - started,
                               detail=f"generation exceeded {self.timeout:.0f}s and was stopped")
        except Exception as exc:
            return ImageResult(prompt=prompt, detail=f"could not start the image engine: {exc}")
        finally:
            self._process = None

        elapsed = time.perf_counter() - started
        # A bigger frame is the detail lever, but 768 does not always fit beside
        # a resident language model on an 8 GB card, and the engine answers that
        # with ErrorOutOfDeviceMemory rather than with a smaller picture. Step
        # down and try again instead of handing back a failure for a request
        # that succeeds one size smaller: the best quality that actually fits.
        fallback = smaller_frame(width, height)
        if (code != 0 or not target.is_file()) and _is_out_of_memory(output) and fallback:
            target.unlink(missing_ok=True)
            return self.generate(
                prompt, negative=negative, steps=steps, width=fallback[0], height=fallback[1],
                seed=seed, name=name, quality=quality, enhance=enhance, style=style,
            )
        if code != 0 or not target.is_file():
            tail = " ".join((output or "").strip().splitlines()[-3:])[:400]
            return ImageResult(prompt=prompt, steps=steps, width=width, height=height,
                               seconds=elapsed, detail=tail or f"the engine exited with code {code}")

        valid, validation_detail = validate_png(target, width, height)
        if not valid:
            target.unlink(missing_ok=True)
            return ImageResult(prompt=prompt, steps=steps, width=width, height=height,
                               seconds=elapsed, detail=validation_detail)

        # The final pass: enlarge the clean small render to the target long edge
        # with a super-resolution model. Best-effort -- if there is no upscaler,
        # or it fails, the base image stands rather than the whole request. The
        # returned width/height reflect what is actually on disk.
        final_width, final_height = width, height
        upscale_target = int(preset.get("upscale", 0) or 0)
        if upscale_target and sdxl:
            upscale_target = SDXL_UPSCALE_TARGET
        if upscale_target and max(width, height) < upscale_target:
            upscaled = self._upscale(target, upscale_target)
            if upscaled:
                final_width, final_height = upscaled

        result = ImageResult(
            path=target, prompt=prompt, seed=_extract_seed(output) if seed is None else seed,
            steps=steps, width=final_width, height=final_height,
            seconds=time.perf_counter() - started, ok=True,
            detail=f"generated in {time.perf_counter() - started:.1f}s at {final_width}x{final_height}",
        )
        self._record(
            result, quality=quality, enhanced=enhance, negative=negative, style=style,
            sampler=preset["sampler"], scheduler=preset["scheduler"], cfg_scale=cfg,
            base_size=f"{width}x{height}", generation_prompt=generation_prompt,
            released_models=released_models,
        )
        return result

    def _upscale(self, target: Path, long_edge: int) -> tuple[int, int] | None:
        """Enlarge an image in place to `long_edge` on its longer side.

        Runs the ESRGAN model (a 4x pass) then resizes down to the requested
        size with a good filter, which is sharper than either alone. Returns
        the new dimensions, or None if there is no upscaler or the pass failed
        -- in which case the original file is left untouched.
        """
        upscaler = find_upscaler(UPSCALE_ROOT)
        if upscaler is None:
            return None
        enlarged = target.with_name(f"{target.stem}-4x.png")
        command = [
            str(self.engine_path), "-M", "upscale",
            "--upscale-model", str(upscaler), "-i", str(target), "-o", str(enlarged),
        ]
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=self.timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode != 0 or not enlarged.is_file():
                return None
            from PIL import Image

            with Image.open(enlarged) as image, Image.open(target) as original:
                image.load()
                original.load()
                ratio = long_edge / max(image.width, image.height)
                size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
                sharp = image.convert("RGB").resize(size, Image.LANCZOS)
                plain = original.convert("RGB").resize(size, Image.LANCZOS)
                Image.blend(plain, sharp, UPSCALE_BLEND).save(target)
            enlarged.unlink(missing_ok=True)
            return size
        except Exception:
            enlarged.unlink(missing_ok=True)
            return None

    def video_available(self) -> bool:
        return bool(self.video_status()["available"])

    def video_verdict(self, video_model: Path, motion: Path) -> dict[str, Any]:
        from app.hardware import profile

        return profile(refresh=True).can_run(video_model.stat().st_size + motion.stat().st_size)

    def video_status(self) -> dict[str, Any]:
        from app.video_workflow import executable

        model = find_video_model(self.model_path.parent) if self.model_path else None
        motion = find_motion_module(self.motion_root)
        reason = ("video engine missing" if not self.engine_path or not self.engine_path.is_file()
                  else "compatible SD 1.5 checkpoint missing" if model is None
                  else "AnimateDiff motion module missing" if motion is None
                  else "FFprobe is required to verify output" if not executable("ffprobe") else "ready")
        return {"available": reason == "ready", "reason": reason, "model": model.name if model else None,
                "motion_module": motion.name if motion else None,
                "motion_modules": list(installed_motion_modules(self.motion_root)),
                "optional_modules_require_selection": True,
                "sampler": "euler", "scheduler": "discrete", "cfg_scale": 8.0,
                "native_output": "avi", "local_only": True}

    def generate_video(self, prompt: str, *, negative: str = "", frames: int | None = None,
                       fps: int | None = None, steps: int | None = None,
                       width: int | None = None, height: int | None = None, seed: int | None = None,
                       name: str = "", quality: str = "ultra", enhance: bool = True,
                       style: str = "auto", motion_module: str = "auto") -> ImageResult:
        """Make a short clip from a text description, locally, on the GPU.

        The same engine and the same SD 1.5 checkpoint as a still image, plus
        an AnimateDiff motion module -- `-M vid_gen`, which runs on Vulkan, so
        the CPU-only PyTorch on this machine is irrelevant. Content is not
        filtered here any more than it is for images; the refusals are all
        technical, and they happen before the model loads.
        """
        prompt = " ".join(str(prompt).split()).strip()
        check_video_cancelled()
        if not prompt:
            return ImageResult(prompt=prompt, detail="a video needs a description")
        if not self.engine_path or not self.engine_path.is_file():
            return ImageResult(prompt=prompt, detail="no image engine has been fetched into data/engines")
        if not self.model_path or not self.model_path.is_file():
            return ImageResult(prompt=prompt, detail="no diffusion model is installed in data/models/diffusion")
        try:
            motion = select_motion_module(self.motion_root, motion_module)
        except ValueError as exc:
            return ImageResult(prompt=prompt, detail=str(exc))
        if motion is None:
            return ImageResult(prompt=prompt, detail="no AnimateDiff motion module is installed in "
                                                     "data/models/motion; video generation needs one")
        video_model = find_video_model(self.model_path.parent)
        if video_model is None:
            return ImageResult(prompt=prompt, detail="video needs an SD 1.5 checkpoint in data/models/diffusion; "
                                                     "the AnimateDiff motion module cannot animate an SDXL model")

        from app.video_workflow import executable
        if not executable("ffprobe"):
            return ImageResult(prompt=prompt, detail="FFprobe is required to verify the generated video")
        verdict = self.video_verdict(video_model, motion)
        released_models: list[str] = []
        if verdict.get("placement") == "GPU_AFTER_FREEING":
            released_models = self._release_idle_models(list(verdict.get("free_first") or []))
            if released_models:
                verdict = self.video_verdict(video_model, motion)
        if not verdict["runnable"]:
            return ImageResult(prompt=prompt, detail=verdict["reason"])

        quality = quality.lower().strip()
        if quality in {"", "auto"}:
            capacity = self.suggest_quality()
            feedback = getattr(self, "feedback", None)
            learned = feedback.recommend("video") if feedback is not None else {}
            preferred = str(learned.get("quality") or "") if learned.get("trusted") else ""
            order = {"fast": 0, "balanced": 1, "quality": 2, "ultra": 3}
            quality = preferred if preferred in order and order[preferred] <= order.get(capacity, 1) else capacity
        if quality not in QUALITY_PRESETS:
            quality = "balanced"
        # Steps cost time once per frame, so a still's budget is the wrong one
        # for a clip. When the caller says nothing, use the video default, not
        # the preset's still-image steps -- the single biggest speed lever.
        steps = DEFAULT_VIDEO_STEPS if steps is None else steps
        steps = max(MIN_STEPS, min(int(steps), MAX_STEPS))
        frames = DEFAULT_VIDEO_FRAMES if frames is None else int(frames)
        frames = max(2, min(frames, MAX_VIDEO_FRAMES))
        fps = DEFAULT_FPS if fps is None else max(1, min(int(fps), 30))
        # Resolution is the biggest quality lever, and AnimateDiff is trained at
        # 512, so its own native size is also its best. 512 is slow on an 8 GB
        # card -- about ten minutes a clip -- but it is what "quality" and
        # "ultra" ask for, and it only became possible once the lighter
        # DreamShaper checkpoint freed the VRAM the old base model ate. Anything
        # not explicitly sized defaults to fast 256 unless a quality tier was
        # requested. Explicit multiples of 64, including v2's native 384, work.
        native = quality in {"quality", "ultra"}
        if width is None:
            width = 512 if native else 256
        if height is None:
            height = 512 if native else 256
        width, height = _round_dimension(width), _round_dimension(height)
        style = style.lower().strip()
        if style in {"", "auto"}:
            style = infer_style(prompt)
        if style not in PROMPT_STYLES:
            style = "auto"

        generation_prompt = enhance_prompt(prompt, style) if enhance else prompt
        negative = build_negative_prompt(prompt, negative, enhance=enhance)
        # Steady the motion. Appended after the shared image negatives so a
        # clip carries both the still-quality terms and the temporal ones.
        if enhance:
            negative = f"{negative}, {VIDEO_NEGATIVE_EXTRA}" if negative else VIDEO_NEGATIVE_EXTRA

        self.video_root.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", (name or prompt)[:48]).strip("-").lower() or "video"
        target = self.video_root / f"{stem}-{uuid.uuid4().hex[:8]}.avi"

        command = [
            str(self.engine_path), "-M", "vid_gen", "-m", str(video_model),
            "--motion-module", str(motion), "--video-frames", str(frames), "--fps", str(fps),
            "-p", generation_prompt, "--steps", str(steps), "-W", str(width), "-H", str(height),
            "--sampling-method", "euler", "--scheduler", "discrete",
            "--cfg-scale", "8.0", "--auto-fit", "-o", str(target),
        ]
        vae = find_vae(VAE_ROOT)
        if vae is not None:
            command += ["--vae", str(vae)]
        if negative.strip():
            command += ["-n", negative.strip()]
        if seed is not None:
            command += ["-s", str(int(seed))]

        from app.resources import NotEnoughMemory

        # Frames multiply the working set; the motion module rides on top of the
        # checkpoint, so reserve for both.
        need = video_model.stat().st_size + motion.stat().st_size
        started = time.perf_counter()
        process = None
        try:
            with reserve_video_memory(f"video: {prompt[:40]}", needs_bytes=need):
                check_video_cancelled()
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self._process = process
                output, _ = wait_video_process(process, self.timeout)
                code = process.returncode
        except NotEnoughMemory as exc:
            return ImageResult(prompt=prompt, steps=steps, width=width, height=height,
                               seconds=time.perf_counter() - started, detail=str(exc))
        except subprocess.TimeoutExpired:
            target.unlink(missing_ok=True)
            return ImageResult(prompt=prompt, steps=steps, width=width, height=height,
                               seconds=time.perf_counter() - started,
                               detail=f"video generation exceeded {self.timeout:.0f}s and was stopped")
        except Exception as exc:
            target.unlink(missing_ok=True)
            check_video_cancelled()
            return ImageResult(prompt=prompt, detail=f"could not start the video engine: {exc}")
        finally:
            if self._process is process:
                self._process = None

        elapsed = time.perf_counter() - started
        if code != 0 or not target.is_file():
            tail = " ".join((output or "").strip().splitlines()[-3:])[:400]
            return ImageResult(prompt=prompt, steps=steps, width=width, height=height,
                               seconds=elapsed, detail=tail or f"the engine exited with code {code}")

        valid, validation_detail = validate_video(target)
        if not valid:
            target.unlink(missing_ok=True)
            return ImageResult(prompt=prompt, steps=steps, width=width, height=height,
                               seconds=elapsed, detail=validation_detail)

        try:
            raw_metadata = probe_generated_video(target)
        except Exception as exc:
            target.unlink(missing_ok=True)
            check_video_cancelled()
            return ImageResult(prompt=prompt, detail=str(exc), seconds=time.perf_counter() - started)

        # The deep tiers get a richer finish: 30 fps, upscaled to 720/768 and lightly sharpened. It is a
        # CPU ffmpeg pass, so it adds seconds, not GPU time, and does not risk the 8 GB card.
        deep = quality in {"quality", "ultra"}
        smooth_fps = 30 if deep else 24
        try:
            smoothed = smooth_video(target, width=width, fps=smooth_fps,
                                    target_width=min(width * 2, 768 if quality == "ultra" else 720) if deep else None,
                                    sharpen=deep)
            delivered_metadata = probe_generated_video(smoothed) if smoothed else raw_metadata
            check_video_cancelled()
        except Exception:
            target.with_name(target.stem + "-smooth.mp4").unlink(missing_ok=True)
            try:
                check_video_cancelled()
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            smoothed, delivered_metadata = None, raw_metadata
        delivered = smoothed or target
        elapsed = time.perf_counter() - started
        result = ImageResult(
            path=delivered, prompt=prompt, seed=_extract_seed(output) if seed is None else seed,
            steps=steps, width=delivered_metadata["width"], height=delivered_metadata["height"], seconds=elapsed, ok=True,
            video=delivered_metadata | {"model": video_model.name, "motion_module": motion.relative_to(self.motion_root).as_posix(),
                                      "generation_width": width, "generation_height": height,
                                      "generation_frames": frames, "generation_fps": fps,
                                      "sampler": "euler", "scheduler": "discrete", "cfg_scale": 8.0},
            detail=(f"{frames} frames at {fps} fps, smoothed to {smooth_fps} fps" + (" and sharpened" if deep else "")
                    + f", in {elapsed:.1f}s" if smoothed
                    else f"{frames} frames at {fps} fps in {elapsed:.1f}s"),
        )
        check_video_cancelled()
        self._record(
            result, into=self.video_root, kind="video", quality=quality, enhanced=enhance,
            negative=negative, style=style, frames=delivered_metadata["frames"], fps=delivered_metadata["fps"],
            motion_module=motion.relative_to(self.motion_root).as_posix(),
            generation_prompt=generation_prompt, released_models=released_models,
            model=video_model.name, original=str(target) if smoothed else None,
        )
        return result

    @staticmethod
    def _release_idle_models(models: list[str]) -> list[str]:
        """Ask Ollama to unload named resident models before diffusion.

        This is enabled only by the explicit resource setting. It does not
        stop Ollama, delete a model, or kill a process; the next language turn
        can load the same model normally.
        """
        from app.config import load_settings

        if not load_settings().get("resources", {}).get("automatic_model_eviction", False):
            return []
        if not models:
            return []
        import os
        import httpx

        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        released: list[str] = []
        for model in models:
            try:
                response = httpx.post(
                    base + "/api/generate",
                    json={"model": model, "prompt": "", "keep_alive": 0, "stream": False},
                    timeout=15,
                )
                response.raise_for_status()
                released.append(model)
            except Exception:
                log_swallowed(_log, "unloading a model to free VRAM")
                continue
        return released

    def video_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Clips made so far, from the video manifest."""
        manifest = self.video_root / "manifest.jsonl"
        rows: list[dict[str, Any]] = []
        if manifest.is_file():
            for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        return rows[-max(1, min(int(limit), 200)):]

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        manifest = self.output_root / "manifest.jsonl"
        rows: list[dict[str, Any]] = []
        if manifest.is_file():
            for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        recorded = {str(item.get("path", "")).lower() for item in rows}
        # Older builds retained the PNG but did not maintain an index. Surface
        # those files honestly as legacy artifacts instead of making them look
        # lost; prompt/seed are unknown and therefore left unknown.
        if self.output_root.is_dir():
            for path in sorted(self.output_root.glob("*.png"), key=lambda item: item.stat().st_mtime):
                if str(path).lower() in recorded:
                    continue
                rows.append({
                    "path": str(path), "prompt": None, "seed": None,
                    "created_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "retained": True, "legacy_unindexed": True,
                })
        return rows[-max(1, min(int(limit), 200)):]

    def _record(self, result: ImageResult, *, into: Path | None = None, **metadata: Any) -> None:
        """Persist provenance beside the artifact so generations are findable.

        `into` names the folder whose manifest this belongs in -- images and
        videos keep separate ones -- and defaults to the image root.
        """
        if not result.path or not result.path.is_file():
            return
        payload = result.to_dict() | metadata | {
            "sha256": hashlib.sha256(result.path.read_bytes()).hexdigest(),
            "retained": True,
            "model": metadata.get("model") or (self.model_path.name if self.model_path else None),
            "engine": self.engine_path.name if self.engine_path else None,
        }
        manifest = (into or self.output_root) / "manifest.jsonl"
        with manifest.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")

    def _stop(self) -> None:
        """Stop an owned generation, workers included."""
        if self._process is None:
            return
        from app.process import stop_owned_process

        stop_owned_process(self._process, self._process.pid)
        self._process = None


_SEED = re.compile(r"seed\s+(\d+)", re.IGNORECASE)


def _extract_seed(output: str) -> int | None:
    """Recover the seed the engine chose, so an image can be reproduced."""
    match = _SEED.search(output or "")
    return int(match.group(1)) if match else None
