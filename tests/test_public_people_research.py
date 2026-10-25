"""Namesake separation, public-work scope, bounded retrieval and no knowledge dossier."""
import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.research.fetcher import FetchedResource
from app.research.jobs import ResearchJob, ResearchJobs, compose_answer
from app.research.manager import ResearchManager
from app.research.models import ResearchBudget, SearchResult
from app.research.people import PersonRequest, blocked_source, parse_person_request


@pytest.fixture
def research(tmp_path):
    class Search:
        def __init__(self):
            self.queries = []

        def search(self, query, limit=10):
            self.queries.append(query)
            return [SearchResult('Jane Example', url, rank=index) for index, url in enumerate((
                'https://institute.example/bio', 'https://gallery.example/artist',
                'https://other.example/colleague', 'https://www.spokeo.com/Jane-Example'))][:limit]

    class Fetcher:
        def __init__(self):
            self.urls = []

        def fetch(self, url, max_bytes):
            self.urls.append(url)
            if 'institute' in url:
                paragraph = ('Jane Example is a professor of physics at Example Institute and published a paper on optics. '
                             'Jane Example has a personal email jane.private@example.com and phone +1 555 555 1234. '
                             'John Other is a professor of history at Another University.')
            elif 'gallery' in url:
                paragraph = 'Jane Example is an artist whose exhibition features paintings and published work on sculpture.'
            else:
                paragraph = 'John Other is a professor and published a paper on biology at Another Institute.'
            body = f'<html><head><title>Public work profile</title></head><body><main><p>{paragraph}</p></main></body></html>'.encode()
            return FetchedResource(url, url, 200, {'content-type': 'text/html'}, body, 1,
                                   datetime.now(UTC).isoformat(), hashlib.sha256(body).hexdigest())

        def close(self):
            pass

    settings = {'database': str(tmp_path / 'knowledge.db'), 'model_synthesis': True,
                'budgets': {'deep': {'searches': 4, 'pages': 8, 'max_seconds': 5, 'max_bytes': 1000000, 'max_workers': 2}}}
    search = Search()
    manager = ResearchManager(settings, model=None, search_provider=search)
    manager.fetcher.close()
    manager.fetcher = Fetcher()
    try:
        yield manager, search
    finally:
        manager.close()


def test_people_results_keep_namesakes_separate_exclude_private_facts_and_do_not_cache_or_remember(research):
    manager, search = research
    result = manager.research_person('Jane Example', 'public work')
    assert result.complete and len(result.sources) == 2
    assert result.trace['person_scope']['identity_verified'] is False
    assert 'may describe different people' in result.answer and 'professor of physics' in result.answer
    assert 'sculpture' in result.answer and 'John Other' not in result.answer
    serialized = json.dumps(result.to_dict(True), default=str)
    assert 'jane.private' not in serialized and '+1 555' not in serialized
    assert all('John Other' not in document.text and not document.links for document in result.sources)
    assert all(document.metadata['scope'] == 'public_work_excerpts_only' for document in result.sources)
    from app.research.citation_validator import CitationValidator
    assert all(item.valid for claim in result.claims for item in CitationValidator().validate_claim(claim, result.sources))
    assert all('spokeo' not in url for url in manager.fetcher.urls)
    assert len(search.queries) == 4 and all('"Jane Example"' in query for query in search.queries)
    with manager.knowledge.connect() as db:
        for table in ('claims', 'source_cache', 'query_cache'):
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    assert compose_answer(result.query, 'research person Jane Example', result) == result.answer


def test_provided_public_profile_can_be_read_even_when_search_has_no_results(research):
    manager, search = research
    search.search = lambda *_args, **_kwargs: []
    result = manager.research_person('Jane Example', profile_url='https://institute.example/bio')
    assert len(result.sources) == 1 and 'professor' in result.answer


def test_person_scope_does_not_call_the_general_model_synthesizer(research, monkeypatch):
    manager, _search = research
    monkeypatch.setattr(manager, '_synthesize', lambda *_: pytest.fail('must not merge people into a model biography'))
    result = manager.research_person('Jane Example')
    assert not result.trace['person_scope']['identity_verified']
    with pytest.raises(ValueError, match='local-only'):
        manager.research_person('Jane Example', level='local_knowledge_only')


