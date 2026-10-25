"""Model-free income planning from explicit assumptions and recorded work.

Ideas are reusable offer patterns, not evidence of market demand. Calculated
targets never enter the cash ledger. Osiris is a local operations coordinator.
"""
from decimal import Decimal, ROUND_CEILING

from app.revenue_metrics import amount, count, number


# title, useful skills, buyer, first deliverable, validation, tools
IDEAS = (
    ('CSV cleanup service', 'python data spreadsheets excel', 'Teams with messy exports',
     'Clean a supplied sample and deliver a change report', 'Ask a buyer to review a before/after sample',
     ('data_cleanup_service', 'quote_builder')),
    ('Monthly reporting retainer', 'data spreadsheets excel analytics', 'Small teams with recurring reports',
     'One report from actual data with a repeatable refresh process', 'Confirm recurring reporting needs and access',
     ('reporting_service', 'retainer_builder')),
    ('Workflow automation pilot', 'python code api automation', 'Teams doing repetitive manual work',
     'A bounded automation prototype and measured time comparison', 'Observe the process before estimating savings',
     ('ai_automation_audit', 'workflow_builder', 'automation_roi_calculator')),
    ('Website refresh package', 'web html css design', 'Businesses with outdated websites',
     'An audit and a local page prototype', 'Show observed issues and ask which matter to the owner',
     ('website_audit_seller', 'website_builder_agent')),
    ('Website maintenance retainer', 'web code support', 'Owners of existing websites',
     'A maintenance scope with backup and update procedures', 'Verify stack access and maintenance responsibilities',
     ('maintenance_plan_builder', 'retainer_builder')),
    ('Short video editing package', 'video editing content', 'Creators with existing footage',
     'A sample clip from authorized footage', 'Ask the creator to review a sample and confirm rights',
     ('clip_generator', 'service_generator')),
    ('Source-based newsletter', 'writing research content', 'A clearly defined niche audience',
     'One cited issue with a subscription offer draft', 'Interview readers and measure opt-in interest',
     ('newsletter_builder', 'sponsorship_finder')),
    ('Resume and interview pack', 'writing career coaching', 'Job seekers with actual experience',
     'A tailored resume and interview practice pack', 'Review role fit with a real job seeker',
     ('resume_service', 'interview_prep_product')),
    ('Downloadable template pack', 'design spreadsheets writing', 'People repeating a documented task',
     'A usable template with examples and instructions', 'Observe a target user completing the task',
     ('template_marketplace_builder', 'digital_product_builder')),
    ('Code starter license', 'python code api web', 'Developers repeating the same setup',
     'A source starter with tests, setup instructions and license draft', 'Ask developers to run the starter on a real task',
     ('code_template_builder', 'licensing_offer_builder')),
    ('Live skills workshop', 'teaching coaching writing code data', 'Teams needing a specific practical skill',
     'A lesson outline, exercise and workshop offer', 'Ask organizers about learning needs and willingness to book',
     ('workshop_offer_builder', 'quote_builder')),
    ('Public competitor research', 'research writing analytics', 'Owners evaluating a specific business decision',
     'A cited comparison with dates, gaps and recommendations', 'Confirm the buyer decision before researching',
     ('research_service', 'competitor_monitor')),
    ('Support knowledge setup', 'writing support automation', 'Teams answering repeated customer questions',
     'An approved knowledge pack and local support prototype', 'Check questions against actual support history',
     ('chatbot_builder', 'customer_feedback_analyzer')),
    ('Accessibility review package', 'web design research', 'Website owners seeking usability improvements',
     'A manual review scope and reproducible findings', 'Confirm target users and review scope; test actual behavior',
     ('website_audit_seller', 'proposal_builder')),
    ('Data dashboard setup', 'data analytics web spreadsheets', 'Owners lacking visibility into recorded metrics',
     'A local dashboard from supplied data', 'Have an owner use it for a real decision',
     ('dashboard_builder', 'reporting_service')),
    ('Localization editing service', 'translation languages writing', 'Businesses with multilingual content',
     'A reviewed sample and terminology checklist', 'Confirm language competence and have a native reviewer assess quality',
     ('service_generator', 'proposal_builder')),
    ('Referral partnership', 'sales business networking', 'Complementary service providers',
     'A partner offer with attribution and supplied commercial terms', 'Confirm partner interest and agreed responsibilities',
     ('b2b_partnership_proposal', 'referral_request')),
    ('Licensed data pack', 'data research analytics', 'Teams needing reusable permitted datasets',
     'A documented sample with provenance and resale rights', 'Verify license and buyer use case before packaging',
     ('data_product_builder', 'licensing_offer_builder')),
)


def currency(data):
    value = data.get('currency', 'USD')
    if not isinstance(value, str) or len(value) != 3 or not value.isascii() or not value.isalpha():
        raise ValueError('currency must be a three-letter currency code; currencies are never combined.')
    return value.upper()


def percent(data, key, default=None):
    return number(data, key, default, maximum=Decimal(100)) / 100


