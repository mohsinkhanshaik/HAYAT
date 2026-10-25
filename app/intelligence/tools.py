"""Registry, deterministic chat routes and truthful model-free delivery."""
import json
import re


TOOL_NAMES = ('intelligence_dashboard', 'intelligence_refresh', 'intelligence_watch',
              'intelligence_alerts', 'intelligence_brief', 'intelligence_monitor', 'intelligence_history',
              'intelligence_event', 'intelligence_assess', 'intelligence_preferences', 'intelligence_views', 'intelligence_cameras',
              'intelligence_camera_frame', 'intelligence_camera_play', 'intelligence_camera_control',
              'intelligence_resources', 'intelligence_weather', 'intelligence_recall', 'intelligence_map', 'intelligence_analyze',
              'intelligence_around', 'intelligence_changes', 'intelligence_best_camera', 'intelligence_sources',
              'intelligence_camera_look', 'intelligence_aircraft', 'intelligence_positions',
              'intelligence_region_changes', 'intelligence_objects', 'intelligence_correlate',
              'intelligence_anomalies', 'intelligence_search', 'intelligence_deep_look')
# Every tool a chat sentence may route to here, including launches, satellites, maps and routes (explore.py).
from app.intelligence.explore import EXPLORE_TOOLS  # noqa: E402
MY_CAMERA_TOOLS = ('my_camera_list', 'my_camera_add', 'my_camera_remove', 'my_camera_play')
# The radar answers "am I safe?" / "what's happening around me"; without it here the router never offered it.
ROUTED_NAMES = TOOL_NAMES + EXPLORE_TOOLS + MY_CAMERA_TOOLS + ('intelligence_radar',)


