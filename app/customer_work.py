"""Persistent customer briefs and bounded, reviewable service work."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

SERVICES = {
    'csv_cleanup': {'label': 'CSV cleanup', 'tool': 'data_cleanup_service',
                    'terms': r'\b(?:csv|deduplicat\w*|duplicate rows|clean\w* data)\b',
                    'deliverable': 'Cleaned CSV and a change report; original preserved'},
    'content_pack': {'label': 'Content writing', 'tool': 'script_generator',
                     'terms': r'\b(?:content|newsletter|social posts?|scripts?|copywriting|captions?)\b',
                     'deliverable': 'Reviewable content draft with a clear audience and call to action'},
    'digital_templates': {'label': 'Digital templates', 'tool': 'digital_product_builder',
                          'terms': r'\b(?:templates?|planners?|workbooks?|prompt packs?)\b',
                          'deliverable': 'Editable local template files and usage instructions'},
    'website': {'label': 'Website build', 'tool': 'website_builder_agent',
                'terms': r'\b(?:websites?|landing pages?|web pages?)\b',
                'deliverable': 'Local website source and setup instructions; integrations explicitly configured or missing'},
    'research_report': {'label': 'Research or reporting', 'tool': 'reporting_service',
                        'terms': r'\b(?:research|reports?|competitors?|analysis|analy[sz]e)\b',
                        'deliverable': 'Report grounded in supplied evidence, with unresolved claims labelled'},
}
FACT_KEYS = {'audience', 'budget', 'deadline', 'source_path', 'cleanup_rule', 'source_urls', 'requirements'}


def timestamp():
    return datetime.now(UTC).isoformat(timespec='seconds')


class CustomerWorkspace:
    def __init__(self, revenue):
        self.revenue, self.ledger = revenue, revenue.ledger
        self._lock = threading.Lock()
        self.ledger._run(lambda db: db.executescript('''
            CREATE TABLE IF NOT EXISTS service_customers (
                id TEXT PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE NOT NULL,
                status TEXT NOT NULL, messages TEXT NOT NULL, facts TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS service_customer_jobs (
                id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, revision TEXT NOT NULL,
                action TEXT NOT NULL, state TEXT NOT NULL, result TEXT, error TEXT, updated TEXT NOT NULL,
                UNIQUE(customer_id,revision,action));
            CREATE TABLE IF NOT EXISTS service_customer_job_history (
                id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, revision TEXT NOT NULL,
                action TEXT NOT NULL, state TEXT NOT NULL, result TEXT, error TEXT, updated TEXT NOT NULL);
        '''))

    def _row(self, customer):
        row = self.ledger._run(lambda db: db.execute(
            'SELECT * FROM service_customers WHERE name=? COLLATE NOCASE OR id=?',
            (customer, customer)).fetchone())
        if row is None:
            raise ValueError('Customer not found. Start with Customer NAME: their actual inquiry.')
        result = dict(row)
        result['messages'], result['facts'] = json.loads(row['messages']), json.loads(row['facts'])
        return result

    @staticmethod
    def _revision(row):
        payload = {key: row[key] for key in ('id', 'status', 'messages', 'facts')}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _facts(facts):
        if not isinstance(facts, dict) or set(facts) - FACT_KEYS:
            raise ValueError('Only explicit audience, budget, deadline, source_path, cleanup_rule, source_urls and requirements are supported.')
        for key, value in facts.items():
            if key == 'source_urls':
                if not isinstance(value, list) or len(value) > 5 or any(not isinstance(url, str) or len(url) > 2000 for url in value):
                    raise ValueError('Supply at most five source URLs.')
            elif not isinstance(value, str) or len(value) > 20000:
                raise ValueError('Customer facts must be text of at most 20,000 characters.')
        if facts.get('cleanup_rule') not in (None, '', 'trim_only', 'exact_rows'):
            raise ValueError('CSV cleanup_rule must be trim_only or exact_rows. Other rules need a separately scoped workflow.')
        return dict(facts)

    def intake(self, customer, message, facts=None, status=None):
        customer, message = customer.strip(), message.strip()
        if not customer or len(customer) > 160 or not message or len(message) > 20000:
            raise ValueError('Supply a customer name (1–160 characters) and an actual inquiry (1–20,000 characters).')
        if status not in (None, 'prospect', 'customer', 'paused', 'closed'):
            raise ValueError('Status must be prospect, customer, paused or closed.')
        facts = self._facts(facts or {})
        def save(db):
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM service_customers WHERE name=? COLLATE NOCASE', (customer,)).fetchone()
            messages = json.loads(old['messages']) if old else []
            # Duplicate chat submissions do not create a new revision/job.
            if not messages or messages[-1] != message:
                messages.append(message)
            if len(messages) > 100 or sum(len(item) for item in messages) > 100000:
                raise ValueError('Customer history is full; start a separately named engagement.')
            merged = json.loads(old['facts']) if old else {}
            merged.update(facts)
            values = (old['id'] if old else uuid4().hex, customer,
                      status or (old['status'] if old else 'prospect'), json.dumps(messages), json.dumps(merged), timestamp())
            db.execute('INSERT INTO service_customers VALUES (?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET '
                       'status=excluded.status,messages=excluded.messages,facts=excluded.facts,updated=excluded.updated', values)
        self.ledger._run(save)
        result = self.decide(customer)
        result['saved'] = True
        result['text'] = 'Saved customer brief.\n' + result['text']
        return result

    def customers(self):
        rows = self.ledger._run(lambda db: [dict(row) for row in db.execute(
            'SELECT id,name,status,updated FROM service_customers ORDER BY updated DESC,name LIMIT 500')])
        text = 'Tracked customers and prospects:\n' + ('\n'.join(f"• {row['name']} — {row['status']}" for row in rows) or 'None yet. Start with Customer NAME: their actual inquiry.')
        return {'customers': rows, 'text': text, 'basis': 'Owner-supplied inquiries; customer status is not a verified sale.'}

    def details(self, customer):
        row = self._row(customer)
        jobs = self.ledger._run(lambda db: [dict(job) for job in db.execute(
            'SELECT id,action,state,error,updated FROM service_customer_jobs WHERE customer_id=? '
            'UNION ALL SELECT id,action,state,error,updated FROM service_customer_job_history WHERE customer_id=? '
            'ORDER BY updated DESC LIMIT 30', (row['id'], row['id']))])
        decision = self._decision(row)
        return {**decision, 'messages': row['messages'], 'jobs': jobs,
                'text': decision['text'] + '\n\nAction history:\n' + ('\n'.join(f"{job['action']}: {job['state']}" for job in jobs) or 'No work prepared yet.')}

    def decide(self, customer):
        return self._decision(self._row(customer))

    def _decision(self, row):
        facts, messages = row['facts'], row['messages']
        # A later explicit service request supersedes an earlier topic. With
        # multiple plausible services, ask which deliverable takes priority.
        candidates = []
        evidence = ''
        from app.customer_chat import labelled_facts
        for message in reversed(messages):
            service_text = '\n'.join(line for line in message.splitlines() if not labelled_facts(line))
            matches = [key for key, spec in SERVICES.items() if re.search(spec['terms'], service_text, re.I)]
            if matches:
                candidates, evidence = matches, message
                break
        chosen = candidates[0] if len(candidates) == 1 else None
        missing = []
        if not chosen:
            missing.append('Which concrete deliverable should we prepare first?')
        elif chosen == 'csv_cleanup':
            if not facts.get('source_path'):
                missing.append('Which local CSV file should be cleaned?')
            if not facts.get('cleanup_rule'):
                missing.append('Trim fields only, or also remove exact duplicate rows?')
        elif chosen == 'research_report':
            if not (facts.get('source_path') or facts.get('requirements') or facts.get('source_urls')):
                missing.append('Provide the actual dataset or source evidence and the question the report must answer.')
        elif not facts.get('audience'):
            missing.append('Who is the intended audience or buyer?')
        latest_inquiry = next(('\n'.join(line for line in message.splitlines() if not labelled_facts(line))
                               for message in reversed(messages)
                               if any(line.strip() and not labelled_facts(line) for line in message.splitlines())), '')
        declined = bool(re.search(r"\b(?:not interested|no thanks|do not contact|don['’]?t contact|stop contacting|unsubscribe)\b", latest_inquiry, re.I))
        stopped = declined or row['status'] in {'paused', 'closed'}
        next_action = 'stop' if stopped else 'reply' if missing else 'deliver' if chosen == 'csv_cleanup' else 'proposal'
        revision = self._revision(row)
        prepared_actions = self.ledger._run(lambda db: [job['action'] for job in db.execute(
            "SELECT action FROM service_customer_jobs WHERE customer_id=? AND revision=? AND state='prepared' ORDER BY updated,action",
            (row['id'], revision))])
        can_draft = self.revenue.model is not None or (chosen == 'research_report' and bool(facts.get('source_urls')))
        if not stopped and not missing and ('deliver' in prepared_actions or ('proposal' in prepared_actions and can_draft)):
            next_action = 'deliver'
        label = SERVICES[chosen]['label'] if chosen else 'Needs clarification'
        basis = 'Matched wording in the supplied inquiry; not proof of demand, willingness to pay, or a sale.'
        text = f"{row['name']} · {row['status']}\nService: {label}\n"
        if evidence:
            text += f'Customer evidence: {evidence[:1000]}\n'
        text += 'Next step: ' + {'stop': 'Keep this engagement paused; no follow-up prepared.',
                                'reply': 'Prepare a reply that closes the requirements gap.',
                                'proposal': 'Prepare a scoped proposal for review.',
                                'deliver': ('Clean the supplied CSV and prepare a change report.' if chosen == 'csv_cleanup'
                                            else 'Check and reuse the prepared deliverable.' if 'deliver' in prepared_actions
                                            else 'Prepare the draft deliverable from the saved customer brief.')}[next_action]
        if prepared_actions:
            text += '\nAlready prepared for this brief: ' + ', '.join(prepared_actions) + '.'
        if missing and not stopped:
            text += '\nNeeds input:\n' + '\n'.join('• ' + value for value in missing)
        supplied = '\n'.join(f'{key.replace("_", " ").capitalize()}: {facts[key]}' for key in ('audience', 'budget', 'deadline') if facts.get(key))
        if supplied:
            text += '\n' + supplied
        if chosen and chosen != 'csv_cleanup' and self.revenue.model is None:
            text += '\nAI drafting needs a configured local model. Qualification replies and service briefs work without it.'
        if not stopped:
            text += '\nSay “Decide and do the next step” to prepare local work, or “Do the work for this customer” for a draft deliverable.'
        return {'customer_reference': row['name'], 'customer_id': row['id'], 'status': row['status'],
                'revision': revision, 'facts': facts, 'service': chosen, 'alternatives': candidates,
                'prepared_actions': prepared_actions,
                'evidence': evidence, 'missing_fields': missing, 'next_action': next_action,
                'basis': basis, 'text': text, 'published': False, 'sent': False}

    @staticmethod
    def _file_hash(path, limit):
        digest, total = hashlib.sha256(), 0
        with path.open('rb') as handle:
            while chunk := handle.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise ValueError('File exceeds the service verification limit.')
                digest.update(chunk)
        return digest.hexdigest()

    def _fingerprints(self, result):
        values = {}
        for field in ('path', 'bundle'):
            if result.get(field):
                path = self.ledger.permissions.validate_project_path(result[field])
                if path.stat().st_size > 12 * 1024 * 1024:
                    raise ValueError('Service artifact exceeds the verification limit.')
                values[str(path)] = self._file_hash(path, 12 * 1024 * 1024)
        if not values:
            raise ValueError('Service work did not return a saved artifact.')
        return values

    def work(self, customer, action='next', retry=False):
        if action not in {'next', 'reply', 'proposal', 'deliver'}:
            raise ValueError('Choose next, reply, proposal or deliver.')
        row = self._row(customer)
        decision = self._decision(row)
        if decision['next_action'] == 'stop':
            return {**decision, 'prepared': False}
        if retry and action == 'next':
            previous = self.ledger._run(lambda db: db.execute(
                "SELECT action FROM service_customer_jobs WHERE customer_id=? AND action IN ('reply','proposal','deliver') ORDER BY updated DESC,rowid DESC LIMIT 1", (row['id'],)).fetchone())
            if previous:
                action = previous['action']
        action = decision['next_action'] if action == 'next' else action
        if action == 'deliver' and decision['missing_fields']:
            return {**decision, 'prepared': False, 'text': 'Delivery needs the following inputs first.\n' + decision['text']}
        revision = decision['revision']
        # Source changes are part of a delivery revision, even if the message
        # did not change. Inquiries cannot choose arbitrary tools or code.
        if action == 'deliver' and decision['facts'].get('source_path'):
            path = self.ledger.permissions.validate_project_path(decision['facts']['source_path'])
            if not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
                raise ValueError('Supply an existing source file of at most 10 MiB.')
            revision = hashlib.sha256((revision + self._file_hash(path, 10 * 1024 * 1024)).encode()).hexdigest()
        return self._run_prepared(row, revision, action, lambda: self._prepare(row, decision, action), retry)

    def _run_prepared(self, row, revision, action, prepare, retry=False):
        def claim(db):
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM service_customer_jobs WHERE customer_id=? AND revision=? AND action=?',
                             (row['id'], revision, action)).fetchone()
            if old and old['state'] == 'prepared' and (not retry or action in {'quote', 'invoice'}):
                saved = json.loads(old['result'])
                if self._fingerprints(saved) != saved['artifact_sha256']:
                    if action in {'quote', 'invoice'}:
                        raise ValueError('Prepared billing PDF changed. Review the ledger document; repeated requests do not overwrite it.')
                    raise ValueError('Prepared files changed. Review them; use retry=true to rebuild in a new local folder.')
                return {**saved, 'reused': True, 'text': 'Using the existing prepared work for this brief.\n' + saved['text']}
            if old and old['state'] == 'running':
                age = (datetime.now(UTC) - datetime.fromisoformat(old['updated'])).total_seconds()
                if not retry or age < 900:
                    raise ValueError('Work for this brief is already running. An interrupted job can be retried after 15 minutes using retry=true.')
            if old and old['state'] == 'failed' and not retry:
                raise ValueError('The previous attempt failed: ' + str(old['error']) + '. Use retry=true after correcting the problem.')
            if old:
                archived = dict(old)
                if archived['state'] == 'running':
                    archived.update(state='superseded', error='Explicit retry after the waiting period; previous outcome is unknown.')
                db.execute('INSERT OR IGNORE INTO service_customer_job_history VALUES (?,?,?,?,?,?,?,?)', tuple(archived.values()))
            job_id = uuid4().hex
            db.execute('INSERT INTO service_customer_jobs VALUES (?,?,?,?,?,?,?,?) '
                'ON CONFLICT(customer_id,revision,action) DO UPDATE SET id=excluded.id,state=excluded.state,result=NULL,error=NULL,updated=excluded.updated',
                (job_id, row['id'], revision, action, 'running', None, None, timestamp()))
            return job_id
        with self._lock:
            claimed = self.ledger._run(claim)
        if isinstance(claimed, dict):
            return claimed
        job_id = claimed
        try:
            result = prepare()
            result.update(customer_reference=row['name'], job_id=job_id, prepared=True, action=action,
                          sent=False, published=False, customer_delivery_confirmed=False)
            result['artifact_sha256'] = self._fingerprints(result)
            result['text'] = f"{row['name']}: {action} prepared locally.\n" + result['text'] + '\nReview before sending or treating this as completed customer delivery.'
            self.ledger._run(lambda db: db.execute('UPDATE service_customer_jobs SET state=?,result=?,updated=? WHERE id=?',
                ('prepared', json.dumps(result), timestamp(), job_id)))
            return result
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
            self.ledger._run(lambda db: db.execute('UPDATE service_customer_jobs SET state=?,error=?,updated=? WHERE id=?',
                ('failed', error, timestamp(), job_id)))
            raise

    @staticmethod
    def _billing_result(document):
        return {'document': document, 'number': document['number'], 'path': document['path'],
                'payment_collected': False, 'payment_verified': False,
                'text': f"{document['kind'].capitalize()} {document['number']}: {document['total']}\n{document['path']}\n"
                        'Prepared in the local business ledger. This action does not send or collect payment.'}

    def quote(self, customer, price=None, currency=None, description=None, retry=False):
        from app.tools.business import _currency, cents
        row = self._row(customer)
        decision = self._decision(row)
        if decision['next_action'] == 'stop' or decision['missing_fields']:
            return {**decision, 'prepared': False, 'text': 'Quote needs an active, scoped customer brief.\n' + decision['text']}
        if price is None or currency is None:
            return {**decision, 'prepared': False, 'missing_fields': ['Supply the proposed price and currency explicitly.'],
                    'text': 'Quote needs your proposed price and currency. Say: Prepare a quote for this customer for AMOUNT CURRENCY. Customer budget is not an agreed price.'}
        if not isinstance(price, str) or not re.fullmatch(r'\d+(?:\.\d{1,2})?', price.strip()):
            raise ValueError('Supply the explicit quote price with at most two decimal places.')
        if not isinstance(currency, str) or not currency.strip():
            raise ValueError('Supply the quote currency explicitly.')
        amount = cents(price)
        if amount <= 0:
            raise ValueError('Supply a positive quote price; customer budget is not an agreed price.')
        currency = _currency(currency, '')
        description = (description or SERVICES[decision['service']]['deliverable']).strip()
        if not description or len(description) > 2000:
            raise ValueError('Supply a quote description of 1–2,000 characters.')
        payload = json.dumps([decision['revision'], amount, currency, description])
        revision = hashlib.sha256(payload.encode()).hexdigest()
        notes = ('Proposed scope and price for review. Customer acceptance and delivery date are unconfirmed.\n'
                 'Engagement reference: HAYAT-' + revision[:20])
        def prepare():
            existing = self.ledger._run(lambda db: db.execute(
                "SELECT number FROM documents WHERE kind='quote' AND notes=?", (notes,)).fetchone())
            if existing:
                return self._billing_result(self.revenue._call('invoice_get', {'number': existing['number']}))
            clients = self.revenue._call('client_list', {})['clients']
            client = next((item['name'] for item in clients if item['name'].casefold() == row['name'].casefold()), None)
            if client is None:
                client = self.revenue._call('client_add', {'name': row['name'], 'currency': currency})['client']['name']
            document = self.revenue._call('quote_create', {
                'client': client, 'currency': currency,
                'items': [{'description': description, 'quantity': '1', 'unit_price': f'{amount // 100}.{amount % 100:02d}'}],
                'notes': notes})
            return self._billing_result(document)
        result = self._run_prepared(row, revision, 'quote', prepare, retry)
        # Read current ledger status on reuse (a quote may since be invoiced).
        result['document'] = self.revenue._call('invoice_get', {'number': result['number']})
        return result

    def invoice(self, customer, quote_number=None, retry=False):
        row = self._row(customer)
        decision = self._decision(row)
        if decision['next_action'] == 'stop':
            return {**decision, 'prepared': False}
        if quote_number is None:
            return {**decision, 'prepared': False, 'missing_fields': ['Choose this customer’s quote number.'],
                    'text': 'Choose the quote number shown in chat or Business. Say: Prepare an invoice for this customer from quote Q-YEAR-NUMBER. Payment remains unconfirmed.'}
        quote = self.revenue._call('invoice_get', {'number': quote_number})
        if quote['kind'] != 'quote' or quote['client'].casefold() != row['name'].casefold():
            raise ValueError('Choose a quote belonging to the selected customer.')
        revision = hashlib.sha256((row['id'] + ':' + quote['number']).encode()).hexdigest()
        def prepare():
            saved = self.ledger._run(lambda db: db.execute(
                "SELECT number FROM documents WHERE kind='invoice' AND source_quote=?", (quote['number'],)).fetchone())
            if saved:
                existing = self.revenue._call('invoice_get', {'number': saved['number']})
                if existing['status'] == 'void':
                    raise ValueError('The invoice from this quote was voided. Review it in Business before issuing another.')
                return self._billing_result(existing)
            document = self.revenue._call('invoice_create', {'client': quote['client'], 'currency': quote['currency'],
                'from_quote': quote['number'], 'notes': 'Prepared on owner request. Payment has not been collected or verified by HAYAT.'})
            return self._billing_result(document)
        result = self._run_prepared(row, revision, 'invoice', prepare, retry)
        result['document'] = self.revenue._call('invoice_get', {'number': result['number']})
        if result['document']['status'] == 'void':
            raise ValueError('The invoice from this quote was voided. Review it in Business before issuing another.')
        return result

    def _prepare(self, row, decision, action):
        facts = decision['facts']
        context = json.dumps({'customer': row['name'], 'inquiries': row['messages'], 'explicit_facts': facts,
                              'service': decision['service'], 'missing_fields': decision['missing_fields']})
        if action == 'deliver':
            spec = SERVICES[decision['service']]
            data = dict(facts)
            if decision['service'] == 'csv_cleanup':
                return self.revenue._call(spec['tool'], {'data': {'path': facts['source_path'], 'drop_duplicates': facts['cleanup_rule'] == 'exact_rows'}})
            if decision['service'] == 'research_report' and facts.get('source_urls'):
                result = self.revenue._call('research_service', {'goal': 'Answer the supplied customer research question using attributable public source evidence. ' + str(facts.get('requirements') or ''),
                                                               'data': {'urls': facts['source_urls']}, 'context': context})
                if result.get('path'):
                    return result
                notes = '# Retrieved research evidence\n\nNo synthesis model was configured. This bundle contains observed page evidence, not a completed analytical report.\n\n'
                notes += '\n'.join(str(source.get('final_url') or source.get('url')) for source in result['sources'])
                saved = self.revenue._save('research_service', 'Research evidence — ' + row['name'],
                    [{'path': 'research-notes.md', 'content': notes}, {'path': 'sources.json', 'content': json.dumps(result, indent=2)}], kind='evidence')
                return {**result, **saved}
            if facts.get('source_path'):
                data['source_content'] = self._source_content(facts['source_path'])
            if decision['service'] == 'content_pack':
                from app.agent.context import UNTRUSTED_DATA_NOTICE
                prompt = ('Write the complete content in the format actually requested by the customer '
                          '(for example newsletter, social post, caption or video script). Honor the supplied audience, '
                          'length and call to action. Use only supplied facts. Mark missing facts, prices and dates as '
                          'unconfirmed; do not claim customer approval, publication or measured results. '
                          'Do not invent demos, availability, setup simplicity, integrations, guarantees or promotions. '
                          'Return the finished draft in Markdown. ' + UNTRUSTED_DATA_NOTICE)
                content = self.revenue._generate([{'role': 'system', 'content': prompt},
                    {'role': 'user', 'content': json.dumps({'brief': json.loads(context), 'data': data}, default=str)}])
                review_prompt = ('Edit the draft against the supplied customer evidence. Remove every unsupported '
                                 'specific claim or promise. Product facts must be directly supported by the brief or source data. '
                                 'Do not infer website integration, setup ease, speed, measured benefits, demos, discounts, '
                                 'availability or dates. A draft can omit unknown details or label them unconfirmed. '
                                 'If no call to action is supplied, invite questions without promising a demo, event or delivery date. '
                                 'Preserve the requested format, subject line if requested, audience and word limit. '
                                 'Return only the corrected Markdown draft. ' + UNTRUSTED_DATA_NOTICE)
                content = self.revenue._generate([{'role': 'system', 'content': review_prompt},
                    {'role': 'user', 'content': json.dumps({'brief': json.loads(context), 'data': data, 'draft': content}, default=str)}])
                saved = self.revenue._save('customer_work', 'Content draft — ' + row['name'],
                                           [{'path': 'content-draft.md', 'content': content}], kind='service-draft')
                return {**saved, 'preview': content, 'content_review': 'AI grounding edit against supplied evidence; human review still required.'}
            goal = 'Prepare a small complete ' + spec['deliverable'] + ' from the supplied customer requirements. Keep the first draft focused; do not invent missing facts.'
            return self.revenue._call(spec['tool'], {'goal': goal, 'context': context, 'data': data})
        # Every customer can get useful qualification work without an AI
        # server. Rich proposals use the existing local model when present.
        if action == 'proposal' and self.revenue.model is not None:
            return self.revenue._call('proposal_builder', {'goal': 'Prepare a concise scoped proposal. State supplied budget and deadline as customer constraints, not an agreed price or commitment. Leave unapproved price and dates TBD.',
                                                          'context': context, 'data': {'business_profile': self.ledger.profile()}})
        service = SERVICES.get(decision['service'], {})
        questions = decision['missing_fields'] or ['Confirm the final scope, acceptance criteria and any constraints before agreeing price or delivery.']
        reply = f"Hello {row['name']},\n\nThank you for explaining what you need. "
        reply += ('We can scope ' + service['label'].lower() + ' around your requirements. ' if service else 'Let us clarify the first useful deliverable. ')
        reply += '\n\n' + '\n'.join('• ' + question for question in questions)
        reply += '\n\nWe can confirm a quote and timeline once the scope is clear.\n'
        brief = f"# Service brief: {row['name']}\n\n## Supplied inquiries\n\n" + '\n\n'.join(row['messages'])
        brief += '\n\n## Supplied constraints\n\n' + json.dumps(facts, indent=2)
        brief += '\n\n## Proposed deliverable\n\n' + service.get('deliverable', 'To be clarified')
        brief += '\n\n## Acceptance review\n\nCheck the output against the supplied scope; verify claims and file integrity. Price, customer approval and delivery remain unconfirmed.\n'
        brief += '\n## Next questions\n\n' + '\n'.join('• ' + question for question in questions)
        result = self.revenue._save('proposal_builder', 'Customer work — ' + row['name'],
            [{'path': 'customer-reply.md', 'content': reply}, {'path': 'service-brief.md', 'content': brief}], kind='service-plan')
        result['preview'] = reply
        result['model_used'] = False
        return result

    def _source_content(self, path):
        suffix = Path(path).suffix.lower()
        if suffix == '.xlsx':
            source = self.revenue._call('read_spreadsheet_for_edit', {'path': path, 'cell_range': 'A1:F30'})
            body = '\n'.join(f"{cell['cell']} [{cell['type']}]: {cell['value']}" for cell in source['cells'])
            scope = f"Only {source['sheet']}!A1:F30. Formulas are shown as formulas, not calculated values. Full workbook totals are unverified."
        elif suffix in {'.docx', '.pptx'}:
            source = self.revenue._call('read_document_for_edit', {'path': path})
            body = '\n\n'.join(source['paragraphs'])
            scope = 'Supported document paragraphs/tables or slide text; embedded objects and other document regions are not evidence.'
        else:
            source = self.revenue._call('read_editable_file', {'path': path})
            body, scope = source['content'], 'UTF-8 text evidence; at most 12,000 characters are supplied to the model.'
        return {'path': path, 'sha256': source['sha256'], 'content': body[:12000], 'scope': scope,
                'truncated': bool(source.get('truncated') or len(body) > 12000 or suffix == '.xlsx')}


def register_customer_tools(registry, workspace):
    text = {'type': 'string', 'minLength': 1, 'maxLength': 20000}
    customer = {**text, 'maxLength': 160}
    facts = {'type': 'object', 'properties': {key: {'type': 'string', 'maxLength': 20000} for key in FACT_KEYS if key != 'source_urls'}, 'additionalProperties': False}
    facts['properties']['source_urls'] = {'type': 'array', 'maxItems': 5, 'items': {'type': 'string', 'maxLength': 2000}}
    definitions = [
        ('customer_intake', 'Save an actual customer/prospect inquiry and explicit facts; preserve evidence. Budget is not a verified sale.',
         {'customer': customer, 'message': text, 'facts': facts, 'status': {'type': 'string', 'enum': ['prospect', 'customer', 'paused', 'closed']}}, ['customer', 'message'], workspace.intake, True),
        ('customer_details', 'Read a saved customer brief, recommended next step and work history.', {'customer': customer}, ['customer'], workspace.details, False),
        ('customer_list', 'List locally tracked customers and prospects; no inferred sales.', {}, [], workspace.customers, False),
        ('customer_decide', 'Match supplied customer wording to supported services and explain missing requirements and the next local action. No close probability or willingness to pay is invented.', {'customer': customer}, ['customer'], workspace.decide, False),
        ('customer_work', 'Prepare the recommended local reply/proposal or explicitly requested draft deliverable through existing tools. Reuse matching checked work; retry=true explicitly rebuilds prepared/failed work. Never send, publish, collect payment or execute generated code.',
         {'customer': customer, 'action': {'type': 'string', 'enum': ['next', 'reply', 'proposal', 'deliver']}, 'retry': {'type': 'boolean'}}, ['customer'], workspace.work, True),
        ('customer_quote', 'Prepare a numbered local quote PDF for a scoped customer from an explicit owner-supplied positive price and currency. Reuse matching checked quotes. Budget is not consent or an agreed price; no sends or payments.',
         {'customer': customer, 'price': {'type': 'string', 'minLength': 1, 'maxLength': 30, 'pattern': r'^\d+(?:\.\d{1,2})?$'},
          'currency': {'type': 'string', 'enum': sorted(__import__('app.tools.products', fromlist=['CURRENCIES']).CURRENCIES)},
          'description': {'type': 'string', 'minLength': 1, 'maxLength': 2000}, 'retry': {'type': 'boolean'}}, ['customer'], workspace.quote, True),
        ('customer_invoice', 'Prepare or reuse a local invoice PDF from a quote belonging to this customer, on explicit owner request. Read the current ledger status on reuse. Never claim payment, acceptance, or completed customer delivery.',
         {'customer': customer, 'quote_number': {**text, 'maxLength': 60}, 'retry': {'type': 'boolean'}}, ['customer'], workspace.invoice, True),
    ]
    for name, description, properties, required, handler, writes in definitions:
        registry.register(name, description, {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False},
                          'medium' if writes else 'low', 'filesystem_write_project' if writes else 'filesystem_read', handler)
