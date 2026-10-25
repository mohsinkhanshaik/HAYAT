"""Opt-in proof that real chat customer decisions produce local AI artifacts."""
import os
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('HAYAT_LIVE') != '1', reason='Opt-in real local model customer chat check')


def test_real_chat_qualifies_drafts_and_builds_a_customer_website(tmp_path):
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=True, start_background_services=False)
    runtime.revenue.output = tmp_path / 'customer-live'
    runtime.revenue.ledger.output = tmp_path / 'billing-live'
    try:
        intake = runtime.assistant.handle('Customer FICTIONAL-LOCAL-TEST Cafe: We need a simple website with a menu section. '
            'Build exactly two short files: index.html with a static placeholder menu and README.md with review instructions. '
            'This is a fictional local test. No real address, price, contact details or testimonials were supplied. Do not publish.\n'
            'Audience: Local cafe visitors\nBudget: Not agreed\nDeadline: Not agreed')
        assert 'Website build' in intake.display_response
        proposal = runtime.assistant.handle('Decide and do the next step')
        assert proposal.complete
        drafted = proposal.outcome.history[-1]['result']['output']
        assert drafted['action'] == 'proposal' and Path(drafted['path']).is_file()
        print('Customer proposal:', drafted['path'], flush=True)
        delivered = runtime.assistant.handle('Please decide and do the next step')
        assert delivered.complete
        output = delivered.outcome.history[-1]['result']['output']
        assert output['prepared'] and not output['sent'] and not output['published']
        with zipfile.ZipFile(output['bundle']) as archive:
            assert archive.testzip() is None
            assert 'index.html' in archive.namelist() and 'README.md' in archive.namelist()
            assert '<' in archive.read('index.html').decode()
        print('Customer delivery bundle:', output['bundle'], flush=True)
        cached = runtime.assistant.handle('Decide and do the next step')
        assert cached.outcome.history[-1]['result']['output']['reused']
        quoted = runtime.assistant.handle('Prepare a quote for this customer for 105 USD')
        assert quoted.complete
        quote = quoted.outcome.history[-1]['result']['output']
        assert quote['document']['total_cents'] == 10500 and not quote['sent']
        print('Customer quote PDF:', quote['path'], flush=True)
        billed = runtime.assistant.handle('Prepare an invoice for this customer from quote ' + quote['number'])
        assert billed.complete
        invoice = billed.outcome.history[-1]['result']['output']
        assert invoice['document']['status'] == 'unpaid' and invoice['document']['paid_cents'] == 0
        assert not invoice['payment_collected'] and not invoice['sent']
        print('Customer unpaid invoice PDF:', invoice['path'], flush=True)
    finally:
        runtime.close()


def test_real_chat_writes_the_requested_newsletter_format(tmp_path):
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=True, start_background_services=False)
    runtime.revenue.output = tmp_path / 'newsletter-live'
    try:
        runtime.assistant.handle('Customer FICTIONAL-NEWSLETTER-TEST: Write a newsletter under 80 words about our scheduling tool. '
            'The only supplied product fact is that it lets visitors book appointments. Include a subject line. '
            'Price, release date and signup URL are not supplied; leave them unconfirmed. This is a fictional local test; do not send.\n'
            'Audience: Independent shop owners')
        reply = runtime.assistant.handle('Do the work for this customer')
        assert reply.complete
        result = reply.outcome.history[-1]['result']['output']
        content = Path(result['path']).read_text(encoding='utf-8')
        assert 'subject' in content.lower() and len(content.split()) <= 110
        for unsupported in ('this week', 'today', 'instantly', 'no complex setup', 'demo', 'on your site'):
            assert unsupported not in content.lower()
        assert result['content_review']
        assert result['kind'] == 'service-draft' and not result['sent'] and not result['published']
        print('Newsletter draft:', result['path'], flush=True)
        print(content, flush=True)
    finally:
        runtime.close()
