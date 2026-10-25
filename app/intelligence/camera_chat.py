"""Chat requests for public camera playback, and choosing which camera plays.

Requests are parsed here without a model. On 2026-10-05 the owner listed London
cameras and replied "play"; nothing matched, the model answered "Playing the last
intelligence result…" and no player ever opened. Follow-ups such as "play it",
"play the second one" or "play camera 3" now resolve against the last verified
camera list in the session, and anything else falls through unchanged.

Bare "stop", "mute" and "unmute" stay voice controls, and "open camera" or
"show camera" stay the owner's own webcam: none of them names a public camera.
"""
from __future__ import annotations

import re
import time

from app.intelligence.providers import camera_media_profile

ORDINALS = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5, 'sixth': 6, 'seventh': 7,
            'eighth': 8, 'ninth': 9, 'tenth': 10, '1st': 1, '2nd': 2, '3rd': 3, '4th': 4, '5th': 5,
            '6th': 6, '7th': 7, '8th': 8, '9th': 9, '10th': 10}
NOUN = r'(?:camera|cameras|cam|cams|cctv|webcam|webcams|livestream|livestreams|stream|streams|feed|feeds)'
PLACE_NOUN = r'(?:camera|cameras|cam|cams|cctv|webcam|webcams)'
ADJECTIVE = r'(?:public|live|traffic|highway|road|street|city|cctv|webcam|real[- ]time|video)'
# Words that name the owner's own devices or places, never a publisher region.
NOT_PLACES = frozenset({'my', 'your', 'our', 'his', 'her', 'their', 'front', 'back', 'rear', 'security', 'phone',
                        'laptop', 'web', 'usb', 'local', 'this', 'that', 'it', 'the', 'a', 'an', 'me', 'home',
                        'house', 'door', 'doorbell', 'baby', 'room', 'office', 'next', 'another', 'other'})
# Places people say that the catalogs spell differently select a publisher network.
PLACE_FEEDS = {
    'luxembourg': ('cameras_luxembourg',), 'luxemburg': ('cameras_luxembourg',),
    'los angeles': ('cameras_caltrans_7',), 'la': ('cameras_caltrans_7',), 'l.a.': ('cameras_caltrans_7',),
    'san francisco': ('cameras_caltrans_4',), 'sf': ('cameras_caltrans_4',), 'bay area': ('cameras_caltrans_4',),
    'orange county': ('cameras_caltrans_12',), 'nyc': ('cameras_nyc',), 'new york city': ('cameras_nyc',),
    'uk': ('cameras_london',), 'england': ('cameras_london',), 'britain': ('cameras_london',),
    'bc': ('cameras_drivebc',), 'vancouver': ('cameras_drivebc',), 'washington state': ('cameras_washington', 'cameras_seattle'),
    'india': ('livestreams',), 'indian': ('livestreams',),
}
LIST_VERBS = ('show', 'open', 'start', 'view', 'load', 'bring up')
# Pronoun follow-ups ("play it") only refer to a camera list from the last half hour.
CONTEXT_SECONDS = 1800


LIVESTREAM_NAMES = {'makkah': 'makkah', 'mecca': 'makkah', 'makka': 'makkah', 'macca': 'makkah', 'haram': 'makkah',
                    'kaaba': 'makkah', 'masjid al haram': 'makkah', 'madinah': 'madinah', 'medina': 'madinah',
                    'madina': 'madinah', 'nabawi': 'madinah', 'masjid an nabawi': 'madinah', 'nasa': 'nasa_iss',
                    'iss': 'nasa_iss', 'space station': 'nasa_iss', 'international space station': 'nasa_iss'}
LIVESTREAM_NAMES.update({'somnath': 'somnath', 'siddhivinayak': 'siddhivinayak', 'shirdi': 'shirdi',
                         'sai baba': 'shirdi', 'saibaba': 'shirdi'})
# The owner's spellings (2026-10-10: "mekkah and madina cam" did nothing), common
# transliterations, and the mosques' and the Kaaba's own names.
LIVESTREAM_NAMES.update({name: 'makkah' for name in (
    'mekkah', 'mekka', 'mekah', 'meccah', 'makah', 'makkah al mukarramah', 'mecca sharif', 'makkah sharif',
    'kaba', 'kabah', 'ka bah', 'kaaba sharif', 'khana kaba', 'khana kaaba', 'baitullah', 'bait ullah',
    'masjid al haram', 'masjid ul haram', 'masjid e haram', 'masjidil haram', 'al haram', 'haram sharif', 'haram shareef',
    'grand mosque')})
