"""Small model-free revenue routes; richer requests use the existing schema planner."""
import json
import re

from app.revenue_catalog import BY_NAME


def direct_revenue_plan(text, names):
    value = text.strip()
    normalized = value.lower().strip(' .!?')
    tool, arguments = None, {}
    exact = re.fullmatch(r'(?:use\s+|run\s+)?([a-z_]+)\s*(\{.*\})?', value, flags=re.DOTALL)
    if exact and exact[1] in [*BY_NAME, 'revenue_tool_catalog']:
        tool = exact[1]
        arguments = json.loads(exact[2]) if exact[2] else {}
        if not isinstance(arguments, dict):
            raise ValueError('Named tool arguments must be a JSON object.')
    elif re.fullmatch(r'(?:show|open|list)(?: me)?(?: my| the)? revenue (?:dashboard|report)', normalized):
        tool = 'revenue_dashboard'
    elif re.fullmatch(r'(?:show|list)(?: me)?(?: my| the)? (?:revenue|growth) tools', normalized):
        tool = 'revenue_tool_catalog'
    elif re.fullmatch(r'(?:show|list)(?: my| the)? sales pipeline', normalized):
        tool = 'sales_pipeline'
    elif re.fullmatch(r'(?:show|check|open)(?: me)?(?: my| the)? osiris(?: status)?', normalized):
        tool = 'osiris_status'
    elif re.fullmatch(r'(?:show|build|create|generate)(?: me)?(?: my| the)? osiris (?:plan|playbook)', normalized):
        tool = 'osiris_playbook'
    elif re.fullmatch(r'(?:show|list)(?: me)?(?: my| the)? income (?:actions|queue)', normalized):
        tool = 'income_action_queue'
    elif re.fullmatch(r'(?:show|generate|find)(?: me)?(?: more)? income ideas', normalized):
        tool = 'income_idea_engine'
    elif (idea := re.fullmatch(r'(?is)(?:show|generate|find)(?: me)?(?: more)? income ideas (?:for|with) (.+)', value)):
        tool, arguments = 'income_idea_engine', {'data': {'skills': idea[1].strip()}}
    else:
        search = re.fullmatch(r'(?is)(?:find|search(?: for)?)\s+(?:me\s+)?(?:freelance\s+(?:jobs|gigs)|gigs)\s+(?:for|in|about)\s+(.+)', value)
        if search:
            tool, arguments = 'find_freelance_jobs', {'goal': search[1].strip()}
    if tool not in names:
        return None
    return {'description': 'Run the requested revenue workflow using actual supplied inputs.', 'tool': tool,
            'arguments': arguments, 'expected': {'type': 'tool_success'}, 'max_retries': 0}
