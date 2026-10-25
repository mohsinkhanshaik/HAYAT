r"""Run the fixed 220-case held-out set against a local model and save the report.

    .\.venv\Scripts\python.exe scripts\evaluate_holdout.py                 # the brain (hayat:latest)
    .\.venv\Scripts\python.exe scripts\evaluate_holdout.py --per-family 3  # a quick smoke run

Local Ollama only; nothing is trained, promoted or sent anywhere. Reports go to
data/benchmarks/holdout/ as JSON (every prompt, answer and grade) and Markdown.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_yaml  # noqa: E402
from app.evaluation.holdout import build_cases, markdown, run  # noqa: E402


def _exit_code(report):
    if not report['evaluation_complete']:
        return 1
    return 2 if report['score_gate_passed'] is False else 0


def main(argv: list[str] | None = None) -> int:
    from app.portable import base_url
    parser = argparse.ArgumentParser(description="Evaluate a local model on the fixed held-out set.")
    parser.add_argument("--model", default=load_yaml("config/models.yaml")["conversation_model"]["model"])
    parser.add_argument("--endpoint", default=base_url(), help="Default: HAYAT's own Ollama (started if needed).")
    parser.add_argument("--per-family", type=int, default=0, help="Ask only the first N cases of each family.")
    parser.add_argument("--minimum-accuracy", type=float,
                        help="Optional explicit score gate from 0 to 1; exit 2 means complete but below this target.")
    parser.add_argument("--output", type=Path, default=ROOT / "data/benchmarks/holdout")
    parser.add_argument("--regrade", type=Path, metavar="REPORT_JSON",
                        help="Score a saved report's answers again with today's graders; no model calls.")
    parser.add_argument('--regrade-output', type=Path, help='New output filename stem for offline regrading.')
    args = parser.parse_args(argv)
    if not 0 <= args.per_family <= 20:
        parser.error("Per-family must be from 0 (all) to 20.")
    if args.minimum_accuracy is not None and not 0 <= args.minimum_accuracy <= 1:
        parser.error("Minimum accuracy must be a finite fraction from 0 to 1.")
    if args.regrade:
        from app.evaluation.holdout import regrade
        original = json.loads(args.regrade.read_text(encoding='utf-8'))
        if args.minimum_accuracy is not None:
            original['minimum_accuracy_requested'] = args.minimum_accuracy
        report = regrade(original)
        target = args.regrade_output or args.regrade.with_name(args.regrade.stem + "-regraded")
        if target.with_suffix('.json').exists() or target.with_suffix('.md').exists():
            raise FileExistsError('Preserve the previous regrading; choose a new --regrade-output filename.')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        target.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
        print(json.dumps({"report": str(target.with_suffix(".md")), "correct": report["summary"]["correct"],
                          "was": report["regraded_from_summary"]["correct"]}, indent=2))
        return _exit_code(report)
    from app.policy.cost import is_local_endpoint
    if not is_local_endpoint(args.endpoint) or urlsplit(args.endpoint).path not in {'', '/'}:
        parser.error("The held-out evaluation runs against a local Ollama endpoint only.")
    from app.models.base import Message
    from app.models.local import OllamaProvider
    provider = OllamaProvider(args.endpoint, args.model, timeout=300)
    cases = build_cases()
    if args.per_family:
        kept: list[dict] = []
        for case in cases:
            if sum(1 for chosen in kept if chosen["family"] == case["family"]) < args.per_family:
                kept.append(case)
        cases = kept

    def generate(prompt: str) -> str:
        return provider.generate([Message("user", prompt)], temperature=0.0, max_tokens=600).content

    def progress(done: int, total: int) -> None:
        if done % 10 == 0 or done == total:
            print(f"{done}/{total}", flush=True)

    def evaluate():
        from app.evaluation.local_transport import validate_local_model
        from scripts.audit_local_models import request
        name = args.model if ':' in args.model.rsplit('/', 1)[-1] else args.model + ':latest'
        def model_tag():
            return next((item for item in request(args.endpoint, '/api/tags')['models'] if item['name'] == name), None)
        initial = model_tag()
        if initial is None:
            raise ValueError('The exact model tag is not installed on this local endpoint.')
        validate_local_model(initial)
        result = run(generate, cases, model=args.model, on_progress=progress)
        final = model_tag()
        result.update(model_digest=initial['digest'], model_details=initial.get('details'),
                      digest_unchanged=bool(final and final['digest'] == initial['digest']))
        return result

    if args.endpoint.rstrip("/") == base_url():
        # HAYAT's own Ollama serves the brain from runtime\; start it for this run if
        # needed, and stop it afterwards only if this run started it.
        from app.launcher import folder_runtime
        with folder_runtime(lambda message: print(message, flush=True)):
            report = evaluate()
    else:
        report = evaluate()
    report["endpoint"] = args.endpoint
    report['inference_options'] = {'temperature': 0.0, 'max_tokens': 600,
                                    'num_ctx': getattr(provider, 'num_ctx', None),
                                    'think': getattr(provider, 'think', None)}
    report["partial"] = bool(args.per_family)
    report['evaluation_complete'] = report['summary']['evaluation_complete'] and report['digest_unchanged']
    report['minimum_accuracy_requested'] = args.minimum_accuracy
    report['score_gate_passed'] = (None if args.minimum_accuracy is None else
                                   report['evaluation_complete'] and report['summary']['accuracy'] >= args.minimum_accuracy)
    args.output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("holdout-%Y%m%d-%H%M%S")
    (args.output / f"{stamp}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.output / f"{stamp}.md").write_text(markdown(report), encoding="utf-8")
    summary = report["summary"]
    print(json.dumps({"report": str(args.output / f"{stamp}.md"), "cases": summary["cases"],
                      "accuracy": summary["accuracy"], "errors": summary["errors"]}, indent=2))
    return _exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
