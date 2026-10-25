"""Automatic public-feed collection with honest outages and bounded local state."""
from datetime import UTC, datetime, timedelta
from collections import Counter
import hashlib
import json
import logging
import threading
import uuid

from app.intelligence.classify import CATEGORIES, PRIMARY_ISSUERS, SEVERITY_LABEL, SEVERITY_RANK, assess, recommended_actions
from app.intelligence.feeds import FEEDS, ALL_FEEDS, BY_KEY, configured, finite, normalize, redact, resolved_url, timestamp
from app.intelligence.providers import CAMERA_COVERAGE
from app.intelligence.store import IntelligenceStore
from app.intelligence.services import ExplorationServices
from app.logging import log_swallowed


def network_failure(exc):
    """A failure of this computer's connection (no route, no DNS), not of the publisher."""
    import httpx
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return True
    text = str(exc)
    return 'could not be resolved' in text or 'getaddrinfo failed' in text or 'Name or service not known' in text


def internet_reachable():
    """Whether names resolve at all: two well-known hosts, so one publisher's outage never looks like ours."""
    import socket
    for host in ('www.google.com', 'cloudflare.com', 'www.microsoft.com'):
        try:
            socket.getaddrinfo(host, 443)
            return True
        except OSError:
            continue
    return False


def rate_limited(exc, feed_key):
    """The publisher asked HAYAT to slow down (429, or GDELT's own "no article list" when it throttles)."""
    text = str(exc)
    return '429' in text or 'Too Many Requests' in text or (feed_key.startswith('gdelt') and 'no article list' in text)

_log = logging.getLogger(__name__)


def now():
    return datetime.now(UTC)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_watch(data):
    if not isinstance(data, dict):
        raise ValueError('Watch must be an object.')
    allowed = {'title', 'feeds', 'keywords', 'bbox', 'min_magnitude', 'enabled'}
    if set(data) - allowed:
        raise ValueError('Unsupported watch fields.')
    result = dict(data)
    title = result.get('title')
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        raise ValueError('Supply a watch title of 1–300 characters.')
    feeds = result.get('feeds', [])
    if not isinstance(feeds, list) or any(not isinstance(feed, str) or feed not in BY_KEY for feed in feeds):
        raise ValueError('Select registered public feeds.')
    words = result.get('keywords', [])
    if not isinstance(words, list) or len(words) > 20 or any(not isinstance(word, str) or not word.strip() or len(word) > 120 for word in words):
        raise ValueError('Use up to 20 nonempty keyword phrases.')
    bbox = result.get('bbox')
    if bbox is not None:
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError('bbox must be [west, south, east, north].')
        values = [finite(value) for value in bbox]
        if any(value is None for value in values):
            raise ValueError('Watch bounds must be finite numbers.')
        west, south, east, north = values
        if not -180 <= west <= 180 or not -180 <= east <= 180 or not -90 <= south <= north <= 90:
            raise ValueError('Watch bounds are outside world coordinates.')
        result['bbox'] = values
    magnitude = result.get('min_magnitude')
    if magnitude is not None:
        magnitude = finite(magnitude)
        if magnitude is None or not 0 <= magnitude <= 10:
            raise ValueError('Earthquake magnitude threshold must be between 0 and 10.')
        if feeds != ['earthquakes']:
            raise ValueError('Magnitude thresholds require only the earthquakes feed; magnitudes across feeds use different units.')
        result['min_magnitude'] = magnitude
    if type(result.get('enabled', True)) is not bool:
        raise ValueError('enabled must be true or false.')
    if not feeds and not words and bbox is None:
        raise ValueError('Choose a feed, region or keyword to bound the watch.')
    result.update({'enabled': result.get('enabled', True), 'feeds': feeds, 'keywords': words})
    return result


def matches(row, watch):
    if not watch.get('enabled', True) or watch.get('feeds') and row['feed'] not in watch['feeds']:
        return False
    if watch.get('keywords') and not any(word.lower() in json.dumps(row, ensure_ascii=False).lower() for word in watch['keywords']):
        return False
    if watch.get('bbox') is not None:
        lat, lon = row.get('latitude'), row.get('longitude')
        if lat is None or lon is None:
            return False
        west, south, east, north = watch['bbox']
        longitude_match = west <= lon <= east if west <= east else lon >= west or lon <= east
        if not longitude_match or not south <= lat <= north:
            return False
    if watch.get('min_magnitude') is not None:
        mag = row.get('details', {}).get('magnitude')
        if row['feed'] != 'earthquakes' or mag is None or mag < watch['min_magnitude']:
            return False
    return True


def xml_records(content):
    """Flat records from a simple XML list such as <image-list><image><key>…</key></image>.

    DOCTYPE declarations are refused outright, so entity-expansion tricks in a
    provider response cannot reach the parser.
    """
    from app.security import xml as ElementTree
    root = ElementTree.fromstring(content)
    return [{child.tag: (child.text or '').strip() for child in record} for record in root][:10001]


def hours_apart(left, right):
    first, second = timestamp(left), timestamp(right)
    if not first or not second:
        return float('inf')
    return abs((datetime.fromisoformat(first) - datetime.fromisoformat(second)).total_seconds()) / 3600


def camera_actions(row, cameras):
    """Point the owner at the closest public camera, in whatever form its publisher offers."""
    if row['category'] == 'cameras' or not cameras:
        return []
    camera = cameras[0]
    medium = 'snapshot' if camera.get('image_url') else 'live video' if camera.get('video_url') else 'publisher player'
    return [{'text': f"View the nearest public camera ({medium}): {camera['title'][:90]} · {camera['distance_km']} km",
             'action': 'camera:' + camera['id']}]