LIVESTREAM_NAMES.update({name: 'madinah' for name in (
    'madeena', 'madeenah', 'medinah', 'medina sharif', 'madina sharif', 'madinah sharif', 'madina munawwara',
    'madinah al munawwarah', 'al madinah', 'masjid nabawi', 'masjid e nabawi', 'masjid an nabawi', 'masjid al nabawi',
    'masjid un nabawi', 'nabawi mosque', 'prophets mosque', 'prophet s mosque', 'the prophets mosque', 'roza mubarak',
    'green dome')})
LIVESTREAM_NAMES.update({
    'monterey bay aquarium': 'monterey_aquarium', 'monterey aquarium': 'monterey_aquarium', 'aquarium': 'monterey_aquarium',
    'jellyfish': 'monterey_aquarium', 'explore org': 'explore_nature', 'bear': 'explore_nature', 'bears': 'explore_nature',
    'brooks falls': 'explore_nature', 'katmai': 'explore_nature', 'nature': 'explore_nature', 'wildlife': 'explore_nature',
    'earthcam': 'earthcam', 'times square': 'earthcam', 'sen': 'sen_earth', 'earth from space': 'sen_earth',
    'earth': 'sen_earth', 'planet earth': 'sen_earth', 'jackson hole': 'jackson_hole', 'kilauea': 'kilauea',
    'volcano': 'kilauea', 'lava': 'kilauea', 'kilauea volcano': 'kilauea', 'news': 'news_aljazeera',
    'world news': 'news_aljazeera', 'news channel': 'news_aljazeera', 'live news': 'news_aljazeera', 'al jazeera': 'news_aljazeera', 'aljazeera': 'news_aljazeera',
    'sky news': 'news_sky', 'dw news': 'news_dw', 'dw': 'news_dw', 'deutsche welle': 'news_dw', 'abc news': 'news_abc',
    'nbc news': 'news_nbc', 'nbc': 'news_nbc', 'cbs news': 'news_cbs', 'cbs': 'news_cbs', 'wion': 'news_wion',
    'ndtv': 'news_ndtv', 'india today': 'news_indiatoday', 'trt world': 'news_trt', 'trt': 'news_trt',
    'cgtn': 'news_cgtn', 'euronews': 'news_euronews', 'nhk': 'news_nhk', 'nhk world': 'news_nhk'})
_NAMES = '|'.join(sorted(map(re.escape, LIVESTREAM_NAMES), key=len, reverse=True))
_KIND = r'(?:live|livestream|live stream|livecam|live cam|live camera|stream|tv|broadcast|video|feed|now|cam|camera|webcam|cctv|view)'
_LIVESTREAM = re.compile(r'(?:(?:play|watch|show|open|start|stream|put on|see|view)(?: me)?(?: the)? )?'
                         r'(?:(?:live )?(?:' + _KIND + r' )?(?:of |from |in |at )(?:the )?)?(?:live )?'
                         r'(?P<first>' + _NAMES + r')(?: (?:and|&|then|or) (?:the )?(?P<second>' + _NAMES + r'))?'
                         r'(?: ' + _KIND + r')*')


def livestream_requests(value):
    """The official livestreams the words ask for, in order (["makkah", "madinah"]), or []."""
    words = ' '.join(re.sub(r'[^a-z& ]', ' ', str(value or '').lower()).split())
    match = _LIVESTREAM.fullmatch(words)
    # A bare place name is a topic ("tell me about Makkah" never gets here; "Makkah" alone is
    # not a request to stream it). A viewing word makes it one.
    if not match or not re.search(r'\b(?:live|stream|broadcast|play|watch|cam|camera|webcam|livecam|cctv|tv|view|see)\b', words):
        return []
    found = [LIVESTREAM_NAMES[match['first']]]
    if match['second'] and LIVESTREAM_NAMES[match['second']] not in found:
        found.append(LIVESTREAM_NAMES[match['second']])
    return found


def livestream_request(value):
    """'makkah' / 'madinah' / 'nasa_iss' when the words ask for that official livestream, else None."""
    found = livestream_requests(value)
    return found[0] if found else None


def playback_mode(details):
    """stream, clip or still: what the chat card can show for this camera."""
    if details.get('live_page'):
        return 'stream'  # an official livestream, resolved when it is played
    profile = camera_media_profile(details)
    if details.get('video_url'):
        return 'stream' if profile['stream'] else 'clip'
    return 'still' if details.get('image_url') else None


