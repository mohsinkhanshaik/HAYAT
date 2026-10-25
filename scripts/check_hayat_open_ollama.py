"""Check the separately quantized local export against its adapter diagnostics."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from scripts.audit_local_models import request
    from scripts.train_hayat_adapter import _sha256_file, _write_json

    base = ROOT / "data/models/hayat-open-4b"
    exported = json.loads((base / "ollama-export.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "models/hayat-open/manifest.json").read_text(encoding="utf-8"))
    evaluation = json.loads((ROOT / manifest["evaluation_file"]).read_text(encoding="utf-8"))
    if exported["stage"] != "imported" or exported["adapter_sha256"] != _sha256_file(
            ROOT / manifest["candidate_adapter_directory"] / "adapter_model.safetensors"):
        raise ValueError("The export must match the active evaluated adapter.")
    endpoint, name = exported["endpoint"], exported["ollama_model"]
    if urlsplit(endpoint).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Runtime checks are local only.")
    tag = next(m for m in request(endpoint, "/api/tags")["models"] if m["name"] == name)
    reference = evaluation.get("retention_diagnostics", evaluation["results"]["candidate"])
    tasks = reference["exact_match_tasks"]
    report = {"model": name, "endpoint": endpoint, "digest": tag["digest"],
              "adapter_sha256": exported["adapter_sha256"], "checks": [], "local_only": True,
              "quantization_requested": exported.get("quantization_requested"),
              "scope": "Repeated adapter diagnostics after BF16 merging and local quantization; no token-loss or general capability certification."}
    destination = base / "ollama-evaluation.json"
    for index, task in enumerate(tasks):
        started = time.monotonic()
        item = {"prompt": task["prompt"], "expected": task["expected"],
                "family": task.get("family", "original"), "passed": False}
        try:
            data = request(endpoint, "/api/chat", {"model": name, "stream": False, "think": False,
                "messages": [{"role": "user", "content": task["prompt"]}],
                "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 96 if index >= 14 else 32},
                "keep_alive": 0 if index == len(tasks) - 1 else "2m"}, timeout=90)
            answer = data["message"]["content"].strip()
            passed = answer == task["expected"]
            if index >= 14:
                try:
                    passed = type(json.loads(answer)) is type(json.loads(task["expected"])) and json.loads(answer) == json.loads(task["expected"])
                except (ValueError, TypeError):
                    pass
            item.update(answer=answer, passed=passed)
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        item["seconds"] = time.monotonic() - started
        report["checks"].append(item)
        _write_json(destination, report)
        if len(report["checks"]) % 25 == 0 or len(report["checks"]) == len(tasks):
            print(json.dumps({"checked": len(report["checks"]), "total": len(tasks)}), flush=True)
    current = next(m for m in request(endpoint, "/api/tags")["models"] if m["name"] == name)
    if current["digest"] != tag["digest"]:
        raise ValueError("The exported model changed during runtime checks.")
    report["exact_match_passed"] = sum(c["passed"] for c in report["checks"])
    report["exact_match_total"] = len(tasks)
    report["family_scores"] = {family: {
        "passed": sum(c["passed"] for c in report["checks"] if c["family"] == family),
        "total": sum(c["family"] == family for c in report["checks"])}
        for family in sorted({c["family"] for c in report["checks"]})}
    report["adapter_exact_match_passed"] = reference["exact_match_passed"]
    report["runtime_completed"] = all("error" not in c for c in report["checks"])
    report["exact_answer_nonregression"] = (report["runtime_completed"] and
        report["exact_match_passed"] >= reference["exact_match_passed"] and
        all(not before["passed"] or after["passed"] for before, after in zip(tasks[:14], report["checks"][:14], strict=True)) and
        all(report["family_scores"][family]["passed"] >= score["passed"] for family, score in reference.get("family_scores", {}).items()))
    _write_json(destination, report)
    print(json.dumps({k: report[k] for k in ("exact_match_passed", "exact_match_total", "runtime_completed", "exact_answer_nonregression")}), flush=True)
    return 0 if report["runtime_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
