"""Original adapters for openly published disaster and public camera catalogs."""
import re
from urllib.parse import quote, urlsplit

from app.intelligence.feeds import CARS_STATES, KEYED_511, event, finite, text, timestamp


# Each network's own media hosts: (still-image hosts, video hosts, publisher player-page hosts).
# Media from any other host is refused, so a catalog cannot point HAYAT at arbitrary sites.
MEDIA_HOSTS = {
    'cameras_nsw': ({'webcams.transport.nsw.gov.au', 'data.livetraffic.com'}, set(), set()),
    'cameras_lyon': ({'download.data.grandlyon.com'}, set(), set()),
    'cameras_valencia': (set(), set(), {'camaras.valencia.es'}),
    'cameras_luxembourg': ({'www.cita.lu'}, set(), {'www.cita.lu'}),
    'cameras_london': ({'s3-eu-west-1.amazonaws.com'}, {'s3-eu-west-1.amazonaws.com'}, set()),
    'cameras_caltrans': ({'cwwp2.dot.ca.gov', 'wzmedia.dot.ca.gov', 'wzmmedia.dot.ca.gov'},
                         {'cwwp2.dot.ca.gov', 'wzmedia.dot.ca.gov', 'wzmmedia.dot.ca.gov'}, set()),
    'cameras_nyc': ({'webcams.nyctmc.org'}, set(), set()),
    'cameras_finland': ({'weathercam.digitraffic.fi'}, set(), set()),
    'cameras_singapore': ({'images.data.gov.sg'}, set(), set()),
    'cameras_hongkong': ({'tdcctv.data.one.gov.hk'}, set(), set()),
    'cameras_nz': ({'trafficnz.info'}, set(), set()),
    'cameras_delaware': (set(), {'video.deldot.gov'}, set()),
    'cameras_maryland': (set(), set(), {'chart.maryland.gov'}),
    'cameras_austin': ({'cctv.austinmobility.io'}, set(), set()),
    'cameras_seattle': ({'www.seattle.gov', 'images.wsdot.wa.gov'}, set(), set()),
    'cameras_iowa': ({'atmsqf.iowadot.gov'}, {f'video{number}.iowadot.gov' for number in range(1, 10)}, set()),
    'cameras_quebec': (set(), set(), {'www.quebec511.info'}),
    'cameras_alertca': ({'cameras.alertcalifornia.org'}, set(), {'cameras.alertcalifornia.org'}),
    'cameras_drivebc': ({'www.drivebc.ca'}, set(), set()),
    'cameras_iceland': ({'www.vegagerdin.is'}, set(), set()),
    'cameras_toronto': ({'opendata.toronto.ca'}, set(), set()),
    'cameras_calgary': ({'trafficcam.calgary.ca'}, set(), set()),
    # Illinois and Michigan image servers disallow automated retrieval in robots.txt:
    # HAYAT links to the publisher's own viewer instead of fetching their images.
    'cameras_illinois': (set(), set(), {'travelmidwest.com', 'www.travelmidwest.com'}),
    'cameras_michigan': (set(), set(), {'mdotjboss.state.mi.us'}),
    'cameras_wsdot': ({'images.wsdot.wa.gov'}, set(), set()),
    'cameras_washington': ({'images.wsdot.wa.gov', 'www.tripcheck.com'}, set(), set()),
    'cameras_spain': ({'etraffic.dgt.es'}, set(), set()),
    'cameras_madrid': ({'informo.madrid.es'}, set(), set()),
    'cameras_oregon': ({'www.tripcheck.com'}, set(), set()),
    'cameras_ottawa': ({'traffic.ottawa.ca'}, set(), set()),
    # Norway: stills at /api/images/<camera id> (no file suffix), live HLS at /public/<camera id>/manifest.m3u8.
    'cameras_norway': ({'kamera.atlas.vegvesen.no'}, {'kamera.vegvesen.no'}, set()),
    'cameras_estonia': ({'tarktee.transpordiamet.ee'}, set(), set()),
    'cameras_tallinn': ({'ristmikud.tallinn.ee'}, set(), set()),
    'cameras_volcanoes': ({'avo-volcview.wr.usgs.gov', 'volcview.wr.usgs.gov'}, set(), set()),
    # TxDOT answers a snapshot request with JSON carrying the JPEG in base64 (see camera_frame).
    'cameras_txdot': ({'its.txdot.gov'}, set(), set()),
    'cameras_alabama': ({'api.algotraffic.com'}, {'cdn3.wowza.com'}, {'www.algotraffic.com'}),
    # Official channels' /live pages; the stream itself is resolved when the owner asks to play it.
    'livestreams': ({'webcam.arunachala.org'}, set(), {'www.youtube.com', 'www.siddhivinayak.org',
                                                     'somnath.org', 'sai.org.in', 'webcam.arunachala.org'}),
    # Every host each CARS catalog used on 2026-10-06, numbered stream servers with headroom.
    'cameras_cars_mn': ({'public.carsprogram.org'}, {'video.dot.state.mn.us'}, set()),
    'cameras_cars_co': ({'cocam.carsprogram.org', 'websvc.coloradosprings.gov'},
                        {f'publicstreamer{number}.cotrip.org' for number in range(1, 9)}, set()),
    'cameras_cars_ne': ({'dot511.nebraska.gov'}, set(), set()),
    'cameras_cars_in': ({'public.carsprogram.org'}, {f'skysfs{number}.trafficwise.org' for number in range(1, 9)}, set()),
    'cameras_cars_ks': ({'kscam.carsprogram.org', 'www.kcscout.net'},
                        {f'kdot-sfs{number}.us-east-1.skyvdn.com' for number in range(1, 9)}, set()),
    'cameras_cars_ma': ({'public.carsprogram.org'}, set(), set()),
    # traveler.modot.org's own stream proxy answers 403 to automated requests; its streams are left out.
    'cameras_missouri': (set(), {*(f'sfs{number:02d}-traveler.modot.mo.gov' for number in range(1, 13)),
                                 *(f's{number}.ozarkstrafficoneview.com' for number in range(1, 6))}, set()),
    **{f'cameras_511_{name}': ({host}, set(), {host}) for name, host, _title in KEYED_511},
}
# 511 traveler-information sites serve camera stills from /map/Cctv/<id> without a file suffix.
IBI_IMAGE = re.compile(r'/map/Cctv/[\w.-]+')
MICHIGAN_LINK = re.compile(r'href="(/MiDrive/map\?[^"]*lat=(-?\d+(?:\.\d+)?)&(?:amp;)?lon=(-?\d+(?:\.\d+)?)[^"]*id=(\d+)[^"]*)"')
TAGS = re.compile(r'<[^>]+>')
# Iowa's public HLS servers listen on 8888; every other media URL must use the default HTTPS port.
CAMERA_COVERAGE = ('New South Wales (Australia; publisher catalog, snapshot availability varies), Lyon (France), '
                   'Luxembourg (CITA), London, Spain (DGT national roads and Madrid city), California (Caltrans and ALERTCalifornia wildfire '
                   'cameras), New York City, Finland, Iceland, Norway (with live video), Estonia and Tallinn, Singapore, Hong Kong, New Zealand, British Columbia, Toronto, '
                   'Ottawa, Calgary, Québec, Delaware, Maryland, Austin, Seattle, Washington statewide, Oregon, Iowa, '
                   'Illinois, Michigan, Minnesota, Colorado, Nebraska, Indiana, Kansas, Massachusetts, Missouri, Texas, '
                   'Alabama, and USGS '
                   'volcano webcams in Alaska, the Cascades and Hawaii; official Makkah and Madinah broadcasts; '
                   'selected Indian publisher views in Maharashtra, Gujarat and Tamil Nadu (not nationwide CCTV)')
