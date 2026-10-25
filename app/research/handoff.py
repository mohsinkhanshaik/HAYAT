"""User-operated search access, distinct from automated retrieval."""
from urllib.parse import urlencode


def browser_handoff(research, query: str, onion: bool = False, open_browser: bool = False, *, tor_only: bool = False):
    from app.privacy_masks import ROUTE
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError('Supply a search query of 1–500 characters.')
    if type(onion) is not bool or type(open_browser) is not bool or type(tor_only) is not bool:
        raise ValueError('onion, open_browser and tor_only must be booleans.')
    query = query.strip()
    endpoint = 'https://ahmia.fi/search/' if onion else 'https://duckduckgo.com/'
    url = endpoint + '?' + urlencode({'q': query})
    required = onion or tor_only or ROUTE.proxy is not None
    result = {'query': query, 'url': url, 'tor_required': required, 'status': 'ready_to_open',
              'automatic_extraction': False,
              'next_step': 'Read the source in the browser. Complete any publisher sign-in or human check yourself, '
                           'then provide a selected public source URL for HAYAT to research. Opening the browser does not import its contents.',
              'limits': 'Interactive access does not grant automated retrieval access. A proxied Edge/Chrome window is not Tor Browser fingerprint protection.'}
    if open_browser:
        launch = getattr(research, 'search_browser', None)
        if launch is None:
            raise RuntimeError('Interactive search browser is unavailable in this runtime; use the returned search URL.')
        outcome = launch(url, required)
        if not isinstance(outcome, dict) or outcome.get('opened') is not True:
            reason = outcome.get('reason') if isinstance(outcome, dict) else None
            raise RuntimeError(str(reason or 'The search browser did not confirm that it opened.'))
        result['browser'] = outcome
        result['status'] = 'opened'
    return result
