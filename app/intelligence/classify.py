"""Source-derived categories, severity labels and suggested next steps.

Severity is a transparent rule over fields the provider itself publishes
(earthquake magnitude and PAGER alert, GDACS alert level, NOAA space-weather
scale, CISA ransomware flag, EONET wind speed or burned area). Every label
carries the rule that produced it; nothing here forecasts impact.
"""
import re

# key: (title, colour, map shape)
#: Short, whole words for road-event kinds ("RESTRI" and "WARNIN" were cut from the kind).
ROAD_BADGES = {'construction': 'ROADWORK', 'restriction': 'RESTRICT', 'ramp restriction': 'RAMP', 'warning': 'WARNING',
               'conditions': 'CONDITION', 'lane closure': 'LANE SHUT', 'closure': 'CLOSED', 'crash': 'CRASH',
               'road event': 'ROAD', 'information': 'INFO', 'weather': 'WEATHER'}

CATEGORIES = {
    'earthquakes': ('Earthquakes', '#FF5A5F', 'circle'),
    'volcanoes': ('Volcanoes', '#FF8A3D', 'triangle'),
    'wildfires': ('Wildfires', '#FFB020', 'flame'),
    'severe_weather': ('Severe weather', '#4FC3F7', 'diamond'),
    'floods': ('Floods', '#3D8BFD', 'drop'),
    'space_weather': ('Space weather', '#F5E663', 'star'),
    'cyber': ('Cyber', '#B388FF', 'square'),
    'other': ('Other events', '#9FB3C8', 'ring'),
    'cameras': ('Public cameras', '#5ED6C0', 'camera'),
    'traffic': ('Road events', '#E8A33D', 'square'),
    'conflict': ('Conflict', '#D9534F', 'diamond'),
    'attack': ('Attacks & explosions', '#FF3B30', 'star'),
    'emergency': ('Emergencies', '#FF9F0A', 'triangle'),
    'aviation': ('Airports & airspace', '#64D2FF', 'square'),
    'tsunami': ('Tsunami', '#1E90FF', 'drop'),
    'health': ('Disease outbreaks', '#C77DFF', 'ring'),
    'space': ('Asteroids & fireballs', '#9AD0FF', 'star'),
    'air_quality': ('Air quality', '#B5B35C', 'ring'),
    'news': ('News', '#A0A7B4', 'ring'),
}
SEVERITY_RANK = {'high': 3, 'medium': 2, 'low': 1, 'info': 0}
SEVERITY_LABEL = {'high': 'High', 'medium': 'Medium', 'low': 'Low', 'info': 'Info'}
SEVERITY_COLOURS = {'high': '#F05D5E', 'medium': '#F2A541', 'low': '#4FA3E0', 'info': '#7D8B99'}
EONET = {'wildfires': 'wildfires', 'severe storms': 'severe_weather', 'volcanoes': 'volcanoes',
         'floods': 'floods', 'earthquakes': 'earthquakes', 'snow': 'severe_weather',
         'temperature extremes': 'severe_weather', 'landslides': 'other', 'drought': 'other'}
GDACS = {'EQ': 'earthquakes', 'TC': 'severe_weather', 'FL': 'floods', 'VO': 'volcanoes',
         'WF': 'wildfires', 'DR': 'other'}
GDACS_NAMES = {'EQ': 'Earthquake', 'TC': 'Tropical cyclone', 'FL': 'Flood', 'VO': 'Volcano',
               'WF': 'Wildfire', 'DR': 'Drought'}
PROVIDERS = {'earthquakes': 'USGS', 'natural_events': 'NASA EONET', 'cyber': 'CISA',
             'space_weather': 'NOAA SWPC', 'disasters': 'GDACS', 'cameras_london': 'Transport for London'}
# Issuers of their own records: the USGS catalog, CISA's KEV list and NOAA's
# bulletins are the authoritative origin of that record, not a re-report.
# The hazard warnings (app/intelligence/hazards.py) come from the issuing agencies too.
from app.intelligence.hazards import HAZARD_FEEDS  # noqa: E402 - the hazard parsers import nothing from here

HAZARD_ASSESSED = frozenset(HAZARD_FEEDS)
PRIMARY_ISSUERS = {'earthquakes', 'cyber', 'space_weather', *HAZARD_ASSESSED}

SPACE_HEADLINE = re.compile(r'^\s*(EXTENDED WARNING|CONTINUED ALERT|CANCEL WATCH|CANCEL WARNING|CANCEL ALERT|'
                            r'CANCEL SUMMARY|WARNING|ALERT|WATCH|SUMMARY)\s*:\s*(.+?)\s*$', re.IGNORECASE | re.MULTILINE)