MEDIA_PORTS = {'cameras_iowa': (None, 443, 8888)}
NYC_IMAGE = re.compile(r'/api/cameras/[0-9a-fA-F-]{36}/image')
NORWAY_IMAGE = re.compile(r'/api/images/[\w.-]{1,40}')
TXDOT_SNAPSHOT = '/its/DistrictIts/GetCctvSnapshotByIcdId'
# CARS stills such as /cameras/MN/C30405-v1 carry no file suffix.
CARS_IMAGE = re.compile(r'/cameras/[A-Z]{2}/[\w.-]+')
CARS_STATE_NAMES = {'mn': 'Minnesota', 'co': 'Colorado', 'ne': 'Nebraska', 'in': 'Indiana', 'ks': 'Kansas',
                    'ma': 'Massachusetts'}
#: Minnesota's video previews answer 403; its stills and streams work.
CARS_NO_PREVIEW = {'mn'}


def network(feed):
    if feed.startswith('cameras_caltrans_'):
        return 'cameras_caltrans'
    return 'cameras_txdot' if feed.startswith('cameras_txdot_') else feed


def open_network(key):
    """Feeds whose records are public cameras parsed by open_camera."""
    return key != 'cameras_london' and (key in MEDIA_HOSTS or key.startswith('cameras_txdot_'))


def camera_url(url, feed, *, video=False, page=False):
    """Accept only the registered publisher's public camera URL patterns."""
    if not isinstance(url, str) or len(url) > 2000:
        return ''
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return ''
    key = network(feed)
    if parts.scheme != 'https' or parts.username or parts.password or port not in MEDIA_PORTS.get(key, (None, 443)):
        return ''
    images, videos, pages = MEDIA_HOSTS.get(key, (set(), set(), set()))
    host, path = parts.hostname, parts.path
    if page:
        if key == 'cameras_valencia':
            return url if host in pages and path == '/camaras/visualizador.html' and re.fullmatch(r'id=[0-9]{1,8}', parts.query) and not parts.fragment else ''
        if key == 'cameras_luxembourg':
            return url if host in pages and re.fullmatch(r'/webcam/map/[0-9]{1,6}', path) else ''
        return url if host in pages else ''
    if key == 'cameras_london' and not path.startswith('/jamcams.tfl.gov.uk/'):
        return ''
    if video:
        return url if host in videos and path.lower().endswith(('.mp4', '.m3u8')) else ''
    if key == 'cameras_nsw':
        pattern = (r'/livetraffic-webcams/cameras/[A-Za-z0-9_-]+\.jpe?g' if host == 'webcams.transport.nsw.gov.au'
                   else r'/cameras/[A-Za-z0-9_-]+\.jpe?g')
        return url if host in images and re.fullmatch(pattern, path) and not parts.query and not parts.fragment else ''
    if key == 'cameras_lyon':
        return url if host in images and re.fullmatch(r'/files/rdata/pvo_patrimoine_voirie\.pvocameracriter/[A-Za-z0-9_-]{1,40}\.JPG', path) and not parts.query and not parts.fragment else ''
    if key == 'cameras_nyc':
        return url if host in images and NYC_IMAGE.fullmatch(path) else ''
    if key == 'cameras_luxembourg':
        return url if host in images and re.fullmatch(r'/info_trafic/cameras/images/cccam_[0-9]{1,6}\.jpg', path) else ''
    if key.startswith('cameras_511_') and host in images and IBI_IMAGE.fullmatch(path):
        return url
    if key == 'cameras_txdot':
        return url if host in images and path == TXDOT_SNAPSHOT and parts.query.startswith('icdId=') else ''
    if key.startswith('cameras_cars_') and host in images and (CARS_IMAGE.fullmatch(path) or
                                                               path.lower().endswith(('.jpg', '.jpeg', '.png'))):
        return url
    if key == 'cameras_norway':
        return url if host in images and NORWAY_IMAGE.fullmatch(path) else ''
    if key == 'cameras_ottawa':
        # Ottawa serves stills from /beta/camera?id=<camera number>, with no file suffix.
        return url if host in images and path == '/beta/camera' and re.fullmatch(r'id=\d{1,6}', parts.query) else ''
    return url if host in images and path.lower().endswith(('.jpg', '.jpeg', '.png')) else ''


def camera(feed, identifier, title, point, provider, country, *, image='', video='', page='', available=True,
           modified=None, refresh=300, media_kind='periodically refreshed still image', **extra):
    """One normalized public camera record; media URLs pass the network's allowlist or are dropped."""
    return event(feed, identifier, title, 'public_camera', None, feed.documentation, point, provider=provider,
                 country=country, image_url=camera_url(image, feed.key), video_url=camera_url(video, feed.key, video=True),
                 page_url=camera_url(page, feed.key, page=True), available=bool(available), catalog_modified_at=modified,
                 refresh_seconds=refresh, media_kind=media_kind,
                 limitation='Publisher catalog entry. Availability is provider-reported; fetch time is not capture time.',
                 **{key: value for key, value in extra.items() if value not in (None, '')})


def camera_media_profile(details):
    """Describe supplied media without asserting playback or capture freshness."""
    video = details.get('video_url') or ''
    stream = bool(details.get('live_page') or video and urlsplit(video).path.lower().endswith('.m3u8'))
    kinds = []
    if video:
        kinds.append('HLS stream' if stream else 'Video clip')
    elif details.get('live_page'):
        kinds.append('Official live broadcast (checked when played)')
    if details.get('image_url'):
        kinds.append('Refreshing still')
    if details.get('page_url'):
        kinds.append('Publisher player')
    return {'label': ' + '.join(kinds) or 'No media', 'stream': stream,
            'video_clip': bool(video and not stream), 'image': bool(details.get('image_url'))}


