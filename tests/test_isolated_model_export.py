"""Candidate export gates run without loading weights or starting a model service."""
from copy import deepcopy
import argparse
import hashlib
import json

import pytest

from scripts import export_hayat_open_ollama as export
from scripts import train_hayat_adapter as training


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def package(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "ROOT", tmp_path)
    base = tmp_path / "data/models/hayat-open-4b"
    adapter = base / "adapter-new"
    comparison = base / "adapter"
    for folder in (adapter, comparison, base / "foundation"):
        folder.mkdir(parents=True)
        (folder / "adapter_model.safetensors").write_bytes(folder.name.encode())
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    candidate_hash = digest(adapter / "adapter_model.safetensors")
    previous_hash = digest(comparison / "adapter_model.safetensors")
    dataset = {split: hashlib.sha256(split.encode()).hexdigest() for split in ("train", "validation", "test")}
    run = {"rank": 16, "alpha": 32, "targets": ["q_proj", "v_proj"], "dataset_sha256": dataset,
           "initial_adapter": {"directory": str(comparison), "weights_sha256": previous_hash}}
    write_json(adapter / "run-config.json", run)
    write_json(adapter / "local-training-metrics.json", {"dataset_sha256": dataset})
    tasks = [{"prompt": f"Case {index}", "expected": str(index), "family": "original", "passed": True}
             for index in range(14)]
    result = {"held_out_token_loss": 1., "exact_match_tasks": tasks, "exact_match_passed": 14,
              "groups": {"public": {"token_loss": 1.}}, "family_scores": {"original": {"passed": 14, "total": 14}}}
    evaluation = {"training_completed": True, "measured_nonregression_passed": True,
                  "candidate_adapter": str(adapter), "candidate_adapter_sha256": candidate_hash,
                  "comparison_adapter": str(comparison), "comparison_adapter_sha256": previous_hash,
                  "dataset_sha256": dataset, "results": {"baseline": result, "candidate": deepcopy(result)}}
    evaluation_file = base / "evaluation-new.json"
    write_json(evaluation_file, evaluation)
    monkeypatch.setattr(training, "_initial_adapter_signature", lambda *a: {"weights_sha256": candidate_hash})
    manifest = {"foundation_directory": "data/models/hayat-open-4b/foundation",
                "candidate_adapter_directory": "data/models/hayat-open-4b/adapter",
                "evaluation_file": "data/models/hayat-open-4b/evaluation-retained.json"}
    args = argparse.Namespace(adapter=adapter, evaluation_file=evaluation_file,
        output_dir=base / "merged-new", report_file=base / "export-new.json",
        name="hayat-open:hardening-test-q4_k_m", resume_import=False)
    return base, adapter, evaluation_file, evaluation, manifest, args


def test_separate_completed_strictly_evaluated_candidate_is_accepted(package):
    base, adapter, evaluation_file, _, manifest, args = package
    proof, run = export._evaluation_proof(adapter, base / "foundation", evaluation_file, custom=True)
    paths = export._export_paths(args, manifest)
    assert proof["weights_sha256"] and run["dataset_sha256"]
    assert paths[1] == adapter and paths[-2:] == (args.name, True)


@pytest.mark.parametrize("mutation", ["failed", "unfinished", "weights", "dataset", "comparison", "boolean"])
def test_candidate_export_refuses_failed_or_mismatched_evidence(package, mutation):
    base, adapter, evaluation_file, evaluation, _, _ = package
    if mutation == "failed":
        evaluation["measured_nonregression_passed"] = False
    elif mutation == "unfinished":
        evaluation["training_completed"] = False
    elif mutation == "weights":
        evaluation["candidate_adapter_sha256"] = "0" * 64
    elif mutation == "dataset":
        evaluation["dataset_sha256"]["test"] = "0" * 64
    elif mutation == "comparison":
        (base / "adapter/adapter_model.safetensors").write_bytes(b"changed")
    elif mutation == "boolean":
        evaluation["measured_nonregression_passed"] = "true"
    write_json(evaluation_file, evaluation)
    with pytest.raises(ValueError):
        export._evaluation_proof(adapter, base / "foundation", evaluation_file, custom=True)


