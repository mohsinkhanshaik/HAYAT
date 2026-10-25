"""Run fresh multi-turn and correction challenges on a pinned local Ollama model."""
from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.evaluation.curveballs import GRADER_VERSION, SEED, VERSION, build_cases, grade, regrade_report, suite_hash, summarize  # noqa: E402


def main(argv=None):
    from app.launcher import folder_runtime
    from app.portable import base_url
    from app.policy.cost import is_local_endpoint
    from app.evaluation.local_transport import validate_local_model
    from scripts.audit_local_models import request
    from scripts.train_hayat_adapter import _write_json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='hayat:latest')
    parser.add_argument('--endpoint', default=base_url())
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--regrade', type=Path, help='Regrade saved answers offline; no model or service starts.')
    args = parser.parse_args(argv)
    if not is_local_endpoint(args.endpoint) or urlsplit(args.endpoint).path not in {'', '/'}:
        parser.error('Only local Ollama is supported.')
    if args.output.exists():
        parser.error('Refusing to overwrite an existing evaluation report.')
    if args.regrade:
        report = regrade_report(json.loads(args.regrade.read_text(encoding='utf-8')))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        _write_json(args.output, report)
        print(json.dumps(report['summary'], indent=2))
        return 0
    cases = build_cases(args.seed)
    report = {'version': VERSION, 'grader_version': GRADER_VERSION, 'seed': args.seed, 'suite_sha256': suite_hash(cases), 'cases': cases,
              'inference': {'temperature': 0, 'num_ctx': 4096, 'num_predict': 160, 'think': False},
              'model': args.model, 'endpoint': args.endpoint, 'started_at': datetime.now(UTC).isoformat(),
              'local_only': True, 'evaluation_only': True, 'checks': [],
              'scope': 'Raw model multi-turn diagnostics with independent JSON oracles; no tools executed, training or automatic promotion.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    managed = args.endpoint.rstrip('/') == base_url().rstrip('/')
    with folder_runtime(lambda message: print(message, flush=True)) if managed else nullcontext():
        tags = request(args.endpoint, '/api/tags')['models']
        tag = next((tag for tag in tags if tag['name'] == args.model), None)
        if tag is None:
            raise ValueError('The exact model tag is not installed on the selected local endpoint.')
        validate_local_model(tag)
        report['model_digest'] = tag['digest']
        report['model_details'] = tag.get('details')
        _write_json(args.output, report)
        for index, case in enumerate(cases):
            started = time.monotonic()
            check = {'id': case['id'], 'family': case['family'], 'critical': case['critical'], 'passed': False}
            try:
                response = request(args.endpoint, '/api/chat', {'model': args.model, 'messages': case['messages'],
                    'stream': False, 'think': report['inference']['think'],
                    'options': {key: value for key, value in report['inference'].items() if key != 'think'},
                    'keep_alive': 0 if index == len(cases) - 1 else '5m'}, timeout=240)
                answer = response['message']['content']
                check.update(answer=answer, **grade(case, answer))
                check['tokens'] = response.get('eval_count')
                check['finish_reason'] = response.get('done_reason')
            except Exception as error:
                check['error'] = f'{type(error).__name__}: {error}'[:800]
            check['seconds'] = round(time.monotonic() - started, 3)
            report['checks'].append(check)
            report['summary'] = summarize(report['checks'])
            _write_json(args.output, report)
            print(f"{index + 1}/{len(cases)} {case['family']}: {'pass' if check['passed'] else 'FAIL'}", flush=True)
        final_tags = request(args.endpoint, '/api/tags')['models']
        final = next((tag for tag in final_tags if tag['name'] == args.model), None)
        report['digest_unchanged'] = bool(final and final['digest'] == report['model_digest'])
        report['completed_at'] = datetime.now(UTC).isoformat()
        report['evaluation_complete'] = len(report['checks']) == len(cases) and not report['summary']['provider_errors'] and report['digest_unchanged']
        _write_json(args.output, report)
    print(json.dumps(report['summary'], indent=2), flush=True)
    # 0 means completed measurements; correctness is explicit in the report.
    return 0 if report['evaluation_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
