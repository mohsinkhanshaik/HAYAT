"""Merge the evaluated active adapter on CPU and import a separate local Ollama tag."""
from __future__ import annotations

import json
import argparse
import os
import subprocess
import sys
import shutil
import re
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _local_path(value, label):
    """Candidate exports stay within the existing model package, after symlink resolution."""
    path = (ROOT / value).resolve()
    package = (ROOT / "data/models/hayat-open-4b").resolve()
    if path == package or not path.is_relative_to(package):
        raise ValueError(f"{label} must be inside the local HAYAT Open model package.")
    return path


def _validate_name(name):
    if not re.fullmatch(r"hayat-open:[a-z0-9][a-z0-9._-]{0,79}", name):
        raise ValueError("Use a separate local hayat-open:<tag> name; main HAYAT and other model aliases are protected.")
    return name


def _local_endpoint(endpoint):
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("Export is restricted to a local HTTP Ollama endpoint without credentials.")
    return endpoint.rstrip("/")


def _evaluation_proof(adapter, foundation, evaluation_file, *, custom=False, retained=False):
    from scripts.train_hayat_adapter import _initial_adapter_signature, _sha256_file
    from scripts.evaluate_hayat_open import measured_nonregression

    evaluation = json.loads(evaluation_file.read_text(encoding="utf-8"))
    run = json.loads((adapter / "run-config.json").read_text(encoding="utf-8"))
    proof = _initial_adapter_signature(adapter, foundation, run["rank"], run["alpha"], run["targets"])
    if (evaluation.get("training_completed") is not True or evaluation.get("measured_nonregression_passed") is not True
            or evaluation.get("candidate_adapter_sha256") != proof["weights_sha256"]):
        raise ValueError("Only a completed, hash-matched evaluated adapter can be exported.")
    if custom and not retained:
        metrics = json.loads((adapter / "local-training-metrics.json").read_text(encoding="utf-8"))
        dataset = run.get("dataset_sha256")
        initial = run.get("initial_adapter") or {}
        if (not isinstance(dataset, dict) or set(dataset) != {"train", "validation", "test"}
                or any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v) for v in dataset.values())
                or metrics.get("dataset_sha256") != dataset or evaluation.get("dataset_sha256") != dataset
                or Path(evaluation.get("candidate_adapter", "")).resolve() != adapter
                or not initial.get("directory") or not initial.get("weights_sha256")):
            raise ValueError("Candidate evaluation must match the completed continuation and its training corpus hashes.")
        comparison = _local_path(initial["directory"], "Comparison adapter")
        if (Path(evaluation.get("comparison_adapter") or "").resolve() != comparison
                or evaluation.get("comparison_adapter_sha256") != initial["weights_sha256"]
                or _sha256_file(comparison / "adapter_model.safetensors") != initial["weights_sha256"]):
            raise ValueError("The continuation comparison adapter changed or does not match evaluation.")
        results = evaluation.get("results") or {}
        if (not all(isinstance(results.get(mode, {}).get("exact_match_tasks"), list)
                    and len(results[mode]["exact_match_tasks"]) >= 14 for mode in ("baseline", "candidate"))
                or not _consistent_scores(results)
                or not measured_nonregression(results, strict=True)):
            raise ValueError("A separate continuation export requires the strict measured nonregression gate.")
    return proof, run


def _consistent_scores(results):
    """A reported total must describe the actual paired measured task records."""
    before = results["baseline"]["exact_match_tasks"]
    after = results["candidate"]["exact_match_tasks"]
    if len(before) != len(after):
        return False
    if any(any(a.get(key) != b.get(key) for key in ("prompt", "expected", "family"))
           for a, b in zip(before, after, strict=True)):
        return False
    for mode in ("baseline", "candidate"):
        result = results[mode]
        tasks = result["exact_match_tasks"]
        if (any(type(task.get("passed")) is not bool for task in tasks)
                or result.get("exact_match_passed") != sum(task["passed"] for task in tasks)):
            return False
        scores = result.get("family_scores")
        families = {task.get("family") for task in tasks}
        if not isinstance(scores, dict) or set(scores) != families:
            return False
        for family in families:
            score = scores[family]
            if (score.get("passed") != sum(task["passed"] for task in tasks if task.get("family") == family)
                    or score.get("total") != sum(task.get("family") == family for task in tasks)):
                return False
    return True