def rank_cameras(rows, query, feed_titles):
    """The best-matching tier only, so a region never loses to a street with its name.

    Tier 0 names the network's region (Iowa → Iowa DOT); tier 1 is a whole word in
    the camera's own place fields; tier 2 is any substring. "Iowa" used to play a
    California camera on Iowa Avenue, and "Dover" a "Road Overcross".
    """
    phrase = ' '.join(str(query or '').casefold().split())
    if not phrase:
        return list(rows)
    word = re.compile(r'(?<![\w])' + re.escape(phrase) + r'(?![\w])')
    feeds = PLACE_FEEDS.get(phrase, ())
    tiers = ([], [], [])
    for row in rows:
        details = row['details']
        # Parenthetical operators are not places: "ALERTCalifornia (UC San Diego)" covers all of California.
        network = re.sub(r'\([^)]*\)', ' ', feed_titles.get(row['feed'], ''))
        region = ' '.join(str(value) for value in (network, details.get('country'), details.get('state'),
                                                   details.get('city'), details.get('region')) if value).casefold()
        place = ' '.join(str(value) for value in (row.get('title'), details.get('county'), details.get('nearby_place'),
                                                  details.get('road')) if value).casefold()
        if (row['feed'] in feeds and phrase not in ('india', 'indian')) or word.search(region) or (
                phrase in ('india', 'indian') and details.get('country') == 'India'):
            tiers[0].append(row)
        elif word.search(place):
            tiers[1].append(row)
        elif phrase in place or phrase in region:
            tiers[2].append(row)
    return next((tier for tier in tiers if tier), [])


def _place(text):
    """A publisher place from free words, or '' when the words name the owner's own camera."""
    words = re.sub(r'\b(?:the|a|an|some|any)\b', ' ', text)
    words = re.sub(r'\b' + ADJECTIVE + r'\b', ' ', words)
    words = ' '.join(words.split())
    if not words or len(words) > 40 or not re.fullmatch(r"[^\W\d_][\w .'’-]*", words):
        return ''
    return '' if set(words.split()) & NOT_PLACES else words


def _strip(normalized):
    """Drop courtesy and placement words; report whether 'in chat' was said."""
    value, in_chat = normalized, False
    while True:
        trimmed = re.sub(r'\s+(?:in (?:the )?chat|here|now|please|for me|right now)$', '', value)
        in_chat = in_chat or bool(re.search(r'\bin (?:the )?chat$', value))
        if trimmed == value:
            return value, in_chat
        value = trimmed


def _choose(cameras, index=None):
    """A camera from the last list: by position, else the first that can show video."""
    if index is not None:
        return cameras[index]['id'] if -len(cameras) <= index < len(cameras) else None
    for wanted in ('stream', 'clip', 'still'):
        for row in cameras:
            if row.get('mode') == wanted:
                return row['id']
    return None


