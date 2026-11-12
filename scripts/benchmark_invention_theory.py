"""Theory benchmark: does HAYAT's theory layer read the 32 benchmark laws the way a physicist would?

Each law is worked out from its cases exactly as the workbench does (app/invention_sim.work_out, no model), then
theorised with the law's real SI units (app/invention_theory.theorise). A physicist expects:

* a law built only from pure numbers and fundamental constants to **agree** with dimensional analysis, and the
  constant to be named (g, G, R, sigma, the speed of sound);
* a law holding a property of the particular device (a resistance, a time constant, an activation energy) to stay
  **open** -- units can never supply such a number, so it is not a failure;
* nothing to **disagree** when the units are right.

    .\\.venv\\Scripts\\python.exe -m scripts.benchmark_invention_theory [--out report.json]

Exit status 1 when any verdict differs from the expectation below.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import invention_sim, invention_theory  # noqa: E402
from scripts.benchmark_invention import HARD_TASKS, TASKS, _cases  # noqa: E402

#: law name -> (input units, result unit, expected agreement, constant that should be named)
UNITS: dict[str, tuple[dict[str, str], str, bool | None, str | None]] = {
    'pendulum period': ({'L': 'm'}, 's', True, 'g'),
    'kinetic energy': ({'m': 'kg', 'v': 'm/s'}, 'J', True, None),
    'electrical power': ({'V': 'V', 'R': 'ohm'}, 'W', True, None),
    'gravitation (tiny constant)': ({'m1': 'kg', 'm2': 'kg', 'r': 'm'}, 'N', True, 'G'),
    'ideal gas (3 inputs)': ({'n': 'mol', 'T': 'K', 'V': 'm**3'}, 'Pa', True, 'R'),
    'drag force (4 inputs)': ({'rho': 'kg/m**3', 'v': 'm/s', 'Cd': '1', 'A': 'm**2'}, 'N', True, None),
    'radioactive decay': ({'N0': '1', 't': 's'}, '1', None, None),
    'battery with internal resistance': ({'V': 'V', 'R': 'ohm'}, 'A', None, None),
    'projectile height': ({'v': 'm/s', 't': 's'}, 'm', True, 'g'),
    'logistic growth': ({'t': 's'}, '1', None, None),
    "noisy Ohm's law (2 % noise)": ({'V': 'V'}, 'A', None, None),
    'noisy cooling (1 % noise)': ({'t': 's'}, 'K', None, None),
    'beam deflection (4 inputs)': ({'F': 'N', 'L': 'm', 'E': 'Pa', 'I': 'm**4'}, 'm', True, None),
    'damped oscillation': ({'t': 's'}, '1', None, None),
    'thin lens image distance': ({'u': 'm', 'f': 'm'}, 'm', True, None),
    'parallel resistors': ({'R1': 'ohm', 'R2': 'ohm'}, 'ohm', True, None),
    'Pythagorean distance': ({'x': 'm', 'y': 'm'}, 'm', True, None),
    'Michaelis-Menten': ({'S': 'mol/m**3'}, 'mol/m**3/s', None, None),
    'Arrhenius rate': ({'T': 'K'}, '1/s', None, None),
    'sine with phase and offset': ({'t': 's'}, '1', None, None),
    'relativistic gamma': ({'b': '1'}, '1', True, None),
    'Stefan-Boltzmann (tiny constant)': ({'A': 'm**2', 'T': 'K'}, 'W', True, 'σ'),
    'Doppler (moving source)': ({'f': 'Hz', 'v': 'm/s'}, 'Hz', True, 'v_sound'),
    'inverse square plus background': ({'d': 'm'}, 'W/m**2', None, None),
    'noisy drag (5 % noise)': ({'v': 'm/s'}, 'N', None, None),
    'RC charging': ({'t': 's'}, 'V', None, None),
}


def run(task: dict) -> dict:
    units, output, expected, constant = UNITS[task['name']]
    tests = _cases(task['law'], task['train'], task.get('noise', 0.0))
    started = time.perf_counter()
    found = invention_sim.work_out(tests, task['tolerance'])
    best = found['best']
    data = {'tests': tests, 'tolerance': task['tolerance'], 'constraints': [],
            'rounds': [{'round': 1, 'candidate': {'formula': best['formula']}, 'measurement': best['measurement'],
                        'alternatives': [{'formula': row['formula'], 'description': row['description'],
                                          'passed': row['measurement']['passed']} for row in found['alternatives']]}],
            'best': {'candidate': {'formula': best['formula']}, 'leave_one_out': best.get('leave_one_out')}}
    result = invention_theory.theorise(data, units=units, output=output)
    agreement = result.get('agreement') or {}
    named = {c.get('constant') for c in (result.get('units') or {}).get('coefficients', []) if c.get('constant')}
    named |= {((result.get('derivation') or {}).get('completed_with') or {}).get('symbol')} - {None}
    row = {'name': task['name'], 'formula': best['formula'], 'seconds': round(time.perf_counter() - started, 2),
           'units_consistent': (result.get('units') or {}).get('consistent'), 'agrees': agreement.get('agrees'),
           'expected': expected, 'named': sorted(named), 'expected_constant': constant,
           'rungs_met': sum(1 for rung in result['ladder'] if rung['met']), 'plain': result['summary'][0]}
    row['ok'] = (row['units_consistent'] is True and row['agrees'] == expected and (constant is None or constant in named))
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--out', type=Path, help='write the full report as JSON')
    args = parser.parse_args()
    rows = [run(task) for task in TASKS + HARD_TASKS if task['name'] in UNITS]
    for row in rows:
        verdict = {True: 'agrees', None: 'open', False: 'DISAGREES'}[row['agrees']]
        print(f"{'ok ' if row['ok'] else 'BAD'} {row['name'][:34]:34} {verdict:9} {','.join(row['named']) or '-':10} "
              f"{row['seconds']:5.1f}s  {row['formula'][:48]}")
    good = sum(row['ok'] for row in rows)
    print(f'\n{good} of {len(rows)} laws read as a physicist expects; '
          f"{sum(row['agrees'] is True for row in rows)} agree, {sum(row['agrees'] is None for row in rows)} open, "
          f"{sum(row['agrees'] is False for row in rows)} disagree; slowest {max(row['seconds'] for row in rows)} s.")
    if args.out:
        args.out.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding='utf-8')
    return 0 if good == len(rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
