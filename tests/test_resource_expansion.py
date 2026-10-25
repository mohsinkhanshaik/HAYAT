"""Public knowledge discovery stays bounded, source-linked and charge-free."""
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from app.intelligence.feeds import BY_KEY, normalize
from app.intelligence.providers import camera_url
from app.intelligence.resources import RESOURCES, resource_catalog
from app.research.public_data import PublicData, discovery_report
from tests.test_public_data import Fetcher


def literature_item(**values):
    return {'id': '12345', 'source': 'MED', 'title': '<i>Climate</i> health study', 'pubYear': '2027',
            'authorString': 'Researcher A, Researcher B', 'journalTitle': 'Example journal', 'isOpenAccess': 'Y', **values}


def literature_payload(*items, **values):
    return {'hitCount': 12, 'resultList': {'result': list(items)}, 'nextCursorMark': 'next token +/=', **values}


def test_literature_keeps_native_ids_without_doi_and_flags_future_dates():
    fetcher = Fetcher(literature_payload(literature_item()))
    result = PublicData(fetcher).search('climate', 'literature', limit=2)
    assert result['complete_provider_response'] and result['count'] == 1
    record = result['records'][0]
    assert record['record_id'] == 'MED:12345' and record['doi'] == ''
    assert record['title'] == 'Climate health study'
    assert record['landing_url'] == 'https://europepmc.org/article/MED/12345'
    assert record['open_access_reported'] and not record['reported_license_urls']
    assert record['metadata_warnings'] and record['year'] == 2027
    assert record['retrieved_at'] and result['providers'][0]['sha256']
    assert 'Full text' in result['basis'] and result['next_page'] is None
    assert 'later than retrieval' in discovery_report(result)


def test_literature_cursor_is_encoded_at_the_fixed_public_endpoint():
    cursor = 'next token +/='
    fetcher = Fetcher(literature_payload(literature_item(), nextPageUrl='http://127.0.0.1/private'))
    result = PublicData(fetcher).search('climate & health', 'literature', cursor=cursor)
    assert result['next_cursor'] is None  # repeated cursors cannot create an infinite loop
    assert len(fetcher.calls) == 1
    url = urlsplit(fetcher.calls[0])
    assert url.hostname == 'www.ebi.ac.uk' and url.path.endswith('/rest/search')
    assert parse_qs(url.query)['cursorMark'] == [cursor]
    assert parse_qs(url.query)['query'] == ['climate & health']


def test_literature_skips_malformed_and_duplicate_records_without_email_leaks():
    item = literature_item(doi='https://doi.org/10.1234/TEST', authorString='Public Author, secret@example.com')
    fetcher = Fetcher(literature_payload(None, item, item, literature_item(id='../private'), literature_item(source='bad/source')))
    result = PublicData(fetcher).search('climate', 'literature', limit=10)
    assert result['count'] == 1 and result['providers'][0]['skipped_records'] == 4
    assert result['records'][0]['doi'] == '10.1234/test'
    assert result['records'][0]['authors'] == ['Public Author']


@pytest.mark.parametrize('overrides', [{'limit': 0}, {'limit': True}, {'limit': 11}, {'page': 2},
    {'cursor': ''}, {'cursor': 'a\nb'}, {'cursor': 'a' * 2049}, {'query': 'x' * 501}])
def test_literature_rejects_invalid_requests_before_fetch(overrides):
    fetcher = Fetcher()
    with pytest.raises(ValueError):
        PublicData(fetcher).search(**({'query': 'climate', 'kind': 'literature'} | overrides))
    assert not fetcher.calls


def test_literature_provider_errors_are_visible_and_never_become_findings():
    result = PublicData(Fetcher(RuntimeError('publisher unavailable'))).search('climate', 'literature')
    assert not result['complete_provider_response'] and result['metadata_only'] and not result['records']
    assert 'publisher unavailable' in discovery_report(result)


