"""Image generation as a registered tool, so it passes the same gate as everything else.

Writing a picture into the project is a filesystem_write_project action --
ORDINARY tier, which owner mode pre-authorises, so it does not interrupt
Mohsin. It still travels the normal dispatch path, which means the authority
kernel sees it, the background learner cannot reach it, and a request that
arrived from a web page cannot ask for it.
"""
from __future__ import annotations

from typing import Any

from app.models.diffusion import ImageGenerator
from app.media_feedback import MediaFeedback
from app.tools.registry import ToolRegistry


def register_image_tools(registry: ToolRegistry, generator: ImageGenerator | None = None) -> ImageGenerator:
    images = generator or ImageGenerator()
    feedback = MediaFeedback(images)
    images.feedback = feedback

    def generate_image(prompt: str, negative: str = "", steps: int | None = None,
                       width: int | None = None, height: int | None = None, seed: int | None = None,
                       quality: str = "ultra", enhance: bool = True,
                       style: str = "auto") -> dict[str, Any]:
        result = images.generate(
            prompt, negative=negative, steps=steps, width=width, height=height, seed=seed,
            quality=quality, enhance=enhance, style=style,
        )
        payload = result.to_dict()
        if not result.ok:
            # A failure is returned as a fact rather than raised, so the plan
            # can say what went wrong instead of collapsing into a traceback.
            payload["error"] = result.detail
        return payload

    def generate_video(prompt: str, negative: str = "", frames: int | None = None,
                       fps: int | None = None, steps: int | None = None,
                       width: int | None = None, height: int | None = None, seed: int | None = None,
                       quality: str = "ultra", enhance: bool = True,
                       style: str = "auto", motion_module: str = "auto") -> dict[str, Any]:
        from app.models.video_generation import video_generation_controls

        ticket = registry.interlock.ticket()
        with video_generation_controls(check=lambda: registry.interlock.check(ticket)):
            result = images.generate_video(
                prompt, negative=negative, frames=frames, fps=fps, steps=steps,
                width=width, height=height, seed=seed, quality=quality, enhance=enhance, style=style,
                motion_module=motion_module,
            )
        payload = result.to_dict()
        if not result.ok:
            payload["error"] = result.detail
        return payload

    def image_status() -> dict[str, Any]:
        status = images.status()
        status["video_available"] = images.video_available()
        return status

    def image_history(limit: int = 20) -> dict[str, Any]:
        rows = images.history(limit)
        return {"count": len(rows), "images": rows}

    def video_history(limit: int = 20) -> dict[str, Any]:
        rows = images.video_history(limit)
        return {"count": len(rows), "videos": rows}

    def rate_generated_media(artifact: str, score: int, notes: str = "") -> dict[str, Any]:
        return feedback.record(artifact, score, notes)

    def media_quality_learning_status() -> dict[str, Any]:
        return feedback.status()

    registry.register(
        "generate_image",
        "Generate a picture from a text description, locally. Nothing is uploaded.",
        {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What the picture should show."},
                "negative": {"type": "string", "description": "What to keep out of it."},
                "steps": {"type": "integer", "minimum": 1, "maximum": 50,
                          "description": "More steps look better and take longer."},
                "width": {"type": "integer", "minimum": 64, "maximum": 1024},
                "height": {"type": "integer", "minimum": 64, "maximum": 1024},
                "seed": {"type": "integer", "description": "Reuse to reproduce an image."},
                # "auto" reads free VRAM and asks for what the card can
                # actually deliver. It was accepted by the generator and
                # rejected by this schema, so every caller that asked for it
                # -- including the window's own dialog -- failed validation
                # before reaching the code that handles it.
                "quality": {"type": "string",
                            "enum": ["auto", "fast", "balanced", "quality", "ultra"],
                            "description": "auto picks from free VRAM."},
                "style": {"type": "string", "enum": ["auto", "photo", "cinematic", "illustration", "concept"]},
                "enhance": {"type": "boolean", "description": "Add conservative quality guidance to the prompt."},
            },
            "required": ["prompt"],
        },
        "medium", "filesystem_write_project", generate_image,
    )
    registry.register(
        "generate_video",
        "Generate a short video clip from a text description, locally on the GPU. Nothing is uploaded.",
        {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What the clip should show."},
                "negative": {"type": "string", "description": "What to keep out of it."},
                "frames": {"type": "integer", "minimum": 2, "maximum": 32,
                           "description": "How many frames; AnimateDiff is trained for 16."},
                "fps": {"type": "integer", "minimum": 1, "maximum": 30,
                        "description": "Playback speed of the clip."},
                "steps": {"type": "integer", "minimum": 1, "maximum": 50},
                "width": {"type": "integer", "minimum": 64, "maximum": 1024,
                          "description": "Multiple of 64; defaults 256 for fast/balanced or 512 for quality/ultra. Larger frames require more memory and time."},
                "height": {"type": "integer", "minimum": 64, "maximum": 1024,
                           "description": "Defaults by quality: 256 fast, 512 for quality/ultra."},
                "seed": {"type": "integer", "description": "Reuse to reproduce a clip."},
                "quality": {"type": "string", "enum": ["auto", "fast", "balanced", "quality", "ultra"],
                            "description": "quality/ultra default to 512px and a 30fps CPU finish; fast/balanced default to 256px and a 24fps finish. Explicit dimensions and steps control render cost."},
                "style": {"type": "string"},
                "enhance": {"type": "boolean"},
                "motion_module": {"type": "string", "description": "auto preserves the default. Choose an installed relative name from status.video.motion_modules; v3 requires explicit selection."},
            },
            "required": ["prompt"],
        },
        "medium", "filesystem_write_project", generate_video,
    )
    registry.register(
        "image_generation_status",
        "Report whether local image generation is available and what it can run.",
        {"type": "object", "properties": {}},
        "low", "system_read", image_status,
    )
    registry.register(
        "image_generation_history",
        "List retained locally generated images with hashes and reproducibility metadata.",
        {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False},
        "low", "filesystem_read", image_history,
    )
    registry.register(
        "video_generation_history",
        "List retained locally generated video clips with hashes and reproducibility metadata.",
        {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False},
        "low", "filesystem_read", video_history,
    )
    registry.register(
        "rate_generated_media",
        "Record the owner's 1-5 rating for a generated image or video so auto settings learn only from verified human preference.",
        {"type": "object", "properties": {"artifact": {"type": "string"},
         "score": {"type": "integer", "minimum": 1, "maximum": 5}, "notes": {"type": "string"}},
         "required": ["artifact", "score"], "additionalProperties": False},
        "low", "filesystem_write_project", rate_generated_media,
    )
    registry.register(
        "media_quality_learning_status",
        "Show evidence-backed image/video preferences and whether enough owner ratings exist to trust them.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "low", "system_read", media_quality_learning_status,
    )
    return images
