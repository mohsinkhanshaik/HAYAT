"""Benchmark the local OCR backend against synthetic images with known ground truth.

Renders text at a realistic on-screen size (not a scanned-document size) since
the primary intended use is reading error dialogs, terminal output, and UI
labels from a screenshot or camera frame, not scanning paper documents.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image, ImageDraw, ImageFont

from app.perception.camera.ocr import LocalOCR

CASES = [
    "AI OCR VALIDATION 48291",
    "CUDA ERROR 700: an illegal memory access was encountered",
    "NCCL TIMEOUT: WorkNCCL(SeqNum=1284, OpType=ALLREDUCE) timed out",
    "ModuleNotFoundError: No module named 'pywinauto'",
    "C:\\Users\\test\\project> python train.py --epochs 10 --batch-size 32",
    "Connection refused on port 11434 (Ollama unreachable)",
    "Warning: 3 tests failed, 289 passed in 55.12s",
    "GPU 0: NVIDIA GeForce RTX 5060 Laptop GPU, 8151 MiB, 19% utilization",
]

_FONT_CANDIDATES = ["C:\\Windows\\Fonts\\consola.ttf", "C:\\Windows\\Fonts\\segoeui.ttf", "C:\\Windows\\Fonts\\arial.ttf"]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _render(text: str, path: Path, *, size: int = 22) -> None:
    font = _load_font(size)
    width = max(400, int(len(text) * size * 0.62) + 40)
    image = Image.new("RGB", (width, size + 40), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 20), text, fill="black", font=font)
    image.save(path)


def _char_accuracy(expected: str, actual: str) -> float:
    if not expected:
        return 1.0 if not actual else 0.0
    previous = list(range(len(actual) + 1))
    for i, e_char in enumerate(expected, start=1):
        current = [i] + [0] * len(actual)
        for j, a_char in enumerate(actual, start=1):
            cost = 0 if e_char == a_char else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
        previous = current
    distance = previous[len(actual)]
    return max(0.0, 1.0 - distance / len(expected))


def _word_accuracy(expected: str, actual: str) -> float:
    expected_words = expected.split()
    actual_words = set(actual.split())
    if not expected_words:
        return 1.0
    matched = sum(1 for word in expected_words if word in actual_words)
    return matched / len(expected_words)


def main() -> int:
    ocr = LocalOCR()
    status = ocr.status()
    workdir = PROJECT_ROOT / "data/camera"
    workdir.mkdir(parents=True, exist_ok=True)

    if not (status["backends"]["windows_media_ocr"]["available"] or status["backends"]["tesseract"]["available"]):
        report = {"generated_at": datetime.now(UTC).isoformat(), "status": status, "error": "No OCR backend available on this machine."}
        _write(report)
        print(json.dumps(report, indent=2))
        return 1

    results = []
    for index, expected in enumerate(CASES):
        path = workdir / f"ocr-bench-{index}.png"
        try:
            _render(expected, path)
            observed = ocr.read(path)
            actual = observed.get("text", "")
            results.append({
                "expected": expected, "actual": actual,
                "char_accuracy": round(_char_accuracy(expected, actual), 4),
                "word_accuracy": round(_word_accuracy(expected, actual), 4),
                "latency_ms": observed.get("latency_ms"),
                "provider": observed.get("provider"),
                "failed": False,
            })
        except Exception as exc:
            results.append({"expected": expected, "actual": "", "char_accuracy": 0.0, "word_accuracy": 0.0, "latency_ms": None, "provider": status.get("active_backend"), "failed": True, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            path.unlink(missing_ok=True)

    failures = [row for row in results if row["failed"]]
    latencies = [row["latency_ms"] for row in results if row["latency_ms"] is not None]
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "backend": status.get("active_backend"),
        "status": status,
        "case_count": len(results),
        "failure_count": len(failures),
        "failure_rate": round(len(failures) / len(results), 4) if results else None,
        "mean_char_accuracy": round(sum(r["char_accuracy"] for r in results) / len(results), 4) if results else None,
        "mean_word_accuracy": round(sum(r["word_accuracy"] for r in results) / len(results), 4) if results else None,
        "mean_latency_ms": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "cases": results,
    }
    _write(summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "cases"}, indent=2))
    return 0


def _write(payload: dict) -> None:
    output = PROJECT_ROOT / "data/benchmarks/ocr/latest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
