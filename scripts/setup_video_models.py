"""Install an optional, hash-pinned AnimateDiff motion module without selecting it.

The default v2 remains in data/models/motion. Explicit --module v3 installs into
its optional/ subdirectory; select that module explicitly in video generation.
Installing a module does not replace the image model or demonstrate video quality.

    .venv\\Scripts\\python.exe scripts/setup_video_models.py --module v2
    .venv\\Scripts\\python.exe scripts/setup_video_models.py --module v3

Downloads stream to disk and resume interrupted transfers. The full pinned size
and SHA-256 must match before the completed file becomes available to HAYAT.
The official guoyww/animatediff model card declares Apache-2.0 licensing.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Callable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MOTION_MODELS = {
    "v3": {"name": "v3_sd15_mm.ckpt", "bytes": 1673262583,
           "sha256": "2412711886f61091846f53204aabc38aa6e09356d62a9808abe4daa802168343",
           "url": "https://huggingface.co/guoyww/animatediff/resolve/main/v3_sd15_mm.ckpt"},
    "v2": {"name": "mm_sd_v15_v2.ckpt", "bytes": 1817888431,
           "sha256": "69ed0f5fef82b110aca51bcab73b21104242bc65d6ab4b8b2a2a94d31cad1bf0",
           "url": "https://huggingface.co/guoyww/animatediff/resolve/main/mm_sd_v15_v2.ckpt"},
}
MODEL_CARD_URL = "https://huggingface.co/guoyww/animatediff"
MODEL_LICENSE = "Apache-2.0"
CHUNK_BYTES = 1024 * 1024
_CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)\Z")


class DownloadError(RuntimeError):
    """A transfer failed validation; no unverified model was installed."""


def motion_model_path(module: str = "v2", root: Path | None = None) -> Path:
    if module not in MOTION_MODELS:
        raise ValueError(f"Unknown motion module: {module}")
    if root is None:
        from app.models.diffusion import MOTION_ROOT
        root = MOTION_ROOT
    folder = Path(root) / "optional" if module == "v3" else Path(root)
    return folder / str(MOTION_MODELS[module]["name"])


def _hash_file(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _verified(path: Path, choice: dict) -> bool:
    return (not path.is_symlink() and path.is_file()
            and path.stat().st_size == choice["bytes"]
            and _hash_file(path) == choice["sha256"])


def _write_json(path: Path, value: dict, *, replace: bool = True) -> None:
    """Replace only installer-owned metadata, never the checkpoint itself."""
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass  # Preserve metadata created by another program.
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _install_lock(path: Path):
    """An OS-released lock also permits recovery after a process is interrupted."""
    if path.is_symlink():
        raise DownloadError(f"Refusing a symbolic-link installer lock: {path}")
    with path.open("a+b") as handle:
        if not path.stat().st_size:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise DownloadError(f"Another installer is already using {path.name}") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _discard(partial: Path, metadata: Path) -> None:
    partial.unlink(missing_ok=True)
    metadata.unlink(missing_ok=True)


def _partial_metadata(path: Path, pin: dict) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
        raise DownloadError("Partial download has no valid pin metadata; preserve it and move it aside.")
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise DownloadError("Partial download metadata is unreadable; move it aside before retrying.") from exc
    if not isinstance(saved, dict) or any(saved.get(key) != value for key, value in pin.items()):
        raise DownloadError("Partial download belongs to a different pin; preserving it unchanged.")
    return saved


def _publish(partial: Path, metadata: Path, target: Path, choice: dict, module: str) -> Path:
    # Same-directory hard linking is atomic and refuses to overwrite an existing
    # filename on Windows and POSIX, unlike os.replace's clobbering behavior.
    try:
        os.link(partial, target)
    except FileExistsError:
        if not _verified(target, choice):
            raise FileExistsError(f"A different file already occupies {target}; preserving it.") from None
    provenance = target.with_name(f"{target.name}.provenance.json")
    if not provenance.exists():
        _write_json(provenance, {
            "module": module, "filename": target.name, "bytes": choice["bytes"],
            "sha256": choice["sha256"], "source_url": choice["url"],
            "model_card": MODEL_CARD_URL, "license": MODEL_LICENSE,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "requires_manual_selection": module == "v3",
        }, replace=False)
    _discard(partial, metadata)
    return target


def install_motion_model(
    module: str = "v2", root: Path | None = None, *, client=None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Return a verified checkpoint, retaining only resumable interrupted data.

    ``root`` is the motion directory; v3 is isolated under ``root/optional``.
    An injected httpx-compatible client remains open and is never reconfigured.
    Progress receives downloaded and expected bytes, including a resumed prefix.
    """
    target = motion_model_path(module, root)
    choice = MOTION_MODELS[module]
    source = urlsplit(str(choice["url"]))
    if source.scheme != "https" or source.hostname != "huggingface.co" or source.username:
        raise DownloadError("Unexpected download host; only the pinned HTTPS Hugging Face source is allowed.")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.partial")
    metadata = partial.with_name(f"{partial.name}.json")
    pin = {"version": 1, "module": module, "url": choice["url"],
           "bytes": choice["bytes"], "sha256": choice["sha256"]}
    with ExitStack() as stack:
        stack.enter_context(_install_lock(target.with_name(f"{target.name}.lock")))
        if target.exists() or target.is_symlink():
            if _verified(target, choice):
                return target
            raise FileExistsError(f"A different file already occupies {target}; move it aside and re-run.")
        if partial.is_symlink() or (partial.exists() and not partial.is_file()):
            raise DownloadError("Refusing a nonregular partial download.")
        if partial.exists():
            saved = _partial_metadata(metadata, pin)
            offset = partial.stat().st_size
            if offset > choice["bytes"]:
                raise DownloadError("Partial download exceeds its pin; preserving it for inspection.")
        else:
            if metadata.exists() or metadata.is_symlink():
                _partial_metadata(metadata, pin)
            saved, offset = pin.copy(), 0
        if offset == choice["bytes"]:
            if _verified(partial, choice):
                return _publish(partial, metadata, target, choice, module)
            _discard(partial, metadata)
            raise DownloadError("Checksum mismatch; corrupt partial download discarded.")
        if client is None:
            import httpx
            client = stack.enter_context(httpx.Client(follow_redirects=True, timeout=120))
        headers = {"Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            validator = saved.get("etag") or saved.get("last_modified")
            if isinstance(validator, str) and not validator.startswith("W/"):
                headers["If-Range"] = validator
        with client.stream("GET", str(choice["url"]), headers=headers, follow_redirects=True) as response:
            response.raise_for_status()
            if response.url.scheme != "https":
                raise DownloadError("The download redirected away from HTTPS; nothing installed.")
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise DownloadError("Compressed transfer cannot be resumed safely; expected identity encoding.")
            if response.status_code == 206:
                span = _CONTENT_RANGE.fullmatch(response.headers.get("content-range", ""))
                if not span:
                    raise DownloadError("Missing or invalid Content-Range; partial download preserved.")
                start, end, total = map(int, span.groups())
                if start != offset or total != choice["bytes"] or not start <= end < total:
                    raise DownloadError("Content-Range disagrees with the pinned file; partial download preserved.")
                expected_transfer = end - start + 1
            elif response.status_code == 200:
                # The server may ignore Range or reject If-Range. Restart instead
                # of appending a full response to an already-downloaded prefix.
                offset, expected_transfer = 0, choice["bytes"]
            else:
                raise DownloadError(f"Unexpected download status {response.status_code}.")
            length = response.headers.get("content-length")
            if length is not None and (not length.isdecimal() or int(length) != expected_transfer):
                raise DownloadError("Response size disagrees with its range or pin; nothing installed.")
            digest = hashlib.sha256()
            if offset:
                with partial.open("rb") as prefix:
                    for chunk in iter(lambda: prefix.read(CHUNK_BYTES), b""):
                        digest.update(chunk)
            saved = pin | {"etag": response.headers.get("etag"),
                           "last_modified": response.headers.get("last-modified")}
            _write_json(metadata, saved)
            size, transferred = offset, 0
            if progress:
                progress(size, int(choice["bytes"]))
            # Buffered output is deliberately flushed even after interruption;
            # the next process hashes the actual on-disk prefix before appending.
            oversized = False
            try:
                with partial.open("ab" if offset else "wb") as output:
                    for chunk in response.iter_bytes(chunk_size=CHUNK_BYTES):
                        if len(chunk) > CHUNK_BYTES or size + len(chunk) > choice["bytes"]:
                            oversized = True
                            raise DownloadError("Download exceeded its pinned size or chunk bound; discarded.")
                        output.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                        transferred += len(chunk)
                        if progress:
                            progress(size, int(choice["bytes"]))
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                if oversized:
                    _discard(partial, metadata)
            if transferred != expected_transfer or size != choice["bytes"]:
                raise DownloadError("Download interrupted before the pinned size; partial retained for resume.")
            if digest.hexdigest() != choice["sha256"]:
                _discard(partial, metadata)
                raise DownloadError("Checksum mismatch; corrupt download discarded and nothing installed.")
        return _publish(partial, metadata, target, choice, module)


def _check_sd15_checkpoint() -> None:
    from app.models.diffusion import find_video_model
    checkpoint = find_video_model()
    if checkpoint is None:
        print("Note: no SD 1.5 checkpoint is installed in data/models/diffusion. "
              "The motion module needs one; an SDXL image model cannot be used.")
    else:
        print(f"SD 1.5 checkpoint present for animation: {checkpoint.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stream and verify an optional AnimateDiff motion module.")
    parser.add_argument("--module", choices=sorted(MOTION_MODELS), default="v2",
                        help="v2 (existing default) or v3 (optional; requires explicit video selection).")
    module = parser.parse_args().module
    choice = MOTION_MODELS[module]
    print(f"Verifying/downloading {choice['name']} ({choice['bytes'] / 1e9:.2f} GB). "
          "Interrupted transfers can resume; no paid service is used.", flush=True)
    import httpx

    try:
        target = install_motion_model(module)
    except (DownloadError, FileExistsError, OSError, httpx.HTTPError) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"Verified motion module: {target}")
    if module == "v3":
        print("v3 is optional and requires explicit selection for video generation; the default is unchanged.")
    _check_sd15_checkpoint()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
