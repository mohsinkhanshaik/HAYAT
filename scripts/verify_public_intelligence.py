"""Live read-only provider proof in a separate project test workspace."""
import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.intelligence.manager import IntelligenceManager
from app.intelligence.tools import register_intelligence_tools
from app.research.fetcher import PublicFetcher
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='data/work/intelligence-validation')
    parser.add_argument('--extended', action='store_true', help='Include GDACS, TfL and twelve Caltrans camera districts.')
    args = parser.parse_args()
    permissions = PermissionManager({'default': 'ALLOW'}, ROOT)
    folder = permissions.validate_project_path(args.output, write=True)
    folder.mkdir(parents=True, exist_ok=True)
    registry = ToolRegistry(permissions)
    manager = IntelligenceManager(folder / 'intelligence.db',
        PublicFetcher('HAYAT-PublicIntelligence/2.0', timeout=10), registry, extended=args.extended)
    register_intelligence_tools(registry, manager)
    try:
        result = registry.execute('intelligence_refresh', {})
        if not result.success:
            raise RuntimeError(result.error)
        data = manager.dashboard(limit=1)
        evidence = {'checked_at': datetime.now(UTC).isoformat(), 'model_calls': 0,
            'osiris_service_calls': 0, 'providers': [{key: source.get(key) for key in
                ('key', 'status', 'source_url', 'last_success_at', 'count', 'rejected', 'sha256', 'stale', 'error')}
                for source in data['sources']], 'matching_events': data['total_matching'],
            'brief_path': result.output['brief_path'], 'watch_rules': len(manager.store.watches())}
        (folder / 'verification.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        print(json.dumps(evidence, indent=2))
        return 0 if all(row['status'] == 'ok' and not row['stale'] for row in evidence['providers']) else 1
    finally:
        manager.close()


if __name__ == '__main__':
    raise SystemExit(main())