def _export_paths(args, manifest):
    options = (args.adapter, args.evaluation_file, args.output_dir, args.report_file, args.name)
    custom = any(value is not None for value in options)
    if custom and not all(value is not None for value in options):
        raise ValueError("An isolated export requires --adapter, --evaluation-file, --output-dir, --report-file and --name together.")
    foundation = _local_path(manifest["foundation_directory"], "Foundation")
    adapter = _local_path(args.adapter or manifest["candidate_adapter_directory"], "Adapter")
    evaluation_file = _local_path(args.evaluation_file or manifest["evaluation_file"], "Evaluation")
    destination = _local_path(args.output_dir or "data/models/hayat-open-4b/merged-ollama", "Output")
    report_path = _local_path(args.report_file or "data/models/hayat-open-4b/ollama-export.json", "Report")
    name = _validate_name(args.name or "hayat-open:4b")
    if custom and name == "hayat-open:4b":
        raise ValueError("The retained hayat-open:4b alias is protected; select a unique candidate tag.")
    if custom and (destination == (ROOT / "data/models/hayat-open-4b/merged-ollama").resolve()
                   or report_path == (ROOT / "data/models/hayat-open-4b/ollama-export.json").resolve()):
        raise ValueError("Use separate candidate paths; the retained export artifacts are protected.")
    for source in (foundation, adapter, evaluation_file, ROOT / "models/hayat-open/manifest.json"):
        if (destination == source or destination in source.parents or source in destination.parents
                or report_path == source or report_path in source.parents or source in report_path.parents):
            raise ValueError("Export output and report must not overlap input artifacts.")
    if report_path == destination or report_path in destination.parents:
        raise ValueError("The export report must be a separate file.")
    if not args.resume_import:
        if destination.exists() and any(destination.iterdir()):
            raise FileExistsError("Refusing to overwrite an existing merged export.")
        if report_path.exists():
            raise FileExistsError("Refusing to overwrite an existing export report.")
    return foundation, adapter, evaluation_file, destination, report_path, name, custom


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-import", action="store_true", help="Reuse only a hash-verified, previously merged export.")
    backend = parser.add_mutually_exclusive_group()
    backend.add_argument("--gguf", dest="gguf", action="store_true", help="Use the installed llama.cpp converter and quantizer; default on Windows.")
    backend.add_argument("--native-safetensors", dest="gguf", action="store_false", help="Require an available native Safetensors import runtime.")
    parser.set_defaults(gguf=os.name == "nt")
    parser.add_argument("--quantize", choices=("int4", "int8", "nvfp4", "mxfp4", "mxfp8", "Q8_0", "Q4_K_M"))
    parser.add_argument("--adapter", type=Path, help="Completed separately evaluated continuation adapter.")
    parser.add_argument("--evaluation-file", type=Path, help="Strict held-out comparison report for that exact adapter.")
    parser.add_argument("--output-dir", type=Path, help="New isolated merge/conversion directory.")
    parser.add_argument("--report-file", type=Path, help="New provenance report; existing files are preserved.")
    parser.add_argument("--name", help="Unique local hayat-open:<tag>; active aliases are protected.")
    args = parser.parse_args(argv)
    quantize = args.quantize or ("Q8_0" if args.gguf else "int8")
    if (quantize in {"Q8_0", "Q4_K_M"}) != args.gguf:
        parser.error("Use a GGUF quantization with --gguf, or a native type without it.")
    from app.config import load_yaml
    from scripts.audit_local_models import request
    from scripts.train_hayat_adapter import _sha256_file
    manifest = json.loads((ROOT / "models/hayat-open/manifest.json").read_text(encoding="utf-8"))
    foundation, adapter, evaluation_file, destination, report_path, name, custom = _export_paths(args, manifest)
    retained = (adapter == (ROOT / manifest["candidate_adapter_directory"]).resolve()
                and evaluation_file == (ROOT / manifest["evaluation_file"]).resolve())
    proof, run = _evaluation_proof(adapter, foundation, evaluation_file, custom=custom, retained=retained)
    evaluation_sha256 = _sha256_file(evaluation_file)
    endpoint = _local_endpoint(load_yaml("config/models.yaml")["providers"]["ollama"]["base_url"])
    if any(m["name"] == name for m in request(endpoint, "/api/tags")["models"]):
        raise FileExistsError("Preserve the existing Ollama tag; select a separate export explicitly.")
    if args.resume_import:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        actual = {p.name: _sha256_file(p) for p in destination.glob("*.safetensors")}
        if (report["stage"] != "merged" or report["adapter_sha256"] != proof["weights_sha256"]
                or report["foundation_revision"] != manifest["base_revision"]
                or Path(report["adapter"]).resolve() != adapter
                or report["ollama_model"] != name or _local_endpoint(report["endpoint"]) != endpoint
                or custom and report.get("evaluation_sha256") != _sha256_file(evaluation_file)
                or not actual or actual != report["merged_sha256"]):
            raise ValueError("Cannot resume a changed or mismatched merged export.")
        _import(endpoint, name, destination / "Modelfile", report, report_path, quantize, gguf=args.gguf)
        return
    import psutil
    weight_bytes = sum(p.stat().st_size for p in foundation.glob("*.safetensors"))
    if shutil.disk_usage(ROOT).free < weight_bytes * 3:
        raise RuntimeError("Merge and local import need three times the foundation bytes in free disk space.")
    if psutil.virtual_memory().available < weight_bytes * 2:
        raise RuntimeError("CPU merge needs twice the foundation weight bytes in free host RAM.")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer
    base = AutoModelForCausalLM.from_pretrained(foundation, local_files_only=True, trust_remote_code=False,
                                              dtype=torch.bfloat16, device_map={"": "cpu"})
    model = PeftModel.from_pretrained(base, adapter, is_trainable=False).merge_and_unload(safe_merge=True)
    if (_sha256_file(adapter / "adapter_model.safetensors") != proof["weights_sha256"]
            or _sha256_file(evaluation_file) != evaluation_sha256):
        raise ValueError("The evaluated adapter changed during merging.")
    destination.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(destination, safe_serialization=True, max_shard_size="4GB")
    AutoTokenizer.from_pretrained(foundation, local_files_only=True, trust_remote_code=False).save_pretrained(destination)
    shutil.copyfile(foundation / "LICENSE", destination / "LICENSE")
    del model, base
    import gc
    gc.collect()
    system = (ROOT / manifest["system_prompt_file"]).read_text(encoding="utf-8").strip()
    license_text = (foundation / "LICENSE").read_text(encoding="utf-8")
    if '"""' in system or '"""' in license_text:
        raise ValueError("Unexpected Modelfile delimiter in the system prompt.")
    modelfile = destination / "Modelfile"
    modelfile.write_text(f'FROM "{destination.as_posix()}"\nPARAMETER num_ctx 4096\nPARAMETER temperature 0.2\n'
                         f'SYSTEM """{system}"""\nLICENSE """{license_text}"""\n', encoding="utf-8")
    report = {"stage": "merged", "adapter": str(adapter), "adapter_sha256": proof["weights_sha256"],
              "foundation_model": manifest["base_model"], "foundation_revision": manifest["base_revision"],
              "merged_sha256": {p.name: _sha256_file(p) for p in destination.glob("*.safetensors")},
              "modelfile_sha256": _sha256_file(modelfile),
              "evaluation_file": str(evaluation_file), "evaluation_sha256": evaluation_sha256,
              "dataset_sha256": run.get("dataset_sha256"), "strict_continuation_gate": custom and not retained,
              "evaluated_active_adapter": retained,
              "ollama_model": name, "endpoint": endpoint, "local_only": True,
              "scope": "Merged trained adapter, separately quantized by local Ollama; runtime checks still required."}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _import(endpoint, name, modelfile, report, report_path, quantize, gguf=args.gguf)