def register_intelligence_tools(registry, manager):
    from app.intelligence.classify import CATEGORIES, SEVERITY_RANK
    from app.intelligence.feeds import BY_KEY
    from app.intelligence.providers import CAMERA_COVERAGE
    obj = lambda props, required=None: {'type': 'object', 'properties': props, 'required': required or [], 'additionalProperties': False}
    filters = {'query': {'type': 'string', 'maxLength': 500},
               'feed': {'type': 'string', 'enum': ['', *BY_KEY]},
               'bbox': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 4, 'maxItems': 4}}
    # Source-derived categories, severity rules and an event-time window.
    refine = {'category': {'type': 'string', 'enum': ['', *CATEGORIES]},
              'min_severity': {'type': 'string', 'enum': ['', *SEVERITY_RANK]},
              'since_hours': {'type': 'number', 'minimum': 0.1, 'maximum': 8784}}
    registry.register('intelligence_dashboard', 'Read cached public-feed events, map coordinates, source freshness, local alerts and monitor status; filter by category, minimum source-derived severity and recent hours; does not fetch.',
        obj({**filters, **refine, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 1000},
             'offset': {'type': 'integer', 'minimum': 0, 'maximum': 50000}, 'located_only': {'type': 'boolean'}}), 'low', 'filesystem_read', manager.dashboard)
    registry.register('intelligence_refresh', 'Refresh registered public USGS, NASA, CISA, NOAA, GDACS, TfL and Caltrans catalogs; preserve failed snapshots, source cooldowns and local watch alerts.',
        obj({'feeds': {'type': 'array', 'items': {'enum': list(BY_KEY)}, 'minItems': 1, 'maxItems': len(BY_KEY), 'uniqueItems': True},
             'due_only': {'type': 'boolean'}}), 'low', 'public_web_read', manager.refresh)
    registry.register('intelligence_watch', 'Save/list/delete local feed, keyword or bounding-box watch rules. bbox is [west,south,east,north]; west>east wraps the dateline. No messages are sent.',
        obj({'action': {'enum': ['save', 'list', 'delete']}, 'key': {'type': 'string', 'maxLength': 120}, 'data': {'type': 'object'}}),
        'low', 'filesystem_write_project', manager.watch)
    registry.register('intelligence_alerts', 'Read deduplicated local alerts from new or changed public feed events matching watch rules.',
        obj({**filters, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 1000},
             'offset': {'type': 'integer', 'minimum': 0, 'maximum': 1000}}), 'low', 'filesystem_read', manager.alerts)
    registry.register('intelligence_brief', 'Create a source-linked factual brief from current cached events, watch matches and freshness gaps; no invented forecast or cause.',
        obj(filters), 'low', 'filesystem_read', manager.brief)
    registry.register('intelligence_monitor', 'Read/start/stop the autonomous public-feed monitor; refreshes and local alerts need no human approval per cycle.',
        obj({'action': {'enum': ['status', 'start', 'stop']}}), 'low', 'knowledge_research',
        lambda action='status': control(manager, action))
    registry.register('intelligence_history', 'Read the last 12 retained change snapshots for a public feed.',
        obj({'feed': {'enum': list(BY_KEY)}}, ['feed']), 'low', 'filesystem_read',
        lambda feed: {'intelligence': True, 'feed': feed, 'snapshots': manager.store.history(feed)})
    registry.register('intelligence_event', 'Inspect a source event deeply: provider facts, location, fetch freshness, distinct retained revisions, matching watches and nearby observations. Nearby does not imply causation.',
        obj({'event_id': {'type': 'string', 'maxLength': 220}}, ['event_id']), 'low', 'filesystem_read', manager.event_record)
    from app.intelligence.view import REGIONS
    registry.register('intelligence_assess', 'Analyze every matching cached world or regional observation, feed counts, locations, source changes and coverage gaps. Separate evidence from inference; no fabricated world coverage or forecast.',
        obj({**filters, **refine, 'region': {'type': 'string', 'enum': list(REGIONS)}}), 'low', 'filesystem_read', manager.assessment)
    registry.register('intelligence_analyze', 'Ask the local model for a short Summary / Risk / What to watch reading of one intelligence event, grounded only in its retained evidence. Inference is labelled; nothing is verified or forecast.',
        obj({'event_id': {'type': 'string', 'maxLength': 220}}, ['event_id']), 'low', 'filesystem_read', manager.analyze)
    registry.register('intelligence_preferences', 'Read or save local intelligence map/home preferences: region, query, zoom, center, compact/detailed alerts and home visibility. Does not change source permissions or monitor authority.',
        obj({'action': {'enum': ['get', 'save']}, 'data': {'type': 'object'}}), 'low', 'filesystem_write_project', manager.preferences)
    registry.register('intelligence_views', 'List, save, open or delete named intelligence views (region, search, category, severity, time range, feed). Saving records the sources that backed the view; opening re-checks the same filters now and reports what changed. Display state only; no source permissions change.',
        obj({'action': {'enum': ['list', 'save', 'open', 'delete']}, 'name': {'type': 'string', 'maxLength': 80},
             'data': {'type': 'object'}}), 'low', 'filesystem_write_project', manager.named_views)
    registry.register('intelligence_cameras', f'Search public cameras across {CAMERA_COVERAGE}, plus keyed 511/WSDOT APIs once the owner adds free keys, by keywords, bounds, media type, or nearest to a point or event. Provider availability is not proof of a fresh live frame.',
        obj({**filters, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 1000},
             'offset': {'type': 'integer', 'minimum': 0, 'maximum': 50000}, 'available_only': {'type': 'boolean'},
             'near': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 2, 'maxItems': 2},
             'near_event': {'type': 'string', 'maxLength': 220},
             'sort': {'enum': ['nearest', 'title', 'media', 'freshness']},
             'radius_km': {'type': 'number', 'minimum': 0.1, 'maximum': 2000},
             'media': {'type': 'string', 'enum': ['', 'image', 'video', 'stream', 'page']}}), 'low', 'filesystem_read', manager.cameras)
    registry.register('intelligence_camera_frame', 'Retrieve the selected registered public camera’s latest JPEG/PNG with publisher cooldowns, bounded storage and provenance. No private camera discovery or recognition; fetch time is not capture time.',
        obj({'event_id': {'type': 'string', 'maxLength': 220}}, ['event_id']), 'low', 'public_web_read', manager.camera_frame)
    registry.register('intelligence_camera_play', 'Prepare an available registered public camera for inline desktop chat playback: a responding live HLS stream first, else the publisher clip or refreshing still, with alternatives for Next camera. Refresh missing catalogs with cooldowns. No arbitrary URL, private CCTV, or recording.',
        obj({'event_id': {'type': 'string', 'maxLength': 220}, 'query': filters['query'],
             'live_only': {'type': 'boolean'}, 'refresh': {'type': 'boolean'}, 'still_only': {'type': 'boolean'}}),
        'low', 'public_web_read', manager.camera_play)
    registry.register('intelligence_camera_control', 'Play, pause, stop, mute/unmute or switch to the next matching public camera in desktop chat. Stop releases the network player; it can be reopened with a camera playback request.',
        obj({'action': {'enum': ['play', 'pause', 'stop', 'next', 'mute', 'unmute']}}, ['action']), 'low', 'filesystem_read', manager.camera_control)
    registry.register('intelligence_resources', 'Discover curated world camera directories, disaster/weather/satellite resources and open-source libraries. Distinguish connected feeds, external directories and required API keys.',
        obj({'query': filters['query'], 'category': {'type': 'string'}, 'region': {'enum': list(REGIONS)},
             'integration': {'enum': ['', 'connected', 'directory', 'key_required', 'on_demand', 'bundled']}}), 'low', 'filesystem_read', manager.resources)
    registry.register('intelligence_weather', 'Read bounded MET Norway model forecasts at a selected coordinate (CC BY 4.0). Cache for ten minutes; distinguish model output from local observation.',
        obj({'latitude': {'type': 'number', 'minimum': -90, 'maximum': 90}, 'longitude': {'type': 'number', 'minimum': -180, 'maximum': 180}}, ['latitude', 'longitude']), 'low', 'public_web_read', manager.weather)
    registry.register('intelligence_recall', 'Recall retained current public observations with source times and stale flags. Feed sources are also cached with expiry for Hayat’s research context; source text never becomes instructions or verified claims.',
        obj({'query': filters['query'], 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}), 'low', 'filesystem_read', manager.recall)
    registry.register('intelligence_map', 'Open a token-addressed local interactive Leaflet atlas with offline land, optional street tiles, clustered cameras and explicit snapshot/video controls. Returns a local URL; no camera media loads automatically.',
        obj({'event_id': {'type': 'string', 'maxLength': 220}}), 'low', 'filesystem_read', manager.open_map)