def ceiling(value):
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def ideas(data, goal=''):
    import re
    skills = data.get('skills', goal)
    if isinstance(skills, list):
        if not all(isinstance(item, str) for item in skills):
            raise ValueError('skills must contain text.')
        skills = ' '.join(skills)
    if not isinstance(skills, str):
        raise ValueError('skills must be text or a list of text.')
    words = set(re.findall(r'[a-z]+', skills.lower()))
    limit = count(data, 'limit', 10)
    if not 1 <= limit <= len(IDEAS):
        raise ValueError(f'limit must be between 1 and {len(IDEAS)}.')
    rows = []
    for index, (title, tags, buyer, deliverable, validation, tools) in enumerate(IDEAS):
        fit = sorted(words & set(tags.split()))
        rows.append((len(fit), -index, {'idea': title, 'buyer': buyer, 'first_deliverable': deliverable,
                     'validation': validation, 'matched_skills': fit, 'tools': list(tools),
                     'demand_verified': False, 'price': None}))
    return {'ideas': [row for _, _, row in sorted(rows, key=lambda item: item[:2], reverse=True)[:limit]],
            'basis': 'Reusable offer patterns ranked by supplied skill keywords. Competence, demand and prices need validation.'}


def economics(data):
    price = number(data, 'price')
    costs = number(data, 'direct_costs')
    hours = number(data, 'delivery_hours')
    hourly = number(data, 'hourly_cost')
    fee = percent(data, 'fee_percent', 0)
    acquisition = number(data, 'acquisition_cost', 0)
    fixed = number(data, 'fixed_costs', 0)
    contribution = price * (1 - fee) - costs - hours * hourly - acquisition
    return {'currency': currency(data), 'contribution_per_sale': amount(contribution),
            'margin_percent': amount(contribution / price * 100) if price else None,
            'break_even_sales': ceiling(fixed / contribution) if contribution > 0 else None,
            'viable_under_assumptions': contribution > 0,
            'basis': 'Price less fees, direct costs, valued delivery time and acquisition cost. Tax excluded; supplied assumptions, not receipts.'}


def goal_plan(data, recurring=False):
    target = number(data, 'monthly_target')
    fixed = number(data, 'monthly_fixed_costs', 0)
    contribution = number(data, 'contribution_per_customer' if recurring else 'contribution_per_sale')
    if contribution <= 0:
        raise ValueError('Supply a positive contribution after variable costs; use offer_unit_economics first.')
    units = ceiling((target + fixed) / contribution)
    hours = number(data, 'hours_per_customer' if recurring else 'hours_per_sale')
    available = number(data, 'monthly_available_hours')
    result = {'currency': currency(data), 'required_active_customers' if recurring else 'required_sales': units,
              'required_delivery_hours': amount(units * hours),
              'capacity_units': int(available / hours) if hours else None,
              'capacity_feasible': units * hours <= available,
              'basis': 'Monthly contribution target plus fixed costs, rounded up. All inputs are explicit planning assumptions; acquisition and admin time need separate allowance.'}
    if recurring:
        existing = count(data, 'active_customers', 0)
        churn = percent(data, 'monthly_churn_percent', 0)
        replacements = ceiling(existing * churn)
        result.update({'expected_churn_replacements': replacements,
                       'new_customers_needed': max(0, units - existing + replacements),
                       'churn_basis': 'Replacement allowance from supplied churn assumption, rounded up; not a forecast.'})
    return result


def funnel(data):
    stages = [count(data, field) for field in ('leads', 'replies', 'qualified', 'proposals', 'wins')]
    if any(right > left for left, right in zip(stages, stages[1:])):
        raise ValueError('Funnel counts must describe one cohort and cannot increase between stages.')
    labels = ('lead_to_reply', 'reply_to_qualified', 'qualified_to_proposal', 'proposal_to_win')
    rates = {name: amount(Decimal(right) / left * 100) if left else None
             for name, left, right in zip(labels, stages, stages[1:])}
    observed = [(Decimal(value), name) for name, value in rates.items() if value is not None]
    return {'conversion_percent': rates, 'lowest_observed_transition': min(observed)[1] if observed else None,
            'cohort': data.get('cohort'), 'sample_size': stages[0],
            'next_step': 'Review lost or unanswered cases at the weakest transition and test one change on a comparable cohort.',
            'basis': 'Owner-supplied counts from one cohort. The weakest observed rate is a diagnostic signal, not proof of a cause.'}


def validation(data, goal):
    offer, buyer = data.get('offer', goal), data.get('buyer')
    if not isinstance(offer, str) or not offer.strip() or not isinstance(buyer, str) or not buyer.strip():
        raise ValueError('Supply a concrete offer and buyer.')
    interviews = count(data, 'interviews', 5)
    commitments = count(data, 'minimum_commitments', 1)
    if not 1 <= interviews <= 1000 or not 1 <= commitments <= interviews:
        raise ValueError('Use 1–1000 interviews and minimum_commitments between 1 and interviews.')
    return {'offer': offer, 'buyer': buyer, 'demand_verified': False,
            'test': f'Interview {interviews} relevant buyers and show one bounded sample.',
            'go_criterion': f'At least {commitments} documented commitments with scope and supplied price agreed.',
            'stop_criterion': 'If the criterion is unmet, revise the buyer/problem/offer before building a larger product.',
            'evidence_to_record': ['buyer problem in their words', 'sample feedback', 'agreed scope and price', 'dated commitment or rejection'],
            'tools': ['service_generator', 'proposal_builder', 'revenue_experiment_manager', 'income_action_queue'],
            'basis': 'User-selected validation rule. Interest and commitments do not equal received income.'}


