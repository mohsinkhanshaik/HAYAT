"""Evidence integrity, revocable local access, real encryption and image sources."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from app.research.image_sources import exact_sources, inspect_image
from app.research.investigations import build_report, contact_sources, export_report, profile_seed
from app.research.models import ClaimStatus
from app.research.record_vault import RecordVault
from tests.test_public_people_research import research as research


def test_report_keeps_namesakes_apart_and_citations_retrievable(research):
    manager, _ = research
    result = manager.research_person('Jane Example')
    report = build_report(result, 'physics')
    assert len(report['sources']) == 2
    assert len({row['id'] for row in report['sources']}) == 2
    assert not report['identity_verified']
    assert report['claims']
    for claim in report['claims']:
        for evidence in claim['evidence']:
            original = next(source for source in result.sources if source.final_url == evidence['url'])
            assert evidence['excerpt'] in original.text
            assert evidence['retrieved_at']
    assert 'jane.private' not in json.dumps(report)
    assert all(edge['relationship'] == 'publishes source' for edge in report['graph']['edges'])


def test_fabricated_source_hash_or_excerpt_never_reaches_report(research):
    manager, _ = research
    result = manager.research_person('Jane Example')
    for claim in result.claims:
        for evidence in claim.evidence:
            evidence.source_url = 'https://fabricated.example/not-retrieved'
    assert build_report(result)['claims'] == []
    result = manager.research_person('Jane Example')
    for claim in result.claims:
        for evidence in claim.evidence:
            evidence.source_hash = 'wrong'
    assert build_report(result)['claims'] == []


def test_conflicts_are_not_rewritten_as_confirmed_facts(research):
    manager, _ = research
    result = manager.research_person('Jane Example')
    assert result.claims
    result.claims[0].status = ClaimStatus.DISPUTED
    report = build_report(result)
    assert report['claims'][0]['status'] == 'conflicting_evidence'
    assert result.claims[0].status == ClaimStatus.DISPUTED


def test_duplicate_profile_urls_deduplicate_but_hostile_canonical_tags_do_not_merge_namesakes(research):
    manager, _ = research
    result = manager.research_person('Jane Example')
    result.sources[1].canonical_url = result.sources[0].final_url
    assert len(build_report(result)['sources']) == 2
    duplicate = deepcopy(result.sources[0])
    duplicate.final_url += '?utm_source=duplicate'
    result.sources.append(duplicate)
    assert len(build_report(result)['sources']) == 2


def test_github_handle_uses_official_public_api_and_never_exposes_contacts():
    calls = []
    def get(url):
        calls.append(url)
        return {'name': 'Jane Example', 'email': 'private@not-returned.example'}
    assert profile_seed('@jane-example', get_json=get) == ('Jane Example', 'https://github.com/jane-example')
    assert calls == ['https://api.github.com/users/jane-example']
    with pytest.raises(ValueError):
        profile_seed('@bad/../../name', get_json=get)
    with pytest.raises(ValueError, match='known name'):
        profile_seed('https://institute.example/profile', get_json=get)
    with pytest.raises(ValueError, match='credentials'):
        profile_seed('https://password:secret@github.com/jane', get_json=get)
    assert len(calls) == 1


def test_backend_profile_seed_is_resolved_and_local_only_rejects_before_network(research, monkeypatch):
    manager, _ = research
    calls = []
    def get(url):
        calls.append(url)
        if url.startswith('https://api.github.com/users/'):
            return {'login': 'jane', 'name': 'Jane Example', 'public_repos': 2, 'type': 'User'}
        return {'results': []}
    monkeypatch.setattr(manager.fetcher, 'get_json', get, raising=False)
    with pytest.raises(ValueError, match='local-only'):
        manager.research_person('@jane', level='local_knowledge_only')
    with pytest.raises(ValueError, match='outside this search'):
        manager.research_person('@jane', context='home address')
    assert calls == []
    result = manager.research_person('https://github.com/jane')
    assert result.trace['github_profile']['projection']['login'] == 'jane'
    assert result.complete


def test_public_contact_discovery_rejects_unauthorized_or_unscoped_requests_before_network():
    manager = SimpleNamespace(search=SimpleNamespace(search=lambda *_a, **_k: pytest.fail('must not search')))
    with pytest.raises(PermissionError):
        contact_sources(manager, 'name@example.org', 'Example Institute')
    with pytest.raises(ValueError):
        contact_sources(manager, 'name@example.org', 'home address', publicly_listed=True)


def test_public_contact_discovery_only_returns_retrieved_directory_sources(research):
    manager, _ = research
    original_fetch = manager.fetcher.fetch
    def directory(url, max_bytes):
        from dataclasses import replace
        result = original_fetch(url, max_bytes)
        body = result.content.replace(b'personal email jane.private@example.com', b'professional office office@example.org')
        return replace(result, content=body, sha256=hashlib.sha256(body).hexdigest())
    manager.fetcher.fetch = directory
    report = contact_sources(manager, 'office@example.org', 'Example Institute', publicly_listed=True)
    assert len(report['sources']) == 1
    assert report['sources'][0]['url'] == 'https://institute.example/bio'
    assert report['claims'] == [] and not report['identity_verified']
    assert 'office@example.org' not in json.dumps(report)


def test_publisher_labelled_personal_contact_is_not_treated_as_a_business_directory(research):
    manager, _ = research
    report = contact_sources(manager, 'jane.private@example.com', 'Example Institute', publicly_listed=True)
    assert report['sources'] == []


def test_public_github_projection_excludes_private_fields_and_rejects_a_different_display_name():
    from app.research.professional_profiles import github_profile
    from app.research.people import PersonRequest
    fields = {'login': 'jane', 'name': 'Jane Example', 'public_repos': 12, 'type': 'User',
              'email': 'private@not-exported.example', 'location': 'private location'}
    fetcher = SimpleNamespace(get_json=lambda _url: fields)
    source, claim = github_profile(PersonRequest('Jane Example', profile_url='https://github.com/jane'), fetcher)
    assert '12 public repositories' in claim.text
    assert 'private' not in json.dumps(source.to_dict())
    fields['name'] = 'Different Person'
    with pytest.raises(ValueError, match='keep this profile separate'):
        github_profile(PersonRequest('Jane Example', profile_url='https://github.com/jane'), fetcher)


def test_json_and_pdf_exports_contain_real_citations(research, tmp_path):
    manager, _ = research
    report = build_report(manager.research_person('Jane Example'))
    target = tmp_path / 'report.json'
    export_report(report, target)
    assert json.loads(target.read_text(encoding='utf-8')) == report
    pdf = tmp_path / 'report.pdf'
    export_report(report, pdf)
    import fitz
    with fitz.open(pdf) as document:
        text = ''.join(page.get_text() for page in document)
    assert 'Identity unverified' in text
    assert all(source['url'] in text for source in report['sources'])


def test_real_windows_encryption_revocation_expiry_and_deletion(tmp_path, monkeypatch):
    vault = RecordVault(tmp_path)
    with pytest.raises(PermissionError):
        vault.grant('I own this sample record', consent=False)
    token = vault.grant('I own this sample record', consent=True, ttl=60)
    rid = vault.put(token, 'My authorized sample', 'secret record content 82346')
    assert b'secret record' not in (tmp_path / 'records.dpapi').read_bytes()
    assert vault.get(token, rid)['content'] == 'secret record content 82346'
    vault.revoke(token)
    with pytest.raises(PermissionError, match='revoked'):
        vault.get(token, rid)
    next_token = vault.grant('I authorize this sample record again', consent=True, ttl=1)
    with pytest.raises(PermissionError, match='does not cover'):
        vault.get(next_token, rid)
    vault.reauthorize(next_token, rid)
    assert vault.get(next_token, rid)
    import time
    monkeypatch.setattr('app.research.record_vault.time.time', lambda: time.monotonic() + 1e12)
    with pytest.raises(PermissionError, match='expired'):
        vault.get(next_token, rid)
    vault.delete(rid)
    assert not vault.list()
    assert {'record_read', 'authorization_revoked', 'record_deleted'} <= {r['action'] for r in vault.audit()}


def test_vault_rejects_nonowner_and_ciphertext_tampering(tmp_path):
    with pytest.raises(PermissionError):
        RecordVault(tmp_path, principal='untrusted_external_content')
    vault = RecordVault(tmp_path)
    token = vault.grant('This is an owned sample', consent=True)
    vault.put(token, 'sample', 'authorized')
    path = tmp_path / 'records.dpapi'
    original = path.read_bytes()
    path.write_bytes(original[:-10] + b'tampered!!')
    with pytest.raises(OSError):
        vault.list()


def test_image_lookup_verifies_downloaded_bytes_and_omits_gps(tmp_path):
    path = tmp_path / 'image.png'
    Image.new('RGB', (24, 16), 'blue').save(path)
    image_bytes = path.read_bytes()
    calls = []
    def listing(url):
        calls.append(url)
        return {'query': {'allimages': [
            {'url': 'https://upload.wikimedia.org/a/image.png', 'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Image.png'},
            {'url': 'http://127.0.0.1/private', 'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Private.png'}]}}
    fetcher = SimpleNamespace(get_json=listing, fetch=lambda *_a, **_k: SimpleNamespace(
        content=image_bytes, final_url='https://upload.wikimedia.org/a/image.png', retrieved_at='2026-10-23T12:00:00Z'))
    result = exact_sources(path, fetcher)
    assert len(result['matches']) == 1 and result['matches'][0]['match'] == 'verified_exact_bytes'
    assert hashlib.sha1(image_bytes).hexdigest() in calls[0]
    assert result['metadata']['width'] == 24
    assert result['metadata']['authenticity'].startswith('Unknown')
    assert 'GPSInfo' not in result['metadata']['metadata']
    fetcher.fetch = lambda *_a, **_k: SimpleNamespace(content=b'wrong')
    assert exact_sources(path, fetcher)['matches'] == []


def test_image_provider_timeout_is_a_failure_not_a_match(tmp_path):
    path = tmp_path / 'source.png'
    Image.new('RGB', (3, 3)).save(path)
    def fail(_url):
        raise TimeoutError('provider timed out')
    report = exact_sources(path, SimpleNamespace(get_json=fail))
    assert report['matches'] == [] and 'TimeoutError' in report['errors'][0]
    assert report['metadata']['sha256'] == inspect_image(path)['sha256']
    assert inspect_image(path)['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('payload', [None, [], {'error': {'code': 'ratelimited'}}, {'query': {'allimages': 'malformed'}}])
def test_image_api_error_and_malformed_responses_preserve_local_analysis(tmp_path, payload):
    path = tmp_path / 'source.png'
    Image.new('RGB', (5, 7)).save(path)
    report = exact_sources(path, SimpleNamespace(get_json=lambda _url: payload))
    assert report['matches'] == [] and report['errors']
    assert report['metadata']['height'] == 7