def control(manager, action):
    if action == 'start':
        manager.enabled = True
        manager.start()
    elif action == 'stop':
        stopped = manager.stop()
        return {'intelligence': True, **manager.status(), 'stopped': stopped}
    return {'intelligence': True, **manager.status()}


def direct_plan(text, names, session=None):
    """A deterministic intelligence step, or None. `session` lets "play the second one" refer to the last camera list."""
    value = text.strip()
    exact = re.fullmatch(r'(?:use\s+|run\s+)?(intelligence_[a-z_]+)\s*(\{.*\})?', value, re.DOTALL)
    tool, arguments, refresh = None, {}, None
    if exact and exact[1] in TOOL_NAMES:
        tool, arguments = exact[1], json.loads(exact[2]) if exact[2] else {}
        if not isinstance(arguments, dict):
            raise ValueError('Intelligence tool arguments must be a JSON object.')
    else:
        normalized = ' '.join(value.lower().strip(' .!?').split())
        cleaned = re.sub(r'^(?:hayat[, ]+)?(?:please )?(?:(?:can|could|would) you )?', '', value.strip(' .!?'), flags=re.IGNORECASE)
        normalized = ' '.join(cleaned.lower().split())
        from app.intelligence.camera_chat import camera_request
        from app.intelligence.camera_eyes import look_request
        from app.intelligence.fusion import fusion_request
        from app.intelligence.osint import osint_request
        from app.intelligence.world_requests import world_request
        from app.intelligence.explore import explore_request
        from app.intelligence.deep import deep_request
        from app.intelligence.my_cameras import my_camera_request
        if (mine := my_camera_request(value)) and mine['tool'] in names:
            # "play my front door camera" is the owner's camera, never a public one with that name.
            tool, arguments = mine['tool'], mine['arguments']
        elif (look := look_request(value)) and look['tool'] in names:
            tool, arguments = look['tool'], look['arguments']
        elif camera := camera_request(value, session):
            tool, arguments = camera
        elif (osint := osint_request(cleaned)) and osint['tool'] in names:
            tool, arguments = osint['tool'], osint['arguments']
            if arguments.get('object_id') == '@last':
                # Resolve against an observed result in this conversation only.
                from app.intelligence.osint import last_object_id
                object_id = last_object_id(session)
                if not object_id:
                    return None
                arguments['object_id'] = object_id
        elif (deep := deep_request(value)) and deep['tool'] in names:
            tool, arguments = deep['tool'], deep['arguments']
        elif (fusion := fusion_request(value)) and fusion['tool'] in names:
            tool, arguments = fusion['tool'], fusion['arguments']
        elif (world := world_request(value)) and world['tool'] in names:
            tool, arguments = world['tool'], world['arguments']
            refresh = world.get('refresh') if 'intelligence_refresh' in names else None
        elif (explore := explore_request(value)) and explore['tool'] in names:
            tool, arguments = explore['tool'], explore['arguments']
        elif re.fullmatch(r'(?:show|open)(?: the)? (?:world atlas|interactive intelligence map|world map)', normalized):
            tool = 'intelligence_map'
        elif (match := re.fullmatch(r'(?:show|list|find)(?: me)?(?: the)? (?:public )?(?:live |hls )?(?:camera |cctv )?streams(?: (?:in|for) (.+))?', value.strip(' .!?'), re.IGNORECASE)):
            tool, arguments = 'intelligence_cameras', {'media': 'stream', 'available_only': True}
            if match[1]:
                arguments['query'] = match[1]
        elif re.fullmatch(r'(?:show|list|find)(?: me)?(?: the)? (?:public |live )?(?:cameras|cctv|webcams)', normalized):
            tool = 'intelligence_cameras'
        elif (match := re.fullmatch(r'(?:show|list|find)(?: me)?(?: the)? (?:nearest |closest )?(?:public |live )?(?:cameras?|cctv|webcams?) (?:near|around|closest to) '
                                    r'(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)(?: within (\d+(?:\.\d+)?) ?km)?', value.strip(' .!?'), re.IGNORECASE)):
            tool, arguments = 'intelligence_cameras', {'near': [float(match[1]), float(match[2])], 'radius_km': float(match[3] or 50), 'limit': 20}
        elif (match := re.fullmatch(r'(?:show|list|find)(?: me)?(?: the)? (?:nearest |closest )?(?:public |live )?(?:cameras?|cctv|webcams?) (?:near|around|closest to) '
                                    r'(?:intelligence )?event (\S+)(?: within (\d+(?:\.\d+)?) ?km)?', value.strip(' .!?'), re.IGNORECASE)):
            tool, arguments = 'intelligence_cameras', {'near_event': match[1], 'radius_km': float(match[2] or 100), 'limit': 20}
        elif (match := re.fullmatch(r'(?:show|list|find)(?: me)? (?:public |live )?(?:cameras|cctv|webcams) (?:in|for|about) (.+)', value.strip(' .!?'), re.IGNORECASE)):
            tool, arguments = 'intelligence_cameras', {'query': match[1]}
        elif re.fullmatch(r'(?:show|list|find)(?: the)? (?:intelligence resources|world resources|camera directories)', normalized):
            tool = 'intelligence_resources'
        elif (match := re.fullmatch(r'(?:recall|remember|explain) (?:world observations|intelligence knowledge)(?: about (.+))?', value.strip(' .!?'), re.IGNORECASE)):
            tool, arguments = 'intelligence_recall', {'query': match[1] or ''}
        elif re.fullmatch(r'(?:show|open|check)(?: me)?(?: my| the)? (?:global intelligence|intelligence dashboard|osiris(?: status)?)', normalized):
            tool = 'intelligence_dashboard'
        elif re.fullmatch(r'(?:refresh|update)(?: my| the)? (?:global intelligence|intelligence feeds|osiris)', normalized):
            tool = 'intelligence_refresh'
        elif re.fullmatch(r'(?:show|list)(?: me)?(?: my| the)? intelligence alerts', normalized):
            tool = 'intelligence_alerts'
        elif re.fullmatch(r'(?:show|create|build|generate)(?: me)?(?: my| the)? (?:intelligence brief|osiris (?:brief|playbook|plan))', normalized):
            tool = 'intelligence_brief'
        elif (match := re.fullmatch(r'(start|stop|check)(?: the)? intelligence monitor', normalized)):
            tool, arguments = 'intelligence_monitor', {'action': {'check': 'status'}.get(match[1], match[1])}
        elif re.fullmatch(r'(?:analyze|analyse|explain)(?: the)? (?:global intelligence|world intelligence|world situation)', normalized) or normalized in ('what is happening around the world', 'what is happening in the world'):
            tool = 'intelligence_assess'
        elif (match := re.fullmatch(r'(?:analyze|analyse|explain) intelligence (?:in|for) (.+)', normalized)):
            from app.intelligence.view import REGIONS
            region = next((region for region in REGIONS if region.casefold() == match[1]), None)
            if region:
                tool, arguments = 'intelligence_assess', {'region': region}
        elif (match := re.fullmatch(r'(?:analyze|analyse|explain) intelligence about (.+)', value.strip(' .!?'), re.IGNORECASE)):
            tool, arguments = 'intelligence_assess', {'query': match[1]}
        elif (match := re.fullmatch(r'(?:analy[sz]e|assess) intelligence event (.+?)(?: with hayat)?', value.strip(' .!?'), re.IGNORECASE)):
            tool, arguments = 'intelligence_analyze', {'event_id': match[1]}
        elif (match := re.fullmatch(r'(?:explain|inspect|show) intelligence event (.+)', value.strip(' .!?'), re.IGNORECASE)):
            tool, arguments = 'intelligence_event', {'event_id': match[1]}
    if tool not in names:
        return None
    if tool == 'intelligence_cameras' and re.search(r'(?i)\blive\b', value):
        arguments.update(media='stream', available_only=True)
    step = {'description': 'Run the independent public intelligence workflow and report evidence and freshness.',
            'tool': tool, 'arguments': arguments, 'expected': {'type': 'tool_success'}, 'max_retries': 0}
    if refresh:
        # Read by the planner as a first step: fetch these feeds (cooldowns apply).
        step['refresh_first'] = list(refresh)
    return step