@pytest.mark.parametrize("mutation", ["regression", "public_loss", "reported_total", "family_total", "unpaired", "short"])
def test_reported_pass_cannot_hide_strict_gate_regressions(package, mutation):
    base, adapter, evaluation_file, evaluation, _, _ = package
    candidate = evaluation["results"]["candidate"]
    if mutation == "regression":
        candidate["exact_match_tasks"][0]["passed"] = False
        candidate["exact_match_passed"] = 13
        candidate["family_scores"]["original"]["passed"] = 13
    elif mutation == "public_loss":
        candidate["groups"]["public"]["token_loss"] = 1.04
    elif mutation == "reported_total":
        candidate["exact_match_passed"] = 900
    elif mutation == "family_total":
        candidate["family_scores"]["original"]["total"] = 900
    elif mutation == "unpaired":
        candidate["exact_match_tasks"][0]["prompt"] = "Different task"
    else:
        candidate["exact_match_tasks"] = candidate["exact_match_tasks"][:13]
    write_json(evaluation_file, evaluation)
    with pytest.raises(ValueError, match="strict"):
        export._evaluation_proof(adapter, base / "foundation", evaluation_file, custom=True)


def test_retained_model_can_be_compressed_separately_without_claiming_new_training(package):
    base, adapter, evaluation_file, evaluation, _, _ = package
    evaluation.pop("results")
    write_json(evaluation_file, evaluation)
    assert export._evaluation_proof(adapter, base / "foundation", evaluation_file, custom=True, retained=True)[0]
    with pytest.raises(ValueError, match="strict"):
        export._evaluation_proof(adapter, base / "foundation", evaluation_file, custom=True)


@pytest.mark.parametrize("mutation", ["main_alias", "retained_alias", "missing_option", "outside", "input_overlap", "report_input", "retained_output", "existing_report", "existing_output"])
def test_export_paths_protect_current_models_and_existing_artifacts(package, mutation):
    base, _, _, _, manifest, args = package
    if mutation == "main_alias":
        args.name = "hayat:latest"
    elif mutation == "retained_alias":
        args.name = "hayat-open:4b"
    elif mutation == "missing_option":
        args.evaluation_file = None
    elif mutation == "outside":
        args.output_dir = base / "../../../foreign-export"
    elif mutation == "input_overlap":
        args.output_dir = args.adapter / "merged"
    elif mutation == "report_input":
        args.report_file = args.adapter / "run-config.json"
    elif mutation == "retained_output":
        args.output_dir = base / "merged-ollama"
    elif mutation == "existing_report":
        args.report_file.write_text("preserve")
    else:
        args.output_dir.mkdir()
        (args.output_dir / "weights").write_text("preserve")
    with pytest.raises((ValueError, FileExistsError)):
        export._export_paths(args, manifest)


def test_default_export_paths_stay_compatible(package):
    _, _, _, _, manifest, args = package
    for key in ("adapter", "evaluation_file", "output_dir", "report_file", "name"):
        setattr(args, key, None)
    paths = export._export_paths(args, manifest)
    assert paths[-2:] == ("hayat-open:4b", False)
    assert paths[3].name == "merged-ollama"


@pytest.mark.parametrize("endpoint", ["https://api.example.org", "http://127.0.0.1.evil", "http://user:secret@127.0.0.1:11435", "file:///tmp", "http://127.0.0.1:11435/other"])
def test_exports_refuse_nonlocal_or_credentialed_endpoints(endpoint):
    with pytest.raises(ValueError, match="local"):
        export._local_endpoint(endpoint)


def test_quantized_import_requires_requested_type_and_records_digest(package, monkeypatch):
    from scripts import audit_local_models
    base, _, _, _, _, _ = package
    modelfile = base / "candidate.Modelfile"
    modelfile.write_text("FROM candidate.gguf\n", encoding="utf-8")
    report_path = base / "export-candidate.json"
    report = {}
    calls = []
    created = False

    def run(command, **kwargs):
        nonlocal created
        calls.append((command, kwargs))
        created = True

    def request(endpoint, route, payload=None):
        if route == "/api/show":
            return {"details": {"quantization_level": "Q4_K_M"}, "capabilities": ["completion"]}
        return {"models": [{"name": "hayat-open:candidate", "digest": "new-digest", "size": 123}] if created else []}

    monkeypatch.setattr(audit_local_models, "request", request)
    monkeypatch.setattr(export, "_convert_gguf", lambda *a: modelfile)
    monkeypatch.setattr(export.subprocess, "run", run)
    export._import("http://127.0.0.1:11435", "hayat-open:candidate", modelfile,
                   report, report_path, "Q4_K_M", gguf=True)
    assert calls[0][0][-3:] == ["hayat-open:candidate", "-f", str(modelfile)]
    assert calls[0][1]["env"]["OLLAMA_HOST"] == "http://127.0.0.1:11435"
    assert report["digest"] == "new-digest" and report["runtime_evaluation_completed"] is False
    assert report["automatic_promotion"] is False


