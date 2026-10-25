"""Persistent, executable earning plans from owner-supplied facts."""
import json
import uuid

from app.income import action_queue, currency, economics, ideas
from app.revenue_metrics import number


def create_plan(workspace, goal, data, context=''):
    fields = {}
    for key in ('skills', 'buyer', 'offer', 'deliverable'):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 4000:
            raise ValueError(f'Supply data.{key} as nonempty text up to 4,000 characters.')
        fields[key] = value.strip()
    hours = number(data, 'weekly_hours', 5, maximum=168)
    if hours <= 0:
        raise ValueError('weekly_hours must be positive.')
    selected = ideas({'skills': fields['skills'], 'limit': 3})['ideas']
    delivery_tool = selected[0]['tools'][0]
    economics_fields = ('price', 'direct_costs', 'delivery_hours', 'hourly_cost')
    supplied = [key for key in economics_fields if key in data]
    if supplied and len(supplied) != len(economics_fields):
        raise ValueError('To calculate economics supply price, direct_costs, delivery_hours and hourly_cost together.')
    unit = economics(data) if supplied else {'status': 'needs_input', 'required': economics_fields}
    stages = [
        ('Validate buyer problem', 'demand_validation_plan',
         'Write a list of five potential buyers. Ask about their current process, frequency, cost and decision maker. '
         'Record actual answers and objections; revise the offer if no repeated problem appears.',
         'Five interview notes and at least one explicit request for a scoped pilot; otherwise revise, do not scale.'),
        ('Make a small proof of work', delivery_tool,
         f'Use authorized input to produce: {fields["deliverable"]}. Cap the sample at one small representative task. '
         'Record baseline, steps, output and limitations; remove private client details from portfolio copies.',
         'An output file and a reproducible quality check accepted by a prospective buyer.'),
        ('Price and scope a paid pilot', 'pricing_engine',
         'Count delivery, revision, support and acquisition hours. Include direct costs and platform fees. '
         'Agree the deliverable, acceptance criteria, revision limit and payment terms before starting.',
         'Positive contribution under stated assumptions and written buyer acceptance of scope and price.'),
        ('Prepare a tailored proposal', 'proposal_builder',
         'Use interview evidence and the actual sample. State the buyer outcome, milestones, exclusions, '
         'price and next step. Review the exact proposal before contacting the buyer.',
         'A reviewed proposal; record sent/reply/win evidence separately.'),
        ('Deliver and verify', delivery_tool,
         'Work against the agreed checklist; test on representative and edge-case inputs. '
         'Package output, setup instructions and a change report. Obtain acceptance and record time spent.',
         'Delivery bundle, test results and buyer acceptance; incomplete work stays in progress.'),
        ('Invoice and reconcile', 'invoice_automation',
         'Create an invoice for the agreed amount using the actual client record. Reconcile received payment '
         'and costs from real evidence. Compare effective hourly return with the estimate.',
         'Invoice plus actual payment evidence; an issued invoice is not received revenue.'),
        ('Decide whether to repeat', 'retainer_builder',
         'Review objections, rework and margin. Offer a recurring scope only if the buyer has a recurring need. '
         'If the pilot loses money or no buyer commits, change the scope or stop that experiment.',
         'A repeat purchase, validated recurring need, or a documented stop/revise decision.'),
    ]
    missing_tools = {tool for _, tool, _, _ in stages} - set(workspace.registry.names())
    if missing_tools:
        raise ValueError('Required workflows are not registered: ' + ', '.join(sorted(missing_tools)))
    plan_id = 'plan-' + uuid.uuid4().hex[:10]
    lines = [f'# {goal.strip() or fields["offer"]}', '', '## Your supplied brief',
             *[f'- {key.title()}: {value}' for key, value in fields.items()],
             f'- Available time: {hours} hours/week', f'- Currency: {currency(data)}', '',
             '## Commercial test',
             'This plan tests a buyer problem and produces a deliverable. Demand, timing and earnings are unverified.',
             'Suggested pacing: week 1 discovery/sample; week 2 scoped proposals; weeks 3–4 an accepted paid pilot. '
             'Move on only when the evidence gate passes; this is not an income forecast.', '',
             '## Unit economics', '```json', json.dumps(unit, indent=2), '```', '', '## Work and evidence gates']
    actions = []
    for index, (title, tool, work, gate) in enumerate(stages, 1):
        lines.extend([f'### {index}. {title}', work, f'**Evidence gate:** {gate}', f'**Workflow:** `{tool}`', ''])
        actions.append({'key': f'{plan_id}-{index}', 'title': title, 'tool': tool,
                        'priority': min(index, 5), 'status': 'planned', 'plan_id': plan_id,
                        'instructions': work, 'acceptance': gate,
                        'depends_on': f'{plan_id}-{index-1}' if index > 1 else None})
    if context:
        lines.extend(['## Owner notes', context])
    lines.extend(['## Other offer patterns to evaluate', *[f'- {row["idea"]}: {row["validation"]}' for row in selected]])
    brief = {**fields, 'context': context, 'attachments': data.get('attachments', []),
             'weekly_hours': str(hours), 'unit_economics': unit, 'actions': actions}
    result = workspace._save('income_execution_plan', fields['offer'], [
        {'path': 'earning-plan.md', 'content': '\n'.join(lines)},
        {'path': 'brief-and-actions.json', 'content': json.dumps(brief, indent=2)},
        {'path': 'delivery-checklist.md', 'content': '# Delivery acceptance\n\n- [ ] Input rights and scope confirmed\n'
         '- [ ] Representative inputs checked\n- [ ] Edge cases and errors checked\n- [ ] Instructions included\n'
         '- [ ] Buyer acceptance recorded\n- [ ] Actual costs and hours recorded\n- [ ] Payment reconciled from evidence\n'},
    ], kind='plan')
    for row in actions:
        action_queue(workspace, 'create', row['key'], row)
    return {**result, 'plan_id': plan_id, 'actions_created': len(actions), 'unit_economics': unit,
            'preview': '\n'.join(lines), 'income_recorded': False}