_CATALOG = re.compile(r'cameras_|livestreams$')


def refresh_line(value):
    """What fetching the feeds behind an answer did, in one line."""
    if value.get('refresh_in_progress'):
        return 'Feeds are already being refreshed in the background; the answer uses the latest stored reports.'
    parts = []
    for source in value.get('sources', []):
        if source.get('cached'):
            parts.append(f"{source['feed']} recently fetched")
        elif source.get('status') == 'ok':
            parts.append(f"{source['feed']} updated ({source.get('count', 0)} items)")
        else:
            parts.append(f"{source['feed']} could not be read ({str(source.get('error') or source.get('status'))[:80]})")
    line = 'Checked public feeds first: ' + '; '.join(parts) + '.'
    if value.get('failed') and value.get('failed') == len(value.get('sources', [])):
        line += ' None could be read, so the answer below is from older stored reports and may miss recent events.'
    return line


def report(history):
    sections = []
    # A repeated read of the same scope supersedes earlier reads in this plan.
    # Different categories/places/queries remain separate requested results.
    selected, seen = [], set()
    for event in reversed(history or []):
        if not (event.get('result') or {}).get('success') or not (event.get('verification') or {}).get('passed'):
            continue
        arguments = event.get('arguments')
        if event.get('tool') in {'intelligence_objects', 'intelligence_search'} and isinstance(arguments, dict):
            scope = {key: value for key, value in arguments.items()
                     if key != 'limit' and value not in (None, '')}
            key = (event['tool'], json.dumps(scope, sort_keys=True, default=str))
            if key in seen:
                continue
            seen.add(key)
        selected.append(event)
    for event in reversed(selected):
        if event.get('tool') in MY_CAMERA_TOOLS and (event.get('result') or {}).get('success'):
            from app.intelligence.my_cameras import my_camera_report
            sections.append(my_camera_report(event['tool'], event['result'].get('output') or {}))
            continue
        if event.get('tool') in EXPLORE_TOOLS and (event.get('result') or {}).get('success'):
            from app.intelligence.explore import explore_report
            if reading := explore_report(event['result'].get('output') or {}):
                sections.append(reading)
            continue
        if event.get('tool') not in TOOL_NAMES or not (event.get('result') or {}).get('success') or not (event.get('verification') or {}).get('passed'):
            continue
        value = event['result'].get('output') or {}
        if event.get('tool') == 'intelligence_refresh' and any(other.get('tool') != 'intelligence_refresh' for other in selected):
            # A refresh made only so the answer below is current: one line, not every feed's changes.
            sections.append(refresh_line(value))
            continue
        from app.intelligence.deep import deep_report
        from app.intelligence.osint import osint_report
        reading = deep_report(value) or osint_report(value)
        if reading:
            sections.append(reading)
            continue
        lines = ['Hayat intelligence']
        if value.get('fusion'):
            lines.append(value['interpretation'])
            if value.get('changes_only') and not value.get('changes'):
                lines.append('No comparable earlier observation is available for this place yet.')
            lines.extend(f"Camera: {row['title']} ({row['id']}) · {row['why']}" for row in value.get('cameras') or [])
            lines.append(value['basis'])
            sections.append('\n'.join(lines))
            continue
        if value.get('camera_look'):
            lines.append(f"Looked at {value['title']} ({value['provider']}): {value['picture']['method']}, fetched {value['picture']['fetched_at']}.")
            lines.append(value['seen'] or 'The vision model returned no description.')
            if value.get('previous'):
                lines.append(f"Last look ({value['previous']['at']}): {value['previous']['seen'][:600]}")
            lines.append(value['basis'])
            sections.append('\n'.join(lines))
            continue
        if value.get('source_catalog'):
            lines.append(f"{value['count']} public sources available.")
            lines.extend(f"{row['title']}: {row['sources']} sources · {row['why']}" for row in value['types'])
            lines.extend(f"{row['title']} · {row['health']} · {row['authentication']}\n  {row['documentation']}"
                         for row in value['sources'])
            lines.append(value['basis'])
            sections.append('\n'.join(lines))
            continue
        if value.get('camera_playback'):
            shown = {'stream': 'live stream', 'clip': 'latest recorded clip', 'still': 'refreshing still image'}.get(value.get('mode'), 'video')
            lines.extend(['Opening public camera in desktop chat: ' + value['title'],
                          f"{value['media_label']} · {value['provider']} · showing the {shown}"])
            if value.get('notice'):
                lines.append(value['notice'])
            if value.get('alternatives'):
                lines.append(f"{len(value['alternatives'])} more cameras here: say \"next camera\" or press Next camera.")
            lines.extend([value['video_url'] or value.get('source_url', ''), value['basis']])
        if value.get('camera_control'):
            lines.append('Camera player accepted: ' + value['camera_control']
                         + (' · switching to ' + value['title'] if value.get('title') else ''))
        if value.get('headline'):
            lines.append(value['headline'])
        if value.get('event_summary'):
            lines.append(value['event_summary'])
            lines.append('Present in current feed: ' + str(value['in_current_feed']))
            lines.append(f"{len(value['retained_revisions'])} distinct retained revisions; {len(value['matching_watches'])} matching watches; {value['nearby_count']} observations within 300 km.")
            for nearby in value['nearby_events'][:5]:
                lines.append(f"• {nearby['title']} · {nearby['distance_km']} km · {nearby.get('observed_at') or 'time unknown'}\n  {nearby['url']}")
            if value.get('assessment'):
                judged = value['assessment']
                lines.append(judged['summary'])
                lines.extend(f"{name.capitalize()}: {judged[name]['label']} — {judged[name]['basis']}" for name in ('risk', 'relevance', 'confidence'))
                lines.append('Suggested next steps:')
                lines.extend('• ' + action['text'] for action in judged['actions'])
        if value.get('analysis'):
            lines.append(value['analysis'])
            # A dashboard's 'truncated' means more rows than shown, not a cut-off model reply.
            if value.get('truncated') and event.get('tool') == 'intelligence_analyze':
                lines.append('(The model reached its length limit; the analysis may be incomplete.)')
        if value.get('map_url'):
            lines.append('Interactive world atlas: ' + value['map_url'])
        if 'cameras' in value:
            lines.append(f"{value['total_matching']} matching public camera records; showing {len(value['cameras'])}.")
            if value.get('networks'):
                lines.append('Networks: ' + ', '.join(f'{name} {count}' for name, count in list(value['networks'].items())[:13]))
            for row in value['cameras'][:10]:
                details = row['details']
                from app.intelligence.providers import camera_media_profile
                media = camera_media_profile(details)['label']
                lines.append(f"• {row['title']} · {details.get('provider', '')}" + (f" · {row['distance_km']} km" if 'distance_km' in row else '')
                             + f" · {media} · available: {details['available']}\n  {row['id']}" + (f"\n  {details['page_url']}" if details.get('page_url') else ''))
        if 'resources' in value:
            lines.extend(f"• {row['title']} · {row['integration']}\n  {row['url']}\n  {row['notes']}" for row in value['resources'])
        if 'weather' in value:
            lines.extend(f"{key}: {item} {value['units'].get(key, '')}" for key, item in value['weather'].items())
            lines.append('Provider: ' + value['url'])
        if value.get('path') and value.get('image_url'):
            lines.extend(['Camera preview: ' + value['path'], 'Fetched: ' + value['fetched_at'], 'Provider: ' + value['image_url']])
        if value.get('findings'):
            lines.extend(value['findings'])
        for change in value.get('source_changes', []):
            if change.get('change') is not None:
                detail = change['change']
                lines.append(f"Last fetch changes · {change['feed']}: {detail['added_count']} added, {detail['changed_count']} revised, {detail['missing_count']} absent from feed"
                             + (' (initial baseline)' if detail['baseline'] else '')
                             + (' (stale source)' if change['stale'] else ''))
        if value.get('aggregates'):
            aggregate = value['aggregates']
            lines.append(f"{aggregate['located']} items have coordinates; {aggregate['unknown_location']} have unknown locations.")
            if aggregate['largest_earthquake_magnitude'] is not None:
                lines.append(f"Largest reported earthquake magnitude among matches: {aggregate['largest_earthquake_magnitude']}")
        if value.get('coverage_gaps'):
            lines.append('Coverage and interpretation:')
            lines.extend(value['coverage_gaps'])
            lines.append(value['interpretation'])
        if 'preferences' in value:
            lines.append(('Saved' if value['saved'] else 'Current') + ' intelligence display preferences:\n' + json.dumps(value['preferences'], ensure_ascii=False))
        if 'total_matching' in value and 'events' in value:
            lines.append(f"{value['total_matching']} matching source items; showing {len(value['events'])}.")
        sources = value.get('sources', [])
        if value.get('headline'):
            # A brief is about events; camera catalogs are listed in the camera views.
            cameras = [source for source in sources if _CATALOG.match(str(source.get('key', source.get('feed', ''))))]
            sources = [source for source in sources if source not in cameras]
            feed = (event.get('arguments') or {}).get('feed')
            if feed:  # a brief on one feed reports that feed's freshness
                sources = [source for source in sources if source.get('key', source.get('feed')) == feed]
            if cameras and not feed:
                lines.append(f"{len(cameras)} camera catalogs not listed here.")
        for source in sources:
            key = source.get('key', source.get('feed', 'source'))
            state = 'cached' if source.get('cached') else source.get('status', 'unknown')
            lines.append(f"{key}: {state}" + (' · stale' if source.get('stale') else '') + (f" · {source['error']}" if source.get('error') else ''))
        for row in value.get('events', [])[:8]:
            lines.append(f"• {row['title']} — {row.get('observed_at') or 'event time unknown'}\n  {row['url']}" + (' (stale source)' if row.get('stale') else ''))
        if 'alerts' in value:
            lines.append(f"{len(value['alerts'])} recent local alerts shown.")
            for alert in value['alerts'][:5]:
                lines.append(f"• {alert['watch_title']}: {alert['event']['title']}\n  {alert['event']['url']}")
        if 'saved' in value and 'watch' in value:
            lines.append(f"Watch {value['key']} saved; matching new or changed events create local alerts automatically.")
        if 'running' in value:
            lines.append('Automatic monitor is running.' if value['running'] else 'Automatic monitor is stopped.')
        if 'watches' in value:
            lines.append(f"{len(value['watches'])} local watch rules.")
        if 'snapshots' in value:
            lines.extend(f"{row['captured_at']}: {row['count']} events" for row in value['snapshots'])
        if value.get('basis'):
            lines.append(value['basis'])
        if value.get('brief_path'):
            lines.append('Updated local evidence brief: ' + value['brief_path'])
        sections.append('\n'.join(lines))
    return '\n\n'.join(dict.fromkeys(sections)) or None
