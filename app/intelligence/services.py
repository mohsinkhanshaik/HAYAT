"""Point context, public camera previews and the local interactive map service."""
from datetime import timedelta
from io import BytesIO
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import logging

from app.intelligence.camera_health import STREAM
from app.intelligence.feeds import finite
from app.intelligence.providers import CAMERA_COVERAGE, camera_url, camera_media_profile
from app.intelligence.resources import resource_catalog
from app.logging import log_swallowed

_log = logging.getLogger(__name__)
# What publishers label HLS playlists; the body must still start with #EXTM3U.
HLS_TYPES = {'application/vnd.apple.mpegurl', 'application/x-mpegurl', 'audio/mpegurl', 'audio/x-mpegurl',
             'text/plain', 'application/octet-stream', 'video/mp2t', 'binary/octet-stream'}
CLIP_TYPES = {'video/mp4', 'application/octet-stream', 'binary/octet-stream'}


#: Next camera from one holy-site broadcast goes to its pair first.
LIVESTREAM_PAIRS = {'makkah': 'madinah', 'madinah': 'makkah'}


def livestream_alternatives(event_id, query=''):
    """The other official livestreams for Next camera: the one asked for second ("then madinah"), its pair, the rest."""
    from app.intelligence.live import LIVESTREAMS
    current = event_id.split(':', 1)[1]
    order = [item['id'] for item in LIVESTREAMS if item['id'] != current]
    wanted = str(query or '').removeprefix('then ').strip()
    # News leads to other news channels, a webcam to other webcams.
    order.sort(key=lambda key: key.startswith('news_') != current.startswith('news_'))
    for first in (LIVESTREAM_PAIRS.get(current), wanted):
        if first in order:
            order.remove(first)
            order.insert(0, first)
    titles = {item['id']: item['title'] for item in LIVESTREAMS}
    return [{'id': 'livestreams:' + key, 'title': titles[key]} for key in order]


def token_expiry(url):
    """When a signed stream address stops working (ISO UTC), from its JWT 'exp', or '' when unsigned.

    Kansas CARS issues stream URLs with a token valid for 300 seconds; the
    catalog is fetched hourly, so the stored address is usually already dead.
    """
    import base64
    import re
    from datetime import UTC, datetime
    from urllib.parse import parse_qsl, urlsplit
    for _key, value in parse_qsl(urlsplit(str(url or '')).query):
        if re.fullmatch(r'[\w-]+\.([\w-]+)\.[\w-]+', value):
            payload = value.split('.')[1]
            try:
                claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
                return datetime.fromtimestamp(float(claims['exp']), UTC).isoformat()
            except (ValueError, KeyError, TypeError):
                continue
    return ''


def token_expired(url, margin_seconds=45):
    """True when a signed address has expired or will within the margin (a player needs a moment to connect)."""
    from datetime import UTC, datetime
    expiry = token_expiry(url)
    return bool(expiry) and datetime.fromisoformat(expiry) <= datetime.now(UTC) + timedelta(seconds=margin_seconds)