def event_assessment(row, in_feed, source, matching_watches, watches, nearby, corroborating, cameras=()):
    """Deterministic summary, risk, relevance, confidence and next steps for one event."""
    from app.intelligence.classify import space_weather
    from app.intelligence.view import format_point, relative_time
    details, key = row.get('details') or {}, row['category']
    when = relative_time(row.get('observed_at'))
    when_text = f' ({when})' if when else ''
    if key == 'earthquakes':
        magnitude, depth = details.get('magnitude'), details.get('depth_km')
        summary = (f"A magnitude {magnitude:.1f} earthquake" if magnitude is not None else 'An earthquake') + f" was reported {row['title']}"
        summary += (f' at a depth of {depth:.0f} km' if depth is not None else '') + f'{when_text}, according to USGS.'
        alert = details.get('alert')
        summary += f' USGS PAGER impact alert: {alert}.' if alert else ' USGS has issued no PAGER impact alert.'
        if details.get('tsunami_flag') == 1:
            summary += ' USGS flags it as a large oceanic event; this flag is not a tsunami warning.'
    elif key == 'cyber':
        summary = (f"{row['subtitle'] or row['id']}: {row['title']}. CISA added it to the Known Exploited Vulnerabilities "
                   f"catalog{when_text}. " + str(details.get('description') or '')[:400]).strip()
    elif key == 'space_weather':
        value = space_weather(details.get('message') or row.get('title'))
        summary = f"NOAA's Space Weather Prediction Center issued: {row['headline']}{when_text}."
        if value['scale']:
            summary += f" NOAA scale {value['scale']}" + (f" ({value['scale_word']})." if value['scale_word'] else '.')
    elif key == 'cameras':
        summary = f"{row['title']} is a public {row['provider']} camera catalog entry. Availability is publisher-reported."
    else:
        summary = f"{row['headline']}" + (f" — {row['subtitle']}" if row['subtitle'] else '') + f". Reported by {row['provider']}{when_text}."
    if row.get('latitude') is not None:
        summary += f" Location {format_point(row['latitude'], row['longitude'])}."
    others = len(nearby)
    if others:
        summary += f" {others} other observation{'s lie' if others != 1 else ' lies'} within 300 km"
        summary += f"; {len(corroborating)} from another source describe the same kind of event." if corroborating else '.'
    summary += ' It is in the current feed.' if in_feed else ' It is retained but no longer in the current feed; that does not prove it has ended.'
    risk = {'level': row['severity'], 'label': SEVERITY_LABEL[row['severity']], 'basis': row['severity_basis']}
    related_watches = [watch for watch in watches if watch.get('enabled', True) and row['feed'] in watch.get('feeds', [])
                       and watch not in matching_watches]
    if matching_watches:
        relevance = {'level': 'high', 'label': 'High', 'basis': 'Matches your watch: ' + ', '.join(watch['title'] for watch in matching_watches[:3]) + '.'}
    elif related_watches:
        relevance = {'level': 'medium', 'label': 'Medium', 'basis': f"Same layer as your watch '{related_watches[0]['title']}', but outside its area, keywords or threshold."}
    else:
        relevance = {'level': 'low', 'label': 'Low', 'basis': 'No saved watch covers this. Add one to be alerted about similar events.'}
    if source.get('stale'):
        confidence = {'level': 'low', 'label': 'Low', 'basis': 'The source feed is stale; this is the last retained observation.'}
    elif not in_feed:
        confidence = {'level': 'low', 'label': 'Low', 'basis': 'Retained historical item, absent from the current feed.'}
    elif corroborating:
        names = sorted({item['feed'] for item in corroborating})
        confidence = {'level': 'high', 'label': 'High', 'basis': f"{row['provider']} report corroborated by {len(names)} other source{'s' if len(names) != 1 else ''}: {', '.join(names)}."}
    elif row['feed'] in PRIMARY_ISSUERS:
        confidence = {'level': 'high', 'label': 'High', 'basis': f"Issued directly by the authoritative agency ({row['provider']}) in a current fetch."}
    else:
        confidence = {'level': 'medium', 'label': 'Medium', 'basis': f"Single current source ({row['provider']}); not corroborated by another connected feed."}
    return {'summary': summary, 'risk': risk, 'relevance': relevance, 'confidence': confidence,
            'actions': camera_actions(row, cameras) + recommended_actions(row),
            'context': [f"Provider: {row['provider']} · layer {row['feed']} · category {row['category_title']}",
                        f"Event time: {row.get('observed_at') or 'unknown'}" + (f' ({when})' if when else ''),
                        f"Last fetched: {row.get('retrieved_at') or 'unknown'}",
                        f"Matching watches: {', '.join(watch['title'] for watch in matching_watches) or 'none'}",
                        'Source: ' + row['url']]}


def happening_first(rows):
    """Newest reports first, then scheduled items dated in the future (soonest first), then undated ones.

    Sorting by date alone put 1,212 road works planned for 2027 above every
    earthquake, storm and alert of the day.
    """
    moment = now().isoformat()
    dated = [row for row in rows if row.get('observed_at')]
    present = sorted((row for row in dated if row['observed_at'] <= moment), key=lambda row: row['observed_at'], reverse=True)
    future = sorted((row for row in dated if row['observed_at'] > moment), key=lambda row: row['observed_at'])
    return present + future + [row for row in rows if not row.get('observed_at')]


def live_selection(live, limit, per_category=40):
    """The newest rows, plus each category's own newest, so a category chip is never empty by accident."""
    chosen = {row['id'] for row in live[:limit]}
    taken = Counter(row['category'] for row in live[:limit])
    for row in live[limit:]:
        if taken[row['category']] < per_category:
            chosen.add(row['id'])
            taken[row['category']] += 1
    return [row for row in live if row['id'] in chosen]


def spotlight(live, hours=24):
    """The event to open with: the most severe one reported in the last day, else the newest."""
    cutoff = (now() - timedelta(hours=hours)).isoformat()
    moment = now().isoformat()
    recent = [row for row in live if cutoff <= (row.get('observed_at') or '') <= moment]
    if recent:
        return max(recent, key=lambda row: (row['severity_rank'], row['observed_at']))['id']
    return live[0]['id'] if live else None