def portfolio(data, goal):
    selected = ideas(data, goal)['ideas'][:3]
    return {'candidate_offers': selected,
            'sequence': ['Validate one bounded service before expanding.',
                         'Deliver and measure real customer results; record receipts in the existing books.',
                         'Offer recurring support only for a demonstrated recurring need.',
                         'Package reusable work only after confirming rights and repeat demand.'],
            'tools': ['demand_validation_plan', 'offer_unit_economics', 'revenue_goal_planner',
                      'income_action_queue', 'recurring_revenue_planner'],
            'basis': 'A staged plan from supplied skills; no diversified returns or demand are inferred.'}


def action_queue(workspace, action, key, data):
    if action in {'create', 'record', 'update'}:
        if not key:
            raise ValueError('Supply a stable key for the action.')
        existing = next((row['data'] for row in workspace.records('income_action_queue') if row['key'] == key.strip()), {})
        if action == 'update' and not existing:
            raise ValueError('The action key does not exist.')
        values = {**existing, **data}
        title = values.get('title')
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            raise ValueError('Supply an action title of 1–500 characters.')
        status = values.get('status', 'planned')
        if status not in {'planned', 'in_progress', 'blocked', 'done', 'cancelled'}:
            raise ValueError('Unsupported action status.')
        tool = values.get('tool')
        if tool and tool not in workspace.registry.names():
            raise ValueError('The suggested tool is not registered.')
        priority = count(values, 'priority', 3)
        if not 1 <= priority <= 5:
            raise ValueError('priority must be 1–5; 1 is most urgent.')
        if status == 'done' and (not isinstance(values.get('evidence'), str) or not values['evidence'].strip()):
            raise ValueError('Record outcome evidence before marking an action done.')
        values.update({'status': status, 'priority': priority})
        return workspace.record('income_action_queue', key, values)
    if action not in {'report', 'list'}:
        raise ValueError('Use create, record, update, list or report for income actions.')
    rows = workspace.records('income_action_queue')
    rows.sort(key=lambda row: (row['data'].get('status') in {'done', 'cancelled'}, row['data'].get('priority', 3), row['key']))
    return {'actions': rows, 'basis': 'Owner-reported action states and evidence; suggestions are not executed.'}


def osiris(workspace, data, playbook=False):
    # The owner's supplied Osiris link identifies a global intelligence app.
    # Full runtimes use the independent intelligence workspace; standalone
    # income workspaces retain the old local operations helper for compatibility.
    intelligence_tool = 'intelligence_brief' if playbook else 'intelligence_dashboard'
    if intelligence_tool in workspace.registry.names():
        return workspace._call(intelligence_tool, {})
    queue = action_queue(workspace, 'report', '', {})['actions']
    pipeline = workspace.pipeline('list', {})
    books = workspace.ledger.report(data.get('period', 'this month'))
    result = {'name': 'Osiris', 'integration': 'Built-in local Hayat operations coordinator',
              'books': books, 'pipeline': pipeline, 'actions': queue,
              'desktop': workspace._call('computer_status', {}) if 'computer_status' in workspace.registry.names()
                         else {'available': False, 'reason': 'Desktop tools are not registered.'},
              'basis': 'Reads current local records and backend readiness. No external Osiris service is connected.'}
    if playbook:
        pending = [row for row in queue if row['data']['status'] not in {'done', 'cancelled'}]
        result['next_actions'] = [row['data'] for row in pending[:5]] or [
            {'title': 'Choose one skill-matched offer', 'tool': 'income_idea_engine'},
            {'title': 'Validate the buyer problem with a bounded sample', 'tool': 'demand_validation_plan'},
            {'title': 'Check contribution and delivery capacity', 'tool': 'offer_unit_economics'}]
        result['execution'] = 'Suggestions only; invoke each tool explicitly through Hayat’s normal permissioned executor.'
    return result


def execute(workspace, name, goal, data, action, key):
    if name == 'income_idea_engine':
        return ideas(data, goal)
    if name == 'income_portfolio_plan':
        return portfolio(data, goal)
    if name == 'offer_unit_economics':
        return economics(data)
    if name == 'revenue_goal_planner':
        return goal_plan(data)
    if name == 'recurring_revenue_planner':
        return goal_plan(data, recurring=True)
    if name == 'sales_funnel_diagnostics':
        return funnel(data)
    if name == 'demand_validation_plan':
        return validation(data, goal)
    if name == 'income_action_queue':
        return action_queue(workspace, action, key, data)
    if name in {'osiris_status', 'osiris_playbook'}:
        return osiris(workspace, data, playbook=name == 'osiris_playbook')
    raise ValueError('Unknown income operation.')
