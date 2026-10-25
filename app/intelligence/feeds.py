"""Public feed contracts: original adapters, no Osiris server dependency."""
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import math


@dataclass(frozen=True)
class Feed:
    key: str
    title: str
    url: str
    interval: int
    documentation: str
    format: str = 'json'
    # ArcGIS layers return pages of maxRecordCount; fetch up to this many pages.
    pages: int = 0
    # Name of the .env variable holding a free developer key; the URL carries {key}.
    key_env: str = ''
    # Largest catalog response accepted. Spain's national DATEX II camera file is 3.7 MB.
    max_bytes: int = 4 * 1024 * 1024
    # Seconds for one catalog request when a publisher is slow (Estonia's DATEX takes ~12 s); None = fetcher default.
    timeout: float | None = None


FEEDS = (
    Feed('earthquakes', 'USGS earthquakes · M2.5+ past day',
         'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson', 300,
         'https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php'),
    Feed('natural_events', 'NASA EONET · open natural events',
         'https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=200', 1800,
         'https://eonet.gsfc.nasa.gov/docs/v3'),
    Feed('cyber', 'CISA known exploited vulnerabilities',
         'https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json', 3600,
         'https://github.com/cisagov/kev-data'),
    Feed('space_weather', 'NOAA space weather bulletins',
         'https://services.swpc.noaa.gov/products/alerts.json', 900,
         'https://www.spaceweather.gov/products/alerts-watches-and-warnings'),
)
EXTENDED_FEEDS = (
    Feed('disasters', 'GDACS · global disaster reports',
         'https://www.gdacs.org/gdacsapi/api/Events/geteventlist/SEARCH?eventlist=EQ%3BTC%3BFL%3BVO%3BDR%3BWF', 1800,
         'https://www.gdacs.org/Documents/2025/GDACS_API_quickstart_v1.pdf'),
    Feed('cameras_london', 'Transport for London · public JamCams',
         'https://api.tfl.gov.uk/Place/Type/JamCam', 3600,
         'https://tfl.gov.uk/info-for/open-data-users/our-open-data'),
    *(Feed(f'cameras_caltrans_{district}', f'Caltrans · district {district} public traffic cameras',
           f'https://cwwp2.dot.ca.gov/data/d{district}/cctv/cctvStatusD{district:02d}.json', 3600,
           'https://cwwp2.dot.ca.gov/documentation/cctv/cctv.htm') for district in range(1, 13)),
)
#: CARS states whose road events are small enough to fetch every two hours.
ROAD_EVENT_STATES = ('mn', 'co', 'in', 'ks', 'ma')
#: TxDOT district codes and names.
TXDOT_DISTRICTS = (
    ('ABL', 'Abilene'), ('AMA', 'Amarillo'), ('ATL', 'Atlanta'), ('AUS', 'Austin'), ('BMT', 'Beaumont'),
    ('BWD', 'Brownwood'), ('BRY', 'Bryan'), ('CHS', 'Childress'), ('CRP', 'Corpus Christi'), ('DAL', 'Dallas'),
    ('ELP', 'El Paso'), ('FTW', 'Fort Worth'), ('HOU', 'Houston'), ('LRD', 'Laredo'), ('LBB', 'Lubbock'),
    ('LFK', 'Lufkin'), ('ODA', 'Odessa'), ('PAR', 'Paris'), ('PHR', 'Pharr'), ('SJT', 'San Angelo'),
    ('SAT', 'San Antonio'), ('TYL', 'Tyler'), ('WAC', 'Waco'), ('WFS', 'Wichita Falls'), ('YKM', 'Yoakum'),
)
#: (state code, title, public site) of the CARS 511 gateways connected without a key.
CARS_STATES = (
    ('mn', 'Minnesota DOT (511mn)', 'https://511mn.org/'),
    ('co', 'Colorado DOT (COtrip)', 'https://www.cotrip.org/'),
    ('ne', 'Nebraska DOT (Nebraska 511)', 'https://new.511.nebraska.gov/'),
    ('in', 'Indiana DOT (TrafficWise)', 'https://511.in.gov/'),
    ('ks', 'Kansas DOT (KanDrive)', 'https://www.kandrive.gov/'),
    ('ma', 'MassDOT (Mass511)', 'https://mass511.com/'),
)
# Open camera networks published by transport and weather agencies without an
# API key. Each was checked live, robots rules included, on 2026-10-05.
OPEN_CAMERA_FEEDS = (
    Feed('cameras_nsw', 'Transport for NSW · New South Wales public traffic camera catalog',
         'https://data.livetraffic.com/cameras/traffic-cam.json', 3600,
         'https://data.nsw.gov.au/data/en/dataset/2-live-traffic-cameras'),
    Feed('cameras_lyon', 'Métropole de Lyon · Criter public traffic cameras',
         'https://data.grandlyon.com/geoserver/metropole-de-lyon/ows?SERVICE=WFS&VERSION=2.0.0&request=GetFeature'
         '&typename=metropole-de-lyon:pvo_patrimoine_voirie.pvocameracriter&outputFormat=application/json&SRSNAME=EPSG:4326',
         3600, 'https://www.data.gouv.fr/datasets/cameras-web-criter-de-la-metropole-de-lyon'),
    Feed('cameras_valencia', 'Ajuntament de València · public traffic camera locations and publisher viewers',
         'https://geoportal.valencia.es/server/rest/services/OPENDATA/Trafico/MapServer/190/query'
         '?where=1%3D1&outFields=*&outSR=4326&f=json', 3600,
         'https://opendata.vlci.valencia.es/dataset/cameres-trafic-camaras-trafico', pages=4),
    Feed('cameras_luxembourg', 'CITA · Luxembourg public motorway cameras',
         'https://www.cita.lu/kml/cameras.kml', 3600,
         'https://data.public.lu/fr/datasets/cita-cameras-autoroute/', 'kml'),
    Feed('cameras_washington', 'WSDOT · Washington public camera map (no key)',
         'https://data.wsdot.wa.gov/arcgis/rest/services/TravelInformation/TravelInfoCamerasWeather/FeatureServer/0/query'
         '?where=1%3D1&outFields=*&outSR=4326&f=json', 3600,
         'https://wsdot.com/travel/real-time/cameras', pages=8),
    Feed('cameras_nyc', 'New York City DOT · public traffic cameras',
         'https://webcams.nyctmc.org/api/cameras', 3600, 'https://webcams.nyctmc.org/'),
    Feed('cameras_finland', 'Fintraffic Digitraffic · road weather cameras (Finland)',
         'https://tie.digitraffic.fi/api/weathercam/v1/stations', 3600, 'https://www.digitraffic.fi/en/road-traffic/'),
    Feed('cameras_singapore', 'Singapore LTA · traffic images (data.gov.sg)',
         'https://api.data.gov.sg/v1/transport/traffic-images', 600, 'https://data.gov.sg/'),
    Feed('cameras_hongkong', 'Hong Kong Transport Department · traffic snapshots',
         'https://static.data.gov.hk/td/traffic-snapshot-images/code/Traffic_Camera_Locations_En.xml', 3600,
         'https://data.gov.hk/en-data/dataset/hk-td-tis_2-traffic-snapshot-images', 'xml'),
    Feed('cameras_nz', 'NZ Transport Agency · state highway cameras',
         'https://trafficnz.info/service/traffic/rest/4/cameras/all', 3600, 'https://www.nzta.govt.nz/traffic-and-travel-information/'),
    Feed('cameras_delaware', 'Delaware DOT · live traffic video',
         'https://tmc.deldot.gov/json/videocamera.json', 3600, 'https://deldot.gov/map/'),
    Feed('cameras_maryland', 'Maryland CHART · traffic cameras',
         'https://chartexp1.sha.maryland.gov/CHARTExportClientService/getCameraMapDataJSON.do', 3600, 'https://chart.maryland.gov/'),
    Feed('cameras_austin', 'City of Austin · traffic cameras',
         'https://data.austintexas.gov/resource/b4k4-adkb.json?$limit=5000', 3600,
         'https://data.austintexas.gov/Transportation-and-Mobility/Traffic-Cameras/b4k4-adkb'),
    Feed('cameras_seattle', 'Seattle DOT and WSDOT · Seattle traffic cameras',
         'https://web.seattle.gov/Travelers/api/Map/Data?zoomId=13&type=2', 3600, 'https://web.seattle.gov/Travelers/'),
    Feed('cameras_iowa', 'Iowa DOT · traffic, weather and rest-area cameras',
         'https://services.arcgis.com/8lRhdTsQyJpO52F1/arcgis/rest/services/Traffic_Cameras_View/FeatureServer/0/query?where=1%3D1&outFields=*&f=json',
         3600, 'https://www.511ia.org/'),
    Feed('cameras_quebec', 'Québec 511 · road cameras',
         'https://ws.mapserver.transports.gouv.qc.ca/swtq?service=wfs&version=2.0.0&request=getfeature&typename=ms:infos_cameras'
         '&outfile=Camera&srsname=EPSG:4326&outputformat=geojson', 3600, 'https://www.quebec511.info/'),
    Feed('cameras_alertca', 'ALERTCalifornia (UC San Diego) · wildfire cameras',
         'https://services8.arcgis.com/X84q166Srnyl4JMV/arcgis/rest/services/ALERTCalifornia_Camera_Feed/FeatureServer/0/query'
         '?where=1%3D1&outFields=*&outSR=4326&f=json', 3600, 'https://alertcalifornia.org/', pages=40),
    Feed('cameras_drivebc', 'DriveBC · British Columbia highway cameras',
         'https://www.drivebc.ca/api/webcams/', 3600, 'https://www.drivebc.ca/'),
    Feed('cameras_iceland', 'Vegagerðin · Iceland road cameras',
         'https://gagnaveita.vegagerdin.is/api/vefmyndavelar2014_1', 3600, 'https://www.vegagerdin.is/'),
    Feed('cameras_toronto', 'City of Toronto · traffic cameras',
         'https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/a3309088-5fd4-4d34-8297-77c8301840ac/resource/'
         '4a568300-c7f8-496d-b150-dff6f5dc6d4f/download/traffic-camera-list-4326.geojson', 3600,
         'https://open.toronto.ca/dataset/traffic-cameras/'),
    Feed('cameras_calgary', 'City of Calgary · traffic cameras',
         'https://data.calgary.ca/resource/k7p9-kppz.json', 3600, 'https://data.calgary.ca/'),
    Feed('cameras_illinois', 'Illinois DOT (Travel Midwest) · traffic cameras',
         'https://services2.arcgis.com/aIrBD8yn1TDTEXoz/arcgis/rest/services/TrafficCamerasTM_Public/FeatureServer/0/query'
         '?where=1%3D1&outFields=*&outSR=4326&f=json', 3600, 'https://travelmidwest.com/', pages=6),
    Feed('cameras_michigan', 'Michigan DOT (Mi Drive) · traffic cameras',
         'https://mdotjboss.state.mi.us/MiDrive/camera/list', 3600, 'https://mdotjboss.state.mi.us/MiDrive/map'),
    # Added 2026-10-05 for wider world coverage; each checked live, robots included.
    Feed('cameras_spain', 'DGT · Spain national road cameras (DATEX II)',
         'https://nap.dgt.es/datex2/v3/dgt/DevicePublication/camaras_datex2_v36.xml', 3600,
         'https://nap.dgt.es/', 'datex', max_bytes=16 * 1024 * 1024),
    Feed('cameras_madrid', 'Ayuntamiento de Madrid · city traffic cameras',
         'https://informo.madrid.es/informo/tmadrid/CCTV.kml', 3600,
         'https://datos.madrid.es/portal/site/egob/', 'kml'),
    Feed('cameras_oregon', 'ODOT TripCheck · Oregon road cameras',
         'https://www.tripcheck.com/Scripts/map/data/cctvinventory.js', 3600, 'https://www.tripcheck.com/'),
    # Added 2026-10-11 from the endpoints catalogued by github.com/bilawalsidhu/gods-eye-view (MIT);
    # each checked live through HAYAT's fetcher, robots.txt included.
    Feed('cameras_norway', 'Statens vegvesen · Norway road cameras (stills and live video)',
         'https://ogckart-sn1.atlas.vegvesen.no/ogc/features/v1/collections/datex_3_1:CctvSimple/items'
         '?f=application/geo%2Bjson&limit=5000', 3600, 'https://www.vegvesen.no/trafikk/', max_bytes=8 * 1024 * 1024),
    # Image addresses carry their capture time, so the catalog is re-read every 15 minutes.
    Feed('cameras_estonia', 'Transpordiamet (Tark tee) · Estonia road-weather cameras',
         'https://tarktee.transpordiamet.ee/api/v1/datex/roadCameraLocations', 900, 'https://tarktee.transpordiamet.ee/',
         'tarktee', timeout=30),
    Feed('cameras_tallinn', 'City of Tallinn · intersection cameras', 'static://cameras_tallinn', 86400,
         'https://ristmikud.tallinn.ee/', 'static'),
    Feed('cameras_ottawa', 'City of Ottawa · traffic cameras',
         'https://traffic.ottawa.ca/service/camera', 3600, 'https://traffic.ottawa.ca/'),
    Feed('cameras_volcanoes', 'USGS volcano observatories · AshCam volcano webcams',
         'https://volcview.wr.usgs.gov/ashcam-api/webcamApi/webcams', 3600, 'https://volcview.wr.usgs.gov/'),
    # Castle Rock CARS traveler gateways behind the 511 sites of these states:
    # keyless JSON, robots.txt allows it (South Dakota's disallows, so it is not here).
    *(Feed(f'cameras_cars_{state}', f'{title} · road cameras', f'https://{state}tg.carsprogram.org/cameras_v1/api/cameras',
           3600, site) for state, title, site in CARS_STATES),
    Feed('cameras_missouri', 'MoDOT Traveler · live traffic video (Missouri)',
         'https://traveler.modot.org/timconfig/feed/desktop/StreamingCams2.json', 3600, 'https://traveler.modot.org/'),
    Feed('livestreams', 'Official public broadcasts and webcams', 'static://livestreams', 86400,
         'https://www.youtube.com/@SaudiQuranTv/live', 'static'),
    Feed('cameras_alabama', 'ALDOT (ALGO Traffic) · Alabama traffic cameras',
         'https://api.algotraffic.com/v4.0/Cameras', 3600, 'https://algotraffic.com/'),
    # TxDOT publishes its camera list per district; 4,372 cameras across the 25 on 2026-10-06.
    *(Feed(f'cameras_txdot_{code.lower()}', f'TxDOT · {name} district traffic cameras',
           f'https://its.txdot.gov/its/DistrictIts/GetCctvStatusListByDistrict?districtCode={code}', 3600,
           f'https://its.txdot.gov/its/District/{code}/cameras') for code, name in TXDOT_DISTRICTS),
)
# Traveler-information networks that need a free developer key from the owner.
# Each turns on only when its .env variable is set; keys never leave the fetch URL.
KEYED_511 = (
    ('ontario', '511on.ca', 'Ontario 511'), ('alberta', '511.alberta.ca', 'Alberta 511'),
    ('saskatchewan', 'hotline.gov.sk.ca', 'Saskatchewan Highway Hotline'), ('manitoba', 'www.manitoba511.ca', 'Manitoba 511'),
    ('newbrunswick', '511.gnb.ca', 'New Brunswick 511'), ('novascotia', '511.novascotia.ca', 'Nova Scotia 511'),
    ('arizona', 'az511.com', 'Arizona AZ511'), ('georgia', '511ga.org', 'Georgia 511'), ('newyork', '511ny.org', '511NY'),
    ('pennsylvania', 'www.511pa.com', '511PA'), ('wisconsin', '511wi.gov', 'Wisconsin 511'), ('idaho', '511.idaho.gov', 'Idaho 511'),
    ('nevada', 'www.nvroads.com', 'Nevada NVRoads'), ('louisiana', 'www.511la.org', 'Louisiana 511'),
    ('alaska', '511.alaska.gov', 'Alaska 511'), ('utah', 'www.udottraffic.utah.gov', 'Utah UDOT Traffic'),
    ('connecticut', 'ctroads.org', 'Connecticut CTroads'), ('newengland', 'newengland511.org', 'New England 511'),
)
# Public event feeds beside the cameras: wildfires and road events.
EVENT_FEEDS = (
    Feed('wildfires_us', 'NIFC WFIGS · current US wildfire incidents',
         'https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query'
         "?where=IncidentTypeCategory%3D%27WF%27&outFields=IncidentName,IncidentSize,PercentContained,FireDiscoveryDateTime,"
         'POOState,ModifiedOnDateTime_dt,UniqueFireIdentifier&outSR=4326&f=geojson', 1800,
         'https://data-nifc.opendata.arcgis.com/'),
    # Road incidents, closures and construction from the same CARS gateways as the cameras.
    # Every two hours: about 13 MB per round. Nebraska's file is 50 MB and is left out.
    *(Feed(f'roads_cars_{state}', f'{title} · road events (incidents, closures, construction)',
           f'https://{state}tg.carsprogram.org/events_v1/api/eventMapFeatures', 7200, site, max_bytes=16 * 1024 * 1024)
      for state, title, site in CARS_STATES if state in ROAD_EVENT_STATES),
    # World reporting, classified into conflict / attack / emergency / news and merged into
    # intelligence objects with verification status (app/intelligence/reports.py, objects.py).
    Feed('news_bbc_world', 'BBC News · World', 'https://feeds.bbci.co.uk/news/world/rss.xml', 900,
         'https://www.bbc.co.uk/news/world', 'rss'),
    Feed('news_aljazeera', 'Al Jazeera · All news', 'https://www.aljazeera.com/xml/rss/all.xml', 900,
         'https://www.aljazeera.com/', 'rss'),
    Feed('news_un', 'UN News · All', 'https://news.un.org/feed/subscribe/en/news/all/rss.xml', 1800,
         'https://news.un.org/en/', 'rss'),
    # GDELT asks for at most one request every five seconds; once an hour is far inside that.
    Feed('gdelt_security', 'GDELT news index · conflict and security reporting',
         'https://api.gdeltproject.org/api/v2/doc/doc?query=%28airstrike%20OR%20explosion%20OR%20%22missile%20strike%22'
         '%20OR%20%22drone%20attack%22%20OR%20shelling%20OR%20clashes%29%20sourcelang%3Aenglish&mode=ArtList&format=json'
         '&timespan=6h&maxrecords=150&sort=DateDesc', 3600, 'https://www.gdeltproject.org/'),
    Feed('storms_nhc', 'NOAA National Hurricane Center · active storms', 'https://www.nhc.noaa.gov/CurrentStorms.json', 1800,
         'https://www.nhc.noaa.gov/'),
    Feed('airports_faa', 'FAA · US airport closures, ground stops and delays',
         'https://nasstatus.faa.gov/api/airport-status-information', 900, 'https://nasstatus.faa.gov/', 'faa'),
    Feed('earthquakes_emsc', 'EMSC · earthquakes M4+ worldwide (independent of USGS)',
         'https://www.seismicportal.eu/fdsnws/event/1/query?limit=200&format=json&minmag=4&orderby=time', 1800,
         'https://www.seismicportal.eu/'),
    # Official hazard warnings from the issuing authorities (app/intelligence/hazards.py), checked live 2026-10-14.
    Feed('tsunami_ntwc', 'NOAA National Tsunami Warning Center · tsunami bulletins (Atlantic, Gulf, Alaska, US West Coast)',
         'https://www.tsunami.gov/events/xml/PAAQAtom.xml', 600, 'https://www.tsunami.gov/', 'atom'),
    Feed('tsunami_ptwc', 'NOAA Pacific Tsunami Warning Center · tsunami bulletins (Pacific, Hawaii, Caribbean)',
         'https://www.tsunami.gov/events/xml/PHEBAtom.xml', 600, 'https://www.tsunami.gov/', 'atom'),
    Feed('volcanoes_usgs', 'USGS volcano alert levels · US volcanoes above normal',
         'https://volcanoes.usgs.gov/hans-public/api/volcano/getElevatedVolcanoes', 3600,
         'https://volcanoes.usgs.gov/hans-public/', 'hans', timeout=45),
    Feed('earthquakes_jma', 'Japan Meteorological Agency · earthquakes with measured shaking (shindo)',
         'https://www.jma.go.jp/bosai/quake/data/list.json', 900, 'https://www.jma.go.jp/bosai/map.html#contents=earthquake_map&lang=en'),
    Feed('earthquakes_geonet', 'GeoNet · felt earthquakes in New Zealand', 'https://api.geonet.org.nz/quake?MMI=3', 900,
         'https://api.geonet.org.nz/'),
    Feed('volcanoes_geonet', 'GeoNet · New Zealand volcanic alert levels', 'https://api.geonet.org.nz/volcano/val', 3600,
         'https://www.geonet.org.nz/volcano'),
    Feed('outbreaks_who', 'World Health Organization · Disease Outbreak News',
         'https://www.who.int/api/news/diseaseoutbreaknews?$top=30&$orderby=PublicationDate%20desc', 3600,
         'https://www.who.int/emergencies/disease-outbreak-news'),
    Feed('aviation_sigmet', 'NOAA Aviation Weather Center · SIGMETs (thunderstorms, turbulence, icing, ash)',
         'https://aviationweather.gov/api/data/airsigmet?format=json', 900, 'https://aviationweather.gov/data/api/'),
    Feed('floods_uk', 'Environment Agency · flood warnings in England',
         'https://environment.data.gov.uk/flood-monitoring/id/floods', 900,
         'https://environment.data.gov.uk/flood-monitoring/doc/reference', 'ukflood'),
    # Second wave, checked live 2026-10-15.
    Feed('earthquakes_bmkg', 'BMKG · Indonesia earthquakes M5+ with tsunami potential',
         'https://data.bmkg.go.id/DataMKG/TEWS/gempaterkini.json', 900, 'https://data.bmkg.go.id/gempabumi/'),
    Feed('fireballs_jpl', 'NASA/JPL CNEOS · fireballs (bright meteors) detected by US sensors',
         'https://ssd-api.jpl.nasa.gov/fireball.api?limit=40', 21600, 'https://ssd-api.jpl.nasa.gov/doc/fireball.html'),
    Feed('asteroids_jpl', 'NASA/JPL CNEOS · asteroid close approaches in the next 60 days',
         'https://ssd-api.jpl.nasa.gov/cad.api?dist-max=0.05&date-min=now&date-max=%2B60&sort=dist', 21600,
         'https://ssd-api.jpl.nasa.gov/doc/cad.html'),
    Feed('geomagnetic_kp', 'NOAA SWPC · planetary K-index (geomagnetic storms, aurora)',
         'https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json', 1800,
         'https://www.swpc.noaa.gov/products/planetary-k-index'),
    Feed('solar_flares', 'NOAA SWPC · latest solar flare (GOES X-ray)',
         'https://services.swpc.noaa.gov/json/goes/primary/xray-flares-latest.json', 1800,
         'https://www.swpc.noaa.gov/products/goes-x-ray-flux'),
    Feed('air_quality_sg', 'National Environment Agency · Singapore air quality (PSI)',
         'https://api-open.data.gov.sg/v2/real-time/api/psi', 3600, 'https://data.gov.sg/'),
    Feed('warnings_hko', 'Hong Kong Observatory · weather warnings in force',
         'https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=warnsum&lang=en', 900,
         'https://data.weather.gov.hk/weatherAPI/doc/HKO_Open_Data_API_Documentation.pdf'),
)
KEYED_CAMERA_FEEDS = tuple(
    Feed(f'cameras_511_{name}', f'{title} · traffic cameras (developer key)', f'https://{host}/api/v2/get/cameras?key={{key}}&format=json',
         3600, f'https://{host}/developers/doc', key_env=f'HAYAT_KEY_511_{name.upper()}') for name, host, title in KEYED_511) + (
    Feed('cameras_wsdot', 'WSDOT · Washington statewide highway cameras (access code)',
         'https://wsdot.wa.gov/Traffic/api/HighwayCameras/HighwayCamerasREST.svc/GetCamerasAsJson?AccessCode={key}', 3600,
         'https://wsdot.wa.gov/traffic/api/', key_env='HAYAT_KEY_WSDOT'),
)
EXTENDED_FEEDS = EXTENDED_FEEDS + OPEN_CAMERA_FEEDS + EVENT_FEEDS + KEYED_CAMERA_FEEDS
ALL_FEEDS = FEEDS + EXTENDED_FEEDS
BY_KEY = {feed.key: feed for feed in ALL_FEEDS}