SPACE_SCALE = re.compile(r'\b([GSR])([1-5])\b(?:\s*-\s*([A-Za-z]+))?')
K_INDEX = re.compile(r'K-index of (\d)', re.IGNORECASE)


def provider(feed):
    if feed.startswith('cameras_caltrans_'):
        return 'Caltrans'
    return PROVIDERS.get(feed, feed)


def category(row):
    feed, kind = row.get('feed', ''), str(row.get('kind') or '')
    if kind == 'public_camera' or feed.startswith('cameras_'):
        return 'cameras'
    if feed.startswith('roads_'):
        return 'traffic'
    if feed == 'wildfires_us':
        return 'wildfires'
    if feed.startswith('news_') or feed == 'gdelt_security':
        return (row.get('details') or {}).get('security_category') or 'news'
    if feed == 'storms_nhc':
        return 'severe_weather'
    if feed == 'airports_faa':
        return 'aviation'
    if feed == 'earthquakes_emsc':
        return 'earthquakes'
    from app.intelligence.hazards import CATEGORY as HAZARD_CATEGORY
    if feed in HAZARD_CATEGORY:
        return HAZARD_CATEGORY[feed]
    if feed in ('earthquakes', 'cyber', 'space_weather'):
        return feed
    if feed == 'disasters':
        return GDACS.get(kind.upper(), 'other')
    if feed == 'natural_events':
        for part in kind.split(','):
            hit = EONET.get(part.strip().casefold())
            if hit:
                return hit
    return 'other'


def space_weather(message):
    """Headline, NOAA scale and severity from a SWPC bulletin's own text."""
    message = str(message or '')
    head = SPACE_HEADLINE.search(message)
    prefix, text = (head[1].strip().capitalize(), head[2].strip()) if head else ('', '')
    scales = [(letter.upper(), int(level), word or '') for letter, level, word in
              SPACE_SCALE.findall(text + '\n' + '\n'.join(line for line in message.splitlines() if 'scale' in line.lower()))]
    kindex = K_INDEX.search(text)
    if kindex and int(kindex[1]) >= 5:
        scales.append(('G', int(kindex[1]) - 4, ''))
    scale = max(scales, key=lambda item: item[1], default=None)
    cancelled = prefix.lower().startswith('cancel')
    if cancelled:
        level, basis = 'low', 'NOAA cancelled this watch or warning.'
    elif scale and scale[1] >= 4:
        level, basis = 'high', f'NOAA scale {scale[0]}{scale[1]} (severe or extreme).'
    elif scale and scale[1] >= 2:
        level, basis = 'medium', f'NOAA scale {scale[0]}{scale[1]} (moderate or strong).'
    elif scale:
        level, basis = 'low', f'NOAA scale {scale[0]}{scale[1]} (minor).'
    else:
        level, basis = 'low', 'The bulletin states no NOAA scale level.'
    impacts = []
    capture = False
    for line in message.splitlines():
        if line.strip().lower().startswith('potential impacts'):
            capture = True
            remainder = line.split(':', 1)[1].strip() if ':' in line else ''
            if remainder:
                impacts.append(remainder)
            continue
        if capture:
            if not line.strip():
                break
            impacts.append(line.strip())
    return {'headline': f'{prefix}: {text}' if head else '', 'scale': f'{scale[0]}{scale[1]}' if scale else '',
            'scale_word': scale[2] if scale else '', 'severity': level, 'basis': basis,
            'cancelled': cancelled, 'impacts': impacts[:6]}