def open_camera_items(feed, payload):
    """The record array of each open camera network, validated by shape."""
    key = feed.key
    if key in ('cameras_nyc', 'cameras_austin', 'cameras_hongkong', 'cameras_drivebc', 'cameras_iceland', 'cameras_calgary',
               'cameras_michigan', 'cameras_wsdot', 'cameras_spain', 'cameras_madrid', 'cameras_missouri', 'cameras_estonia',
               'cameras_tallinn', 'cameras_luxembourg') \
            or key.startswith(('cameras_511_', 'cameras_cars_')):
        return payload
    if key == 'cameras_oregon':
        return payload.get('features') if isinstance(payload, dict) else None
    if key == 'cameras_alabama':
        return payload
    if key.startswith('discovered_'):
        from app.intelligence.discovery import DISCOVERED_MAPPINGS, records_at
        return records_at(payload, DISCOVERED_MAPPINGS[key]['records_path'])
    if key.startswith('cameras_txdot_'):
        roads = payload.get('roadwayCctvStatuses') if isinstance(payload, dict) else None
        return [camera for cameras in roads.values() if isinstance(cameras, list) for camera in cameras
                if isinstance(camera, dict)] if isinstance(roads, dict) else None
    if key == 'cameras_ottawa':
        return payload.get('cameras') if isinstance(payload, dict) else None
    if key == 'cameras_volcanoes':
        return payload.get('webcams') if isinstance(payload, dict) else None
    if key in ('cameras_nsw', 'cameras_lyon', 'cameras_finland', 'cameras_quebec', 'cameras_iowa', 'cameras_alertca', 'cameras_illinois', 'cameras_toronto',
               'cameras_washington', 'cameras_norway', 'cameras_valencia'):
        return payload.get('features') if isinstance(payload, dict) else None
    if key == 'cameras_singapore':
        items = payload.get('items') if isinstance(payload, dict) else None
        return items[0].get('cameras') if isinstance(items, list) and items and isinstance(items[0], dict) else None
    if key == 'cameras_nz':
        return ((payload.get('response') or {}).get('camera')) if isinstance(payload, dict) else None
    if key == 'cameras_delaware':
        return payload.get('videoCameras') if isinstance(payload, dict) else None
    if key == 'cameras_maryland':
        return payload.get('data') if isinstance(payload, dict) else None
    if key == 'cameras_seattle':
        features = payload.get('Features') if isinstance(payload, dict) else None
        # A Seattle map point can hold several cameras; flatten while keeping the point.
        return [{**item, '_point': feature.get('PointCoordinate')} for feature in features or [] if isinstance(feature, dict)
                for item in feature.get('Cameras') or [] if isinstance(item, dict)] if isinstance(features, list) else None
    return None


