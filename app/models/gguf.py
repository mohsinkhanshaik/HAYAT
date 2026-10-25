"""Finding the model weights on this disk, without asking Ollama where they are.

Ollama is currently how this system reasons, and that is a dependency the owner
wants gone. Removing it has two halves: something that can execute a model, and
knowledge of where the models are. This module is the second half, and it can
be finished today.

Ollama stores weights as ordinary GGUF files in a content-addressed blob
directory, with small JSON manifests mapping a name like `qwen3.5:latest` to
the digest of its weights. Nothing about reading those requires Ollama to be
running -- the manifests are on disk, the blobs are on disk, and both stay
there if the service never starts again. So the 22GB of models already
downloaded on this machine are usable by any runtime that can load GGUF,
without re-downloading anything and without the service.

What this deliberately does not do is copy or move the blobs. They are large,
they are already local, and duplicating them to claim independence would cost
the owner disk for no benefit.
"""
from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


#: The layer type Ollama uses for the weights themselves, as opposed to the
#: licence, template, and parameter layers that share the same manifest.
_MODEL_MEDIA_TYPES = (
    "application/vnd.ollama.image.model",
)
_PROJECTOR_MEDIA_TYPES = (
    "application/vnd.ollama.image.projector",
)

_GGUF_MAGIC = b"GGUF"


@dataclass
class LocalModel:
    """A set of weights that exists on this disk right now."""

    name: str
    path: Path
    size_bytes: int
    source: str = "ollama-store"
    projector_path: Path | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / 1024**3, 2)

    @property
    def is_gguf(self) -> bool:
        """Whether the file really is GGUF, checked by reading its magic bytes.

        The extension proves nothing and Ollama's blobs have no extension at
        all, so the only honest answer comes from opening the file.
        """
        try:
            with self.path.open("rb") as handle:
                return handle.read(4) == _GGUF_MAGIC
        except OSError:
            return False

    @property
    def supports_vision(self) -> bool:
        return self.projector_path is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "path": str(self.path), "size_gb": self.size_gb,
            "source": self.source, "supports_vision": self.supports_vision,
            "parameters": self.parameters,
        }


def ollama_store_root() -> Path | None:
    """Where Ollama keeps its models on this machine, if it is installed.

    Honours OLLAMA_MODELS, because a user who moved a 22GB store off the system
    drive did so deliberately and should not be silently ignored.
    """
    override = os.getenv("OLLAMA_MODELS")
    if override:
        candidate = Path(os.path.expandvars(os.path.expanduser(override)))
        return candidate if candidate.is_dir() else None
    for base in (Path.home() / ".ollama" / "models", Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "Ollama" / "models"):
        if base.is_dir():
            return base
    return None


def _read_manifest(manifest_path: Path, blobs: Path) -> tuple[Path | None, Path | None, dict[str, Any]]:
    """Resolve one manifest to its weights blob, projector blob, and parameters.

    The size check is not defensive padding. This directory is not guaranteed
    to contain only manifests: on the machine this was written for, two raw
    GGUF files had been placed among them, one of 21GB. Reading that as UTF-8
    took 142 seconds and allocated the file into memory -- a directory listing
    turning into an out-of-memory condition because a filename was in the
    wrong folder. A manifest is a few hundred bytes of JSON; anything far
    larger is something else and is skipped without being opened.
    """
    manifest = _read_small_json(manifest_path, limit=_MAX_MANIFEST_BYTES)
    if not manifest:
        return None, None, {}

    weights = projector = None
    parameters: dict[str, Any] = {}
    for layer in manifest.get("layers", []):
        digest = str(layer.get("digest", "")).replace(":", "-")
        if not digest:
            continue
        blob = blobs / digest
        if not blob.is_file():
            continue
        media = layer.get("mediaType", "")
        if media in _MODEL_MEDIA_TYPES:
            weights = blob
        elif media in _PROJECTOR_MEDIA_TYPES:
            projector = blob
        elif media.endswith("params"):
            parameters = _read_small_json(blob, limit=_MAX_PARAMS_BYTES)
    return weights, projector, parameters


#: A manifest is JSON of a few hundred bytes; the ones here are 707-858.
_MAX_MANIFEST_BYTES = 1024 * 1024
#: A parameters layer is smaller still -- these declare 65 bytes.
_MAX_PARAMS_BYTES = 256 * 1024


def _read_small_json(path: Path, *, limit: int) -> dict[str, Any]:
    """Read a small JSON file, refusing to load a large one into memory.

    The real file size decides, not a size field inside the data being parsed,
    because the point is to be safe against a file that is not what it claims.
    """
    try:
        if path.stat().st_size > limit:
            return {}
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, ValueError):
        return {}


def _manifest_files(root: Path) -> Iterator[tuple[str, Path]]:
    """Every manifest under the store, with the model name it describes.

    Ollama lays these out as registry/namespace/name/tag, so the model name is
    reconstructed from the path rather than stored inside the file.
    """
    manifests = root / "manifests"
    if not manifests.is_dir():
        return
    for path in manifests.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(manifests).parts
        if len(parts) < 2:
            continue
        name, tag = parts[-2], parts[-1]
        # library/<name>/<tag> is the common case; a bare file is a name itself.
        yield (f"{name}:{tag}" if name else tag), path