def assess(row):
    """Severity, badge, headline and subtitle for one provider record."""
    feed, details = row.get('feed', ''), row.get('details') or {}
    title = row.get('title') or ''
    key = category(row)
    headline, subtitle, badge = title, '', ''
    if feed == 'earthquakes':
        magnitude, alert = details.get('magnitude'), str(details.get('alert') or '').lower()
        headline = f'M{magnitude:.1f} Earthquake' if magnitude is not None else 'Earthquake'
        subtitle = title
        badge = f'M{magnitude:.1f}' if magnitude is not None else ''
        if alert in ('red', 'orange'):
            level, basis = 'high', f'USGS PAGER impact alert: {alert}.'
        elif magnitude is not None and magnitude >= 6:
            level, basis = 'high', f'Magnitude {magnitude:.1f} is 6.0 or greater.'
        elif alert == 'yellow' or magnitude is not None and magnitude >= 4.5:
            level, basis = 'medium', (f'USGS PAGER impact alert: {alert}.' if alert == 'yellow'
                                      else f'Magnitude {magnitude:.1f} is between 4.5 and 6.0.')
        elif magnitude is None:
            level, basis = 'low', 'USGS supplied no magnitude.'
        else:
            level, basis = 'low', f'Magnitude {magnitude:.1f} is below 4.5.'
    elif feed == 'disasters':
        alert = str(details.get('alert_level') or '').capitalize()
        level = {'Red': 'high', 'Orange': 'medium', 'Green': 'low'}.get(alert, 'low')
        basis = f'GDACS alert level {alert}.' if alert else 'GDACS supplied no alert level.'
        kind = str(row.get('kind') or '').upper()
        subtitle = ' · '.join(part for part in (GDACS_NAMES.get(kind, kind), details.get('country') or '') if part)
        badge = alert.upper()[:6] if alert else ''
    elif feed == 'cyber':
        ransomware = str(details.get('ransomware_use') or '')
        if ransomware.casefold() == 'known':
            level, basis = 'high', 'CISA lists it as exploited in the wild and used in ransomware campaigns.'
        else:
            level, basis = 'medium', 'CISA lists it as exploited in the wild; ransomware use not known.'
        identifier = row.get('id', '').partition(':')[2]
        product = ' '.join(part for part in (details.get('vendor'), details.get('product')) if part)
        subtitle = ' · '.join(part for part in (identifier, product) if part)
    elif feed == 'space_weather':
        value = space_weather(details.get('message') or title)
        headline = value['headline'] or title
        level, basis = value['severity'], value['basis']
        subtitle = 'NOAA SWPC' + (f" · {value['scale']} {value['scale_word']}".rstrip() if value['scale'] else ' bulletin')
        badge = value['scale']
    elif feed == 'natural_events':
        magnitude, unit = details.get('magnitude'), str(details.get('magnitude_unit') or '')
        subtitle = str(row.get('kind') or '')
        if magnitude is not None and unit:
            subtitle += f' · {magnitude:,.0f} {unit}'
        if key == 'severe_weather' and magnitude is not None and unit.lower().startswith('kt'):
            badge = f'{magnitude:.0f} kt'
            if magnitude >= 64:
                level, basis = 'high', f'Sustained winds of {magnitude:.0f} kt reach hurricane or typhoon strength (64 kt).'
            elif magnitude >= 34:
                level, basis = 'medium', f'Sustained winds of {magnitude:.0f} kt reach tropical-storm strength (34 kt).'
            else:
                level, basis = 'low', f'Sustained winds of {magnitude:.0f} kt are below tropical-storm strength.'
        elif key == 'wildfires' and magnitude is not None and unit.lower().startswith('acre'):
            if magnitude >= 100000:
                level, basis = 'high', f'{magnitude:,.0f} acres burned (100,000 or more).'
            elif magnitude >= 10000:
                level, basis = 'medium', f'{magnitude:,.0f} acres burned (10,000 or more).'
            else:
                level, basis = 'low', f'{magnitude:,.0f} acres burned (under 10,000).'
        else:
            level, basis = 'low', 'NASA EONET supplies no severity for this event; shown as low.'
    elif key == 'traffic':
        kind = str(row.get('kind') or '')
        urgency = str(details.get('urgency') or 'routine')
        level = {'critical': 'high', 'urgent': 'medium'}.get(urgency, 'low')
        basis = f'The publisher marks this {kind or "road event"} as {urgency}.'
        subtitle = ' · '.join(str(part) for part in (details.get('provider'), details.get('state'), kind) if part)
        badge = ROAD_BADGES.get(kind.lower(), kind.split()[0][:9].upper()) if kind else 'ROAD'
    elif feed == 'wildfires_us':
        acres = details.get('size_acres')
        if acres is not None and acres >= 100000:
            level, basis = 'high', f'{acres:,.0f} acres (100,000 or more), per NIFC.'
        elif acres is not None and acres >= 10000:
            level, basis = 'medium', f'{acres:,.0f} acres (10,000 or more), per NIFC.'
        else:
            level, basis = 'low', (f'{acres:,.0f} acres, per NIFC.' if acres is not None else 'NIFC supplied no size yet.')
        contained = details.get('percent_contained')
        subtitle = ' · '.join(str(part) for part in (details.get('state'), f'{contained:.0f}% contained' if contained is not None else '') if part)
    elif feed == 'storms_nhc':
        wind = details.get('wind_kt') or 0
        level = 'high' if wind >= 96 else 'medium' if wind >= 64 else 'low'
        basis = f'NHC reports maximum sustained winds of {wind:.0f} kt' + (' (major hurricane).' if wind >= 96 else '.')
        subtitle = f"{wind:.0f} kt · {details.get('pressure_mb') or '?'} mb"
    elif feed == 'earthquakes_emsc':
        magnitude = details.get('magnitude')
        level = 'high' if magnitude and magnitude >= 7 else 'medium' if magnitude and magnitude >= 6 else 'low'
        basis = f'EMSC magnitude {magnitude}.'
        badge = f'M{magnitude:.1f}' if magnitude is not None else ''
    elif feed in HAZARD_ASSESSED:
        from app.intelligence.hazards import assess as hazard_assess
        level, basis, badge, subtitle = hazard_assess(row)
    elif feed == 'airports_faa':
        kind = str(row.get('kind') or '')
        level = 'medium' if 'ground stop' in kind or 'closure' in kind and 'non sked' not in str(details.get('reason', '')).lower() else 'low'
        basis = f"FAA reports a {kind or 'disruption'} at {details.get('airport_code')}."
        subtitle = str(details.get('reason') or '')[:120]
    elif key in ('attack', 'conflict', 'emergency', 'news'):
        # One outlet's report: shown, never asserted. Merged objects decide verification.
        level = {'attack': 'medium', 'emergency': 'medium', 'conflict': 'low'}.get(key, 'info')
        basis = (f"Reported by {details.get('outlet') or 'one outlet'}; a single report is UNVERIFIED until "
                 "independent sources agree (see the merged intelligence object).")
        subtitle = ' · '.join(str(part) for part in (details.get('outlet'), details.get('place'),
                                                     details.get('location_precision')) if part)
    elif key == 'cameras':
        level, basis = 'info', 'Public camera catalog entry, not an incident.'
        subtitle = ' · '.join(str(part) for part in (details.get('provider'), details.get('city') or details.get('county'),
                                                    details.get('direction')) if part)
        badge = 'CAM'
    else:
        level, basis = 'low', 'No provider severity is available.'
    return {'category': key, 'category_title': CATEGORIES[key][0], 'severity': level,
            'severity_rank': SEVERITY_RANK[level], 'severity_basis': basis,
            'badge': badge or {'high': 'HIGH', 'medium': 'MED', 'low': 'LOW', 'info': 'INFO'}[level],
            'headline': headline[:300], 'subtitle': subtitle[:300],
            'provider': details.get('provider') or provider(feed)}


