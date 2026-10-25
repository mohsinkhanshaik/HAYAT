"""Feed refresh when this computer is offline, and when a publisher asks HAYAT to slow down."""
from datetime import UTC, datetime

import httpx

from app.intelligence.manager import network_failure, rate_limited
from tests.test_public_intelligence import manager, make_due  # noqa: F401 (the fixture)


def fail_with(target, errors):
    """Make the fake fetcher raise a given error for some feeds."""
    original = target.fetcher.fetch

    def fetch(url, *args, **kwargs):
        from app.intelligence.feeds import BY_KEY
        key = next(key for key, feed in BY_KEY.items() if feed.url == url)
        target.fetcher.calls.append(key)
        if key in errors:
            raise errors[key]
        return original(url, *args, **kwargs)
    target.fetcher.fetch = fetch


def test_failures_are_told_apart():
    assert network_failure(httpx.ConnectTimeout("timed out")) and network_failure(ValueError("Host could not be resolved: x.org"))
    assert not network_failure(RuntimeError("Source unavailable"))
    assert rate_limited(RuntimeError("Client error '429 Too Many Requests'"), "cyber")
    assert rate_limited(ValueError("GDELT returned no article list."), "gdelt_security") and not rate_limited(ValueError("x"), "cyber")


def test_an_outage_skips_the_rest_and_does_not_count_against_the_feeds(manager):  # noqa: F811
    keys = ['earthquakes', 'natural_events', 'cyber', 'space_weather']
    manager.refresh(keys)
    for key in keys:
        make_due(manager, key)
    down = httpx.ConnectTimeout("timed out")
    fail_with(manager, {key: down for key in keys})
    manager.reachable = lambda: False
    calls = len(manager.fetcher.calls)
    result = manager.refresh(keys)
    assert result['offline'] and len(manager.fetcher.calls) - calls == 3  # the fourth feed was never tried
    assert [row['status'] for row in result['sources']] == ['skipped_offline'] * 4
    states = manager.store.states()
    assert all(states[key].get('failures', 0) == 0 for key in keys[:3])  # not penalised for this computer's outage
    retry = datetime.fromisoformat(states['earthquakes']['next_attempt_at'])
    assert 200 < (retry - datetime.now(UTC)).total_seconds() <= 300


def test_failures_while_online_still_count(manager):  # noqa: F811
    keys = ['earthquakes', 'natural_events', 'cyber']
    fail_with(manager, {key: httpx.ConnectTimeout("timed out") for key in keys})
    manager.reachable = lambda: True  # the internet works: these publishers are the ones down
    result = manager.refresh(keys)
    assert not result['offline'] and result['failed'] == 3
    assert all(manager.store.states()[key]['failures'] == 1 for key in keys)


def test_a_publisher_asking_to_slow_down_waits_hours_then_longer(manager):  # noqa: F811
    fail_with(manager, {'cyber': httpx.HTTPStatusError("Client error '429 Too Many Requests'", request=None, response=None)})
    manager.refresh(['cyber'])
    first = manager.store.states()['cyber']
    assert first['cooldown_seconds'] == 3 * 3600
    make_due(manager, 'cyber')
    manager.refresh(['cyber'])
    second = manager.store.states()['cyber']
    assert second['cooldown_seconds'] == 6 * 3600
    wait = (datetime.fromisoformat(second['next_attempt_at']) - datetime.now(UTC)).total_seconds()
    assert 5.9 * 3600 < wait <= 6 * 3600
