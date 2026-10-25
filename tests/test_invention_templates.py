"""Starter examples execute the numerical engine and unseen probes without a model."""
import pytest

from app import invention_sim
from app.invention_templates import TEMPLATES
from tests.test_executive_invention import ScriptedModel, bench


@pytest.mark.parametrize(('name', 'inputs', 'expected'), [
    ('Sensor calibration', {'x': 6}, 11),
    ('Delivery cost', {'f': 7, 'u': 3, 'q': 6}, 25),
    ('Distance response', {'d': 10}, 1),
])
def test_example_fits_and_evaluates_unseen_point_locally(tmp_path, name, inputs, expected):
    template = TEMPLATES[name]
    model = ScriptedModel()
    workbench = bench(tmp_path, model)
    project = workbench.create(**template)
    result = workbench.work_out(project['id'])
    best = result['best']
    assert best['measurement']['passed'] == best['measurement']['total'] == 4
    actual, _exact = invention_sim.evaluate(best['candidate']['formula'], inputs)
    assert float(actual) == pytest.approx(expected, rel=1e-6)
    assert not model.calls
    assert 'Illustrative synthetic' in result['constraints'][0]