def _import(endpoint, name, modelfile, report, report_path, quantize, *, gguf=False):
    from scripts.audit_local_models import request
    from scripts.train_hayat_adapter import _sha256_file
    endpoint, name = _local_endpoint(endpoint), _validate_name(name)
    if report.get("modelfile_sha256") and _sha256_file(modelfile) != report["modelfile_sha256"]:
        raise ValueError("The Modelfile changed after merging.")
    if any(m["name"] == name for m in request(endpoint, "/api/tags")["models"]):
        raise FileExistsError("The target tag was created during merging; preserve it rather than overwriting it.")
    report.update(quantization_requested=quantize,
                  scope="Merged trained adapter, separately quantized by local Ollama; runtime checks still required.")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if gguf:
        modelfile = _convert_gguf(modelfile, report, report_path, quantize)
        command = [str(ROOT / "runtime/ollama/ollama.exe"), "create", name, "-f", str(modelfile)]
    else:
        command = [str(ROOT / "runtime/ollama/ollama.exe"), "create", name, "--quantize", quantize, "-f", str(modelfile)]
    if any(m["name"] == name for m in request(endpoint, "/api/tags")["models"]):
        raise FileExistsError("The tag appeared during conversion; preserve it.")
    subprocess.run(command,
                   cwd=ROOT, env=dict(os.environ, OLLAMA_HOST=endpoint), check=True)
    show = request(endpoint, "/api/show", {"model": name})
    if gguf and show.get("details", {}).get("quantization_level") != quantize:
        raise ValueError("The imported model does not report the requested quantization; runtime checks remain required.")
    report.update(stage="imported", details=show.get("details"), capabilities=show.get("capabilities"))
    tag = next(item for item in request(endpoint, "/api/tags")["models"] if item["name"] == name)
    report.update(digest=tag["digest"], size_bytes=tag.get("size"), runtime_evaluation_completed=False,
                  automatic_promotion=False)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