def test_people_browser_fallback_cannot_navigate_to_a_private_search_result(research):
    manager, search = research
    search.search = lambda *_args, **_kwargs: [SearchResult('Jane Example', 'http://127.0.0.1/private')]
    manager.fetcher.fetch = lambda *_args: (_ for _ in ()).throw(ValueError('private URL blocked'))
    manager.browser = SimpleNamespace(navigate=lambda *_args: pytest.fail('must not navigate to a private host'))
    result = manager.research_person('Jane Example')
    assert not result.complete and not result.sources and 'No retrievable' in result.answer


@pytest.mark.parametrize('url', ['http://127.0.0.1/profile', 'http://localhost/profile', 'http://[::1]/profile', 'http://camera.local/profile'])
def test_person_profile_is_public_only_before_any_search(url):
    with pytest.raises(ValueError, match='public internet URL'):
        PersonRequest('Jane Example', profile_url=url)


@pytest.mark.parametrize('context', ['personal email', 'home address', 'phone number', 'family members', 'date of birth', 'net worth'])
def test_person_search_rejects_private_dossier_requests_before_search(context):
    with pytest.raises(ValueError, match='outside this search'):
        PersonRequest('Jane Example', context)


def test_broker_hosts_are_blocked_by_domain_boundary_and_profile_credentials_are_rejected():
    assert blocked_source('https://sub.spokeo.com/Jane')
    assert not blocked_source('https://spokeo.com.example.org/publication')
    with pytest.raises(ValueError, match='public professional profile'):
        PersonRequest('Jane Example', profile_url='https://user:password@example.org/profile')
    plan = PersonRequest('Jane Example', 'Example Institute').plan(ResearchBudget(searches=2))
    assert len(plan) == 2 and all('Example Institute' in item.text for item in plan)


def test_explicit_chat_name_requests_and_job_retry_scope():
    from app.research.subject import requested_level
    assert requested_level('search the public background in deep') == 'deep'
    request = parse_person_request('research person Jane Example at Example Institute in depth')
    assert request.name == 'Jane Example' and request.context == 'Example Institute'
    assert parse_person_request('find the man in this photo') is None
    calls = []
    manager = SimpleNamespace(research_person=lambda **kwargs: calls.append(kwargs) or
                              SimpleNamespace(answer='Separate source candidates', confidence=.5, sources=[],
                                              trace={'person_scope': {'identity_verified': False}}))
    jobs = ResearchJobs(lambda: manager, inline_seconds=5)
    options = {'name': request.name, 'context': request.context, 'profile_url': ''}
    job = jobs.start('ignored generic query', person=options, level='deep')
    assert job._done.wait(3) and job.status == 'done'
    options['name'] = 'Different name'
    assert calls == [{'name': 'Jane Example', 'context': 'Example Institute', 'profile_url': '', 'level': 'deep'}]
    assert job.person['name'] == 'Jane Example'
    assert json.loads(json.dumps(job.to_dict()))['person']['name'] == 'Jane Example'
    assert ResearchJob('id', 'query', 'query', 'deep', '').to_dict()['status'] == 'running'


def test_chat_research_and_search_again_keep_the_public_person_workflow(monkeypatch):
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=False, start_background_services=False)
    original = runtime.research
    calls = []
    def research_person(name, context='', profile_url='', level='deep'):
        calls.append((name, context, profile_url, level))
        return SimpleNamespace(answer='Separate public-work source candidates; identity unverified.', confidence=.5,
                               sources=[], claims=[], errors=[], trace={'person_scope': {'identity_verified': False}})
    manager = SimpleNamespace(research_person=research_person, close=lambda: None)
    monkeypatch.setattr(runtime, 'research', manager)
    runtime.research_jobs.inline_seconds = 2
    try:
        outcome = runtime.assistant.handle('research person Jane Example at Example Institute in depth')
        assert 'identity unverified' in outcome.response.display_text
        assert calls == [('Jane Example', 'Example Institute', '', 'deep')]
        runtime.research_jobs.recent(1)[0].asked = 'Jane Example'  # same draft a GUI people search uses
        runtime.assistant.handle('search again')
        assert calls == [('Jane Example', 'Example Institute', '', 'deep')] * 2
    finally:
        original.close()
        runtime.close()
