"""Workbench operations on HAYAT's governed tool bus."""


def register_invention_tools(registry, workbench, executive, dual_brain):
    def schema(properties=None, required=None):
        return {'type': 'object', 'properties': properties or {}, 'required': required or [], 'additionalProperties': False}
    identifier = {'project_id': {'type': 'string', 'pattern': '^invent-[a-f0-9]{32}$'}}
    registry.register('prefrontal_cortex_status', 'Inspect HAYAT executive goal, working state and stop rules.',
                      schema(), 'low', 'system_read', executive.status)
    registry.register('dual_brain_status', 'Inspect proposer/critic review provenance and unresolved issues.',
                      schema(), 'low', 'system_read', dual_brain.status)
    registry.register('invention_list', 'List saved invention projects and measured outcomes.',
                      schema(), 'low', 'filesystem_read', workbench.list)
    registry.register('invention_get', 'Inspect a saved hypothesis, research, critiques and numeric tests.',
                      schema(identifier, ['project_id']), 'low', 'filesystem_read', workbench.get)
    registry.register('invention_assess', 'Audit recorded invention evidence, source provenance, conflicts, unmeasured constraints and next experiments. '
                      'Offline; does not establish physical validity, novelty or patentability.',
                      schema(identifier, ['project_id']), 'low', 'filesystem_read', workbench.assess)
    registry.register('invention_create', 'Create a persistent invention goal with immutable numeric acceptance cases.',
                      schema({'goal': {'type': 'string', 'minLength': 1, 'maxLength': 6000},
                              'constraints': {'type': 'array', 'maxItems': 16, 'items': {'type': 'string', 'maxLength': 1000}},
                              'tests': {'type': 'array', 'maxItems': 32, 'items': {'type': 'object',
                                  'properties': {'inputs': {'type': 'object'}, 'expected': {'type': ['string', 'number']}},
                                  'required': ['inputs', 'expected'], 'additionalProperties': False}},
                              'tolerance': {'type': 'number', 'minimum': 0, 'maximum': 0.5}}, ['goal']),
                      'medium', 'filesystem_write_project', workbench.create)
    registry.register('invention_run', 'Run bounded research, proposer/critic refinement and exact numeric prototype tests. '
                      'With numeric cases it first works a formula out by fitting standard shapes (work_out, on by default), '
                      'fits any unknown constants the proposer leaves, and simulates the best formula at the end.',
                      schema({**identifier, 'cycles': {'type': 'integer', 'minimum': 1, 'maximum': 8},
                              'research': {'type': 'boolean'}, 'work_out': {'type': 'boolean'}}, ['project_id']),
                      'medium', 'filesystem_write_project', workbench.run)
    registry.register('invention_work_out', 'Work a project’s formula out without a model: fit standard shapes (line, '
                      'power law, quadratic, exponential, log, inverse, products, ratios) to its frozen cases, check each '
                      'by leave-one-out, save the best as a round and simulate it.',
                      schema(identifier, ['project_id']), 'low', 'filesystem_write_project', workbench.work_out)
    registry.register('invention_simulate', 'Simulate a formula (the best candidate’s by default) over a project’s '
                      'inputs: sweep each input to see where it rises, falls or breaks, the effect of a 10 % change in each '
                      'input, and a seeded Monte Carlo with every input off by up to the spread. Unknown constants are '
                      'fitted first. Simulates the formula, not the physical device.',
                      schema({**identifier, 'formula': {'type': 'string', 'minLength': 1, 'maxLength': 240},
                              'spread': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 0.5}}, ['project_id']),
                      'low', 'filesystem_write_project', workbench.simulate)
    registry.register('invention_try', 'Grade a formula on a project\u2019s frozen numeric cases without saving a round.',
                      schema({**identifier, 'formula': {'type': 'string', 'minLength': 1, 'maxLength': 240}}, ['project_id', 'formula']),
                      'low', 'filesystem_read', workbench.try_formula)
    registry.register('invention_stop', 'Stop an active invention run at its next cancellation checkpoint.',
                      schema(identifier, ['project_id']), 'low', 'system_read', workbench.stop)