def open_camera(feed, item):
    key = feed.key
    if key in ('cameras_nsw', 'cameras_lyon'):
        geometry, props = item.get('geometry') or {}, item.get('properties') or {}
        point = geometry.get('coordinates')
        if (item.get('type') != 'Feature' or geometry.get('type') != 'Point' or not isinstance(point, list)
                or len(point) < 2 or any(finite(value) is None for value in point[:2])
                or not -180 <= finite(point[0]) <= 180 or not -90 <= finite(point[1]) <= 90):
            raise ValueError('Public camera requires a finite GeoJSON point.')
        if key == 'cameras_nsw':
            identifier = item.get('id')
            if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', identifier):
                raise ValueError('NSW camera requires its publisher UUID.')
            image = camera_url(props.get('href'), key)
            return camera(feed, identifier, text(props.get('title')), point, 'Transport for NSW', 'Australia',
                          image=image, available=bool(image), refresh=300, state='New South Wales',
                          region=text(props.get('region')), direction=text(props.get('direction')),
                          view=text(props.get('view')), availability_verified=False,
                          availability_basis='Publisher snapshot link present; no online status or capture timestamp is supplied. Some published images may be temporarily unavailable.',
                          catalog_credit='Transport for NSW', catalog_license='Creative Commons Attribution (Data.NSW catalog)',
                          media_kind='traffic camera stills; publisher catalog is not proof of live availability')
        identifier = props.get('identifiant')
        if type(identifier) is not int or not 0 < identifier < 1000000000:
            raise ValueError('Lyon camera requires its publisher numeric identifier.')
        image = camera_url(props.get('url'), key)
        title = ' · '.join(part for part in (text(props.get('nom')), text(props.get('libellelong'))) if part)
        return camera(feed, identifier, title, point, 'Métropole de Lyon', 'France', image=image,
                      available=bool(image), refresh=60, modified=timestamp(props.get('last_update')), city='Lyon',
                      view=text(props.get('observation')), publisher_camera_code=text(props.get('numeromaintenance')),
                      availability_verified=False,
                      availability_basis='Publisher snapshot link present; reachability and capture freshness are checked only when a frame is requested.',
                      catalog_credit='Métropole de Lyon · Criter', catalog_license='Licence Ouverte / Open Licence 2.0',
                      media_kind='traffic camera stills; publisher advertises one image per minute')
    if key == 'cameras_valencia':
        props, geometry = item.get('attributes') or {}, item.get('geometry') or {}
        identifier = str(props.get('idcamara') or '')
        if not re.fullmatch(r'[0-9]{1,8}', identifier):
            raise ValueError('València camera id must be a publisher camera number.')
        page = camera_url(props.get('url'), key, page=True)
        if not page or urlsplit(page).query != f'id={identifier}':
            raise ValueError('València camera lacks an admitted public publisher viewer.')
        return camera(feed, identifier, text(props.get('descripcio')), [geometry.get('x'), geometry.get('y')],
                      'Ajuntament de València', 'Spain', page=page, city='València', available=False,
                      media_kind='Publisher viewer link; no direct image or video URL is ingested',
                      availability_basis='Catalog link only; viewer reachability and camera freshness are not verified',
                      catalog_license='CC BY 4.0 (camera-location dataset)')
    if key == 'cameras_luxembourg':
        identifier = str(item.get('number') or '')
        if not re.fullmatch(r'[0-9]{1,6}', identifier):
            raise ValueError('CITA camera id must be a publisher camera number.')
        return camera(feed, identifier, text(item.get('name')), item.get('point'),
                      'Administration des Ponts et Chaussées (CITA)', 'Luxembourg',
                      image=f'https://www.cita.lu/info_trafic/cameras/images/cccam_{identifier}.jpg',
                      page=f'https://www.cita.lu/webcam/map/{identifier}', refresh=120,
                      media_kind='Publisher traffic snapshot; capture time is not established by catalog retrieval',
                      catalog_license='CC0 (public camera-location dataset)')
    if key == 'cameras_washington':
        props, geometry = item['attributes'], item.get('geometry') or {}
        image = camera_url(props.get('ImageURL'), key)
        return camera(feed, props['OBJECTID'], text(props.get('CameraTitle')), [geometry.get('x'), geometry.get('y')],
                      'WSDOT public map', 'United States', image=image, available=bool(image), refresh=120,
                      state='Washington / border roads', direction=text(props.get('CompassDirection')),
                      media_kind='periodically refreshed highway camera still image')
    if key == 'cameras_nyc':
        return camera(feed, item['id'], item['name'], [item.get('longitude'), item.get('latitude')], 'New York City DOT',
                      'United States', image=item.get('imageUrl'), available=str(item.get('isOnline')).lower() == 'true',
                      refresh=120, city='New York', county=text(item.get('area')))
    if key == 'cameras_finland':
        props = item['properties']
        presets = [preset['id'] for preset in props.get('presets') or [] if isinstance(preset, dict) and preset.get('inCollection') and preset.get('id')]
        images = [f'https://weathercam.digitraffic.fi/{preset}.jpg' for preset in presets[:8]]
        return camera(feed, props['id'], text(props.get('name'), 200).replace('_', ' '), (item.get('geometry') or {}).get('coordinates'),
                      'Fintraffic', 'Finland', image=images[0] if images else '', modified=timestamp(props.get('dataUpdatedTime')),
                      available=props.get('collectionStatus') == 'GATHERING' and bool(images), refresh=600,
                      media_kind='road weather camera stills, several views per station',
                      preset_images=[camera_url(image, key) for image in images[1:]] or None)
    if key == 'cameras_singapore':
        location = item.get('location') or {}
        return camera(feed, item['camera_id'], f"Singapore traffic camera {text(item['camera_id'], 20)}",
                      [location.get('longitude'), location.get('latitude')], 'Land Transport Authority', 'Singapore',
                      image=item.get('image'), modified=timestamp(item.get('timestamp')), refresh=60, city='Singapore')
    if key == 'cameras_hongkong':
        return camera(feed, item['key'], text(item.get('description'), 300), [item.get('longitude'), item.get('latitude')],
                      'Hong Kong Transport Department', 'Hong Kong', image=item.get('url'), refresh=120, city='Hong Kong',
                      county=text(item.get('district')), region=text(item.get('region')))
    if key == 'cameras_nz':
        image = item.get('imageUrl') or ''
        return camera(feed, item['id'], ' · '.join(part for part in (text(item.get('name')).strip(), text(item.get('description'))) if part),
                      [item.get('longitude'), item.get('latitude')], 'NZ Transport Agency', 'New Zealand',
                      image=('https://trafficnz.info' + image) if image.startswith('/') else image, available=not item.get('offline'),
                      refresh=300, direction=text(item.get('direction')), road=text(item.get('highway')),
                      region=text((item.get('region') or {}).get('name')))
    if key == 'cameras_delaware':
        return camera(feed, item['id'], item['title'], [item.get('lon'), item.get('lat')], 'Delaware DOT', 'United States',
                      video=(item.get('urls') or {}).get('m3u8s'), available=item.get('enabled') is True and item.get('status') == 'Active',
                      media_kind='live HLS video stream', state='Delaware', county=text(item.get('county')))
    if key == 'cameras_maryland':
        return camera(feed, item['id'], text(item.get('description') or item.get('name')), [item.get('lon'), item.get('lat')],
                      'Maryland CHART', 'United States', page=item.get('publicVideoURL'),
                      available=item.get('commMode') == 'ONLINE' and item.get('opStatus') == 'OK',
                      media_kind='live video in the publisher player', state='Maryland',
                      modified=timestamp(item.get('lastCachedDataUpdateTime'), milliseconds=True),
                      road=' '.join(text(part) for part in (item.get('routePrefix'), item.get('routeNumber')) if part))
    if key == 'cameras_austin':
        if item.get('camera_status') != 'TURNED_ON':
            return None  # Planned, removed or void entries are not cameras.
        return camera(feed, item['camera_id'], text(item.get('location_name')).strip(), (item.get('location') or {}).get('coordinates'),
                      'City of Austin Transportation', 'United States', image=item.get('screenshot_address'),
                      modified=timestamp(item.get('modified_date')), refresh=120, city='Austin', state='Texas')
    if key == 'cameras_seattle':
        point = item.get('_point') or [None, None]
        image = text(item.get('ImageUrl'))
        base = 'https://images.wsdot.wa.gov/nw/' if item.get('Type') == 'wsdot' else 'https://www.seattle.gov/trafficcams/images/'
        return camera(feed, item['Id'], item['Description'], [point[1], point[0]] if len(point) == 2 else None,
                      'WSDOT' if item.get('Type') == 'wsdot' else 'Seattle DOT', 'United States',
                      image=image if image.startswith('https://') else base + image, refresh=120, city='Seattle', state='Washington')
    if key == 'cameras_iowa':
        attributes = item['attributes']
        image = text(attributes.get('ImageURL'))
        identity = image.rsplit('/', 1)[-1].rsplit('.', 1)[0] if image else str(attributes.get('FID'))
        return camera(feed, identity, text(attributes.get('ImageName') or attributes.get('Desc_')),
                      [attributes.get('longitude'), attributes.get('latitude')], 'Iowa DOT', 'United States', image=image,
                      video=attributes.get('VideoURL'), refresh=300, state='Iowa', road=text(attributes.get('Route')),
                      camera_type=text(attributes.get('Type')),
                      media_kind='still image' + (' and live HLS video' if attributes.get('VideoURL') else ''))
    if key == 'cameras_quebec':
        props = item['properties']
        return camera(feed, props['IDEcamera'], text(props.get('DescriptionLocalisationEn') or props.get('DescriptionLocalisationFr')),
                      (item.get('geometry') or {}).get('coordinates'), 'Québec 511', 'Canada', page=props.get('URL_FLUX_DONNEE'),
                      media_kind='publisher camera viewer', state='Québec', region=text(props.get('NomRegionDiffusion')),
                      road=text(props.get('NumeroRoute')).lstrip('0'))
    if key == 'cameras_alertca':
        attributes, geometry = item['attributes'], item.get('geometry') or {}
        return camera(feed, attributes.get('siteId', '') + '-' + str(attributes['OBJECTID']), attributes['cameraName'],
                      [geometry.get('x'), geometry.get('y')], 'ALERTCalifornia (UC San Diego)', 'United States',
                      image=attributes.get('imageURL'), page=attributes.get('cameraURL'),
                      available=attributes.get('isActive') == 'active' and attributes.get('isOnline') == 'online',
                      modified=timestamp(text(attributes.get('viewTime')).replace(' ', 'T')), refresh=60, state='California',
                      county=text(attributes.get('county')).title(), camera_type='wildfire detection',
                      media_kind='wildfire-watch camera stills (pan/tilt/zoom views change)')
    if key == 'cameras_drivebc':
        links = item.get('links') or {}
        image = text(links.get('imageDisplay'))
        return camera(feed, item['id'], ' · '.join(part for part in (text(item.get('name')), text(item.get('caption'))) if part),
                      (item.get('location') or {}).get('coordinates'), 'DriveBC', 'Canada',
                      image=('https://www.drivebc.ca' + image) if image.startswith('/') else image,
                      available=not item.get('marked_stale') and not item.get('marked_delayed'), refresh=300, state='British Columbia',
                      road=text(item.get('highway_display') or item.get('highway')), region=text(item.get('region_name')),
                      direction=text(item.get('orientation')))
    if key == 'cameras_iceland':
        # A station number covers several cameras; the image file names each one.
        image = text(item.get('Slod'))
        identity = image.rsplit('/', 1)[-1].rsplit('.', 1)[0] or str(item['Maelist_nr'])
        return camera(feed, identity, ' · '.join(part for part in (text(item.get('Myndavel')), text(item.get('Skyring'))) if part),
                      [item.get('Lengd'), item.get('Breidd')], 'Vegagerðin', 'Iceland', image=item.get('Slod'), refresh=600,
                      road=text(item.get('Vegheiti')))
    if key == 'cameras_toronto':
        props, geometry = item['properties'], item.get('geometry') or {}
        point = geometry.get('coordinates')
        if geometry.get('type') == 'MultiPoint' and isinstance(point, list) and point:
            point = point[0]
        return camera(feed, props['REC_ID'], ' / '.join(part for part in (text(props.get('MAINROAD')).title(), text(props.get('CROSSROAD')).title()) if part),
                      point, 'City of Toronto', 'Canada', image=props.get('IMAGEURL'), refresh=120, city='Toronto', state='Ontario')
    if key == 'cameras_calgary':
        image = text((item.get('camera_url') or {}).get('url'))
        return camera(feed, image.rsplit('/', 1)[-1] or item.get('camera_location'), text(item.get('camera_location')),
                      (item.get('point') or {}).get('coordinates'), 'City of Calgary', 'Canada',
                      image=image.replace('http://', 'https://', 1), refresh=120, city='Calgary', state='Alberta',
                      region=text(item.get('quadrant')))
    if key == 'cameras_illinois':
        attributes = item['attributes']
        page = text(attributes.get('ImgPath'))
        # One site has a view per direction; the id+direction pair is the stable identity.
        query = page.partition('?')[2]
        identifier = query.replace('id=', '').replace('&direction=', '-') if query else str(attributes.get('OBJECTID'))
        return camera(feed, identifier, text(attributes.get('CameraLocation')), [attributes.get('x'), attributes.get('y')],
                      'Illinois DOT / Travel Midwest', 'United States', page=page, available=attributes.get('TooOld') != 'true',
                      media_kind='still image in the publisher viewer', state='Illinois',
                      direction=text(attributes.get('CameraDirection')).replace('NONE', ''))
    if key == 'cameras_michigan':
        link = MICHIGAN_LINK.search(text(item.get('county')))
        if not link:
            raise ValueError('Michigan camera has no map link.')
        county = TAGS.sub('', text(item.get('county'))).replace('Go to', '').strip()
        return camera(feed, link[4], ' '.join(part for part in (text(item.get('route')), text(item.get('location')).strip()) if part),
                      [link[3], link[2]], 'Michigan DOT (Mi Drive)', 'United States',
                      page='https://mdotjboss.state.mi.us' + link[1].replace('&amp;', '&'),
                      media_kind='still image in the publisher viewer', state='Michigan', county=county,
                      direction=text(item.get('direction')))
    if key == 'cameras_wsdot':
        location = item.get('CameraLocation') or {}
        return camera(feed, item['CameraID'], text(item.get('Title')), [location.get('Longitude'), location.get('Latitude')],
                      'WSDOT', 'United States', image=item.get('ImageURL'), available=item.get('IsActive') is not False,
                      refresh=120, state='Washington', road=text(location.get('RoadName')), direction=text(location.get('Direction')))
    if key == 'cameras_spain':
        title = ' '.join(part for part in (text(item.get('road')), f"km {item['km']}" if item.get('km') else '',
                                           f"towards {text(item['destination']).title()}" if item.get('destination') else '') if part)
        return camera(feed, item['id'], title or f"DGT camera {item['id']}", [item.get('longitude'), item.get('latitude')],
                      'DGT (Dirección General de Tráfico)', 'Spain', image=item.get('image'), refresh=300,
                      modified=timestamp(item.get('updated')), state=text(item.get('province')).title(),
                      road=text(item.get('road')), direction=text(item.get('destination')).title())
    if key == 'cameras_madrid':
        return camera(feed, item['number'], text(item.get('name')).title(), item.get('point'), 'Ayuntamiento de Madrid',
                      'Spain', image=item.get('image'), refresh=300, city='Madrid', state='Comunidad de Madrid')
    if key == 'cameras_oregon':
        props = item['attributes']
        filename = text(props.get('filename'))
        return camera(feed, props['cameraId'], text(props.get('title')), [props.get('longitude'), props.get('latitude')],
                      'ODOT TripCheck', 'United States',
                      image=('https://www.tripcheck.com/RoadCams/cams/' + quote(filename)) if filename else '',
                      available=bool(filename), refresh=300, state='Oregon', road=text(props.get('route')).strip())
    if key == 'cameras_norway':
        props = item['properties']
        identity = text(props.get('cameraId'), 60)
        if not re.fullmatch(r'[\w.-]{1,40}', identity):
            raise ValueError('Norwegian camera without a usable id.')
        hls = text(props.get('videoEncodingStandard')).lower() == 'hls'
        road = text(props.get('roadNumber'))
        place = text(props.get('orientationDescription'))
        return camera(feed, identity, ' · '.join(part for part in (road, place) if part) or f'Camera {identity}',
                      (item.get('geometry') or {}).get('coordinates'), 'Statens vegvesen', 'Norway',
                      image=text(props.get('stillImageUrl'), 300),
                      video=f'https://kamera.vegvesen.no/public/{identity}/manifest.m3u8' if hls else '',
                      available=props.get('status.stillImageAvailability') == 'videoOrImagesAvailable',
                      modified=timestamp(text(props.get('lastUpdateTime'))), refresh=300, road=road,
                      media_kind='road camera stills' + (' and live HLS video' if hls else ''),
                      licence='NLOD 2.0 — Statens vegvesen')
    if key == 'cameras_estonia':
        return camera(feed, item['id'], item['name'], [item.get('longitude'), item.get('latitude')],
                      'Transpordiamet (Tark tee)', 'Estonia', image=item.get('image'), modified=timestamp(item.get('time')),
                      refresh=900, media_kind='road-weather camera stills (each address is one capture)')
    if key == 'cameras_tallinn':
        return camera(feed, item['id'], item['name'], [item.get('lon'), item.get('lat')], 'City of Tallinn', 'Estonia',
                      image=item.get('image'), refresh=60, city='Tallinn',
                      media_kind='intersection camera stills, replaced about every minute',
                      catalog_credit='Camera list curated by github.com/bilawalsidhu/gods-eye-view (MIT)')
    if key == 'cameras_ottawa':
        owner = text(item.get('cameraOwner'))
        return camera(feed, item['id'], text(item.get('name')), [item.get('longitude'), item.get('latitude')],
                      'City of Ottawa' if owner in ('', 'CITY') else f'Ottawa traffic ({owner})', 'Canada',
                      image=f"https://traffic.ottawa.ca/beta/camera?id={int(item['camera_number'])}", refresh=120,
                      city='Ottawa', state='Ontario')
    if key.startswith('cameras_cars_'):
        state = key.rsplit('_', 1)[-1]
        if item.get('public') is False:
            return None
        views = [view for view in item.get('views') or [] if isinstance(view, dict)]
        stills = [view.get('url') for view in views if view.get('type') == 'STILL_IMAGE']
        previews = [] if state in CARS_NO_PREVIEW else [view.get('videoPreviewUrl') for view in views]
        images = [url for url in dict.fromkeys(stills + previews) if url and camera_url(url, key)]
        videos = [url for url in (view.get('url') for view in views if view.get('type') == 'WMP')
                  if url and camera_url(url, key, video=True)]
        if not images and not videos:
            return None  # MJPEG behind a vendor key, or a host this network does not allowlist.
        location = item.get('location') or {}
        title = next(name for code, name, _site in CARS_STATES if code == state)
        return camera(feed, item['id'], text(item.get('name')), [location.get('longitude'), location.get('latitude')],
                      title.split(' (')[0], 'United States', image=images[0] if images else '',
                      video=videos[0] if videos else '', refresh=120, state=CARS_STATE_NAMES[state],
                      road=text(location.get('routeId')), city=text(location.get('cityReference')).replace('in ', '', 1),
                      other_views=images[1:5] or None,
                      media_kind=('live HLS video' if videos else '') + (' and ' if videos and images else '')
                                 + ('refreshing still' if images else ''))
    if key.startswith('discovered_'):
        from app.intelligence.discovery import discovered_camera
        return discovered_camera(feed, item)
    if key.startswith('cameras_txdot_'):
        code = key.rsplit('_', 1)[-1].upper()
        identity = text(item.get('icd_Id'))
        image = (f'https://its.txdot.gov{TXDOT_SNAPSHOT}?icdId={quote(identity, safe="")}&districtCode={code}'
                 if item.get('hasSnapshot') and identity else '')
        return camera(feed, identity, text(item.get('name')) or identity, [item.get('longitude'), item.get('latitude')],
                      'TxDOT', 'United States', image=image, refresh=120, state='Texas',
                      available=bool(image) and str(item.get('statusDescription')) == 'Device Online',
                      road=text((item.get('equipLoc') or {}).get('roadway')), direction=text(item.get('dirDescription')),
                      region=feed.title.split(' · ')[1].replace(' district traffic cameras', ''))
    if key == 'cameras_alabama':
        if item.get('accessLevel') != 'Public':
            return None  # ALDOT-only and first-responder cameras are not public.
        location = item.get('location') or {}
        title = ' at '.join(part for part in (text(location.get('displayRouteDesignator')),
                                             text(location.get('displayCrossStreet'))) if part)
        return camera(feed, item['id'], title or f"ALGO camera {item['id']}",
                      [location.get('longitude'), location.get('latitude')], 'ALDOT (ALGO Traffic)', 'United States',
                      image=item.get('snapshotImageUrl'), video=(item.get('playbackUrls') or {}).get('hls'),
                      page=item.get('permLink'), refresh=120, state='Alabama', city=text(location.get('city')),
                      county=text(location.get('county')), direction=text(location.get('direction')),
                      media_kind='live HLS video and refreshing still')
    if key == 'cameras_missouri':
        video = camera_url(item.get('html'), key, video=True)
        if not video:
            return None
        identity = video.rstrip('/').split('/')[-2]
        return camera(feed, identity, text(item.get('location')), [item.get('x'), item.get('y')], 'MoDOT', 'United States',
                      video=video, refresh=120, state='Missouri', media_kind='live HLS video stream')
    if key == 'cameras_volcanoes':
        latitude, longitude = finite(item.get('latitude')), finite(item.get('longitude'))
        if latitude is None or longitude is None or (abs(latitude) < 0.01 and abs(longitude) < 0.01):
            return None  # 0,0 is a missing location, not a camera in the Gulf of Guinea.
        newest = finite(item.get('lastImageTimestamp'))
        from datetime import UTC, datetime
        recent = bool(newest and datetime.now(UTC).timestamp() - newest < 3 * 86400)
        volcano = text(item.get('vName'))
        return camera(feed, item['webcamCode'], text(item.get('webcamName')) + (f' ({volcano})' if volcano else ''),
                      [longitude, latitude], 'USGS volcano observatories (AshCam)', '',
                      image=item.get('currentMediumImageUrl') or item.get('currentImageUrl'),
                      available=item.get('hasImages') == 'Y' and recent, refresh=600,
                      modified=timestamp(datetime.fromtimestamp(newest, UTC).isoformat()) if newest else None,
                      volcano=volcano, camera_type='volcano webcam', page=None,
                      media_kind='volcano observatory webcam still; the newest image time is publisher-reported')
    if key.startswith('cameras_511_'):
        # 511 v2 API: one record per site with Views[{Url, Status}]; older sites put Url on the record.
        views = item.get('Views') if isinstance(item.get('Views'), list) else [{'Url': item.get('Url'), 'Status': item.get('Status')}]
        live = [view for view in views if isinstance(view, dict) and str(view.get('Status') or 'Enabled').lower() != 'disabled']
        image = text((live or views or [{}])[0].get('Url'))
        title = text(item.get('Location') or item.get('Name') or item.get('Roadway'))
        return camera(feed, item.get('Id') or item.get('ID'), title, [item.get('Longitude'), item.get('Latitude')],
                      feed.title.split(' · ')[0], 'Canada' if feed.key.split('_')[-1] in CANADIAN_511 else 'United States',
                      image=image, available=bool(live), refresh=120, road=text(item.get('Roadway')),
                      direction=text(item.get('Direction') or item.get('DirectionOfTravel')),
                      other_views=[url for view in live[1:5] if (url := camera_url(text(view.get('Url')), feed.key))] or None)
    raise ValueError('Unknown camera network.')