class IntelligenceManager(ExplorationServices):
    def __init__(self, path, fetcher, registry=None, events=None, *, enabled=True, interval=60, extended=False, knowledge=None):
        self.store = IntelligenceStore(path)
        self.fetcher, self.registry, self.bus = fetcher, registry, events
        #: Whether this computer can resolve names at all; consulted after three connection failures in a row.
        self.reachable = internet_reachable
        self.enabled = bool(enabled)
        self.interval = max(30, min(int(interval), 3600))
        self._lock = threading.Lock()
        self._lifecycle = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self.last_error = None
        # Keyed networks join the schedule only once the owner adds their key to .env.
        self.feeds = tuple(feed for feed in (ALL_FEEDS if extended else FEEDS) if configured(feed))
        self.knowledge = knowledge
        self._map_server = None
        self._map_lock = threading.Lock()
        self._camera_lock = threading.Lock()
        # Parsed, classified rows per feed, keyed by snapshot id; displays
        # re-filter often and must not re-read thousands of JSON rows each time.
        self._row_cache = {}
        self._cache_lock = threading.Lock()
        # Local conversation model for optional "Analyze with HAYAT"; set by runtime.
        self.model = None
        self.store.seed_watches({
            'earthquake-5plus': validate_watch({'title': 'Earthquakes M5+', 'feeds': ['earthquakes'], 'min_magnitude': 5}),
            'new-exploited-vulnerabilities': validate_watch({'title': 'New or revised CISA catalog items', 'feeds': ['cyber']}),
        })

    def refresh(self, feeds=None, due_only=False):
        selected = [feed.key for feed in self.feeds] if feeds is None else feeds
        if not isinstance(selected, list) or not selected or len(selected) > len(BY_KEY) or any(not isinstance(key, str) or key not in BY_KEY for key in selected) or len(set(selected)) != len(selected):
            raise ValueError('Choose unique registered feed keys.')
        if not self._lock.acquire(blocking=False):
            return {'intelligence': True, 'refresh_in_progress': True, 'sources': [], 'updated': 0}
        results = []
        offline, network_failed = False, []
        try:
            states = self.store.states()
            for key in selected:
                if due_only and self._stop.is_set():
                    break
                if offline:
                    # This computer is offline: the feed is not tried, and not counted against it.
                    results.append({'feed': key, 'status': 'skipped_offline', 'cached': True})
                    continue
                feed, previous = BY_KEY[key], states.get(key, {})
                moment = now()
                due_at = timestamp(previous.get('next_attempt_at'))
                if feed.format == 'static' and self._static_catalog_changed(feed):
                    due_at = None
                # Manual refreshes also respect source cooldowns.
                if due_at and moment < datetime.fromisoformat(due_at):
                    results.append({'feed': key, 'cached': True, 'next_attempt_at': due_at})
                    continue
                started = moment.isoformat()
                try:
                    content_types = {'application/json', 'application/*+json', 'text/json'}
                    # EONET's registered endpoint serves JSON with an RSS MIME
                    # header. Permit that header only here; JSON/schema checks
                    # below still reject an actual XML response.
                    if key == 'natural_events':
                        content_types.add('application/rss+xml')
                    if feed.format in ('xml', 'datex', 'faa', 'tarktee'):
                        content_types = {'application/xml', 'text/xml'}
                    if feed.format == 'rss':
                        content_types = {'application/rss+xml', 'application/xml', 'text/xml'}
                    if feed.format == 'atom':
                        content_types = {'application/atom+xml', 'application/xml', 'text/xml'}
                    if feed.format == 'kml':
                        content_types = {'application/vnd.google-earth.kml+xml', 'application/xml', 'text/xml'}
                    # TripCheck publishes its camera inventory as a .js file holding plain JSON.
                    if key == 'cameras_oregon':
                        content_types.add('application/javascript')
                    # USGS AshCam labels its JSON as text/html; the JSON parse still rejects a real page.
                    if key == 'cameras_volcanoes':
                        content_types.add('text/html')
                    # Toronto's open-data portal serves its GeoJSON download as octet-stream;
                    # the JSON parse and schema checks below still reject anything else.
                    if key == 'cameras_toronto':
                        content_types.add('application/octet-stream')
                    if not configured(feed):
                        results.append({'feed': key, 'status': 'needs_key', 'error': f'Add the free key for this network in Studios → Keys ({feed.key_env}) to connect it.'})
                        continue
                    resource, payload = self._fetch_feed(feed, content_types)
                    rows, rejected = normalize(feed, payload)
                    before = self.store.latest(key)
                    old = before['events'] if before else {}
                    added = sorted(set(rows) - set(old))
                    changed = sorted(identifier for identifier in set(rows) & set(old) if rows[identifier] != old[identifier])
                    missing = sorted(set(old) - set(rows))
                    # Same feed content need not consume another retained snapshot.
                    if not before or fingerprint(rows) != fingerprint(old):
                        self.store.save(key, resource.retrieved_at, rows)
                    alerts = self._alerts([rows[identifier] for identifier in added + changed]) if before else 0
                    state = {'feed': key, 'status': 'ok', 'last_attempt_at': started,
                             'last_success_at': resource.retrieved_at, 'source_url': redact(resource.final_url, feed),
                             'sha256': resource.sha256, 'count': len(rows), 'rejected': rejected,
                             'failures': 0, 'error': None,
                             'next_attempt_at': (now() + timedelta(seconds=feed.interval)).isoformat(),
                             'change': {'added': added[:100], 'changed': changed[:100], 'no_longer_in_feed': missing[:100],
                                        'added_count': len(added), 'changed_count': len(changed), 'missing_count': len(missing),
                                        'baseline': before is None, 'alerts_created': alerts}}
                    self.store.state(key, state)
                    self.store.record_health(key, True, len(rows))
                    self._cache_knowledge(feed, rows, resource)
                    results.append(state)
                except Exception as exc:
                    failures = previous.get('failures', 0) + 1
                    wait = min(3600, 60 * 2 ** min(failures, 6))
                    if rate_limited(exc, key):
                        # Asked to slow down: hours, not minutes, doubling to a day.
                        wait = min(86400, max(3 * 3600, 2 * int(previous.get('cooldown_seconds') or 0)))
                    state = {**previous, 'feed': key, 'status': 'failed', 'last_attempt_at': started,
                             'failures': failures, 'error': redact(f'{type(exc).__name__}: {exc}', feed)[:1000],
                             'cooldown_seconds': wait if rate_limited(exc, key) else 0,
                             'next_attempt_at': (now() + timedelta(seconds=wait)).isoformat()}
                    self.store.state(key, state)
                    self.store.record_health(key, False, 0, state['error'])
                    results.append(state)
                    network_failed = network_failed + [(key, previous)] if network_failure(exc) else []
                    if len(network_failed) >= 3 and not self.reachable():
                        # Three connection failures in a row and no name resolves: the outage is ours. Those feeds are
                        # not penalised, and the rest wait for the next round instead of each timing out in turn.
                        offline = True
                        retry = (now() + timedelta(seconds=300)).isoformat()
                        for failed_key, before in network_failed:
                            self.store.state(failed_key, {**before, 'feed': failed_key, 'status': 'skipped_offline', 'next_attempt_at': retry})
                        for row in results:
                            if row.get('feed') in {failed for failed, _before in network_failed}:
                                row.update(status='skipped_offline')
            brief_path = self._write_brief()
            return {'intelligence': True, 'sources': results, 'brief_path': str(brief_path), 'updated': sum(row.get('status') == 'ok' for row in results),
                    'failed': sum(row.get('status') == 'failed' for row in results), 'offline': offline,
                    'basis': 'Direct public-source observations. Missing events may have aged out of a feed; this does not establish resolution.'}
        finally:
            self._lock.release()

    def _static_catalog_changed(self, feed):
        """A local catalog update needs no network cooldown or process interruption."""
        from app.intelligence.live import static_records
        from app.intelligence.providers import normalize_extended
        records = static_records(feed.key)
        if records is None:
            return False
        snapshot = self.store.latest(feed.key)
        if snapshot is None:
            return True
        expected, _rejected = normalize_extended(feed, records)
        return snapshot['events'] != expected

    def _fetch_feed(self, feed, content_types):
        """One catalog response, or every page of a paged ArcGIS layer merged into one."""
        url = resolved_url(feed)
        if feed.format == 'static':
            from types import SimpleNamespace
            from app.intelligence.live import static_records
            records = static_records(feed.key) or []
            content = json.dumps(records).encode()
            return SimpleNamespace(content=content, retrieved_at=now().isoformat(), final_url=feed.url,
                                   sha256=hashlib.sha256(content).hexdigest()), records
        if feed.format == 'tarktee':
            # Two DATEX II documents: where each camera is, and its latest image.
            from app.intelligence.providers import tarktee_cameras
            locations = self.fetcher.fetch(url, feed.max_bytes, content_types, timeout=feed.timeout)
            images = self.fetcher.fetch(url.replace('roadCameraLocations', 'roadCameraImages'), feed.max_bytes, content_types,
                                        timeout=feed.timeout)
            return images, tarktee_cameras(locations.content, images.content)
        if not feed.pages:
            resource = (self.fetcher.fetch(url, feed.max_bytes, content_types, timeout=feed.timeout) if feed.timeout
                        else self.fetcher.fetch(url, feed.max_bytes, content_types))
            if feed.format == 'rss':
                from app.intelligence.reports import parse_rss
                return resource, parse_rss(resource.content)
            if feed.format == 'atom':
                from app.intelligence.hazards import parse_atom
                return resource, parse_atom(resource.content)
            if feed.format in ('hans', 'ukflood'):
                # Each record is placed by a second, cached lookup: volcanoes and flood areas do not move.
                from app.intelligence.hazards import place_floods, place_volcanoes
                def get_json(address):
                    return json.loads(self.fetcher.fetch(address, 1024 * 1024, content_types).content)
                place, cache = ((place_volcanoes, 'volcano-places.json') if feed.format == 'hans'
                                else (place_floods, 'flood-areas.json'))
                return resource, place(json.loads(resource.content), get_json, self.store.path.parent / cache)
            if feed.format == 'faa':
                from app.intelligence import airports
                from app.intelligence.reports import faa_records
                airports.ensure(self.fetcher, self.store.path.parent)
                return resource, faa_records(resource.content, airports.table(self.store.path.parent))
            if feed.format in ('datex', 'kml'):
                from app.intelligence.providers import datex_cameras, kml_cameras
                return resource, (datex_cameras if feed.format == 'datex' else kml_cameras)(resource.content)
            return resource, xml_records(resource.content) if feed.format == 'xml' else json.loads(resource.content)
        features, offset, resource = [], 0, None
        for _page in range(feed.pages):
            resource = self.fetcher.fetch(f'{url}&resultOffset={offset}', 8 * 1024 * 1024, content_types)
            page = json.loads(resource.content)
            if not isinstance(page, dict) or not isinstance(page.get('features'), list):
                raise ValueError('ArcGIS layer returned no features array.')
            features.extend(page['features'])
            offset += len(page['features'])
            if not page.get('exceededTransferLimit') or not page['features']:
                break
        return resource, {'features': features}

    def _write_brief(self):
        """One current local report, atomically replaced; no growing artifact archive."""
        value = self.brief()
        lines = ['# Hayat global intelligence', '', 'Updated: ' + now().isoformat(), '', value['headline'], '', value['basis'], '']
        for source in value['sources']:
            lines.extend([f"## {source['title']}", '',
                f"State: {source['status']}; stale: {source['stale']}; last successful fetch: {source.get('last_success_at') or 'none'}",
                f"Source: {source['url']}", f"SHA-256: {source.get('sha256') or 'unknown'}", ''])
            if source.get('error'):
                lines.extend(['Fetch issue: ' + source['error'], ''])
        lines.extend(['## Recent source observations', ''])
        for row in value['events']:
            lines.extend([row['title'], 'Event time: ' + (row.get('observed_at') or 'unknown'),
                          row['url'], 'Source stale: ' + str(row['stale']), ''])
        lines.extend(['## Recent local watch alerts', ''])
        for alert in value['alerts']:
            lines.extend([alert['watch_title'] + ': ' + alert['event']['title'], alert['event']['url'], ''])
        destination = self.store.path.parent / 'intelligence' / 'latest-brief.md'
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix('.md.partial')
        partial.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        partial.replace(destination)
        return destination

    def _alerts(self, rows):
        count = 0
        watches = self.store.watches()
        for row in rows:
            for watch in watches:
                if matches(row, watch):
                    identifier = fingerprint({'watch': watch, 'event': row})
                    created = now().isoformat()
                    value = {'watch_key': watch['key'], 'watch_title': watch['title'], 'event': row,
                             'basis': 'A new or changed source item matches a local watch rule; no causal or predictive claim.'}
                    if self.store.add_alert(identifier, created, value):
                        count += 1
                        if self.bus:
                            self.bus.publish('INTELLIGENCE_ALERT', 'intelligence', {'alert_id': identifier, 'watch': watch['title'],
                                             'event_id': row['id'], 'title': row['title'], 'url': row['url']})
        return count

    def _cached_rows(self, key, snapshot_id):
        """Classified rows plus a lowercase search text, re-read only when a new snapshot lands."""
        with self._cache_lock:
            cached = self._row_cache.get(key)
            if cached and cached[0] == snapshot_id:
                return cached[1]
        snapshot = self.store.latest(key)
        rows = []
        for row in (snapshot or {}).get('events', {}).values():
            value = {**row, **assess(row)}
            search = ' '.join((json.dumps(row, ensure_ascii=False), value['headline'], value['subtitle'],
                               value['category_title'], value['badge'])).casefold()
            rows.append((value, search))
        with self._cache_lock:
            self._row_cache[key] = (snapshot['id'] if snapshot else None, rows)
        return rows

    def _matching(self, query='', feed='', bbox=None, located_only=False, category='', min_severity='', since_hours=None):
        if feed and feed not in BY_KEY:
            raise ValueError('Unknown feed filter.')
        if not isinstance(query, str) or len(query) > 500:
            raise ValueError('Use a query up to 500 characters.')
        if type(located_only) is not bool:
            raise ValueError('located_only must be true or false.')
        if category and category not in CATEGORIES:
            raise ValueError('Choose a registered event category.')
        if min_severity and min_severity not in SEVERITY_RANK:
            raise ValueError('Choose high, medium, low or info as the minimum severity.')
        if since_hours is not None and (finite(since_hours) is None or not 0 < finite(since_hours) <= 24 * 366):
            raise ValueError('since_hours must be between 0 and 8784.')
        region = validate_watch({'title': 'Query region', 'bbox': bbox}) if bbox is not None else None
        minimum = SEVERITY_RANK[min_severity] if min_severity else 0
        cutoff = (now() - timedelta(hours=finite(since_hours))).isoformat() if since_hours is not None else ''
        needle = query.strip().casefold()
        country_filter = 'india' if needle in ('india', 'indian', 'bharat') else ''
        sources, ids, all_rows = self._sources(), self.store.latest_ids(), []
        for source in sources:
            if feed and feed != source['key'] or source['key'] not in ids:
                continue
            success, stale = timestamp(source.get('last_success_at')), source['stale']
            for row, search in self._cached_rows(source['key'], ids[source['key']]):
                if located_only and row.get('latitude') is None:
                    continue
                if category and row['category'] != category or row['severity_rank'] < minimum:
                    continue
                if cutoff and (row.get('observed_at') or '') < cutoff:
                    continue
                country_camera = bool(country_filter and row['kind'] == 'public_camera')
                if country_camera and str((row.get('details') or {}).get('country') or '').casefold() != country_filter:
                    continue
                if needle and not country_camera and needle not in search:
                    continue
                if region and not matches(row, region):
                    continue
                all_rows.append({**row, 'retrieved_at': success, 'stale': stale, 'source_sha256': source.get('sha256')})
        all_rows.sort(key=lambda row: row.get('observed_at') or '', reverse=True)
        return sources, all_rows

    def _sources(self):
        states, sources = self.store.states(), []
        # Discovered catalogs (app/intelligence/discovery.py) join the built-in feeds at run time.
        for source in (*ALL_FEEDS, *(feed for feed in self.feeds if feed not in ALL_FEEDS)):
            if source not in self.feeds and source.key not in states:
                continue
            state = states.get(source.key, {})
            success = timestamp(state.get('last_success_at'))
            age = max(0, (now() - datetime.fromisoformat(success)).total_seconds()) if success else None
            stale = state.get('status') != 'ok' or age is None or age > source.interval * 2
            sources.append({'key': source.key, 'title': source.title, 'url': source.url,
                            'documentation': source.documentation, 'interval_seconds': source.interval,
                            **state, 'age_seconds': round(age) if age is not None else None, 'stale': stale,
                            'status': state.get('status', 'not_fetched')})
        return sources

    @staticmethod
    def _aggregates(all_rows):
        magnitudes = [row['details']['magnitude'] for row in all_rows if row['feed'] == 'earthquakes' and row['details'].get('magnitude') is not None]
        dated = [row['observed_at'] for row in all_rows if row.get('observed_at')]
        mapped = sum(row.get('latitude') is not None for row in all_rows)
        return {'counts_by_feed': dict(Counter(row['feed'] for row in all_rows)),
                'counts_by_category': dict(Counter(row['category'] for row in all_rows)),
                'counts_by_severity': dict(Counter(row['severity'] for row in all_rows)),
                'located': mapped, 'unknown_location': len(all_rows) - mapped,
                'largest_earthquake_magnitude': max(magnitudes, default=None),
                'earliest_event_time': min(dated, default=None), 'latest_event_time': max(dated, default=None)}

    def dashboard(self, query='', feed='', bbox=None, limit=200, offset=0, located_only=False,
                  category='', min_severity='', since_hours=None):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('Use a query up to 500 characters and a limit of 1–1000.')
        if type(offset) is not int or not 0 <= offset <= 50000:
            raise ValueError('Use an event offset of 0–50000.')
        sources, all_rows = self._matching(query, feed, bbox, located_only, category, min_severity, since_hours)
        alerts = self.alerts(query=query, feed=feed, bbox=bbox, limit=30)
        return {'intelligence': True, 'sources': sources, 'events': all_rows[offset:offset+limit], 'total_matching': len(all_rows),
                'offset': offset, 'next_offset': offset + limit if len(all_rows) > offset + limit else None,
                'aggregates': self._aggregates(all_rows),
                'truncated': len(all_rows) > offset + limit or offset > 0, 'alerts': alerts['alerts'],
                'alert_total_matching': alerts['total_matching'], 'watches': self.store.watches(),
                'monitor': self.status(), 'basis': 'Source-reported events, not independent verification. Offline or failed feeds retain their last snapshot and are marked stale.'}

    def view(self, query='', feed='', bbox=None, category='', min_severity='', since_hours=None,
             offset=0, limit=200, map_categories=None, map_limit=1000, live_limit=60):
        """Everything one display refresh needs, from a single filtering pass.

        Public cameras are places to look, not events: unless the cameras category
        or a camera feed is chosen they stay on the map layer and the Cameras tab,
        out of the event list, counts and live feed (35,000 cameras made "All
        events" 41,000 items and the category chart one bar). Scheduled items dated
        in the future (road works next year) follow what is happening now.
        """
        sources, rows = self._matching(query, feed, bbox, False, category, min_severity, since_hours)
        located = [row for row in rows if row.get('latitude') is not None
                   and (map_categories is None or row['category'] in map_categories)]
        # Dense views keep the most severe, then most recent, markers.
        located.sort(key=lambda row: (row['severity_rank'], row.get('observed_at') or ''), reverse=True)
        wants_cameras = category == 'cameras' or feed.startswith('cameras') or feed == 'livestreams'
        events = rows if wants_cameras else [row for row in rows if row['category'] != 'cameras']
        events = happening_first(events)
        live = [row for row in events if row.get('observed_at') and row['category'] != 'cameras']
        alerts = self.alerts(query=query, feed=feed, bbox=bbox, limit=1000)
        return {'intelligence': True, 'sources': sources, 'events': events[offset:offset+limit],
                'total_matching': len(events), 'offset': offset,
                'next_offset': offset + limit if len(events) > offset + limit else None,
                'cameras_total': len(rows) - len(events) if not wants_cameras else 0,
                'mapped': located[:map_limit], 'mapped_total': len(located),
                'live': live_selection(live, live_limit), 'live_total': len(live), 'aggregates': self._aggregates(events),
                'spotlight': spotlight(live),
                'alerts': alerts['alerts'], 'alert_total_matching': alerts['total_matching'],
                'watches': self.store.watches(), 'monitor': self.status(), 'revision': self.store.revision}

    def alerts(self, limit=100, query='', feed='', bbox=None, offset=0):
        if not isinstance(query, str) or len(query) > 500 or not isinstance(feed, str) or feed and feed not in BY_KEY:
            raise ValueError('Use a search up to 500 characters and a registered feed.')
        if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or not 0 <= offset <= 1000:
            raise ValueError('Use an alert limit of 1–1000 and offset of 0–1000.')
        region = validate_watch({'title': 'Alert region', 'bbox': bbox}) if bbox is not None else None
        rows = [alert for alert in self.store.alerts(1000)
                if (not feed or alert['event']['feed'] == feed)
                and (not query or query.casefold() in json.dumps(alert, ensure_ascii=False).casefold())
                and (not region or matches(alert['event'], region))]
        return {'intelligence': True, 'alerts': rows[offset:offset+limit], 'total_matching': len(rows),
                'offset': offset, 'next_offset': offset + limit if len(rows) > offset + limit else None,
                'basis': 'Up to 1000 retained local watch alerts; a recorded alert is not proof of a currently active incident.'}

    def preferences(self, action='get', data=None):
        from app.intelligence.view import DEFAULT_VIEW, validate_view
        if action not in ('get', 'save'):
            raise ValueError('Use get or save for display preferences.')
        patch = validate_view(data) if action == 'save' else {}
        with self.store.lock:
            value = {**DEFAULT_VIEW, **self.store.preference('display_preferences', {}), **patch}
            validate_view(value)
            if action == 'save':
                self.store.save_preference('display_preferences', value)
        return {'intelligence': True, 'preferences': value, 'saved': action == 'save'}

    MAX_NAMED_VIEWS = 30

    def named_views(self, action='list', name='', data=None):
        """Named filter views, each saved with the sources that backed it.

        Saving records the matching count and, per contributing feed, its item
        count, last successful fetch and staleness. Opening re-checks the same
        filters against today's cache and says what changed, so an old view
        never passes for current coverage.
        """
        from app.intelligence.view import validate_view
        if action not in ('list', 'save', 'open', 'delete'):
            raise ValueError('Use list, save, open or delete for saved views.')
        name = ' '.join(str(name or '').split())
        if action != 'list' and not 1 <= len(name) <= 80:
            raise ValueError('Give the view a name of 1–80 characters.')
        with self.store.lock:
            views = dict(self.store.preference('named_views', {}))
        if action == 'list':
            rows = [{'name': view['name'], 'saved_at': view['saved_at'], 'headline': view['evidence']['headline'],
                     **{key: view['options'].get(key, '') for key in ('region', 'query', 'category', 'time_range')}}
                    for view in sorted(views.values(), key=lambda view: view['name'].casefold())]
            return {'intelligence': True, 'views': rows}
        if action == 'delete':
            removed = views.pop(name, None) is not None
            if removed:
                self.store.save_preference('named_views', views)
            return {'intelligence': True, 'deleted': removed, 'name': name}
        if action == 'save':
            options = validate_view(data or {})
            if name not in views and len(views) >= self.MAX_NAMED_VIEWS:
                raise ValueError(f'Up to {self.MAX_NAMED_VIEWS} saved views; delete one first.')
            views[name] = {'name': name, 'options': options, 'saved_at': now().isoformat(),
                           'evidence': self._view_evidence(options)}
            self.store.save_preference('named_views', views)
            return {'intelligence': True, 'saved': True, 'view': views[name]}
        if name not in views:
            raise ValueError(f'No saved view is called "{name}".')
        view = views[name]
        current = self._view_evidence(view['options'])
        return {'intelligence': True, 'view': view, 'now': current,
                'changes': self._view_changes(view['evidence'], current)}

    def _view_evidence(self, options):
        from app.intelligence.view import DEFAULT_VIEW, TIME_RANGES
        options = {**DEFAULT_VIEW, **options}
        value = self.assessment(options['query'], options['layer'], None, options['region'], options['category'],
                                options['min_severity'], TIME_RANGES.get(options['time_range']))
        counts = value['aggregates']['counts_by_feed']
        sources = [{'feed': row['feed'], 'items': counts.get(row['feed'], 0),
                    'last_success_at': row['last_success_at'], 'stale': row['stale']}
                   for row in value['source_changes'] if counts.get(row['feed']) or row['feed'] == options['layer']]
        return {'checked_at': now().isoformat(), 'headline': value['headline'],
                'total_matching': value['total_matching'], 'sources': sources,
                'stale_feeds': sum(1 for row in value['source_changes'] if row['stale'])}

    @staticmethod
    def _view_changes(before, after):
        old = {row['feed']: row for row in before['sources']}
        new = {row['feed']: row for row in after['sources']}
        changes = [f"Matching items: {before['total_matching']} when saved, {after['total_matching']} now."]
        for feed in sorted(set(old) | set(new)):
            was, now_row = old.get(feed), new.get(feed)
            if was and not now_row:
                changes.append(f'{feed}: contributed {was["items"]} items when saved, none now.')
            elif now_row and not was:
                changes.append(f'{feed}: new source, {now_row["items"]} items now.')
            elif now_row['stale'] and not was['stale']:
                changes.append(f'{feed}: stale or unavailable now (last success {now_row["last_success_at"] or "never"}).')
            elif was['stale'] and not now_row['stale']:
                changes.append(f'{feed}: current again.')
        return changes

    def assessment(self, query='', feed='', bbox=None, region='World', category='', min_severity='', since_hours=None):
        from app.intelligence.view import REGIONS
        if not isinstance(region, str) or region not in REGIONS:
            raise ValueError('Choose a registered region preset.')
        bounds = bbox if bbox is not None else (REGIONS[region] if region != 'World' else None)
        value = self.dashboard(query, feed, bounds, limit=20, category=category, min_severity=min_severity, since_hours=since_hours)
        relevant = [source for source in value['sources'] if not feed or source['key'] == feed]
        gaps = [source['key'] for source in relevant if source['stale']]
        counts = value['aggregates']['counts_by_feed']
        severity = value['aggregates']['counts_by_severity']
        _sources, rows = self._matching(query, feed, bounds, False, category, min_severity, since_hours)
        # Severe first; within a level, what has happened before what is only scheduled.
        moment = now().isoformat()
        notable = sorted((row for row in rows if row['severity'] in ('high', 'medium')),
                         key=lambda row: (row['severity_rank'], (row.get('observed_at') or '') <= moment,
                                          row.get('observed_at') or ''), reverse=True)[:8]
        # Camera catalogs are places to look, not events: one line for all of them,
        # instead of ~70 lines burying the event feeds.
        camera_feed = lambda key: key.startswith('cameras') or key == 'livestreams'
        camera_count = sum(count for key, count in counts.items() if camera_feed(key))
        camera_networks = sum(1 for key in counts if camera_feed(key))
        value.update({'region': region, 'bbox': bounds,
            'headline': f"{value['total_matching'] - camera_count} event reports"
                        + (f" and {camera_count} public camera entries" if camera_count else '')
                        + f" match {region}; {len(gaps)} selected feeds are stale or unavailable.",
            'findings': [f"{key}: {count} matching source items" for key, count in sorted(counts.items()) if not camera_feed(key)]
                + ([f"Public cameras: {camera_count} entries from {camera_networks} catalogs (browse them in Cameras)"]
                   if camera_count else [])
                + ([f"Severity mix (source-derived rules): {severity.get('high', 0)} high, {severity.get('medium', 0)} medium, "
                    f"{severity.get('low', 0)} low, {severity.get('info', 0)} informational."] if rows else [])
                + [f"{SEVERITY_LABEL[row['severity']]} · {row['headline']}" + (f" · {row['subtitle']}" if row['subtitle'] else '')
                   + f" · {row.get('observed_at') or 'time unknown'} — {row['severity_basis']}" for row in notable],
            'source_changes': [{'feed': source['key'], 'last_success_at': source.get('last_success_at'),
                                'change': source.get('change'), 'stale': source['stale']} for source in relevant],
            'coverage_gaps': ['World coverage is partial: news reports come from selected outlets; markets are linked to events only by written '
                              'industry rules (Trade Center → World events), never as proof of cause. '
                              'Public ADS-B aircraft and Baltic AIS vessels are on-demand layers, not complete surveillance.',
                'Unknown coordinates are omitted from geographic searches, including most cyber and space-weather items.',
                'CISA catalog dates and NOAA bulletins may be historical; a current fetch does not make every item currently active.',
                'Public camera networks cover ' + CAMERA_COVERAGE + ', not every place. Availability flags and catalog timestamps do not establish current frame freshness.',
                'Provider event counts may overlap; GDACS representative points are not full affected areas.']
                + (['Stale or unavailable: ' + ', '.join(gaps)] if gaps else []),
            'interpretation': 'Counts describe source coverage, not incident risk or severity across different domains. Nearby or simultaneous observations do not establish a causal connection.',
            'analysis_scope': 'Aggregates cover every matching cached item; the evidence examples below are paginated.'})
        return value

    def event_record(self, event_id):
        from app.intelligence.view import brief_event, distance_km
        if not isinstance(event_id, str) or len(event_id) > 220 or event_id.partition(':')[0] not in BY_KEY:
            raise ValueError('Use a complete intelligence event ID from the dashboard.')
        key = event_id.partition(':')[0]
        versions = self.store.event_versions(key, event_id)
        latest = self.store.latest(key)
        current = (latest or {}).get('events', {}).get(event_id)
        row = current or (versions[0]['event'] if versions else None)
        if row is None:
            row = next((alert['event'] for alert in self.store.alerts(1000) if alert['event']['id'] == event_id), None)
        if row is None:
            raise ValueError('That event is not present in retained snapshots or alerts.')
        source = next(source for source in self._sources() if source['key'] == key)
        watches = self.store.watches()
        matching_watches = [watch for watch in watches if matches(row, watch)]
        row = {**row, **assess(row), 'stale': source['stale'],
               'retrieved_at': source.get('last_success_at') if current else (versions[0]['captured_at'] if versions else None),
               'source_sha256': source.get('sha256') if current else None}
        seen = set()
        revisions = []
        for version in versions:
            identity = fingerprint(version['event'])
            if identity not in seen:
                seen.add(identity)
                revisions.append(version)
        # Search all retained current rows for real, bounded geographic context.
        nearby, cameras = [], []
        ids = self.store.latest_ids()
        for feed in (*ALL_FEEDS, *(item for item in self.feeds if item not in ALL_FEEDS)):
            if feed.key not in ids:
                continue
            for candidate, _search in self._cached_rows(feed.key, ids[feed.key]):
                distance = distance_km(row, candidate)
                if candidate['id'] == event_id or distance is None or distance > 300:
                    continue
                if candidate['category'] == 'cameras':
                    # Thousands of city cameras would bury real nearby events; they get their own list.
                    if distance <= 100:
                        details = candidate['details']
                        cameras.append({'id': candidate['id'], 'title': candidate['title'], 'feed': candidate['feed'],
                                        'provider': details.get('provider'), 'distance_km': round(distance, 2),
                                        'available': details.get('available'), 'image_url': details.get('image_url'),
                                        'video_url': details.get('video_url'), 'page_url': details.get('page_url')})
                else:
                    nearby.append({'id': candidate['id'], 'title': candidate['title'], 'feed': candidate['feed'],
                                   'headline': candidate['headline'], 'category': candidate['category'],
                                   'severity': candidate['severity'], 'badge': candidate['badge'],
                                   'distance_km': round(distance, 1), 'observed_at': candidate.get('observed_at'), 'url': candidate['url']})
        nearby.sort(key=lambda item: item['distance_km'])
        cameras = sorted((item for item in cameras if item['available']), key=lambda item: item['distance_km'])[:8]
        corroborating = [item for item in nearby if item['feed'] != key and item['category'] == row['category']
                         and hours_apart(item.get('observed_at'), row.get('observed_at')) <= 72]
        return {'intelligence': True, 'event': row, 'event_summary': brief_event(row), 'source': source,
                'in_current_feed': current is not None, 'retained_revisions': revisions,
                'matching_watches': matching_watches,
                'nearby_events': nearby[:20], 'nearby_count': len(nearby), 'corroborating': corroborating[:10],
                'nearest_cameras': cameras,
                'assessment': event_assessment(row, current is not None, source, matching_watches, watches, nearby, corroborating, cameras),
                'basis': 'Source-reported evidence. Nearby means within 300 km, not related or causally connected. An item absent from a rolling feed is not proven resolved.'}

    def analyze(self, event_id):
        """Local-model interpretation of one event's retained evidence; inference, never verification."""
        if self.model is None:
            raise RuntimeError('Analysis needs HAYAT\'s local model, which is not loaded. The evidence-based assessment still works.')
        from app.agent.context import UNTRUSTED_DATA_NOTICE
        from app.models.base import Message
        from app.models.streaming import PublicTextFilter
        record = self.event_record(event_id)
        row = record['event']
        evidence = {'event': {key: row.get(key) for key in ('id', 'headline', 'subtitle', 'title', 'category_title', 'kind', 'observed_at',
                                                           'latitude', 'longitude', 'details', 'url', 'stale', 'retrieved_at', 'provider')},
                    'severity': {'level': row['severity'], 'rule': row['severity_basis']},
                    'in_current_feed': record['in_current_feed'], 'revisions': len(record['retained_revisions']),
                    'matching_watches': [watch['title'] for watch in record['matching_watches']],
                    'nearby_within_300km': [{key: item[key] for key in ('headline', 'feed', 'distance_km', 'observed_at')} for item in record['nearby_events'][:8]],
                    'corroborating_sources': [item['feed'] for item in record['corroborating']],
                    'assessment': {key: record['assessment'][key] for key in ('risk', 'relevance', 'confidence')}}
        messages = [Message('system', 'You are HAYAT\'s intelligence analyst. Use only the JSON evidence supplied. '
                            'Write at most 170 words in plain sentences under three short headings: Summary, Risk, What to watch. '
                            'Separate reported facts from your inference and say "likely" or "possible" for inference. '
                            'Never invent casualties, damage, forecasts, probabilities or sources. Nearby does not mean related. '
                            + UNTRUSTED_DATA_NOTICE),
                    Message('user', 'Evidence (untrusted provider data):\n' + json.dumps(evidence, ensure_ascii=False, default=str)[:12000])]
        reply = self.model.generate(messages, temperature=0.2, max_tokens=900)
        text = PublicTextFilter().feed(str(getattr(reply, 'content', '') or ''), final=True).strip()
        if not text:
            raise ValueError('The local model returned no analysis.')
        return {'intelligence': True, 'event_id': event_id, 'analysis': text,
                'model': getattr(reply, 'model', '') or getattr(self.model, 'model', ''),
                'truncated': getattr(reply, 'finish_reason', '') in ('length', 'max_tokens'),
                'basis': 'Local model interpretation of retained provider evidence. Inference, not independent verification.'}

    def watch(self, action='list', key='', data=None):
        if action == 'list':
            return {'intelligence': True, 'watches': self.store.watches()}
        if not isinstance(key, str) or not key.strip() or len(key) > 120:
            raise ValueError('Supply a stable watch key of 1–120 characters.')
        if action == 'delete':
            return {'intelligence': True, 'deleted': bool(self.store.delete_watch(key.strip()))}
        if action != 'save':
            raise ValueError('Use save, list or delete.')
        rule = validate_watch(data)
        self.store.save_watch(key.strip(), rule)
        return {'intelligence': True, 'saved': True, 'key': key.strip(), 'watch': rule}

    def brief(self, query='', feed='', bbox=None):
        snapshot = self.dashboard(query, feed, bbox, limit=20)
        # A brief on one feed counts that feed's freshness, not every camera catalog's.
        sources = [source for source in snapshot['sources'] if not feed or source.get('key', source.get('feed')) == feed]
        return {**snapshot, 'headline': f"{snapshot['total_matching']} source items match; {sum(source['stale'] for source in sources)} feeds stale or unavailable.",
                'analysis': 'This brief lists observed evidence and local watch matches. Co-location or simultaneous reports do not establish causation. No forecast probability is inferred.'}

    def status(self):
        thread = self._thread
        return {'running': bool(thread and thread.is_alive() and not self._stop.is_set()),
                'enabled': self.enabled, 'tick_seconds': self.interval, 'last_error': self.last_error,
                'approval_required_per_cycle': False, 'osiris_service_required': False,
                'scope': 'Public feed reads, bounded local snapshots and local alerts.'}

    def start(self):
        with self._lifecycle:
            if not self.enabled or self._thread and self._thread.is_alive():
                return self.status()
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name='hayat-intelligence', daemon=True)
            self._thread.start()
            return self.status()

    def _loop(self):
        from app.execution import execution_scope
        from app.safety.authority import Principal
        # A monitor owns its call budget. Resetting the chat registry's shared
        # budget would let a background cycle reset an in-flight agent task.
        monitor_registry = None
        if self.registry:
            from app.tools.registry import ToolRegistry
            monitor_registry = ToolRegistry(self.registry.permissions)
            for name in ('authority', 'events', 'failures', 'understanding'):
                setattr(monitor_registry, name, getattr(self.registry, name, None))
            tool = self.registry.get('intelligence_refresh')
            monitor_registry.register(tool.name, tool.description, tool.input_schema, tool.risk, tool.permission, tool.handler)
        while not self._stop.is_set():
            try:
                with execution_scope(principal=Principal.BACKGROUND_LEARNER.value, task_id='intelligence-' + uuid.uuid4().hex):
                    if monitor_registry:
                        monitor_registry.reset_budget()
                        result = monitor_registry.execute('intelligence_refresh', {'due_only': True})
                        self.last_error = result.error if not result.success else None
                    else:
                        self.refresh(due_only=True)
            except Exception as exc:
                self.last_error = f'{type(exc).__name__}: {exc}'[:1000]
                log_swallowed(_log, 'refreshing intelligence feeds')
            self._stop.wait(self.interval)

    def stop(self, timeout=25):
        with self._lifecycle:
            self._stop.set()
            if self._thread and self._thread is not threading.current_thread():
                self._thread.join(timeout)
            return not (self._thread and self._thread.is_alive())

    def close(self):
        if self._map_server is not None:
            self._map_server.close()
        if not self.stop():
            return False
        self.fetcher.close()
        return True

    def _cache_knowledge(self, feed, rows, resource):
        if self.knowledge is None:
            return
        try:
            from urllib.parse import urlsplit
            from app.research.models import SourceDocument
            content = 'Provider observations, not independent verification. Camera metadata is not a live-frame timestamp.\n'
            content += '\n'.join(json.dumps(row, ensure_ascii=False) for row in list(rows.values())[:200])
            document = SourceDocument(feed.url, redact(resource.final_url, feed), feed.title, urlsplit(feed.url).hostname or '',
                content[:100000], resource.retrieved_at, 200, 'application/json', resource.sha256, len(resource.content),
                canonical_url=feed.url, primary=True, independence_group=urlsplit(feed.url).hostname or '',
                source_type='public_intelligence_feed', metadata={'items': len(rows), 'examples': min(200, len(rows)),
                    'intelligence_feed': feed.key, 'inference': False, 'limitations': 'Provider evidence; not a verified claim or consciousness.'})
            self.knowledge.cache_source(document, (now() + timedelta(seconds=feed.interval)).isoformat())
        except Exception:
            log_swallowed(_log, 'caching intelligence source evidence')
