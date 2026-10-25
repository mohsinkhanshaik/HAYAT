"""Verify project-local REA/Ghidra against an inert, generated x64 PE fixture."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def fixture(path: Path):
    """One .text section returning 42; the executable is only parsed, never run."""
    image = bytearray(1024)
    image[:2] = b'MZ'
    struct.pack_into('<I', image, 0x3c, 0x80)
    image[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', image, 0x84, 0x8664, 1, 0, 0, 0, 240, 0x22)
    optional = 0x98
    struct.pack_into('<HBBIIIIIQII', image, optional, 0x20b, 14, 0, 512, 0, 0, 0x1000, 0x1000,
                     0x140000000, 0x1000, 0x200)
    struct.pack_into('<HHHHHH', image, optional + 40, 6, 0, 0, 0, 6, 0)
    struct.pack_into('<IIIHH', image, optional + 52, 0x2000, 0x200, 0, 3, 0x8100)
    struct.pack_into('<QQQQII', image, optional + 72, 0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    section = optional + 240
    image[section:section + 8] = b'.text\0\0\0'
    struct.pack_into('<IIIIIIHHI', image, section + 8, 6, 0x1000, 512, 512, 0, 0, 0, 0, 0x60000020)
    image[512:518] = b'\xb8\x2a\x00\x00\x00\xc3'
    path.write_bytes(image)


def main():
    from app.reverse_engineer import ReaSession, ReverseEngineer
    folder = ROOT / '.test-runs' / 'native-analysis'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'owned-fixture.exe'
    fixture(target)
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    engine = ReverseEngineer(lambda: ReaSession(start_timeout=60))
    report = {'fixture': str(target), 'sha256': before, 'executed': False, 'checks': {}}
    try:
        listing = engine.tools()
        report['tool_count'] = listing['count']
        opened = engine.act('open_binary', {'path': str(target), 'provider_id': 'ghidra'}, timeout=360)
        report['checks']['open'] = opened
        operations = [(name, {}) for name in ('binary_overview', 'list_procedures', 'list_segments', 'list_names', 'list_strings')]
        operations += [(name, {'procedure': 'entry'}) for name in (
            'procedure_info', 'procedure_pseudo_code', 'procedure_assembly', 'read_function_instructions',
            'procedure_callers', 'procedure_callees', 'procedure_references', 'analyze_function')]
        for name, args in operations:
            print(f'Checking {name}...', flush=True)
            report['checks'][name] = engine.read(name, args, timeout=180)
        text = report['checks']['procedure_pseudo_code']['report']
        report['decompiled_return_42'] = '42' in text or '0x2a' in text.lower()
        if not report['decompiled_return_42']:
            raise AssertionError('Generated return value was absent from the decompilation.')
        report['passed'] = True
    except Exception as exc:
        report['passed'] = False
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        engine.close()
        report['source_unchanged'] = hashlib.sha256(target.read_bytes()).hexdigest() == before
        (folder / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'error': report.get('error'),
                      'source_unchanged': report['source_unchanged'], 'report': str(folder / 'verification.json')}, indent=2))
    return 0 if report['passed'] and report['source_unchanged'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