def discover_local_models(root: Path | None = None) -> list[LocalModel]:
    """Every model already on this disk, whether or not Ollama is running.

    Sorted largest first, because that is the order in which capability and
    cost both matter when choosing one.
    """
    store = root or ollama_store_root()
    if store is None:
        return []
    blobs = store / "blobs"
    if not blobs.is_dir():
        return []

    found: list[LocalModel] = []
    for name, manifest_path in _manifest_files(store):
        weights, projector, parameters = _read_manifest(manifest_path, blobs)
        if weights is None:
            continue
        try:
            size = weights.stat().st_size
        except OSError:
            continue
        found.append(LocalModel(
            name=name, path=weights, size_bytes=size,
            projector_path=projector, parameters=parameters,
        ))
    return sorted(found, key=lambda item: item.size_bytes, reverse=True)


def find_model(name: str, root: Path | None = None) -> LocalModel | None:
    """Resolve a model name to weights on disk, tolerating a missing tag."""
    wanted = name.strip().lower()
    models = discover_local_models(root)
    for model in models:
        if model.name.lower() == wanted:
            return model
    base = wanted.split(":")[0]
    return next((model for model in models if model.name.lower().split(":")[0] == base), None)


def read_gguf_metadata(path: Path, *, max_pairs: int = 64) -> dict[str, Any]:
    """Read the header of a GGUF file: architecture, context length, and so on.

    Bounded on purpose. These files are gigabytes and the interesting metadata
    is in the first few kilobytes; reading further would cost real time for
    nothing. Returns what it could parse rather than raising, because partial
    metadata is still more useful than none.
    """
    try:
        with path.open("rb") as handle:
            if handle.read(4) != _GGUF_MAGIC:
                return {}
            version = struct.unpack("<I", handle.read(4))[0]
            handle.read(8)  # tensor count
            kv_count = struct.unpack("<Q", handle.read(8))[0]
            metadata: dict[str, Any] = {"gguf_version": version}
            try:
                for _ in range(min(kv_count, max_pairs)):
                    key = _read_gguf_string(handle)
                    if key is None:
                        break
                    value = _read_gguf_value(handle)
                    if value is not None and _is_interesting(key):
                        metadata[key] = value
            except _StopParsing:
                # Whatever was read before the vocabulary is still true and
                # useful; partial metadata beats minutes of seeking.
                metadata["truncated"] = True
            return metadata
    except (OSError, struct.error, ValueError):
        return {}


_INTERESTING = ("architecture", "context_length", "name", "block_count",
                "embedding_length", "quantization", "chat_template")


def _is_interesting(key: str) -> bool:
    return any(marker in key for marker in _INTERESTING)


def _read_gguf_string(handle) -> str | None:
    raw = handle.read(8)
    if len(raw) < 8:
        return None
    length = struct.unpack("<Q", raw)[0]
    if length > 1_000_000:
        return None
    return handle.read(length).decode("utf-8", "replace")


_SCALARS = {
    0: ("<B", 1), 1: ("<b", 1), 2: ("<H", 2), 3: ("<h", 2), 4: ("<I", 4),
    5: ("<i", 4), 6: ("<f", 4), 7: ("<?", 1), 10: ("<Q", 8), 11: ("<q", 8), 12: ("<d", 8),
}


def _read_gguf_value(handle) -> Any:
    raw = handle.read(4)
    if len(raw) < 4:
        return None
    value_type = struct.unpack("<I", raw)[0]
    if value_type in _SCALARS:
        fmt, size = _SCALARS[value_type]
        chunk = handle.read(size)
        return struct.unpack(fmt, chunk)[0] if len(chunk) == size else None
    if value_type == 8:
        return _read_gguf_string(handle)
    if value_type == 9:
        return _skip_gguf_array(handle)
    return None


#: A string array longer than this is a token vocabulary, not metadata. Walking
#: one costs a seek per token -- measured at minutes across this machine's
#: models -- for information no caller here wants. Parsing stops instead.
_VOCABULARY_THRESHOLD = 20_000


class _StopParsing(Exception):
    """Raised when the remaining header is not worth the syscalls to skip."""


def _skip_gguf_array(handle) -> Any:
    """Step over an array without materialising it.

    Fixed-width elements are skipped with a single seek over the whole block
    rather than one read per element. Strings have no fixed width so they must
    be walked, which is why a vocabulary-sized run ends parsing altogether.
    """
    element_raw = handle.read(4)
    count_raw = handle.read(8)
    if len(element_raw) < 4 or len(count_raw) < 8:
        return None
    element_type = struct.unpack("<I", element_raw)[0]
    count = struct.unpack("<Q", count_raw)[0]

    if element_type in _SCALARS:
        handle.seek(count * _SCALARS[element_type][1], 1)
        return f"<array of {count}>"
    if element_type == 8:
        if count > _VOCABULARY_THRESHOLD:
            raise _StopParsing
        for _ in range(count):
            length_raw = handle.read(8)
            if len(length_raw) < 8:
                break
            handle.seek(struct.unpack("<Q", length_raw)[0], 1)
        return f"<array of {count}>"
    raise _StopParsing
