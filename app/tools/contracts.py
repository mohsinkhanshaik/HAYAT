"""Inspect tool declarations without invoking handlers, services or devices."""
from __future__ import annotations

import inspect
import re
from collections import Counter

from jsonschema import validators
from jsonschema.exceptions import SchemaError, ValidationError


RISKS = ('low', 'medium', 'high')


def _check_authority(spec, issue):
    """The permission category must be one the authority knows, so its tier is the intended one.

    An unknown category falls back to the extended tier: harmless for a reader, but a sending tool
    with a misspelt category ('send_mesage') would silently lose its always-ask protection.
    Shell categories are resolved per command (powershell_safe / unknown / admin) before authority.
    """
    from app.safety.authority import CATEGORY_TIERS, RiskTier
    from app.safety.permissions import PermissionManager

    if spec.risk not in RISKS:
        issue('invalid_risk', f'Risk must be one of {", ".join(RISKS)}, not {spec.risk!r}.')
    category = spec.permission
    if category in PermissionManager.SHELL_CATEGORIES:
        return
    tier = CATEGORY_TIERS.get(category)
    if tier is None:
        issue('unknown_permission', f'{category!r} is not a category the authority knows; it would fall back to the extended '
                                    'tier instead of its intended one.')
    elif tier is RiskTier.PROHIBITED:
        issue('prohibited_permission', f'{category!r} is refused outright by the authority, so this tool can never run.')
    elif tier is RiskTier.HIGH_CONSEQUENCE and spec.risk == 'low':
        issue('risk_understated', f'{category!r} always needs a human answer, but the tool says its risk is low.', 'warning')
    if isinstance(spec.description, str) and 0 < len(spec.description.strip()) < 25:
        issue('thin_description', 'The description is too short for the planner to choose this tool well.', 'warning')


def audit_tool_contracts(registry):
    issues = []
    inspected = schema_valid = 0
    for name in registry.names():
        spec = registry.get(name)
        def issue(code, detail, severity='error'):
            issues.append({'tool': name, 'code': code, 'detail': detail, 'severity': severity})
        if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', name):
            issue('invalid_name', 'Tool names must be function identifiers.')
        if not isinstance(spec.description, str) or not spec.description.strip():
            issue('missing_description', 'A description is required.')
        if not isinstance(spec.permission, str) or not spec.permission:
            issue('missing_permission', 'An explicit permission category is required.')
        else:
            _check_authority(spec, issue)
        if not callable(spec.handler):
            issue('not_callable', 'The handler cannot be invoked.')
            continue
        try:
            validator = validators.validator_for(spec.input_schema)
            validator.check_schema(spec.input_schema)
            schema_valid += 1
        except (SchemaError, TypeError, AttributeError):
            issue('invalid_schema', 'Input schema is not valid JSON Schema.')
            continue
        schema = spec.input_schema
        if schema.get('type') != 'object':
            issue('not_object', 'Tool dispatch passes keyword arguments; the input must be an object.')
            continue
        properties, required = schema.get('properties', {}), set(schema.get('required', []))
        for parameter, declaration in properties.items():
            if isinstance(declaration, dict) and 'default' in declaration:
                try:
                    validator(declaration).validate(declaration['default'])
                except ValidationError:
                    issue('invalid_schema_default', f'{parameter}: its declared default does not satisfy its schema.')
        try:
            signature = inspect.signature(spec.handler)
        except (TypeError, ValueError):
            issue('signature_unavailable', 'Handler signature could not be inspected.', 'warning')
            continue
        inspected += 1
        parameters = signature.parameters
        accepts_extra = any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values())
        keyword_names = {key for key, item in parameters.items()
                         if item.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)}
        for parameter in sorted(set(properties) - keyword_names):
            if not accepts_extra:
                issue('unexpected_argument', f'{parameter}: the schema permits it but the handler does not accept it.')
        for parameter, item in parameters.items():
            if item.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_ONLY):
                if item.default is inspect.Parameter.empty and parameter not in required:
                    issue('missing_required_argument', f'{parameter}: the handler requires it but the schema permits omission.')
                if item.kind == inspect.Parameter.POSITIONAL_ONLY and item.default is inspect.Parameter.empty:
                    issue('positional_only_argument', f'{parameter}: required positional-only arguments cannot be supplied by tool dispatch.')
        if schema.get('additionalProperties', True) is not False and not accepts_extra:
            issue('open_argument_schema', 'Unknown arguments pass schema validation but the handler rejects them.', 'warning')
    counts = Counter(item['severity'] for item in issues)
    return {'tools_checked': len(registry.names()), 'schemas_valid': schema_valid, 'signatures_checked': inspected,
            'errors': counts['error'], 'warnings': counts['warning'], 'issues': issues,
            'contract_checks_passed': counts['error'] == 0,
            'basis': 'Static declarations only. No handler was executed; service, credential, device and live-data readiness are unverified.'}
