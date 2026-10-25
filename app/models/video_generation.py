"""Cancellation and measured output metadata for locally owned video workers."""
from __future__ import annotations

import json
import math
import subprocess
import time
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from fractions import Fraction
from pathlib import Path
from typing import Callable

_check: ContextVar[Callable[[], None] | None] = ContextVar("hayat_video_check", default=None)


@contextmanager
def video_generation_controls(*, check: Callable[[], None] | None = None):
    """Carry the initiating execution ticket through every owned worker stage."""
    previous = _check.get()

    def combined_check():
        if previous:
            previous()
        if check:
            check()

    token = _check.set(combined_check)
    try:
        check_video_cancelled()
        yield
        check_video_cancelled()
    finally:
        _check.reset(token)


def check_video_cancelled() -> None:
    from app.models.streaming import check_cancelled

    check_cancelled()
    if callback := _check.get():
        callback()


@contextmanager
def reserve_video_memory(label: str, needs_bytes: int, *, wait: float = 120):
    """Use the shared heavy lane while keeping a queued request cancellable."""
    from app.resources import NotEnoughMemory, heavy

    deadline = time.monotonic() + max(0, wait)
    with ExitStack() as stack:
        while True:
            check_video_cancelled()
            remaining = max(0, deadline - time.monotonic())
            try:
                reservation = stack.enter_context(heavy(label, needs_bytes=needs_bytes, wait=min(.2, remaining)))
                break
            except NotEnoughMemory:
                check_video_cancelled()
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)
        check_video_cancelled()
        yield reservation


def wait_video_process(process, timeout: float) -> tuple[str, str | None]:
    """Wait in short intervals; stop only this process on cancellation or timeout."""
    deadline = time.monotonic() + max(.01, float(timeout))
    try:
        while True:
            check_video_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(getattr(process, "args", "video worker"), timeout)
            try:
                output = process.communicate(timeout=min(.2, remaining))
                check_video_cancelled()
                return output
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        from app.process import stop_owned_process

        stop_owned_process(process, process.pid)
        raise


def run_video_process(command: list[str], timeout: float):
    check_video_cancelled()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output, errors = wait_video_process(process, timeout)
    return subprocess.CompletedProcess(command, process.returncode, output, errors)


def probe_generated_video(path: Path, *, timeout: float = 45) -> dict:
    """Decode/count frames and report the delivered file, never inferred settings."""
    from app.video_workflow import executable

    ffprobe = executable("ffprobe")
    if not ffprobe:
        raise ValueError("FFprobe is required to verify the generated video")
    completed = run_video_process([
        ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=width,height,r_frame_rate,nb_read_frames,codec_name,duration:format=format_name,duration",
        "-of", "json", str(path),
    ], timeout)
    if completed.returncode != 0 or (completed.stderr or "").strip():
        raise ValueError("generated video could not be decoded completely")
    try:
        payload = json.loads(completed.stdout)
        stream = payload["streams"][0]
        width, height, frames = int(stream["width"]), int(stream["height"]), int(stream["nb_read_frames"])
        fps = float(Fraction(stream["r_frame_rate"]))
        duration = float(stream.get("duration") or payload.get("format", {}).get("duration") or frames / fps)
        if min(width, height, frames) <= 0 or not (0 < fps <= 240) or not math.isfinite(duration) or duration <= 0:
            raise ValueError
        return {"width": width, "height": height, "frames": frames, "fps": fps,
                "duration_seconds": round(duration, 6), "codec": str(stream.get("codec_name", "")),
                "container": str(payload.get("format", {}).get("format_name", "")),
                "decoded_verified": True}
    except (KeyError, IndexError, ValueError, TypeError, ZeroDivisionError) as exc:
        raise ValueError("generated video has no valid decoded video stream") from exc