def _safe_xml(content):
    from app.security import xml as ElementTree
    return ElementTree


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def datex_cameras(content):
    """Camera devices from a DATEX II DevicePublication (Spain's DGT), as flat records."""
    from io import BytesIO
    ElementTree = _safe_xml(content)
    rows = []
    for _event, element in ElementTree.iterparse(BytesIO(content), events=('end',)):
        if _local(element.tag) != 'device':
            continue
        values = {_local(child.tag): (child.text or '').strip() for child in element.iter()}
        if values.get('typeOfDevice', 'camera') == 'camera' and element.get('id'):
            rows.append({'id': element.get('id'), 'road': values.get('roadName'), 'destination': values.get('roadDestination'),
                         'km': values.get('kilometerPoint'), 'province': values.get('province'),
                         'latitude': finite(values.get('latitude')), 'longitude': finite(values.get('longitude')),
                         'image': values.get('deviceUrl'), 'updated': values.get('lastUpdateOfDeviceInformation')})
        element.clear()
        if len(rows) > 10000:
            break
    return rows


TARKTEE_IMAGE = 'https://tarktee.transpordiamet.ee/images/'


def tarktee_cameras(locations, images):
    """Estonia's two DATEX II documents joined: camera places (name, point) and their latest image."""
    from io import BytesIO
    ElementTree = _safe_xml(locations)
    places = {}
    for _event, element in ElementTree.iterparse(BytesIO(locations), events=('end',)):
        if _local(element.tag) != 'predefinedLocation' or not element.get('id'):
            continue
        values = {}
        for child in element.iter():
            name = _local(child.tag)
            if name in ('latitude', 'longitude', 'value') and name not in values:
                values[name] = (child.text or '').strip()
        latitude, longitude = finite(values.get('latitude')), finite(values.get('longitude'))
        if latitude is not None and longitude is not None:
            places[element.get('id')] = {'name': values.get('value') or element.get('id'), 'latitude': latitude,
                                         'longitude': longitude}
        element.clear()
    rows = []
    for _event, element in ElementTree.iterparse(BytesIO(images), events=('end',)):
        if _local(element.tag) != 'trafficView':
            continue
        reference, url, moment = '', '', ''
        for child in element.iter():
            name = _local(child.tag)
            if name == 'linearPredefinedLocationReference':
                reference = child.get('id') or ''
            elif name == 'urlLinkAddress':
                url = (child.text or '').strip()
            elif name == 'trafficViewTime':
                moment = (child.text or '').strip()
        place = places.get(reference)
        if place and url.startswith(TARKTEE_IMAGE):
            rows.append({'id': reference, **place, 'image': url, 'time': moment})
        element.clear()
        if len(rows) > 5000:
            break
    return rows


