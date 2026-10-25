"""Explicit chat/CLI commands for the invention workspace."""
import re


def command(text):
    value = str(text).strip()
    match = re.fullmatch(r'(?is)(?:create|start) (?:an? )?invention (?:project|goal):\s*(.{1,6000})', value)
    if match:
        return 'invention_create', {'goal': match[1].strip()}
    match = re.fullmatch(r'(?i)(?:try|test|grade) (?:the )?formula (.{1,240}?) (?:on|against|for) invention (invent-[a-f0-9]{32})', value)
    if match:
        return 'invention_try', {'project_id': match[2].lower(), 'formula': match[1].strip()}
    match = re.fullmatch(r'(?i)work (?:out|it out on) invention (invent-[a-f0-9]{32})', value)
    if match:
        return 'invention_work_out', {'project_id': match[1].lower()}
    match = re.fullmatch(r'(?i)simulate (?:(?:the )?formula (.{1,240}?) (?:on|for) )?invention (invent-[a-f0-9]{32})'
                         r'(?: with (?:a )?(\d{1,2}(?:\.\d+)?) ?% spread)?', value)
    if match:
        arguments = {'project_id': match[2].lower()}
        if match[1]:
            arguments['formula'] = match[1].strip()
        if match[3]:
            spread = float(match[3]) / 100
            if not 0 < spread <= .5:
                return None
            arguments['spread'] = spread
        return 'invention_simulate', arguments
    match = re.fullmatch(r'(?i)(run|resume|stop|inspect|assess|audit) invention (invent-[a-f0-9]{32})(?: for ([1-8]) cycles?)?', value)
    if match:
        verb, identifier, cycles = match.groups()
        verb, identifier = verb.lower(), identifier.lower()
        if cycles and verb not in {'run', 'resume'}:
            return None
        name = {'run': 'invention_run', 'resume': 'invention_run', 'stop': 'invention_stop', 'inspect': 'invention_get',
                'assess': 'invention_assess', 'audit': 'invention_assess'}[verb.lower()]
        arguments = {'project_id': identifier}
        if name == 'invention_run':
            arguments.update(cycles=int(cycles or 3), research=True)
        return name, arguments
    return {'list invention projects': ('invention_list', {}),
            'prefrontal cortex status': ('prefrontal_cortex_status', {}),
            'dual brain status': ('dual_brain_status', {})}.get(value.casefold())