def recommended_actions(row):
    """Practical, source-grounded next steps; each names its basis or a local action."""
    details, key = row.get('details') or {}, row.get('category') or category(row)
    actions = []
    located = row.get('latitude') is not None
    if key == 'earthquakes':
        magnitude = details.get('magnitude')
        if magnitude is not None and magnitude >= 6.5 or details.get('tsunami_flag') == 1:
            actions.append({'text': 'Check official tsunami advisories for the region (tsunami.gov or the national warning centre).', 'action': 'open_source'})
        actions.append({'text': 'Track aftershocks: add a watch for this area.', 'action': 'watch_area'})
    elif key == 'cyber':
        product = ' '.join(part for part in (details.get('vendor'), details.get('product')) if part)
        if product:
            actions.append({'text': f'Check whether you use {product}.', 'action': None})
        if details.get('required_action'):
            actions.append({'text': 'Provider action: ' + str(details['required_action'])[:300], 'action': 'open_source'})
        if details.get('due_date'):
            actions.append({'text': f"CISA deadline for US federal agencies: {details['due_date']}.", 'action': None})
    elif key == 'space_weather':
        value = space_weather(details.get('message') or row.get('title'))
        actions.extend({'text': 'NOAA impact note: ' + impact, 'action': None} for impact in value['impacts'][:3])
        actions.append({'text': 'Read the full NOAA bulletin.', 'action': 'open_source'})
    elif key == 'cameras':
        actions.append({'text': 'Load the latest publisher snapshot from the Cameras tab.', 'action': 'camera'})
    else:
        actions.append({'text': 'Follow guidance from local emergency authorities.', 'action': 'open_source'})
        if located:
            actions.append({'text': 'Track this area: add a watch for it.', 'action': 'watch_area'})
    if located and key != 'cameras':
        actions.append({'text': 'Check current model weather at this point.', 'action': 'weather'})
    actions.append({'text': 'Correlate with nearby and related observations.', 'action': 'related'})
    return actions
