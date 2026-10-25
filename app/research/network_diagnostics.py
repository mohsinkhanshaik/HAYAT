"""Describe observed retrieval failures without guessing the owner's network state."""
from __future__ import annotations

import errno

import httpx


def connection_diagnostic(error: httpx.RequestError, *, via_tor: bool) -> str:
    """Keep the request route, not the route selected after a failure, in diagnostics."""
    causes, seen, current = [], set(), error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        causes.append(current)
        current = current.__cause__ or current.__context__
    text = ' '.join(str(item).casefold() for item in causes)
    refused = any(getattr(item, 'errno', None) == errno.ECONNREFUSED
                  or getattr(item, 'winerror', None) == 10061 for item in causes)
    if refused or any(word in text for word in ('connection refused', 'winerror 10061', 'actively refused')):
        kind = 'connection_refused'
        observed = 'The connection was refused.'
    elif isinstance(error, httpx.TimeoutException):
        kind, observed = 'network_timeout', 'The network request timed out.'
    elif any(word in text for word in ('certificate_verify_failed', 'certificate verify failed')):
        kind, observed = 'tls_verification_failed', 'The secure connection could not be verified.'
    elif any(word in text for word in ('getaddrinfo failed', 'name or service not known', 'nodename nor servname', 'name resolution')):
        kind, observed = 'dns_failure', 'The destination name could not be resolved.'
    else:
        kind, observed = 'network_connection_failed', 'The network connection could not be established.'
    route = 'tor' if via_tor else 'direct'
    action = ('Check HAYAT\'s Tor connection, local SOCKS listener and bootstrap status. '
              'No direct-network fallback was used.' if via_tor else
              'Check the internet connection and firewall, then retry. Tor was not selected for this request.')
    if kind == 'tls_verification_failed':
        action = 'Check the system clock and trusted certificates; certificate verification remains enabled. ' + action
    return f'[{kind}; route={route}] {observed} {action}'


def failed_search_guidance(error: str, *, tor_required: bool = False) -> str:
    """Explain failed access separately from an actual search with no matching evidence.

    Legacy raw errors are supported for saved research jobs. They do not reveal
    which network route was used, so they must not be called a broken Tor proxy.
    """
    text = error.casefold()
    if any(marker in text for marker in ('[connection_refused;', 'winerror 10061', 'actively refused', 'connection refused')):
        observed = 'A network connection was refused; this does not establish whether matching information exists.'
    elif '[network_timeout;' in text or 'connecttimeout:' in text or 'readtimeout:' in text:
        observed = 'The network request timed out before usable search evidence was retrieved.'
    elif '[dns_failure;' in text:
        observed = 'A destination name could not be resolved; no conclusion about matching information is available.'
    elif '[tls_verification_failed;' in text:
        observed = 'A secure connection could not be verified. Check the system clock and trusted certificates.'
    elif '[network_connection_failed;' in text:
        observed = 'A network connection failed before usable search evidence was retrieved.'
    else:
        return ''
    if tor_required or 'route=tor]' in text:
        return (observed + ' Check HAYAT\'s Tor connection, local SOCKS listener and bootstrap status, '
                'then retry the same search. No direct-network fallback was used.')
    return observed + ' Check the internet connection and firewall, then retry the same search.'