def feed_key(feed):
    """The owner's developer key for a keyed feed, or '' when it is not configured."""
    import os
    return os.environ.get(feed.key_env, '').strip() if feed.key_env else ''


def configured(feed):
    return not feed.key_env or bool(feed_key(feed))


def resolved_url(feed):
    from urllib.parse import quote
    return feed.url.replace('{key}', quote(feed_key(feed), safe='')) if feed.key_env else feed.url


def redact(text, feed):
    """Remove a feed's secret key from any text (URLs, error messages) before it is stored."""
    from urllib.parse import quote
    secret = feed_key(feed)
    text = str(text)
    if secret:
        for form in {secret, quote(secret, safe='')}:
            text = text.replace(form, '***')
    return text


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def coordinates(value):
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None, None
    lon, lat = finite(value[0]), finite(value[1])
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None, None
    return lat, lon


def timestamp(value, *, milliseconds=False):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / (1000 if milliseconds else 1), UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed.replace(tzinfo=UTC).isoformat() if parsed.tzinfo is None else parsed.astimezone(UTC).isoformat()
        except ValueError:
            return None
    return None


def text(value, limit=2000):
    return str(value or '')[:limit]


def event(feed, identifier, title, kind, observed, url='', point=None, **details):
    lat, lon = coordinates(point)
    # Missing geography is unknown; never invent a (0, 0) location.
    return {'id': f'{feed.key}:{text(identifier, 180)}', 'feed': feed.key,
            'title': text(title, 500), 'kind': text(kind, 120), 'observed_at': observed,
            'latitude': lat, 'longitude': lon, 'url': text(url or feed.url),
            'details': details, 'trust': 'untrusted_external_data'}