def _convert_gguf(modelfile, report, report_path, quantize):
    from scripts.train_hayat_adapter import _sha256_file
    converter = ROOT / "runtime/llama-export"
    revision = subprocess.check_output(["git", "-C", str(converter), "rev-parse", "HEAD"], text=True).strip()
    previous = report.get("gguf_conversion", {})
    if previous and previous["converter_git_revision"] != revision:
        raise ValueError("The converter changed since the partial export.")
    previous["converter_git_revision"] = revision
    destination = modelfile.parent
    float_file = destination / "hayat-open-bf16.gguf"
    output = destination / f"hayat-open-{quantize}.gguf"
    converter_env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                         PYTHONPATH=str(ROOT / "runtime/llama-export-deps"), PYTHONIOENCODING="utf-8")
    if float_file.exists():
        if _sha256_file(float_file) != previous.get("float_sha256"):
            raise FileExistsError("Preserve the existing unverified GGUF intermediate.")
    else:
        temporary = destination / f"convert-{uuid4().hex}.gguf"
        subprocess.run([sys.executable, str(converter / "convert_hf_to_gguf.py"), str(destination),
                        "--outtype", "bf16", "--outfile", str(temporary), "--model-name", "HAYAT Open 4B"],
                       cwd=ROOT, env=converter_env, check=True)
        temporary.rename(float_file)
        previous["float_sha256"] = _sha256_file(float_file)
        report["gguf_conversion"] = previous
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    quantizer = ROOT / "runtime/ollama/lib/ollama/llama-quantize.exe"
    if output.exists():
        if _sha256_file(output) != previous.get("quantized_sha256") or previous.get("quantization") != quantize:
            raise FileExistsError("Preserve the existing unverified quantized export.")
    else:
        temporary = destination / f"quantize-{uuid4().hex}.gguf"
        subprocess.run([str(quantizer), str(float_file), str(temporary), quantize, "8"], cwd=ROOT, check=True)
        temporary.rename(output)
        previous.update(quantization=quantize, quantized_sha256=_sha256_file(output),
                        quantizer_sha256=_sha256_file(quantizer),
                        quantizer_library_sha256=_sha256_file(quantizer.with_name("libllama-quantize-impl.dll")))
        report["gguf_conversion"] = previous
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    text = modelfile.read_text(encoding="utf-8")
    text = f'FROM "{output.as_posix()}"\n' + text.split("\n", 1)[1]
    gguf_modelfile = destination / "Modelfile.gguf"
    if gguf_modelfile.exists() and gguf_modelfile.read_text(encoding="utf-8") != text:
        raise FileExistsError("Preserve a changed GGUF Modelfile.")
    gguf_modelfile.write_text(text, encoding="utf-8")
    report["scope"] = "Retained trained adapter merged into BF16 GGUF and independently quantized; runtime checks still required."
    return gguf_modelfile


if __name__ == "__main__":
    main()