KML_IMAGE = re.compile(r"""(?i)src\s*=\s*["']?(https://[^"'\s>?]+\.jpe?g)""")


def kml_cameras(content):
    """Camera placemarks from a KML file (Madrid): number, name, point and the still in its description."""
    ElementTree = _safe_xml(content)
    root = ElementTree.fromstring(content.lstrip(b'\xef\xbb\xbf'))
    rows = []
    for placemark in root.iter():
        if _local(placemark.tag) != 'Placemark':
            continue
        data, description, coordinates = {}, '', ''
        for child in placemark.iter():
            name = _local(child.tag)
            if name == 'Data':
                value = next((item.text for item in child if _local(item.tag).lower() == 'value'), '')
                data[child.get('name')] = (value or '').strip()
            elif name == 'description':
                description = child.text or ''
            elif name == 'coordinates':
                coordinates = (child.text or '').strip()
        parts = coordinates.split(',')
        image = KML_IMAGE.search(description)
        publisher_id = re.fullmatch(r'camera_([0-9]{1,6})', placemark.get('id') or '')
        label = next((child.text for child in placemark if _local(child.tag) == 'name'), '')
        rows.append({'number': data.get('Numero') or (publisher_id[1] if publisher_id else None), 'name': data.get('Nombre') or label,
                     'point': [finite(parts[0]), finite(parts[1])] if len(parts) >= 2 else None,
                     'image': image[1] if image else ''})
    return rows[:10001]


