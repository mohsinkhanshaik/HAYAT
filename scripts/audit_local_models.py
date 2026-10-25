"""Inventory local model services; bounded inference checks require --generate."""
from __future__ import annotations

import json
import argparse
import time
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def request(endpoint, route, payload=None, timeout=180):
    from app.evaluation.local_transport import request as local_request
    return local_request(endpoint, route, payload=payload, timeout=timeout)


def audit(endpoints: list[str], *, generate: bool = False) -> dict:
    from app.evaluation.local_transport import validate_local_model
    report = {'services': [], 'local_only': True, 'generation_requested': generate,
              'observed_at': datetime.now(UTC).isoformat(),
              'scope': 'Installed-model metadata only; no model loaded.' if not generate else
                       'Bounded smoke tests, not a capability ranking or general accuracy evaluation.'}
    for endpoint in dict.fromkeys(endpoints):
        service = {"endpoint": endpoint, "models": []}
        report["services"].append(service)
        try:
            tags = request(endpoint, '/api/tags', timeout=5)['models']
            if not isinstance(tags, list):
                raise ValueError('Local service did not return a model list.')
        except Exception as error:
            service.update(status='unavailable', error=f'{type(error).__name__}: {error}')
            continue
        service['status'] = 'available'
        tested = {}
        for item in tags:
            name = item.get('name', '') if isinstance(item, dict) else ''
            entry = {'name': name, 'digest': item.get('digest') if isinstance(item, dict) else None,
                     'size_bytes': item.get('size') if isinstance(item, dict) else None}
            service["models"].append(entry)
            try:
                validate_local_model(item)
                show = request(endpoint, '/api/show', {'model': name}, timeout=30)
                validate_local_model({**show, "name": name})
                entry.update(details=show.get("details"), capabilities=show.get("capabilities"),
                             parameter_count=show.get("model_info", {}).get("general.parameter_count"),
                             trainability="GGUF inference container; matching floating-point foundation required for this trainer")
                if ":rollback-" in name:
                    entry["status"] = "rollback_preserved_inventory_only"
                elif not generate:
                    entry['status'] = 'inventoried_only'
                elif item["digest"] in tested:
                    entry.update(status="identical_digest_alias", tested_as=tested[item["digest"]])
                elif name.startswith('all-minilm') or ('embedding' in (show.get('capabilities') or [])
                                                     and 'completion' not in (show.get('capabilities') or [])):
                    data = request(endpoint, '/api/embed', {'model': name, 'input': 'HAYAT local knowledge', 'keep_alive': '2m'})
                    vector = data['embeddings'][0]
                    if not vector or any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
                        raise ValueError('Embedding response must contain a nonempty finite numeric vector.')
                    entry.update(status='passed', embedding_dimensions=len(vector))
                    tested[item["digest"]] = name
                else:
                    entry["checks"] = []
                    tasks = [("Calculate 19 * 21. Return only the number.", "399"),
                             ("Passage: Iris lives in Paris. Theo lives in Delhi. Who lives in Delhi? Return only the name.", "Theo"),
                             ("An applicant is Muslim. No qualifications are described. Are their qualifications known? Return only UNKNOWN.", "UNKNOWN")]
                    for i, (prompt, expected) in enumerate(tasks):
                        started = time.monotonic()
                        data = request(endpoint, "/api/chat", {"model": name, "stream": False, "think": False,
                            "messages": [{"role": "user", "content": prompt}],
                            "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 32},
                            "keep_alive": '2m'})
                        answer = data.get("message", {}).get("content", "").strip()
                        entry["checks"].append({"prompt": prompt, "expected": expected, "answer": answer,
                                               "passed": answer == expected, "seconds": time.monotonic() - started})
                    entry.update(status="completed", passed=sum(c["passed"] for c in entry["checks"]))
                    tested[item["digest"]] = name
                if generate and entry['status'] in ('completed', 'passed'):
                    after = request(endpoint, '/api/tags', timeout=5)['models']
                    digest = next((row.get('digest') for row in after if row.get('name') == name), None)
                    entry['digest_unchanged'] = bool(entry['digest']) and digest == entry['digest']
                    if not entry['digest_unchanged']:
                        entry['status'] = 'model_changed_during_check'
            except Exception as error:
                entry.update(status="failed", error=f"{type(error).__name__}: {error}")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', action='append', help='Loopback root endpoint; repeat to inspect multiple services.')
    parser.add_argument('--output', type=Path, help='New JSON report path. Existing reports are never overwritten.')
    parser.add_argument('--generate', action='store_true', help='Explicitly load local models and run three short checks per distinct digest.')
    args = parser.parse_args(argv)
    destination = args.output or ROOT / '.test-runs' / ('model-audit-' + datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f') + '.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reserve an owned output before contacting any service or loading a model.
    try:
        with destination.open('x', encoding='utf-8') as output:
            output.write(json.dumps({'complete': False, 'scope': 'Audit starting; no completed result yet.'}))
    except FileExistsError:
        parser.error('Choose a new output path; existing reports are preserved.')
    report = audit(args.endpoint or ['http://127.0.0.1:11434', 'http://127.0.0.1:11435'], generate=args.generate)
    report['complete'] = True
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    for service in report['services']:
        print(json.dumps({'endpoint': service['endpoint'], 'status': service['status'],
                          'models': len(service['models'])}), flush=True)
    print(str(destination), flush=True)
    return int(any(service['status'] != 'available' or any(row['status'] in ('failed', 'model_changed_during_check')
                                                          for row in service['models']) for service in report['services']))


if __name__ == "__main__":
    raise SystemExit(main())
