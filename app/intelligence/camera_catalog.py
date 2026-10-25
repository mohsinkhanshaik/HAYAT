"""Choose bounded camera-catalog updates from registered publishers only."""
from datetime import UTC, datetime
import re

from app.intelligence.camera_chat import PLACE_FEEDS
from app.intelligence.feeds import ALL_FEEDS, configured, timestamp


def refresh_candidates(query='', feed='', states=None, limit=8, now=None):
    states = states or {}
    now = now or datetime.now(UTC)
    cameras = [item for item in ALL_FEEDS if (item.key.startswith('cameras_') or item.key == 'livestreams') and configured(item)]
    phrase = ' '.join(query.casefold().split())
    aliases = PLACE_FEEDS.get(phrase, ())
    words = re.findall(r'\w+', phrase)
    if feed:
        cameras = [item for item in cameras if item.key == feed]
    elif words:
        cameras = [item for item in cameras if item.key in aliases
                   or all(word in (item.title + ' ' + item.key).casefold() for word in words)
                   or phrase == 'california' and item.key.startswith('cameras_caltrans_')]
    due = []
    for item in cameras:
        state = states.get(item.key, {})
        when = timestamp(state.get('next_attempt_at'))
        if when and datetime.fromisoformat(when) > now:
            continue
        due.append(item)
    # Unloaded networks first, then the oldest successful catalog. Refreshing
    # again advances through the registered catalog without one huge download.
    due.sort(key=lambda item: (bool(states.get(item.key, {}).get('last_success_at')),
                               states.get(item.key, {}).get('last_success_at') or '', item.key))
    return [item.key for item in due[:limit]]