CANADIAN_511 = {'ontario', 'alberta', 'saskatchewan', 'manitoba', 'newbrunswick', 'novascotia'}


# CARS icon names are <type>_<urgency> (tg_closure_urgent.png); order matters ("lane_closure" before "closure").
ROAD_KINDS = (('lane_closure', 'lane closure'), ('closure', 'closure'), ('ramp_impact', 'ramp restriction'),
              ('restriction', 'restriction'), ('roundabout', 'construction'), ('major_construction', 'construction'),
              ('contruction', 'construction'), ('construction', 'construction'), ('roadwork', 'construction'),
              ('crash', 'crash'), ('incident', 'incident'), ('accident', 'crash'), ('weather', 'weather'),
              ('winter', 'weather'), ('flood', 'weather'), ('warning', 'warning'), ('driving_normal', 'conditions'),
              ('enforcement', 'information'), ('info', 'information'))


def road_event(feed, item):
    """One CARS road event: closure, construction, incident, warning or weather."""
    representation = item.get('representation') if isinstance(item.get('representation'), dict) else {}
    icon = str((representation.get('iconProperties') or {}).get('image') or '').lower()
    kind = next((name for word, name in ROAD_KINDS if word in icon), 'road event')
    urgency = 'critical' if 'critical' in icon else 'urgent' if 'urgent' in icon else 'routine'
    words = ' '.join(TAGS.sub(' ', text(item.get('tooltip'), 3000)).split())
    bounds = item.get('bounds') or {}
    point = None
    if all(isinstance(bounds.get(key), (int, float)) for key in ('minLat', 'maxLat', 'minLon', 'maxLon')):
        point = [(bounds['minLon'] + bounds['maxLon']) / 2, (bounds['minLat'] + bounds['maxLat']) / 2]
    state = feed.key.rsplit('_', 1)[-1]
    start = (item.get('startTime') or {}).get('time')
    return event(feed, item['id'], (words.split('. ')[0] or kind.title())[:200], kind,
                 timestamp(start, milliseconds=True) if isinstance(start, (int, float)) else None,
                 feed.documentation, point, provider=feed.title.split(' · ')[0], country='United States',
                 state=CARS_STATE_NAMES.get(state, state.upper()), description=words[:600],
                 priority=item.get('priority'), urgency=urgency, active=bool(item.get('active', True)),
                 verified=bool(item.get('verified')))


def wildfire(feed, item):
    props = item.get('properties') or {}
    size, contained = finite(props.get('IncidentSize')), finite(props.get('PercentContained'))
    return event(feed, props.get('UniqueFireIdentifier') or item.get('id'), text(props.get('IncidentName')) + ' Fire',
                 'wildfire', timestamp(props.get('ModifiedOnDateTime_dt'), milliseconds=True), feed.documentation,
                 (item.get('geometry') or {}).get('coordinates'), provider='NIFC (WFIGS)', country='United States',
                 state=text(props.get('POOState')).replace('US-', ''), size_acres=size, percent_contained=contained,
                 discovered_at=timestamp(props.get('FireDiscoveryDateTime'), milliseconds=True))