def normalize(feed, payload):
    rows, rejected = [], 0
    if feed.key == 'earthquakes':
        if not isinstance(payload, dict) or not isinstance(payload.get('features'), list):
            raise ValueError('USGS response is not a GeoJSON feature collection.')
        items = payload['features']
    elif feed.key == 'natural_events':
        if not isinstance(payload, dict) or not isinstance(payload.get('events'), list):
            raise ValueError('NASA response has no events array.')
        items = payload['events']
    elif feed.key == 'cyber':
        if not isinstance(payload, dict) or not isinstance(payload.get('vulnerabilities'), list):
            raise ValueError('CISA response has no vulnerabilities array.')
        items = payload['vulnerabilities']
    elif feed.key == 'space_weather':
        if not isinstance(payload, list):
            raise ValueError('NOAA response is not a bulletin array.')
        items = payload
    else:
        from app.intelligence.hazards import HAZARD_FEEDS, normalize_hazards
        if feed.key in HAZARD_FEEDS:
            return normalize_hazards(feed, payload)
        from app.intelligence.reports import REPORT_FEEDS, normalize_reports
        if feed.key in REPORT_FEEDS or feed.key.startswith('news_discovered_') and feed.format == 'rss':
            return normalize_reports(feed, payload)
        from app.intelligence.providers import normalize_extended
        return normalize_extended(feed, payload)
    if len(items) > 10000:
        raise ValueError('Feed exceeds 10000 events.')
    for item in items:
        try:
            if not isinstance(item, dict):
                raise ValueError('Event must be an object.')
            if feed.key == 'earthquakes':
                props = item['properties']
                row = event(feed, item['id'], props.get('place'), props.get('type', 'earthquake'),
                            timestamp(props.get('time'), milliseconds=True), props.get('url'),
                            point=(item.get('geometry') or {}).get('coordinates'),
                            magnitude=finite(props.get('mag')), alert=props.get('alert'),
                            tsunami_flag=props.get('tsunami'), depth_km=finite(((item.get('geometry') or {}).get('coordinates') or [None]*3)[2]))
            elif feed.key == 'natural_events':
                geometries = [geo for geo in item.get('geometry', []) if isinstance(geo, dict)]
                latest = max(geometries, key=lambda geo: timestamp(geo.get('date')) or '', default={})
                sources = [source.get('url') for source in item.get('sources', []) if isinstance(source, dict) and source.get('url')]
                row = event(feed, item['id'], item.get('title'),
                            ', '.join(text(category.get('title'), 80) for category in item.get('categories', []) if isinstance(category, dict)),
                            timestamp(latest.get('date')), sources[0] if sources else item.get('link'),
                            point=latest.get('coordinates') if latest.get('type') == 'Point' else None,
                            sources=sources[:10], closed_at=item.get('closed'),
                            magnitude=finite(latest.get('magnitudeValue')), magnitude_unit=latest.get('magnitudeUnit'))
            elif feed.key == 'cyber':
                row = event(feed, item['cveID'], item.get('vulnerabilityName'), 'known_exploited_vulnerability',
                            timestamp(item.get('dateAdded')), feed.documentation,
                            vendor=text(item.get('vendorProject')), product=text(item.get('product')),
                            description=text(item.get('shortDescription')), required_action=text(item.get('requiredAction')),
                            due_date=text(item.get('dueDate')), ransomware_use=text(item.get('knownRansomwareCampaignUse')),
                            applicability='Catalog entry only; installed-system exposure is not established.')
            else:
                message = text(item.get('message'), 6000)
                date = timestamp(item.get('issue_datetime'))
                identifier = hashlib.sha256((text(item.get('product_id')) + str(date) + message).encode()).hexdigest()[:24]
                from app.intelligence.classify import space_weather
                # The first line is only a message code; prefer the bulletin's ALERT/WARNING line.
                headline = space_weather(message)['headline']
                row = event(feed, identifier, headline or (message.splitlines()[0] if message else item.get('product_id')),
                            'space_weather_bulletin', date, feed.documentation, message=message)
            if not row['title'] or row['id'].endswith(':'):
                raise ValueError('Event has no identity or title.')
            rows.append(row)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            rejected += 1
    if items and not rows:
        raise ValueError('All feed events failed schema validation; previous snapshot is retained.')
    return {row['id']: row for row in rows}, rejected
