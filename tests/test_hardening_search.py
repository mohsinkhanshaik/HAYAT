"""Partial results must not hide source failures or leak across concurrent calls."""
from concurrent.futures import ThreadPoolExecutor

from app.research.search import PublicRegistrySearch, SearchProviderChain


def test_partial_registry_failure_is_visible_alongside_usable_results(monkeypatch):
    def lookup(*args):
        return {'records': [{'title': 'Climate observations', 'doi_url': 'https://doi.org/10.1234/climate'}],
                'providers': [{'provider': 'crossref', 'status': 'ok'},
                              {'provider': 'datacite', 'status': 'error', 'error': 'HTTP 503'}]}
    monkeypatch.setattr('app.research.public_data.PublicData.search', lookup)
    chain = SearchProviderChain([PublicRegistrySearch()])
    assert chain.search_wide('scientific paper datasets climate')
    assert chain.last_coverage[0]['usable_results'] == 1
    assert chain.last_errors == ['public_registries: datacite: HTTP 503']
    assert chain.last_coverage[0]['errors'] == chain.last_errors


def test_registry_diagnostics_are_isolated_between_queries_and_workers(monkeypatch):
    def lookup(_self, query, *args):
        return {'records': [{'title': query, 'doi_url': 'https://doi.org/10.1234/sample'}],
                'providers': [{'provider': 'datacite', 'status': 'error' if query == 'climate' else 'ok'}]}
    monkeypatch.setattr('app.research.public_data.PublicData.search', lookup)
    provider = PublicRegistrySearch()
    def read(query):
        assert provider.search('scientific paper ' + query)
        return provider.last_errors
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(read, ['climate', 'oceans']))
    assert results == [['datacite: error'], []]
    assert provider.last_errors == []
    read('climate')
    assert provider.last_errors
    assert provider.search('jobs in London') == [] and not provider.last_errors