class ExplorationServices:
    def camera_play(self, event_id='', query='', live_only=True, refresh=True, still_only=False):
        """Choose registered public media for the chat card, without claiming playback.

        A responding live stream is preferred; where the publisher only offers
        recorded clips (London) or refreshing stills (New York) the card shows
        that instead and says so, rather than refusing. Up to six stream
        playlists are checked first, so a dead camera is skipped instead of
        timing out in the player.
        """
        explicit_selection = bool(event_id)
        notice, alternatives = '', []
        if event_id.startswith('livestreams:') and refresh:
            latest = self.store.latest('livestreams')
            # A channel added in an update is not in the stored list until it is re-read.
            if latest is None or event_id not in latest['events']:
                self.store.state('livestreams', {**self.store.states().get('livestreams', {}), 'next_attempt_at': None})
                self.refresh(['livestreams'])
        if not event_id:
            groups = self._playable_groups(query)
            if not any(groups.values()) and refresh:
                from app.intelligence.camera_chat import PLACE_FEEDS
                from app.intelligence.feeds import ALL_FEEDS
                words = query.lower().split()
                relevant = [*PLACE_FEEDS.get(' '.join(words), ()),
                            *(feed.key for feed in ALL_FEEDS if feed.key.startswith('cameras_')
                              and words and all(word in (feed.key + ' ' + feed.title).lower() for word in words))]
                self.refresh((relevant or ['cameras_delaware', 'cameras_iowa'])[:3])
                groups = self._playable_groups(query)
            if groups['player_only']:
                provider = groups['player_only'][0]['details'].get('provider') or 'This publisher'
                raise ValueError(f'{provider} shows these cameras only in its own web player, which cannot be embedded in chat. '
                                 f'Ask "Find cameras in {query or "<place>"}" for each camera\'s publisher page.')
            if not any(groups.values()):
                raise ValueError((f'No public camera matches "{query}". ' if query else 'No public camera is available. ')
                                 + 'Try "Find cameras in <place>" to see what each publisher covers, or "Refresh intelligence feeds".')
            streams = groups['stream']
            # Signed addresses (Kansas: five minutes) are renewed before they are probed;
            # otherwise every one of them fails the check for 55 minutes in each hour.
            if any(token_expired(row['details'].get('video_url')) for row in streams[:6]):
                if self._renew_stream_tokens({row['feed'] for row in streams[:6]}):
                    streams = self._playable_groups(query)['stream'] or streams
            chosen = next((row for row in streams[:6] if row['details'].get('live_page') or self.stream_health(row)), None)
            if chosen is not None:
                pool = streams
            else:
                pool = groups['clip'] or groups['still']
                if not pool:
                    # A short availability probe must not prevent the owner's
                    # selected publisher stream from reaching the reconnecting player.
                    pool = streams
                chosen = pool[0]
                if pool is groups['still']:
                    # A still is fetched before it is offered: a camera showing the publisher's "unavailable" card or
                    # answering 404 is passed over for the next one (four tries), and remembered as failing.
                    chosen = next((row for row in pool[:4] if self._still_works(row['id'])), chosen)
                provider = chosen['details'].get('provider') or 'This publisher'
                notice = ('The matching live streams did not respond, so this is the next best publisher media. ' if streams else '') + (
                    f'{provider} publishes short recorded clips here, not continuous live video; this plays its latest clip.'
                    if pool is groups['clip'] else
                    f'{provider} publishes refreshing still images here, not video; the card shows the latest still and refreshes it.'
                    if pool is groups['still'] else 'The player will keep trying this live stream while selected.')
            event_id = chosen['id']
            dead = {url for url, (_at, responding, *_state) in self.__dict__.get('_stream_checks', {}).items() if not responding}
            alternatives = [{'id': row['id'], 'title': row['title']} for row in pool if row['id'] != event_id
                            and camera_url(row['details'].get('video_url'), row['feed'], video=True) not in dead][:15]
        if event_id.startswith('livestreams:') and not alternatives:
            alternatives = livestream_alternatives(event_id, query)
        row = self.event_record(event_id)['event']
        if token_expired(row['details'].get('video_url')) and self._renew_stream_tokens({row['feed']}):
            row = self.event_record(event_id)['event']
        details = row['details']
        from app.intelligence.camera_chat import playback_mode
        mode = playback_mode(details) if row['kind'] == 'public_camera' else None
        url = camera_url(details.get('video_url'), row['feed'], video=True) if mode in ('stream', 'clip') else ''
        if refresh and explicit_selection and url:
            # An explicit retry must not reuse the failed one-minute probe.
            self.__dict__.get('_stream_checks', {}).pop(url, None)
        if row['feed'] == 'livestreams' and details.get('live_page'):
            from app.intelligence.live import broadcast_source, resolve_livestream
            broadcast_source(details['live_page'])
            try:
                url = resolve_livestream(details['live_page'])
            except Exception as exc:
                notice = (f"{row['title']}: the official channel is not broadcasting a playable stream right "
                          f"now ({type(exc).__name__}). The selected player will keep reconnecting; "
                          f"publisher page: {details['live_page']}")
        if mode is None and details.get('page_url'):
            provider = details.get('provider') or 'This publisher'
            raise ValueError(f"{provider} shows this camera only in its own web viewer (no stream or image HAYAT may "
                             f"fetch), so it cannot play here. Open it on the publisher's map: {details['page_url']}")
        if mode is None or mode != 'still' and not url and not details.get('live_page') \
                or mode == 'still' and not camera_url(details.get('image_url'), row['feed']):
            raise ValueError('Choose an available registered public camera with video or a still image; publisher-only players open with "Open publisher page".')
        still = camera_url(details.get('image_url'), row['feed'])
        if mode == 'stream' and still and (still_only or row['feed'] != 'livestreams' and not self._stream_responds(url, row['feed'])):
            # A stream that is not answering (or never showed a frame) is not left spinning:
            # the same camera's refreshing still is shown instead, and said to be a still.
            mode, url = 'still', ''
            notice = ('The live stream is not sending video right now, so this shows the same camera\'s latest still '
                      'image and refreshes it. Ask again later for live video.')
        elif mode == 'stream' and row['feed'] != 'livestreams' and not self._stream_responds(url, row['feed']):
            notice = 'The publisher did not answer the availability check. The player will keep reconnecting while selected.'
        if live_only and mode != 'stream' and not notice:
            notice = ('This publisher supplies a short recorded clip here, not a continuous live stream.' if mode == 'clip'
                      else 'This publisher supplies refreshing still images here, not video.')
        profile = camera_media_profile(details)
        return {'intelligence': True, 'camera_playback': True, 'event_id': event_id, 'title': row['title'],
                'feed': row['feed'], 'video_url': url, 'live_page': details.get('live_page', ''),
                'live': mode == 'stream', 'mode': mode, 'expires_at': token_expiry(url), 'has_still': bool(still),
                'place': ', '.join(dict.fromkeys(str(details[key]) for key in ('city', 'county', 'state', 'region', 'country')
                                                 if details.get(key))),
                'refresh_seconds': max(30, int(finite(details.get('refresh_seconds')) or 60)),
                'provider': details.get('provider', ''), 'media_label': profile['label'], 'source_url': row['url'],
                'alternatives': alternatives, 'notice': notice,
                'basis': 'Prepared for the desktop chat player. Its status confirms decoding; publisher video can be delayed. No recording is created.'}

    def _playable_groups(self, query):
        """Available cameras in the best-matching place, split by what the chat card can show."""
        from app.intelligence.camera_chat import playback_mode, rank_cameras
        from app.intelligence.feeds import BY_KEY
        _sources, rows = self._matching('', '', None, False, 'cameras')
        groups = {'stream': [], 'clip': [], 'still': []}
        titles = {key: feed.title for key, feed in BY_KEY.items()}
        ranked = rank_cameras(rows, query, titles)
        for row in ranked:
            mode = playback_mode(row['details'])
            if mode:
                groups[mode].append(row)
        # Cameras whose last real check failed, and networks where every recent check failed, go last (stable sort).
        for mode, group in groups.items():
            broken = self.broken_cameras([row['id'] for row in group], streams=mode != 'still')
            group.sort(key=lambda row: row['id'] in broken)
        # Kept out of any(groups.values()): only reported when nothing here can play.
        groups['player_only'] = [] if any(groups.values()) else [row for row in ranked if row['details'].get('page_url')][:1]
        return groups

    def _renew_stream_tokens(self, feeds):
        """Fetch fresh signed stream addresses: one catalog request per feed, at most every four minutes.

        The publisher's catalog is the only place a new token comes from. The
        normal hourly cooldown is set aside for this one request, which is made
        only when the owner asks to play a camera whose address has expired.
        """
        import time
        renewed = self.__dict__.setdefault('_token_renewals', {})
        due = [feed for feed in sorted(feeds) if time.monotonic() - renewed.get(feed, -1e9) >= 240]
        for feed in due:
            renewed[feed] = time.monotonic()
            self.store.state(feed, {**self.store.states().get(feed, {}), 'next_attempt_at': None})
        if due:
            self.refresh(due)
        return bool(due)

    def _stream_state(self, url, feed, timeout=4):
        """('live' | 'drm' | 'stream_dead' | 'empty', detail): the playlist a player would read and, for a master
        playlist, its first variant (camera_health.hls_state); for a recorded clip (London's MP4s), the clip itself.
        Remembered for a minute."""
        import time
        from urllib.parse import urlsplit
        from app.intelligence.camera_health import hls_state
        url = camera_url(url, feed, video=True)
        if not url:
            return 'stream_dead', 'no admitted stream address'
        checks = self.__dict__.setdefault('_stream_checks', {})
        cached = checks.get(url)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[2]
        if not urlsplit(url).path.lower().endswith('.m3u8'):
            try:
                clip = self.fetcher.fetch(url, 16 * 1024 * 1024, CLIP_TYPES, timeout=max(timeout, 15)).content
                state = ('live', '') if len(clip) >= 10_000 else ('empty', f'clip of {len(clip)} bytes')
            except Exception as exc:  # a missing clip is a verdict, not a crash
                state = ('stream_dead', f'{type(exc).__name__}: {exc}'[:160])
        else:
            state = hls_state(lambda address: self.fetcher.fetch(address, 2 * 1024 * 1024, HLS_TYPES, timeout=timeout).content, url)
        if state[0] == 'stream_dead':
            log_swallowed(_log, 'checking public camera stream ' + feed)
        checks[url] = (time.monotonic(), state[0] == 'live', state)
        while len(checks) > 500:
            checks.pop(next(iter(checks)))
        return state

    def _stream_responds(self, url, feed):
        """True when the stream lists video segments a player here can decode (not DRM)."""
        return self._stream_state(url, feed)[0] == 'live'

    def stream_health(self, row, decode=False, timeout=4):
        """Probe one camera's stream (renewing a signed address first) and record the verdict for that camera.

        With `decode`, one frame is also decoded with FFmpeg, as a player would: a playlist can be fine while the
        picture is the publisher's slate (Indiana's "LOADING VIDEO", 2026-11-06), which only the frame shows."""
        url = row['details'].get('video_url')
        if token_expired(url) and self._renew_stream_tokens({row['feed']}):
            row = self.event_record(row['id'])['event']
            url = row['details'].get('video_url')
        state, detail = self._stream_state(url, row['feed'], timeout)
        verdict = 'working' if state == 'live' else state
        from urllib.parse import urlsplit
        from app.video_workflow import executable
        # Live HLS only: a recorded clip was already downloaded whole and checked by _stream_state.
        if state == 'live' and decode and urlsplit(str(url)).path.lower().endswith('.m3u8') and executable('ffmpeg'):
            import cv2
            from app.intelligence.camera_eyes import ffmpeg_frame
            from app.intelligence.explore import placeholder
            folder = self.store.path.parent / 'intelligence' / 'camera-checks'
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / (hashlib.sha256(row['id'].encode()).hexdigest()[:24] + '.jpg')
            try:
                ffmpeg_frame(camera_url(url, row['feed'], video=True), target, timeout=40)
                image = cv2.imread(str(target))
                if image is None:
                    verdict, detail = 'stream_dead', 'the decoded frame could not be read'
                elif placeholder(image):
                    verdict, detail = 'card', "the stream shows the publisher's slate, not the road"
            except ValueError as exc:
                verdict, detail = 'stream_dead', str(exc)[:200]
            finally:
                target.unlink(missing_ok=True)
        # Kept apart from the still's record: one camera can have a working still and a dead stream.
        self.store.record_camera(row['id'] + STREAM, row['feed'], verdict == 'working', verdict, detail)
        return verdict == 'working'

    def camera_control(self, action):
        dispatch = getattr(self, 'player_dispatch', None)
        if dispatch is None:
            raise ValueError('Camera playback controls require the desktop chat player.')
        return {'intelligence': True, 'camera_control': action, **dispatch(action)}

    def resources(self, query='', category='', region='World', integration=''):
        return resource_catalog(query, category, region, integration)

    def cameras(self, query='', bbox=None, limit=100, offset=0, available_only=False, feed='', near=None, radius_km=50, media='',
                near_event='', located_only=False, sort='', working_only=False):
        """Public cameras from every connected network, optionally the nearest ones to a point or event."""
        from collections import Counter
        from app.intelligence.view import distance_km
        if near_event:
            event = self.event_record(near_event)['event']
            if event.get('latitude') is None:
                raise ValueError('That event has no location, so no nearby cameras can be found.')
            near = [event['latitude'], event['longitude']]
        if type(available_only) is not bool:
            raise ValueError('available_only must be true or false.')
        if type(located_only) is not bool:
            raise ValueError('located_only must be true or false.')
        if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or not 0 <= offset <= 50000:
            raise ValueError('Use a camera limit of 1–1000 and offset of 0–50000.')
        if media not in ('', 'image', 'video', 'page', 'stream'):
            raise ValueError('media must be image, video, stream or page.')
        if sort not in ('', 'nearest', 'title', 'media', 'freshness'):
            raise ValueError('sort must be nearest, title, media or freshness.')
        point = None
        if near is not None:
            values = [finite(value) for value in near] if isinstance(near, (list, tuple)) and len(near) == 2 else [None]
            if any(value is None for value in values) or not -90 <= values[0] <= 90 or not -180 <= values[1] <= 180:
                raise ValueError('near must be [latitude, longitude].')
            radius = finite(radius_km)
            if radius is None or not 0 < radius <= 2000:
                raise ValueError('radius_km must be between 0 and 2000.')
            point = {'latitude': values[0], 'longitude': values[1]}
        if sort == 'nearest' and point is None:
            raise ValueError('Nearest camera sorting needs a point or event.')
        field = {'image': 'image_url', 'video': 'video_url', 'page': 'page_url'}.get(media)
        # One cached filtering pass over every camera network.
        _sources, matching = self._matching(query, feed, bbox, located_only, 'cameras')
        rows = [row for row in matching if (not available_only or row['details']['available'])
                and (not field or row['details'].get(field))
                and (media != 'stream' or camera_media_profile(row['details'])['stream'])]
        if point is not None:
            located = [(distance_km(point, row), row) for row in rows]
            rows = [{**row, 'distance_km': round(distance, 2)} for distance, row in sorted(
                (item for item in located if item[0] is not None and item[0] <= radius), key=lambda item: item[0])]
        order = sort or ('nearest' if point else 'title')
        if order == 'title':
            rows.sort(key=lambda row: (row['title'].casefold(), row['id']))
        elif order == 'media':
            def media_rank(row):
                profile = camera_media_profile(row['details'])
                return (not row['details'].get('available', False), not profile['stream'],
                        not profile['video_clip'], not row['details'].get('image_url'), row['title'].casefold(), row['id'])
            rows.sort(key=media_rank)
        elif order == 'freshness':
            from app.intelligence.objects import reported_at
            rows.sort(key=lambda row: (reported_at({'last_reported': row.get('retrieved_at')}), row['id']), reverse=True)
            rows.sort(key=lambda row: bool(row.get('stale')))
        # What HAYAT's own checks found (camera_health.py): failing cameras after working ones when choosing by
        # distance or media, and each returned camera labelled with its latest check.
        from app.intelligence.camera_health import failing, label, network_states
        health = self.store.camera_health()
        down = {key for key, state in network_states(self.store).items() if state['down']}
        def checked(row):
            """The camera's still record, else its stream record: what was last really seen of it."""
            return health.get(row['id']) or health.get(row['id'] + STREAM)
        if working_only:  # leave out what HAYAT saw fail recently (cameras never checked stay in)
            rows = [row for row in rows if not failing(checked(row)) and row['feed'] not in down]
        if order in ('nearest', 'media'):
            rows.sort(key=lambda row: failing(checked(row)) or row['feed'] in down)
        page = []
        for row in rows[offset:offset + limit]:
            record, stream = checked(row), health.get(row['id'] + STREAM)
            words = label(record) + (f"; video {label(stream)}" if stream and record is not stream else '')
            page.append({**row, 'health': words, 'working': None if record is None else bool(record['ok']) and not failing(record)})
        return {'intelligence': True, 'cameras': page, 'total_matching': len(rows),
                'sort': order,
                'next_offset': offset + limit if len(rows) > offset + limit else None,
                'networks': dict(Counter(row['details'].get('provider') or row['feed'] for row in rows).most_common()),
                'with_still_images': sum(bool(row['details'].get('image_url')) for row in rows),
                'with_video': sum(bool(row['details'].get('video_url')) for row in rows),
                'with_live_streams': sum(camera_media_profile(row['details'])['stream'] for row in rows),
                'with_video_clips': sum(camera_media_profile(row['details'])['video_clip'] for row in rows),
                'with_publisher_player': sum(bool(row['details'].get('page_url')) for row in rows),
                'basis': 'Public publisher camera networks in ' + CAMERA_COVERAGE + '. Availability is provider-reported, not proof of a current live frame.'}

    def points(self, query='', feed='', media=''):
        """Every located record as one compact marker, so the atlas can show the whole world at once.

        The atlas used to page full records 1,000 at a time and stop at 5,000,
        so a world view showed a quarter of the cameras -- whichever networks
        sorted first -- and whole continents looked empty. A marker needs only
        where it is, what it is and which media it offers.
        Each point is [id, latitude, longitude, 'c' camera | 'e' event, title, flags, category]
        with flags from: s HLS stream, v video clip, i still image, p publisher player, x stale,
        b camera whose last real check failed, ! high severity, + medium severity (events only).
        """
        if media not in ('', 'image', 'video', 'page', 'stream'):
            raise ValueError('media must be image, video, stream or page.')
        sources, rows = self._matching(query, feed, None, True)
        field = {'image': 'image_url', 'video': 'video_url', 'page': 'page_url'}.get(media)
        from app.intelligence.camera_health import failing
        health = self.store.camera_health()
        points = []
        for row in rows:
            camera = row['kind'] == 'public_camera'
            details = row.get('details') or {}
            if media and (not camera or (field and not details.get(field))):
                continue
            flags = ''
            if camera:
                profile = camera_media_profile(details)
                if media == 'stream' and not profile['stream']:
                    continue
                flags = ('s' if profile['stream'] else '') + ('v' if profile['video_clip'] else '') + \
                        ('i' if profile['image'] else '') + ('p' if details.get('page_url') else '')
                # b: HAYAT's last real check of this camera failed (camera_health.py); the atlas dims it.
                if failing(health.get(row['id']) or health.get(row['id'] + STREAM)):
                    flags += 'b'
            else:
                flags = {3: '!', 2: '+'}.get(row.get('severity_rank', 0), '')
            if row.get('stale'):
                flags += 'x'
            points.append([row['id'], round(row['latitude'], 5), round(row['longitude'], 5), 'c' if camera else 'e',
                           str(row.get('title') or '')[:90], flags, row.get('category') or ''])
            if len(points) >= 100_000:
                break
        return {'intelligence': True, 'points': points, 'total': len(points),
                'cameras': sum(point[3] == 'c' for point in points), 'sources': sources, 'monitor': self.status(),
                'basis': 'Every located provider record, compact. Open a marker for its full source evidence.'}

    def broken_cameras(self, ids, streams=False):
        """The ids among these whose last real check (of the still, or with `streams` of the video) failed recently,
        or whose whole network is down right now."""
        from app.intelligence.camera_health import failing, network_states
        ids = list(ids)
        if not ids:
            return set()
        suffix = STREAM if streams else ''
        records = self.store.camera_health([identifier + suffix for identifier in ids])
        down = {feed for feed, state in network_states(self.store).items() if state['down']}
        return {identifier for identifier in ids if failing(records.get(identifier + suffix)) or identifier.split(':', 1)[0] in down}

    def _still_works(self, event_id):
        try:
            value = self.camera_frame(event_id)
            return not value.get('publisher_card') and not value.get('stale')  # an old picture is not a working camera
        except Exception:  # camera_frame recorded why; the caller tries the next camera
            return False

    def camera_frame(self, event_id, fresh=False):
        """The camera's current still, checked and recorded (camera_health.py): a real picture, the publisher's
        'unavailable' card (returned, marked publisher_card), or an error (recorded, then raised)."""
        import time as _time

        row = self.event_record(event_id)['event']
        failures = self.__dict__.setdefault('_camera_failures', {})
        recent = failures.get(event_id)
        if recent and not fresh and _time.monotonic() - recent[0] < self.CAMERA_FAILURE_PAUSE:
            # The owner's log (2026-11-07/09): the same Caltrans still clicked 3-4 times, 25 s of waiting each time.
            stale = self._stale_still(event_id, recent[1])
            if stale:
                return stale
            wait = round(self.CAMERA_FAILURE_PAUSE - (_time.monotonic() - recent[0]))
            raise ValueError(f"This camera did not answer {round(_time.monotonic() - recent[0])} s ago ({recent[1]}); asking again "
                             f"now would most likely wait another 25 s. Try it again in about {wait} s, or choose another camera.")
        try:
            value = self._camera_frame(row, event_id, fresh)
        except Exception as exc:
            text = str(exc)
            verdict = 'web_page' if 'text/html' in text else 'error'
            if row['kind'] == 'public_camera' and camera_url(row['details'].get('image_url'), row['feed']):
                self.store.record_camera(event_id, row['feed'], False, verdict, f'{type(exc).__name__}: {text}'[:300])
            if verdict == 'web_page':
                raise ValueError(f"{row['details'].get('provider') or 'The publisher'} answered with a web page instead of this "
                                 "camera's picture: the image is offline, or the publisher refuses automated requests. HAYAT "
                                 "will try other cameras first for the next six hours.") from exc
            if isinstance(exc, ValueError):
                raise
            reason = f'{type(exc).__name__}: {text}'[:160] + self._route_hint()
            failures[event_id] = (_time.monotonic(), reason)
            # A health check (fresh) must see the failure itself, not an old picture standing in for it.
            stale = None if fresh else self._stale_still(event_id, reason)
            if stale:
                return stale
            raise
        failures.pop(event_id, None)
        if not value.get('cached'):
            self.store.record_camera(event_id, row['feed'], not value['publisher_card'],
                                     'card' if value['publisher_card'] else 'working')
        return value

    #: How long a camera that just failed (timeout, connection) is not asked again.
    CAMERA_FAILURE_PAUSE = 90

    @staticmethod
    def _route_hint():
        """A timeout through the privacy route is often the route: many camera publishers block or slow Tor exits."""
        try:
            from app.privacy_masks import ROUTE

            if ROUTE.snapshot()[0] is not None:
                return (' The privacy route (Tor) is on: many camera publishers block or slow Tor exits, so this may '
                        'work with the route off.')
        except Exception:  # no privacy module in a minimal setup: no hint
            return ''
        return ''

    def _stale_still(self, event_id, reason):
        """The last picture this camera gave, plainly marked as old, when it cannot give a new one now."""
        from app.intelligence.feeds import timestamp
        from app.intelligence.view import relative_time

        key = hashlib.sha256(event_id.encode()).hexdigest()[:32]
        record = self.store.preference('camera_preview_' + key)
        folder = self.store.path.parent / 'intelligence' / 'camera-previews'
        if not record:
            return None
        path = Path(record.get('path') or '')
        if not (path.is_file() and path.parent.resolve() == folder.resolve() and path.stem == key):
            return None
        age = relative_time(timestamp(record.get('fetched_at'))) or 'earlier'
        return {'intelligence': True, **record, 'cached': True, 'stale': True, 'stale_reason': reason,
                'basis': f"An older picture, fetched {age}: the camera did not answer just now ({reason}). It is not "
                         "the current view."}

    def _camera_frame(self, row, event_id, fresh):
        from PIL import Image
        from app.intelligence.manager import now
        if row['kind'] != 'public_camera':
            raise ValueError('Select a public camera event from the camera catalog.')
        url = camera_url(row['details'].get('image_url'), row['feed'])
        if not url:
            if row['details'].get('video_url'):
                raise ValueError('This camera publishes video, not stills. Ask "Play camera ' + event_id + '" or use Open in atlas.')
            if row['details'].get('page_url'):
                raise ValueError('This camera is viewed in the publisher\'s own player. Use Open publisher page.')
            raise ValueError('This camera has no supported public publisher image URL.')
        with self._camera_lock:
            key = hashlib.sha256(event_id.encode()).hexdigest()[:32]
            folder = self.store.path.parent / 'intelligence' / 'camera-previews'
            record = self.store.preference('camera_preview_' + key)
            if record:
                from app.intelligence.feeds import timestamp
                from datetime import datetime
                date = timestamp(record.get('fetched_at'))
                remaining = (datetime.fromisoformat(date) + timedelta(seconds=row['details']['refresh_seconds']) - now()).total_seconds() if date else 0
                cached_path = Path(record['path'])
                if not fresh and remaining > 0 and cached_path.parent.resolve() == folder.resolve() and cached_path.stem == key and cached_path.suffix in ('.jpg', '.png') and cached_path.is_file():
                    return {'intelligence': True, **record, 'cached': True, 'retry_after_seconds': round(remaining)}
            # Some publishers (Singapore) label JPEGs as octet-stream; the decoder below checks the real format.
            # Ottawa sends the non-standard image/jpg.
            if row['feed'].startswith('cameras_txdot_'):
                resource = self._txdot_snapshot(url)
            else:
                # NSW's published Victoria Pass JPEG is approximately 2.6 MB at
                # 5388 x 3592, and some City of Toronto stills pass 2 MB. Keep those exceptions bounded as well.
                maximum = (4 if row['feed'] in ('cameras_nsw', 'cameras_toronto') else 2) * 1024 * 1024
                # 25 s: Lyon's 500 KB stills timed out at the fetcher's 10 s under load (2026-11-06 audit).
                resource = self.fetcher.fetch(url, maximum, {'image/jpeg', 'image/jpg', 'image/png', 'image/gif',
                                                             'application/octet-stream'}, timeout=25)
            content = bytes(resource.content)
            with Image.open(BytesIO(content)) as picture:
                maximum_pixels = 20000000 if row['feed'] == 'cameras_nsw' else 10000000
                if picture.format not in ('JPEG', 'PNG', 'GIF') or picture.width * picture.height > maximum_pixels:
                    raise ValueError('Camera image format or dimensions exceed preview limits.')
                picture.verify()
            if content.startswith(b'GIF8'):
                # King County labels some stills image/jpeg that are GIFs; the preview keeps JPEG or PNG only.
                with Image.open(BytesIO(content)) as picture:
                    buffer = BytesIO()
                    picture.convert('RGB').save(buffer, 'PNG')
                content = buffer.getvalue()
            import cv2
            import numpy as np
            from app.intelligence.explore import placeholder
            decoded = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
            card = decoded is not None and placeholder(decoded)
            folder.mkdir(parents=True, exist_ok=True)
            destination = folder / (key + ('.png' if content.startswith(b'\x89PNG') else '.jpg'))
            partial = destination.with_suffix('.partial')
            partial.write_bytes(content)
            partial.replace(destination)
            for old in sorted(folder.glob('*.*'), key=lambda item: item.stat().st_mtime, reverse=True)[30:]:
                old.unlink(missing_ok=True)
            value = {'event_id': event_id, 'path': str(destination), 'fetched_at': resource.retrieved_at,
                     'image_url': resource.final_url, 'sha256': resource.sha256, 'publisher_card': card,
                     'basis': ("The publisher is showing its 'unavailable' card for this camera instead of the road; HAYAT "
                               "tries other cameras first for the next six hours." if card else
                               'Latest image retrieved from the public publisher. Fetch time is not verified capture time; '
                               'this may be a cached or delayed still.')}
            self.store.save_preference('camera_preview_' + key, value)
            self.store.trim_preferences('camera_preview_', 30)
            return {'intelligence': True, **value, 'cached': False}

    def _txdot_snapshot(self, url):
        """TxDOT returns {"snippet": base64 JPEG}; decoded here, then checked like any other image."""
        import base64
        from types import SimpleNamespace
        resource = self.fetcher.fetch(url, 4*1024*1024, {'application/json'})
        data = json.loads(resource.content)
        snippet = data.get('snippet') if isinstance(data, dict) else None
        if not isinstance(snippet, str) or not snippet:
            raise ValueError('TxDOT returned no snapshot for this camera right now.')
        content = base64.b64decode(snippet, validate=False)
        return SimpleNamespace(content=content, retrieved_at=resource.retrieved_at, final_url=resource.final_url,
                               sha256=hashlib.sha256(content).hexdigest())

    def weather(self, latitude, longitude):
        lat, lon = finite(latitude), finite(longitude)
        if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError('Use valid finite world coordinates.')
        from app.intelligence.manager import now
        from app.intelligence.feeds import timestamp
        from datetime import datetime
        key = f'weather_{lat:.3f}_{lon:.3f}'
        cached = self.store.preference(key)
        if cached and (now() - datetime.fromisoformat(timestamp(cached['fetched_at']))).total_seconds() < 600:
            return {**cached, 'cached': True}
        # MET Norway, not Open-Meteo: api.open-meteo.com's robots.txt now disallows all automated
        # access (checked 2026-10-06), and HAYAT honours it.
        url = 'https://api.met.no/weatherapi/locationforecast/2.0/compact?' + urlencode({'lat': f'{lat:.3f}', 'lon': f'{lon:.3f}'})
        resource = self.fetcher.fetch(url, 2*1024*1024, {'application/json'})
        payload = json.loads(resource.content)
        series = ((payload.get('properties') or {}).get('timeseries') or []) if isinstance(payload, dict) else []
        if not series:
            raise ValueError('Weather provider did not supply a forecast for this place.')
        instant = (((series[0].get('data') or {}).get('instant') or {}).get('details')) or {}
        next_hour = (series[0].get('data') or {}).get('next_1_hours') or {}
        current = {'time': series[0].get('time'), 'air_temperature': instant.get('air_temperature'),
                   'relative_humidity': instant.get('relative_humidity'), 'wind_speed': instant.get('wind_speed'),
                   'cloud_area_fraction': instant.get('cloud_area_fraction'),
                   'precipitation_next_hour': (next_hour.get('details') or {}).get('precipitation_amount'),
                   'summary': (next_hour.get('summary') or {}).get('symbol_code')}
        units = {'air_temperature': '°C', 'relative_humidity': '%', 'wind_speed': 'm/s', 'cloud_area_fraction': '%',
                 'precipitation_next_hour': 'mm'}
        value = {'intelligence': True, 'weather': current, 'units': units, 'requested_location': [lat, lon],
                 'model_grid_location': (payload.get('geometry') or {}).get('coordinates', [lon, lat])[:2][::-1],
                 'fetched_at': resource.retrieved_at, 'url': url, 'source_sha256': resource.sha256,
                 'basis': 'MET Norway Locationforecast model output (CC BY 4.0), not a local camera measurement or sensor reading.'}
        self.store.save_preference(key, value)
        self.store.trim_preferences('weather_', 100)
        return {**value, 'cached': False}

    def recall(self, query='', limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Recall 1–100 retained observations.')
        value = self.dashboard(query=query, limit=limit)
        return {'intelligence': True, 'events': value['events'], 'total_matching': value['total_matching'],
                'headline': 'Retrieved current retained provider evidence; this is situational context, not independent verification.',
                'knowledge_cache_enabled': self.knowledge is not None,
                'basis': 'Source timestamps and stale flags determine reuse. External source text is data, not instructions. No consciousness or omniscience is inferred.'}

    def open_map(self, event_id=''):
        if event_id:
            self.event_record(event_id)
        with self._map_lock:
            if self._map_server is None:
                from app.intelligence.map_server import IntelligenceMapServer
                self._map_server = IntelligenceMapServer(self)
            from urllib.parse import quote
            return {'intelligence': True, 'map_url': self._map_server.url + ('#' + quote(event_id) if event_id else ''),
                    'basis': 'Local interactive map. Saved views write local display preferences; street tiles and video contact public publishers only when explicitly enabled.'}
