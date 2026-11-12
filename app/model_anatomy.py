"""The real neurons of HAYAT's own language model, read from its model file (2026-11-09).

A transformer's feed-forward layers are made of neurons: each block has `feed_forward_length` of them, and a
mixture-of-experts block has `expert_count` experts of `expert_feed_forward_length` neurons each (plus a
shared expert in some designs). Those numbers are written in the GGUF file's header, so they can be counted
exactly without loading the weights.

What HAYAT can know about these neurons is their structure: which layer, which expert, which unit. It cannot
see them fire: Ollama does not expose activations. The brain view says exactly that.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any, BinaryIO

_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}


def _read(handle: BinaryIO, fmt: str) -> Any:
    size = struct.calcsize(fmt)
    data = handle.read(size)
    if len(data) != size:
        raise ValueError("The model file ended inside its header.")
    return struct.unpack(fmt, data)[0]


def _string(handle: BinaryIO) -> str:
    length = _read(handle, "<Q")
    if length > 1 << 24:
        raise ValueError("Implausible string length in the model header.")
    return handle.read(length).decode("utf-8", errors="replace")


def _value(handle: BinaryIO, kind: int, keep: bool) -> Any:
    if kind in _SCALARS:
        return _read(handle, _SCALARS[kind])
    if kind == 8:
        return _string(handle)
    if kind == 9:
        inner, count = _read(handle, "<I"), _read(handle, "<Q")
        if inner in _SCALARS and not keep:  # skip a numeric array without decoding it
            handle.seek(struct.calcsize(_SCALARS[inner]) * count, 1)
            return None
        values = [_value(handle, inner, keep) for _ in range(count)]
        return values if keep else None
    raise ValueError(f"Unknown GGUF value type {kind}.")


def gguf_metadata(path: Path | str, wanted: tuple[str, ...] = ("general.", "block_count", "feed_forward", "expert",
                                                               "embedding_length", "head_count")) -> dict[str, Any]:
    """The header key/values whose names contain one of `wanted` (large arrays such as the vocabulary are skipped)."""
    with open(path, "rb") as handle:
        if handle.read(4) != b"GGUF":
            raise ValueError(f"{path} is not a GGUF model file.")
        version = _read(handle, "<I")
        _tensors, count = _read(handle, "<Q"), _read(handle, "<Q")
        found: dict[str, Any] = {"gguf_version": version}
        for _ in range(count):
            key = _string(handle)
            kind = _read(handle, "<I")
            keep = any(part in key for part in wanted) and "tokenizer" not in key
            value = _value(handle, kind, keep)
            if keep:
                found[key] = value
        return found


def ollama_model_file(models_dir: Path | str, name: str) -> Path | None:
    """The weights blob of an Ollama model ("hayat:latest") in an Ollama models folder."""
    model, _, tag = name.partition(":")
    manifest = Path(models_dir) / "manifests" / "registry.ollama.ai" / "library" / model / (tag or "latest")
    if not manifest.is_file():
        return None
    layers = json.loads(manifest.read_text(encoding="utf-8")).get("layers", [])
    digest = next((layer["digest"] for layer in layers if layer.get("mediaType") == "application/vnd.ollama.image.model"), "")
    blob = Path(models_dir) / "blobs" / digest.replace(":", "-")
    return blob if digest and blob.is_file() else None


def _per_layer(value: Any, layers: int) -> list[int]:
    if isinstance(value, list):
        return [int(item or 0) for item in value[:layers]]
    return [int(value or 0)] * layers


def anatomy(metadata: dict[str, Any], name: str = "") -> dict[str, Any]:
    """Layers, experts and feed-forward neurons per layer, and their total."""
    arch = str(metadata.get("general.architecture") or "")
    get = lambda key: metadata.get(f"{arch}.{key}")
    layers = int(get("block_count") or 0)
    if not layers:
        raise ValueError("The model header names no layer count.")
    experts = int(get("expert_count") or 0)
    used = int(get("expert_used_count") or 0)
    if experts:
        per_expert = _per_layer(get("expert_feed_forward_length") or get("feed_forward_length"), layers)
        shared = _per_layer(get("expert_shared_feed_forward_length") or 0, layers)
        per_layer = [experts * units + extra for units, extra in zip(per_expert, shared)]
    else:
        per_expert, shared = _per_layer(get("feed_forward_length"), layers), [0] * layers
        per_layer = list(per_expert)
    return {"model": name or metadata.get("general.name") or arch, "architecture": arch, "layers": layers,
            "experts": experts, "experts_used_per_token": used, "neurons_per_expert": per_expert[0] if per_expert else 0,
            "shared_neurons_per_layer": shared[0] if shared else 0, "neurons_per_layer": per_layer,
            "neurons": sum(per_layer), "parameters": metadata.get("general.parameter_count"),
            "hidden_size": get("embedding_length"), "attention_heads": get("attention.head_count"),
            "basis": ("Counted from the model file's header: feed-forward neurons per layer"
                      + (" (experts × neurons per expert, plus any shared expert)" if experts else "")
                      + ". Their structure is known; their activity is not visible to HAYAT (Ollama exposes no activations).")}


def model_anatomy(models_dir: Path | str, name: str) -> dict[str, Any] | None:
    path = ollama_model_file(models_dir, name)
    if path is None:
        return None
    return {**anatomy(gguf_metadata(path), name), "file": str(path), "file_bytes": path.stat().st_size}