def test_scholarly_search_can_return_europe_pmc_records_without_dois(monkeypatch):
    from app.research.search import EuropePmcSearch, build_search_provider
    value = PublicData(Fetcher(literature_payload(literature_item()))).search('climate health', 'literature')
    queries = []
    def lookup(_self, query, limit, cursor):
        queries.append((query, limit, cursor))
        return value
    monkeypatch.setattr(PublicData, 'literature', lookup)
    provider = EuropePmcSearch()
    results = provider.search('scientific papers climate health', limit=2)
    assert queries == [('climate health', 5, '*')]
    assert results[0].url == 'https://europepmc.org/article/MED/12345'
    assert results[0].provider == 'europe_pmc' and 'not verified' in results[0].snippet
    assert not provider.supports('public datasets about rainfall')
    monkeypatch.delenv('AI_SEARCH_PROVIDER', raising=False)
    assert 'europe_pmc' in [item.name for item in build_search_provider({}).providers]


def test_resources_support_combined_words_categories_access_and_scope():
    result = resource_catalog('life science', category='publications', integration='on_demand')
    assert [row['id'] for row in result['resources']] == ['europe_pmc']
    assert resource_catalog('valencia', region='Europe')['count'] == 1
    assert resource_catalog('valencia', region='North America')['count'] == 0
    assert sum(result['integration_counts'].values()) == len(RESOURCES)
    assert len({row[0] for row in RESOURCES}) == len(RESOURCES)
    assert all(row['trust'] == 'untrusted_external_data' for row in resource_catalog()['resources'])
    assert resource_catalog('zenodo')['resources'][0]['integration'] == 'directory'


@pytest.mark.parametrize('kwargs', [{'query': 'x' * 501}, {'integration': 'invented'}, {'integration': []}, {'category': []}])
def test_resource_filters_reject_invalid_inputs(kwargs):
    with pytest.raises(ValueError):
        resource_catalog(**kwargs)


def valencia_item():
    return {'attributes': {'idcamara': '10209', 'descripcio': 'Pintor Maella',
                           'url': 'https://camaras.valencia.es/camaras/visualizador.html?id=10209'},
            'geometry': {'x': -.348257, 'y': 39.458098}}


def test_valencia_catalog_retains_publisher_links_without_inventing_streams():
    records, rejected = normalize(BY_KEY['cameras_valencia'], {'features': [valencia_item()]})
    assert rejected == 0 and len(records) == 1
    row = records['cameras_valencia:10209']
    assert row['id'] == 'cameras_valencia:10209' and row['latitude'] == 39.458098
    assert row['details']['country'] == 'Spain'
    assert row['details']['page_url'].endswith('?id=10209')
    assert row['details']['image_url'] == row['details']['video_url'] == ''
    assert row['details']['available'] is False
    assert 'not verified' in row['details']['availability_basis']


def test_conflicting_duplicate_camera_ids_are_retained_without_a_guessed_map_position():
    first, second = valencia_item(), valencia_item()
    second['attributes']['descripcio'] = 'Another named location'
    second['geometry']['x'] = -.351
    for items in ([first, second], [second, first]):
        records, rejected = normalize(BY_KEY['cameras_valencia'], {'features': items})
        assert len(records) == 1 and rejected == 0
        camera = records['cameras_valencia:10209']
        assert camera['latitude'] is camera['longitude'] is None
        assert 'conflicting publisher locations' in camera['title']
        assert len(camera['details']['reported_locations']) == 2
        assert 'no map position' in camera['details']['catalog_conflicts']


@pytest.mark.parametrize('url', [
    'https://camaras.valencia.es/admin?id=10209',
    'https://camaras.valencia.es/camaras/visualizador.html?id=10209&redirect=http://localhost',
    'https://camaras.valencia.es/camaras/visualizador.html?id=10209#other',
    'https://camaras.valencia.es.evil.test/camaras/visualizador.html?id=10209',
    'http://camaras.valencia.es/camaras/visualizador.html?id=10209',
    'https://user:pass@camaras.valencia.es/camaras/visualizador.html?id=10209',
])
def test_valencia_viewer_rejects_non_public_or_unregistered_patterns(url):
    assert not camera_url(url, 'cameras_valencia', page=True)
    item = deepcopy(valencia_item())
    item['attributes']['url'] = url
    with pytest.raises(ValueError):
        normalize(BY_KEY['cameras_valencia'], {'features': [item]})