def camera_request(text, session=None):
    """(tool, arguments) for a public camera playback request, or None to leave it to other routes."""
    cleaned = re.sub(r'^(?:hayat[,.!]?\s+)?(?:please\s+)?(?:(?:can|could|would|will) you\s+)?(?:please\s+)?', '',
                     text.strip(' .!?'), flags=re.IGNORECASE)
    normalized = ' '.join(cleaned.lower().split())
    if not normalized or re.search(r'https?://', normalized):
        return None
    value, in_chat = _strip(normalized)
    # Bare words ("next", "pause", "play it") only mean a camera while camera talk is recent.
    fresh = time.monotonic() - float(getattr(session, 'camera_context_at', 0) or 0) < CONTEXT_SECONDS
    active = getattr(session, 'active_camera', None) if fresh else None
    cameras = list(getattr(session, 'last_cameras', None) or []) if fresh else []
    listed_last = cameras and getattr(session, 'camera_context', '') == 'list'

    if streams := livestream_requests(value):
        # The second of "Makkah and Madinah" is first in Next camera (see camera_play).
        return 'intelligence_camera_play', {'event_id': 'livestreams:' + streams[0], 'live_only': True,
                                            **({'query': 'then ' + streams[1]} if len(streams) > 1 else {})}
    if match := re.fullmatch(r'(?:play|watch)(?: the)? (?:public )?(live )?(?:camera |cctv |webcam |cam )?(cameras_[\w.-]+:[\w.:-]+)', cleaned, re.IGNORECASE):
        return 'intelligence_camera_play', {'event_id': match[2], 'live_only': bool(match[1])}
    if match := re.fullmatch(r'(pause|stop|resume|mute|unmute)(?: the)? (?:live |public )?(?:camera|cctv|webcam|cam)(?: stream| playback| video| feed)?', value):
        return 'intelligence_camera_control', {'action': 'play' if match[1] == 'resume' else match[1]}
    if re.fullmatch(r'(?:(?:show|play|try|switch to|go to)(?: me)? )?(?:the )?(?:next|another|a different|other)'
                    r'(?: one| (?:public |live )?(?:camera|cam|stream|feed|cctv))?', value) \
            and (active or re.search(r'\b(?:camera|cam|stream|feed|cctv)\b', value)):
        return 'intelligence_camera_control', {'action': 'next'}
    if active and not listed_last and re.fullmatch(r'(?:pause|resume|continue|play|replay)(?: it| this| the (?:camera|stream|video|feed|cam|cctv|clip))?(?: again)?', value):
        return 'intelligence_camera_control', {'action': 'pause' if value.startswith('pause') else 'play'}
    if active and (match := re.fullmatch(r'(stop|mute|unmute)(?: it| this| the (?:camera|stream|video|feed|cam|cctv))', value)):
        return 'intelligence_camera_control', {'action': match[1]}

    verb = re.match(r'(play|watch|stream|show|open|start|view|load|bring up)(?: me)?(?: |$)', value)
    if not verb:
        return None
    rest = value[verb.end():].strip()
    live = bool(re.search(r'\blive\b', value))

    if cameras:
        position = None
        if (match := re.fullmatch(r'(?:the )?(\w+)(?: (?:one|camera|cam|stream|feed))?', rest)) and (match[1] in ORDINALS or match[1] == 'last'):
            position = -1 if match[1] == 'last' else ORDINALS[match[1]] - 1
        if match := re.fullmatch(r'(?:the )?(?:(?:camera|cam|number|no\.?|option) ?|#)?(\d{1,3})(?:st|nd|rd|th)?(?: one)?', rest):
            position = int(match[1]) - 1
        if position is not None and position >= -1:
            chosen = _choose(cameras, position)
            return ('intelligence_camera_play', {'event_id': chosen, 'live_only': False}) if chosen else None
        pronoun = re.fullmatch(r'(?:it|this|that|them|this one|that one|any|any one|one of them)?', rest)
        generic = re.fullmatch(r'(?:the |a |one )?(?:live )?(camera|cam|stream|feed|video|cctv|footage)(s?)(?: live)?', rest)
        # "show live CCTV" still lists; "open camera" and "start camera" stay the owner's webcam.
        if generic and verb[1] not in ('play', 'watch', 'stream') and (verb[1] in ('open', 'start', 'load') or generic[2] or generic[1] == 'cctv'):
            generic = None
        if pronoun and verb[1] in ('play', 'watch', 'stream') or generic and listed_last:
            chosen = _choose(cameras)
            return ('intelligence_camera_play', {'event_id': chosen, 'live_only': False}) if chosen else None
        target = re.sub(r'(?: live)?(?: (?:camera|cam|stream|feed))?$', '', re.sub(r'^the ', '', rest)).strip()
        if len(target) >= 3:
            titled = [row['id'] for row in cameras if target in str(row.get('title', '')).casefold()]
            if titled:
                return 'intelligence_camera_play', {'event_id': titled[0], 'live_only': False}

    # Form A: "play live traffic camera in Delaware", "show me a live stream from Iowa".
    match = re.fullmatch(r'(?:(?:the|a|an|some|any|one) )?(?:' + ADJECTIVE + r' )*(?P<noun>' + NOUN + r')'
                         r'(?: live)?(?: (?:in|from|for|at|of|near|around|on) (?P<query>.+?))?(?: live)?', rest)
    if match:
        noun, query = match['noun'], _place(match['query'] or '')
        if match['query'] and not query:
            return None  # "play the camera in my office" is the owner's own device, not a publisher.
        if noun.startswith(('stream', 'feed', 'livestream')) and not (live or re.search(r'\b(?:traffic|cctv|webcam|highway|road|public)\b', rest)):
            return None
        if verb[1] in LIST_VERBS and not (in_chat or ((live or query) and not noun.endswith('s') and noun != 'cctv')):
            return None  # "show live cameras" lists them; "open camera" is the owner's webcam.
        arguments = {'live_only': live or not query}
        if query:
            arguments['query'] = query
        return 'intelligence_camera_play', arguments

    # Form B: "play london camera", "watch the Delaware live traffic cam".
    match = re.fullmatch(r'(?:(?:the|a|an|some|any) )?(?P<query>.+?) (?:' + ADJECTIVE + r' )*(?P<noun>' + PLACE_NOUN + r')(?: live)?', rest)
    if not match or verb[1] in LIST_VERBS and not (live or in_chat) or verb[1] in LIST_VERBS and match['noun'].endswith('s') and not in_chat:
        return None
    query = _place(match['query'])
    return ('intelligence_camera_play', {'live_only': live, 'query': query}) if query else None


def remember_cameras(rows):
    """The compact list a follow-up like "play the second one" refers to."""
    return [{'id': row['id'], 'title': str(row.get('title', ''))[:120], 'mode': playback_mode(row.get('details') or {})}
            for row in rows[:25] if row.get('id')]
