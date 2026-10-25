"""Editable teaching examples; datasets are synthetic, not experimental evidence."""

TEMPLATES = {
    'Sensor calibration': {
        'goal': 'Find the calibration rule mapping raw sensor reading x to calibrated output y.',
        'constraints': ['Illustrative synthetic data only; validate against independent physical measurements.',
                        'Keep the rule simple and test an unseen reading before deployment.'],
        'tests': [{'inputs': {'x': x}, 'expected': y} for x, y in [(0, 2), (2, 5), (4, 8), (8, 14)]],
        'tolerance': '0',
    },
    'Delivery cost': {
        'goal': 'Infer a cost rule from fixed setup cost f, unit cost u and quantity q.',
        'constraints': ['Illustrative synthetic costs, all in the same currency.',
                        'No demand, tax, capacity or profit claims are established by these cases.'],
        'tests': [{'inputs': {'f': f, 'u': u, 'q': q}, 'expected': y}
                  for f, u, q, y in [(10, 2, 0, 10), (10, 2, 5, 20), (20, 3, 4, 32), (5, 4, 10, 45)]],
        'tolerance': '0',
    },
    'Distance response': {
        'goal': 'Find a distance-response rule for signal strength s at positive distance d.',
        'constraints': ['Illustrative synthetic data from an idealized model; no measured physical validity.',
                        'Use positive distances only. Probe behavior outside the supplied cases.'],
        'tests': [{'inputs': {'d': d}, 'expected': y} for d, y in [(1, 100), (2, 25), (4, 6.25), (5, 4)]],
        'tolerance': '0.000001',
    },
}