def test_import_does_not_overwrite_an_existing_tag(package, monkeypatch):
    from scripts import audit_local_models
    base, _, _, _, _, _ = package
    monkeypatch.setattr(audit_local_models, "request", lambda *a: {"models": [{"name": "hayat-open:candidate"}]})
    monkeypatch.setattr(export.subprocess, "run", lambda *a, **kw: pytest.fail("No process should run"))
    with pytest.raises(FileExistsError):
        export._import("http://127.0.0.1:11435", "hayat-open:candidate", base / "unused", {},
                       base / "unused-report.json", "Q4_K_M", gguf=True)


@pytest.mark.parametrize("tamper", [None, "weights", "adapter", "name", "endpoint", "evaluation"])
def test_resume_is_bound_to_same_candidate_export_and_evaluation(package, monkeypatch, tamper):
    from app import config
    from scripts import audit_local_models
    base, adapter, evaluation_file, _, manifest, args = package
    manifest["base_revision"] = "pinned-foundation-revision"
    write_json(export.ROOT / "models/hayat-open/manifest.json", manifest)
    args.output_dir.mkdir()
    merged = args.output_dir / "weights.safetensors"
    merged.write_bytes(b"merged candidate")
    modelfile = args.output_dir / "Modelfile"
    modelfile.write_text("FROM isolated\n", encoding="utf-8")
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    report = {"stage": "merged", "adapter": str(adapter), "adapter_sha256": sha(adapter / "adapter_model.safetensors"),
              "foundation_revision": manifest["base_revision"], "ollama_model": args.name,
              "endpoint": "http://127.0.0.1:11435", "evaluation_sha256": sha(evaluation_file),
              "merged_sha256": {merged.name: sha(merged)}}
    if tamper == "weights":
        merged.write_bytes(b"unverified replacement")
    elif tamper == "adapter":
        report["adapter"] = str(base / "adapter-foreign")
    elif tamper == "name":
        report["ollama_model"] = "hayat-open:another-candidate"
    elif tamper == "endpoint":
        report["endpoint"] = "http://127.0.0.1:11434"
    elif tamper == "evaluation":
        report["evaluation_sha256"] = "0" * 64
    write_json(args.report_file, report)
    monkeypatch.setattr(config, "load_yaml", lambda *a: {"providers": {"ollama": {"base_url": "http://127.0.0.1:11435"}}})
    monkeypatch.setattr(audit_local_models, "request", lambda *a: {"models": []})
    imported = []
    monkeypatch.setattr(export, "_import", lambda *a, **kw: imported.append((a, kw)))
    argv = ["--resume-import", "--gguf", "--quantize", "Q4_K_M", "--adapter", str(adapter),
            "--evaluation-file", str(evaluation_file), "--output-dir", str(args.output_dir),
            "--report-file", str(args.report_file), "--name", args.name]
    if tamper:
        with pytest.raises(ValueError, match="resume"):
            export.main(argv)
        assert not imported
    else:
        export.main(argv)
        assert imported[0][0][1] == args.name
        assert imported[0][1]["gguf"] is True


def test_quantization_mismatch_is_not_reported_as_completed_import(package, monkeypatch):
    from scripts import audit_local_models
    base, _, _, _, _, _ = package
    modelfile = base / "candidate.Modelfile"
    modelfile.write_text("FROM candidate.gguf\n", encoding="utf-8")
    report = {"stage": "merged"}
    monkeypatch.setattr(audit_local_models, "request", lambda endpoint, route, *args: (
        {"details": {"quantization_level": "Q8_0"}} if route == "/api/show" else {"models": []}))
    monkeypatch.setattr(export, "_convert_gguf", lambda *a: modelfile)
    monkeypatch.setattr(export.subprocess, "run", lambda *a, **kw: None)
    with pytest.raises(ValueError, match="requested quantization"):
        export._import("http://127.0.0.1:11435", "hayat-open:candidate", modelfile, report,
                       base / "export-candidate.json", "Q4_K_M", gguf=True)
    assert report["stage"] == "merged"
