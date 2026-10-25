"""Public metadata is never promoted into findings; observations retain missingness and provenance."""
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from app.research.fetcher import FetchedResource
from app.research.public_data import PublicData, parse_data_request


class Fetcher:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def fetch(self, url, max_bytes, accept):
        self.calls.append(url)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        body = json.dumps(value).encode()
        return FetchedResource(url, url, 200, {'content-type': 'application/json'}, body, 0,
                               '2026-10-17T10:00:00+00:00', hashlib.sha256(body).hexdigest())


def dataset(doi='10.1234/climate', title='Climate observations', **extra):
    return {'id': doi, 'attributes': {'doi': doi, 'titles': [{'title': title}], 'publicationYear': 2024,
            'publisher': {'name': 'Repository'}, 'types': {'resourceTypeGeneral': 'Dataset'}, **extra}}


def test_metadata_dedup_conflicts_and_pagination_do_not_claim_findings():
    fetcher = Fetcher({'data': [dataset(url='http://127.0.0.1/private')], 'meta': {'total': 12}},
                      {'message': {'items': [{'DOI': '10.1234/CLIMATE', 'title': ['Different title'],
                         'publisher': 'Publisher', 'published': {'date-parts': [[2025]]}}], 'total-results': 1}})
    result = PublicData(fetcher).search('climate', limit=1)
    assert result['metadata_only'] and result['next_page'] == 2
    row = result['records'][0]
    assert row['also_listed_by'] == ['crossref']
    assert set(row['metadata_conflicts']) == {'title', 'year', 'publisher'}
    assert row['landing_url'] == row['doi_url']
    assert 'not retrieved or verified' in result['basis']
    assert row['retrieved_at'] and result['providers'][0]['sha256']
    assert all('127.0.0.1' not in url for url in fetcher.calls)
    query = parse_qs(urlsplit(fetcher.calls[0]).query)
    assert query['resource-type-id'] == ['dataset']


def test_provider_failure_and_malformed_records_leave_other_registry_usable():
    fetcher = Fetcher(RuntimeError('unavailable'), {'message': {'items': [None, {'DOI': 'bad'},
                      {'DOI': '10.1234/paper', 'title': ['Paper']}], 'total-results': 3}})
    result = PublicData(fetcher).search('climate')
    assert not result['complete_provider_response'] and result['count'] == 1
    assert result['providers'][0]['status'] == 'error'
    assert result['providers'][1]['skipped_records'] == 2


def test_dataset_filter_rejects_non_dataset_metadata():
    fetcher = Fetcher({'data': [dataset(types={'resourceTypeGeneral': 'Text'})], 'meta': {}})
    result = PublicData(fetcher).search('climate', kind='datasets')
    assert not result['records'] and result['providers'][0]['skipped_records'] == 1


def observation(year, value, country='US', series='SP.POP.TOTL'):
    return {'indicator': {'id': series}, 'country': {'id': country}, 'countryiso3code': 'USA' if country == 'US' else 'CAN',
            'date': str(year), 'value': value}


def test_indicator_keeps_zero_null_conflicts_and_invalid_values_distinct():
    fetcher = Fetcher([{'pages': 1, 'lastupdated': '2026-10-01'}, [
        observation(2020, 0), observation(2021, None), observation(2022, 2), observation(2022, 3),
        observation(2023, True), observation(2024, 50, country='CA'), observation(2024, 1, series='WRONG')]],
        [{}, [{'id': 'SP.POP.TOTL', 'name': 'Population', 'unit': '', 'sourceNote': 'Annual series'}]])
    result = PublicData(fetcher).indicator('USA', 'SP.POP.TOTL', 2020, 2024)
    rows = result['observations']
    assert [row['value'] for row in rows] == [0, None, None, None, None]
    assert [row['status'] for row in rows] == ['observed', 'missing', 'conflicting values', 'invalid value', 'not returned']
    assert result['invalid_rows'] == 3 and result['duplicate_years'] == 1
    assert result['unit'] is None and result['source_updated_at'] == '2026-10-01'
    assert 'date=2020%3A2024' in fetcher.calls[0] and 'source=2' in fetcher.calls[0]


def test_metadata_failure_does_not_discard_observations():
    result = PublicData(Fetcher([{'pages': 2}, [observation(2024, 1)]], RuntimeError('metadata offline'))).indicator(
        'US', 'SP.POP.TOTL', 2024, 2024)
    assert result['observations'][0]['value'] == 1 and result['metadata_error']
    assert result['response_truncated']


@pytest.mark.parametrize('kwargs', [{'country': 'all'}, {'indicator': '../secret'}, {'start_year': True},
                                    {'start_year': 1900, 'end_year': 2100}, {'source': 0}])
def test_invalid_indicator_request_never_fetches(kwargs):
    fetcher = Fetcher()
    with pytest.raises(ValueError):
        PublicData(fetcher).indicator(**({'country': 'USA', 'indicator': 'SP.POP.TOTL'} | kwargs))
    assert not fetcher.calls


def test_explicit_metadata_request_parser_preserves_ordinary_research():
    assert parse_data_request('find public data and papers about climate') == {'query': 'climate', 'kind': 'all'}
    assert parse_data_request('search for datasets on batteries') == {'query': 'batteries', 'kind': 'datasets'}
    assert parse_data_request('find publication metadata about batteries')['kind'] == 'publications'
    assert parse_data_request('find research papers about battery safety') is None


def test_chat_data_search_and_retry_keep_scope_and_single_word_subject(monkeypatch):
    from types import SimpleNamespace
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=False, start_background_services=False)
    original, calls = runtime.research, []
    def research_data(query, kind='all', level='deep'):
        calls.append((query, kind, level))
        return SimpleNamespace(answer='Registry metadata only; contents unverified.', confidence=0,
                               sources=[], claims=[], errors=[], trace={'public_data_scope': True})
    monkeypatch.setattr(runtime, 'research', SimpleNamespace(research_data=research_data, close=lambda: None))
    runtime.research_jobs.inline_seconds = 2
    try:
        outcome = runtime.assistant.handle('find datasets about climate')
        assert 'contents unverified' in outcome.response.display_text
        assert calls[0][:2] == ('climate', 'datasets')
        job = runtime.research_jobs.recent(1)[0]
        job.asked = 'climate'
        assert job.to_dict()['public_data'] == {'kind': 'datasets'}
        runtime.assistant.handle('search again')
        assert calls[-1] == ('climate', 'datasets', 'deep')
    finally:
        original.close()
        runtime.close()
