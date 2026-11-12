"""Dimension/scale checks and independent holdouts, with unchanged journals."""
from decimal import Decimal

import pytest

from app.engineering_units import audit_dimensions, convert_units
from tests.test_executive_invention import ScriptedModel, bench


@pytest.mark.parametrize('value,source,target,expected', [
    ('1', 'kWh', 'J', '3600000'), ('100', 'cm', 'm', '1'), ('36', 'km/h', 'm/s', '10'),
    ('1', 'N', 'kg*m/s^2', '1'), ('1', 'bar', 'kPa', '100'), ('1', 'V*A', 'W', '1'),
    ('0', 'degC', 'K', '273.15'), ('32', 'degF', 'degC', '0'), ('212', 'degF', 'degC', '100'),
    ('-459.67', 'degF', 'K', '0'), ('-40', 'degC', 'degF', '-40'),
])
def test_units_convert_physical_equivalences(value, source, target, expected):
    assert Decimal(convert_units(value, source, target)['value']) == Decimal(expected)


@pytest.mark.parametrize('value,source,target', [('1', 'W', 'J'), ('1', 'm', 'kg'),
                                               ('NaN', 'm', 'cm'), ('1e999', 'm', 'cm'),
                                               ('-273.16', 'degC', 'K'), ('1', 'degC/s', 'K/s')])
def test_incompatible_units_and_affine_composites_fail(value, source, target):
    with pytest.raises((ValueError, ArithmeticError)):
        convert_units(value, source, target)


@pytest.mark.parametrize('formula,inputs,output', [
    ('0.5*mass*speed**2', {'mass': 'kg', 'speed': 'm/s'}, 'J'),
    ('voltage*current', {'voltage': 'V', 'current': 'A'}, 'W'),
    ('force/area', {'force': 'N', 'area': 'm^2'}, 'Pa'),
    ('sqrt(area)', {'area': 'm^2'}, 'm'),
    ('sin(angle)', {'angle': 'rad'}, '1'),
    ('abs(distance)', {'distance': 'm'}, 'm'),
])
def test_dimensional_identities(formula, inputs, output):
    result = audit_dimensions(formula, inputs, output)
    assert result['compatible'] and result['scale_matches']


def test_scale_is_distinct_from_compatible_dimensions():
    result = audit_dimensions('distance/time', {'distance': 'cm', 'time': 's'}, 'm/s')
    assert result['compatible'] and not result['scale_matches']
    assert result['conversion_factor'] == '0.01'
    assert not audit_dimensions('power', {'power': 'W'}, 'J')['compatible']
    assert audit_dimensions('sqrt(x)**2', {'x': 'kWh'}, 'kWh')['scale_matches']


@pytest.mark.parametrize('formula,inputs', [
    ('distance+time', {'distance': 'm', 'time': 's'}),
    ('a+b', {'a': 'cm', 'b': 'm'}), ('length+1', {'length': 'm'}),
    ('sin(length)', {'length': 'm'}), ('x*c1', {'x': 'm'}),
    ('__import__("os").system("echo bad")', {'x': '1'}),
    ('x**999999', {'x': 'm'}), ('x.real', {'x': 'm'}),
])
def test_formula_analysis_never_executes_code_or_guesses_dimensions(formula, inputs):
    with pytest.raises((ValueError, SyntaxError)):
        audit_dimensions(formula, inputs, '1')


def test_holdouts_are_not_fitted_or_saved_and_overlap_is_visible(tmp_path):
    workbench = bench(tmp_path, ScriptedModel())
    project = workbench.create('Double x', tests=[{'inputs': {'x': 2}, 'expected': 4}])
    before = workbench.get(project['id'])
    result = workbench.holdout(project['id'], [{'inputs': {'x': '2.0'}, 'expected': 4},
                                             {'inputs': {'x': 5}, 'expected': 10},
                                             {'inputs': {'x': '5.0'}, 'expected': 11}], '2*x')
    assert result['measurement']['passed'] == 2
    assert result['unseen_inputs'] == 1 and result['overlapping_cases'] == 1 and result['duplicate_cases'] == 1
    assert not result['saved'] and not result['fitted']
    assert workbench.get(project['id']) == before
    unresolved = workbench.holdout(project['id'], [{'inputs': {'x': 5}, 'expected': 10}], 'c1*x')
    assert unresolved['measurement']['passed'] == 0


def test_distinct_high_precision_holdout_points_are_not_rounded_together(tmp_path):
    workbench = bench(tmp_path, ScriptedModel())
    project = workbench.create('Identity', tests=[{'inputs': {'x': 0}, 'expected': 0}])
    result = workbench.holdout(project['id'], [
        {'inputs': {'x': '1.00000000000000000000000000001'}, 'expected': '1.00000000000000000000000000001'},
        {'inputs': {'x': '1.00000000000000000000000000002'}, 'expected': '1.00000000000000000000000000002'},
    ], 'x')
    assert result['unseen_inputs'] == 2 and result['duplicate_cases'] == 0