def livestream(feed, item):
    point = [item['longitude'], item['latitude']] if item.get('latitude') is not None and item.get('longitude') is not None else None
    row = camera(feed, item['id'], item['title'], point, item['provider'],
                  item['country'], page=item['page'], image=item.get('image_url', ''), refresh=60,
                  city=item.get('city'), state=item.get('state'), official_publisher=True,
                  availability_basis='Registered publisher; current broadcast checked when played',
                  live_page='' if item.get('image_url') else item['page'], keywords=item.get('keywords'),
                  media_kind='refreshing publisher still' if item.get('image_url') else 'official live broadcast (resolved when played)')
    row['url'] = row['details']['page_url']
    return row


def normalize_extended(feed, payload):
    if feed.key.startswith('roads_') or feed.key in ('wildfires_us', 'livestreams'):
        items = (payload.get('features') if isinstance(payload, dict) else None) if feed.key == 'wildfires_us' else payload
        if not isinstance(items, list):
            raise ValueError('Provider response must contain a bounded array.')
        builder = road_event if feed.key.startswith('roads_') else wildfire if feed.key == 'wildfires_us' else livestream
        rows, rejected = {}, 0
        for item in items[:10000]:
            try:
                row = builder(feed, item)
                rows[row['id']] = row
            except (ValueError, TypeError, KeyError, AttributeError, IndexError):
                rejected += 1
        if items and not rows:
            raise ValueError('All provider records failed validation; the prior snapshot is retained.')
        return rows, rejected
    if open_network(feed.key):
        items = open_camera_items(feed, payload)
    elif feed.key == 'cameras_london':
        items = payload
    elif feed.key.startswith('cameras_caltrans_'):
        items = payload.get('data') if isinstance(payload, dict) else None
    elif feed.key == 'disasters':
        items = payload.get('features') if isinstance(payload, dict) else None
    else:
        raise ValueError('Unknown intelligence feed.')
    if not isinstance(items, list) or len(items) > 10000:
        raise ValueError('Provider response must contain a bounded catalog array.')
    rows, rejected = {}, 0
    for item in items:
        try:
            if open_network(feed.key):
                row = open_camera(feed, item)
                if row is None:
                    continue
            elif feed.key == 'cameras_london':
                props = {value['key']: value.get('value') for value in item.get('additionalProperties', [])}
                row = event(feed, item['id'], item['commonName'], 'public_camera', None, feed.documentation,
                            [item.get('lon'), item.get('lat')], provider='Transport for London', country='United Kingdom', city='London',
                            image_url=camera_url(props.get('imageUrl'), feed.key), video_url=camera_url(props.get('videoUrl'), feed.key, video=True),
                            available=str(props.get('available')).lower() in ('true', 'yes'), direction=text(props.get('view')),
                            catalog_modified_at=max((timestamp(value.get('modified')) or '' for value in item.get('additionalProperties', [])), default=''),
                            refresh_seconds=180, media_kind='periodically refreshed image / short video clip',
                            limitation='Catalog availability is provider-reported. Fetching a frame does not prove it depicts the current instant.')
            elif feed.key.startswith('cameras_caltrans_'):
                camera = item['cctv']
                location, image = camera['location'], camera.get('imageData', {})
                still = image.get('static', {})
                url = camera_url(still.get('currentImageURL'), feed.key)
                identity = url or feed.key + ':' + str(camera['index'])
                row = event(feed, identity.rsplit('/', 1)[-1] if url else camera['index'], location['locationName'], 'public_camera', None, feed.documentation,
                            [location.get('longitude'), location.get('latitude')], provider='Caltrans', country='United States', state='California',
                            image_url=url, video_url=camera_url(image.get('streamingVideoURL'), feed.key, video=True),
                            available=str(camera.get('inService')).lower() == 'true', direction=text(location.get('direction')),
                            nearby_place=text(location.get('nearbyPlace')), county=text(location.get('county')), road=text(location.get('route')),
                            catalog_modified_at=timestamp(finite(camera.get('recordTimestamp', {}).get('recordEpoch'))),
                            refresh_seconds=max(60, min(3600, (finite(still.get('currentImageUpdateFrequency')) or 5)*60)),
                            media_kind='periodically refreshed image / HLS video when supplied',
                            limitation='Camera catalog timestamps describe metadata, not frame capture time. Availability and playback can change.')
            else:
                props = item['properties']
                urls = props.get('url') or {}
                row = event(feed, f"{props['eventtype']}-{props['eventid']}", props.get('name') or props.get('eventname'),
                            props['eventtype'], timestamp(props.get('datemodified') or props.get('todate')),
                            urls.get('report') if isinstance(urls, dict) else feed.documentation,
                            point=(item.get('geometry') or {}).get('coordinates') if (item.get('geometry') or {}).get('type') == 'Point' else None,
                            description=text(props.get('description')), provider='Global Disaster Alert and Coordination System, GDACS',
                            country=text(props.get('country')), alert_level=text(props.get('alertlevel')), start_time=timestamp(props.get('fromdate')),
                            end_time=timestamp(props.get('todate')), provider_current=str(props.get('iscurrent')).lower() == 'true',
                            location_basis=text(props.get('polygonlabel') or 'Provider representative point'),
                            severity=text((props.get('severitydata') or {}).get('severitytext')),
                            limitation='A representative point is not the full affected area. GDACS and USGS may report the same event; counts are not unique incidents.')
            if not row['title'] or row['id'].endswith(':'):
                raise ValueError('Missing provider identity or title.')
            if feed.key == 'cameras_valencia' and row['id'] in rows:
                previous = rows[row['id']]
                if (previous['title'], previous['latitude'], previous['longitude']) != (row['title'], row['latitude'], row['longitude']):
                    places = previous['details'].get('reported_locations') or [{
                        'title': previous['title'], 'latitude': previous['latitude'], 'longitude': previous['longitude']}]
                    candidate = {'title': row['title'], 'latitude': row['latitude'], 'longitude': row['longitude']}
                    if candidate not in places and len(places) < 8:
                        places.append(candidate)
                    previous['latitude'] = previous['longitude'] = None
                    previous['title'] = 'Camera ' + row['id'].split(':', 1)[1] + ' · conflicting publisher locations'
                    previous['details']['reported_locations'] = places
                    previous['details']['catalog_conflicts'] = 'The same publisher ID has different titles/locations; no map position is selected.'
                continue
            rows[row['id']] = row
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            rejected += 1
    if items and not rows:
        raise ValueError('All provider records failed validation; the prior snapshot is retained.')
    return rows, rejected
