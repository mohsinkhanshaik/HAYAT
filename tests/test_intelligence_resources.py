"""Extended feed contracts, provenance reuse and guarded public camera previews."""
from datetime import UTC, datetime
from io import BytesIO
import json
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

from app.intelligence.feeds import BY_KEY, ROAD_EVENT_STATES, TXDOT_DISTRICTS
from app.intelligence.live import LIVESTREAMS

#: Cameras added 2026-10-11: Tallinn's list ships with HAYAT (no fetch), plus the Norway and Estonia fixture records.
from app.intelligence.live import static_records  # noqa: E402
ADDED_CAMERAS = len(static_records('cameras_tallinn')) + 6  # Norway, Estonia, Luxembourg, València, NSW and Lyon fixtures

#: Livestreams with a place on the map, plus the ISS broadcast at the station's position.
PLACED_LIVESTREAMS = sum(item.get("latitude") is not None for item in LIVESTREAMS) + 1
from app.intelligence.providers import camera_url
from app.intelligence.manager import IntelligenceManager
from app.intelligence.resources import resource_catalog
from app.intelligence.tools import direct_plan, register_intelligence_tools, TOOL_NAMES
from app.research.knowledge import KnowledgeStore
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from tests.test_public_intelligence import Fetcher


def camera_payload():
    return [{'id': 'JamCams_test', 'commonName': 'Public test road', 'lat': 51.5, 'lon': -.12,
             'additionalProperties': [{'key': 'available', 'value': 'true'},
                {'key': 'imageUrl', 'value': 'https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/test.jpg'},
                {'key': 'videoUrl', 'value': 'https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/test.mp4'}]}]


NYC_ID = '8a6bc417-4877-4ebe-8052-88c1b261baf1'


ESTONIA_LOCATIONS = b"""<?xml version="1.0" encoding="UTF-8"?><d2LogicalModel xmlns="http://datex2.eu/schema/2/2_0">
<payloadPublication><predefinedLocationContainer><predefinedLocation id="ee-1" version="0">
<predefinedLocationName><values><value lang="et">Kolgakula</value></values></predefinedLocationName>
<location><pointByCoordinates><pointCoordinates><latitude>59.52601</latitude><longitude>25.69951</longitude>
</pointCoordinates></pointByCoordinates></location></predefinedLocation></predefinedLocationContainer></payloadPublication>
</d2LogicalModel>"""
ESTONIA_IMAGES = b"""<?xml version="1.0" encoding="UTF-8"?><d2LogicalModel xmlns="http://datex2.eu/schema/2/2_0">
<payloadPublication><trafficView id="987-1"><trafficViewTime>2026-10-11T08:06:39.000+03:00</trafficViewTime>
<linearTrafficView><linearPredefinedLocationReference targetClass="PredefinedLocation" id="ee-1" version="0"/>
<trafficViewRecord><urlLink><urlLinkAddress>https://tarktee.transpordiamet.ee/images/987/987_202610110806.jpg</urlLinkAddress>
</urlLink></trafficViewRecord></linearTrafficView></trafficView></payloadPublication></d2LogicalModel>"""


def open_camera_payloads():
    """One realistic record per open camera network, shaped like the live 2026-10-05 responses."""
    return {
        'cameras_nsw': {'type': 'FeatureCollection', 'features': [{'type': 'Feature',
            'id': '023651ee-389c-4677-978e-d39b6c24c1e7',
            'geometry': {'type': 'Point', 'coordinates': [151.10533, -34.02977]},
            'properties': {'region': 'SYD_SOUTH', 'title': '5 Ways (Miranda)', 'view': 'Looking west towards Sutherland',
                           'direction': 'W', 'href': 'https://webcams.transport.nsw.gov.au/livetraffic-webcams/cameras/5_ways_miranda.jpeg'}}]},
        'cameras_lyon': {'type': 'FeatureCollection', 'features': [{'type': 'Feature',
            'id': 'pvo_patrimoine_voirie.pvocameracriter.41',
            'geometry': {'type': 'Point', 'coordinates': [4.80278803, 45.76352375]},
            'properties': {'nom': 'A7 - Paris', 'libellelong': 'Tunnel sous Fourvière', 'identifiant': 261,
                           'numeromaintenance': 'CWL9801', 'observation': 'Direction : M6 - Paris',
                           'url': 'https://download.data.grandlyon.com/files/rdata/pvo_patrimoine_voirie.pvocameracriter/CWL9801.JPG',
                           'last_update': '2026-10-23T23:32:17+02:00'}}]},
        # Added 2026-10-11 (shapes as returned live that day).
        'cameras_norway': {'type': 'FeatureCollection', 'features': [{'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [10.75, 59.91]},
            'properties': {'cameraId': '3000061_1', 'stillImageUrl': 'https://kamera.atlas.vegvesen.no/api/images/3000061_1',
                           'videoEncodingStandard': 'hls', 'roadNumber': 'E18', 'orientationDescription': 'Oslo sentrum',
                           'status.stillImageAvailability': 'videoOrImagesAvailable',
                           'lastUpdateTime': '2026-10-11T07:11:04+0200'}}]},
        'cameras_estonia': ESTONIA_LOCATIONS,
        'cameras_washington': {'features': [{'attributes': {'OBJECTID': 9001, 'CameraTitle': 'I-5 at Olive Way',
            'ImageURL': 'https://images.wsdot.wa.gov/nw/005vc16500.jpg', 'CompassDirection': 'N'},
            'geometry': {'x': -122.32, 'y': 47.61}}]},
        'cameras_nyc': [{'id': NYC_ID, 'name': 'Central Park West @ 86 St', 'latitude': 40.785302, 'longitude': -73.969353,
                         'area': 'Manhattan', 'isOnline': 'true', 'imageUrl': f'https://webcams.nyctmc.org/api/cameras/{NYC_ID}/image'}],
        'cameras_finland': {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'id': 'C01503',
            'geometry': {'type': 'Point', 'coordinates': [23.99616, 60.05374, 0.0]},
            'properties': {'id': 'C01503', 'name': 'kt51_Inkoo', 'collectionStatus': 'GATHERING', 'dataUpdatedTime': '2026-10-05T03:25:44Z',
                           'presets': [{'id': 'C0150301', 'inCollection': True}, {'id': 'C0150302', 'inCollection': True},
                                       {'id': 'C0150309', 'inCollection': False}]}}]},
        'cameras_singapore': {'items': [{'timestamp': '2026-10-05T17:36:27+08:00', 'cameras': [{'timestamp': '2026-10-05T17:35:47+08:00',
            'image': 'https://images.data.gov.sg/api/traffic-images/2026/10/test.jpg', 'location': {'longitude': 103.7716543, 'latitude': 1.447023728},
            'camera_id': '2701'}]}]},
        'cameras_hongkong': ('<?xml version="1.0" encoding="UTF-8"?><image-list><image><key>H429F</key><region>Hong Kong Island</region>'
                             '<district>Southern</district><description>Aberdeen Praya Road near Fish Market [H429F]</description>'
                             '<latitude>22.24845</latitude><longitude>114.1505</longitude><url>https://tdcctv.data.one.gov.hk/H429F.JPG</url>'
                             '</image></image-list>').encode(),
        'cameras_nz': {'response': {'camera': [{'description': 'South along Hinds Highway', 'direction': 'Southbound', 'highway': 'SH1',
            'id': 714, 'imageUrl': '/camera/714.jpg', 'latitude': -43.919632, 'longitude': 171.721055, 'name': 'SH1 Tinwald ',
            'offline': False, 'region': {'id': 11, 'name': 'Canterbury'}}]}},
        'cameras_delaware': {'videoCameras': [{'urls': {'m3u8s': 'https://video.deldot.gov:443/live/KCAM001.stream/playlist.m3u8',
            'm3u8': 'http://video.deldot.gov:1935/live/KCAM001.stream/playlist.m3u8'}, 'id': 'KCAM001', 'title': 'DE 1 @ MILFORD NECK ROAD',
            'county': 'Kent', 'lat': 38.990931, 'lon': -75.448625, 'enabled': True, 'status': 'Active'}]},
        'cameras_maryland': {'data': [{'commMode': 'ONLINE', 'description': 'I-270 & Old Hundred Rd (MD 109)(CAM 165)',
            'id': '7a00a1dc01250075004d823633235daa', 'lastCachedDataUpdateTime': 1789655296746, 'lat': 39.2773, 'lon': -77.3236,
            'opStatus': 'OK', 'publicVideoURL': 'https://chart.maryland.gov/Video/GetVideo/7a00a1dc01250075004d823633235daa',
            'routeNumber': 270, 'routePrefix': 'IS'}]},
        'cameras_austin': [{'camera_id': '674', 'location_name': ' LAMAR BLVD / 5TH ST', 'camera_status': 'TURNED_ON',
                            'screenshot_address': 'https://cctv.austinmobility.io/image/674.jpg', 'modified_date': '2023-11-30T21:40:00.000Z',
                            'location': {'type': 'Point', 'coordinates': [-97.75, 30.27]}},
                           {'camera_id': '670', 'location_name': 'Planned camera', 'camera_status': 'DESIRED',
                            'screenshot_address': 'https://cctv.austinmobility.io/image/670.jpg', 'location': {'type': 'Point', 'coordinates': [-97.8, 30.2]}}],
        'cameras_seattle': {'Features': [{'PointCoordinate': [47.526783, -122.392755], 'Cameras': [
            {'Id': 'CMR-0112', 'Description': 'Fauntleroy Way SW & SW Cloverdale St', 'ImageUrl': 'Fauntleroy_SW_Cloverdale_NS.jpg', 'Type': 'sdot'}]}]},
        'cameras_iowa': {'features': [{'attributes': {'FID': 819586, 'ImageName': 'RWIS - I-35 MM 140 @ Williams', 'Route': 'I-35',
            'ImageURL': 'https://atmsqf.iowadot.gov/snapshots/Public/RWIS/RWIS_84-01.jpg',
            'VideoURL': 'https://video2.iowadot.gov:8888/councilbluffs/cbtv74lb/playlist.m3u8', 'latitude': 42.4093, 'longitude': -93.5711,
            'Type': 'RWIS'}, 'geometry': {'x': -10416287.2, 'y': 5222489.1}}]},
        'cameras_alertca': {'features': [{'attributes': {'OBJECTID': 1, 'cameraName': 'Antelope Mtn. (Lassen) 1', 'siteId': 'antelopemtn',
            'isActive': 'active', 'isOnline': 'online', 'county': 'lassen', 'cameraURL': 'https://cameras.alertcalifornia.org/?id=Axis-AntelopeMtn',
            'viewTime': '2026-10-04 17:10:44-07:00',
            'imageURL': 'https://cameras.alertcalifornia.org/public-camera-data/Axis-AntelopeMtn/latest-frame.jpg'},
            'geometry': {'x': -120.5715, 'y': 40.4531}}], 'exceededTransferLimit': False},
        'cameras_drivebc': [{'id': 1141, 'name': 'Racetrack - N', 'caption': 'Hwy 5A south of Allison Lake, looking north.',
                             'links': {'imageDisplay': '/images/1141.jpg?t=1791194939'}, 'marked_stale': False, 'marked_delayed': False,
                             'highway_display': '5A', 'region_name': 'Southern Interior', 'orientation': 'N',
                             'location': {'type': 'Point', 'coordinates': [-120.60468, 49.6725]}}],
        'cameras_iceland': [{'Maelist_nr': 7001, 'Myndavel': 'Hellisheiði', 'Vegheiti': 'Hringvegur', 'Skyring': 'Looking west',
                             'Slod': 'https://www.vegagerdin.is/vgdata/vefmyndavelar/hellisheidi_1.jpg', 'Breidd': 64.018296, 'Lengd': -21.342636},
                            {'Maelist_nr': 7001, 'Myndavel': 'Hellisheiði', 'Vegheiti': 'Hringvegur', 'Skyring': 'Looking east',
                             'Slod': 'https://www.vegagerdin.is/vgdata/vefmyndavelar/hellisheidi_3.jpg', 'Breidd': 64.018296, 'Lengd': -21.342636}],
        'cameras_toronto': {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {'REC_ID': 8001,
            'IMAGEURL': 'https://opendata.toronto.ca/transportation/tmc/rescucameraimages/CameraImages/loc8001.jpg',
            'MAINROAD': 'YORK ST', 'CROSSROAD': 'BREMNER BLVD'}, 'geometry': {'type': 'MultiPoint', 'coordinates': [[-79.3826, 43.6426]]}}]},
        'cameras_calgary': [{'camera_url': {'url': 'http://trafficcam.calgary.ca/loc86.jpg', 'description': 'Camera 87'}, 'quadrant': 'SE',
                             'camera_location': 'Stoney Trail / Deerfoot Trail SE', 'point': {'type': 'Point', 'coordinates': [-113.9766, 50.9007]}}],
        'cameras_illinois': {'features': [{'attributes': {'OBJECTID': 1, 'ImgPath': 'https://travelmidwest.com/showCamera?id=IL-IDOTD1-IK14B&direction=NONE',
            'CameraLocation': 'Hillside Tower Camera 9', 'CameraDirection': 'NONE', 'x': -87.91483, 'y': 41.88597, 'TooOld': 'false',
            'SnapShot': 'https://cctv.travelmidwest.com/snapshots/IL-IDOTD1_1.jpg'}}], 'exceededTransferLimit': False},
        'cameras_michigan': [{'route': '11 Mile', 'location': ' @ Mound NB', 'direction': 'Traffic closest to camera is traveling north.',
            'county': 'Wayne County <a href="/MiDrive/map?cameras=true&lat=42.491304&lon=-83.04479&zoom=15&id=1129"target="_blank">Go to</a>',
            'image': '<img src="https://micamerasimages.net/thumbs/semtoc_cam_253.flv.jpg?item=1">'}],
        'cameras_quebec': {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'id': 4057, 'properties': {'IDEcamera': '4057',
            'DescriptionLocalisationEn': 'Route 241 at boulevard de Bromont (Bromont)', 'NomRegionDiffusion': 'Estrie', 'NumeroRoute': '00241',
            'URL_FLUX_DONNEE': 'https://www.quebec511.info/Carte/Fenetres/FenetreVideo.html?id=4057'},
            'geometry': {'type': 'Point', 'coordinates': [-72.64774, 45.32044]}}]},
        # Shaped like the live 2026-10-05 responses of the world-coverage networks.
        'cameras_spain': ('<?xml version="1.0" encoding="UTF-8"?><d2:payload xmlns:d2="http://datex2.eu/schema/3/d2Payload" '
            'xmlns:ns2="http://datex2.eu/schema/3/facilities" xmlns:loc="http://datex2.eu/schema/3/locationReferencing" '
            'xmlns:lse="http://levelC/schema/3/locationReferencingSpanishExtension" xmlns:fse="http://levelC/schema/3/faciliitiesSpanishExtension">'
            '<ns2:device id="176130" version="2"><ns2:typeOfDevice>camera</ns2:typeOfDevice>'
            '<ns2:lastUpdateOfDeviceInformation>2025-10-28T14:19:42.000+01:00</ns2:lastUpdateOfDeviceInformation>'
            '<ns2:pointLocation><loc:roadInformation><loc:roadDestination>BURGOS</loc:roadDestination><loc:roadName>A-62</loc:roadName>'
            '</loc:roadInformation><loc:latitude>42.2624</loc:latitude><loc:longitude>-3.9403</loc:longitude>'
            '<lse:kilometerPoint>25.3</lse:kilometerPoint><lse:province>BURGOS</lse:province></ns2:pointLocation>'
            '<fse:deviceUrl>https://etraffic.dgt.es/camarasEtraffic/176130.jpg</fse:deviceUrl></ns2:device></d2:payload>').encode(),
        'cameras_valencia': {'features': [{'attributes': {'objectid': 138106, 'idcamara': '10209',
            'descripcio': 'Pintor Maella (Av. França)', 'url': 'https://camaras.valencia.es/camaras/visualizador.html?id=10209'},
            'geometry': {'x': -.348257, 'y': 39.458098}}]},
        'cameras_luxembourg': b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark id="camera_3"><name>A6 - Camera 3</name>'
                               b'<Point><coordinates>5.921276,49.636941,0</coordinates></Point></Placemark></Document></kml>',
        'cameras_madrid': ('﻿<kml xmlns="http://earth.google.com/kml/2.2"><Document><Placemark><description>&lt;div align=center&gt;'
            '&lt;img src=https://informo.madrid.es/cameras/Camara06303.jpg?v=74982  width=300 height=220/&gt;&lt;br/&gt;PLAZA DE CASTILLA'
            '&lt;/div&gt;</description><ExtendedData><Data name="Numero"><Value>06303</Value></Data><Data name="Nombre">'
            '<Value>PLAZA DE CASTILLA (NORTE)</Value></Data></ExtendedData><Point><coordinates>-3.68894207537291,40.466063829633,10 '
            '</coordinates></Point></Placemark></Document></kml>').encode('utf-8'),
        'cameras_oregon': {'features': [{'attributes': {'cameraId': 277, 'filename': 'AstoriaUS101MeglerBrNB_pid392.jpg',
            'latitude': 46.18785, 'longitude': -123.85347, 'route': 'US101 ', 'title': 'US101 at Astoria - ODOT District Office'},
            'geometry': {'x': -13787305.2, 'y': 5810503.6}}]},
        'cameras_ottawa': {'cameras': [{'cameraOwner': 'CITY', 'latitude': 45.416354, 'name': 'Booth & Wellington',
            'camera_number': 49, 'id': 33, 'longitude': -75.714726}]},
        'cameras_volcanoes': {'webcams': [{'webcamCode': 'shasta-n', 'webcamName': 'Mount Shasta - N', 'vName': 'Mount Shasta',
            'latitude': 41.409, 'longitude': -122.193, 'hasImages': 'Y', 'lastImageTimestamp': 4102444800,
            'currentMediumImageUrl': 'https://volcview.wr.usgs.gov/ashcam-api/images/webcams/shasta-n/current-medium.jpg'},
            {'webcamCode': 'nowhere', 'webcamName': 'Unplaced camera', 'latitude': 0, 'longitude': 0, 'hasImages': 'Y'}]},
        **cars_payloads(),
        'cameras_missouri': [{'location': '141 AT 21, MM 27.1', 'x': -90.424496, 'y': 38.4623, 'rtmp': None,
                              'html': 'https://sfs02-traveler.modot.mo.gov/rtplive/MODOT_CAM_209/playlist.m3u8'},
                             {'location': 'Refused proxy', 'x': -90.1, 'y': 38.5,
                              'html': 'https://traveler.modot.org/tisvc/api/Tms/CameraStream/M029NBC-07-LQ'}],
        'cameras_alabama': [{'id': 1845, 'accessLevel': 'Public', 'location': {
            'latitude': 30.535105, 'longitude': -88.23953, 'city': 'Mobile', 'county': 'Mobile', 'displayRouteDesignator': 'I-10',
            'displayCrossStreet': 'McDonald Rd', 'direction': 'East'},
            'playbackUrls': {'hls': 'https://cdn3.wowza.com/5/b3FOYzhMRS9aVlI1/mobile-fastly/mob-cam-c095.stream/playlist.m3u8'},
            'permLink': 'https://www.algotraffic.com?cameraId=1845',
            'snapshotImageUrl': 'https://api.algotraffic.com/v4/Cameras/1845/snapshot.jpg'},
            {'id': 9, 'accessLevel': 'FirstResponder', 'location': {'latitude': 31, 'longitude': -87}}],
        **{f'cameras_txdot_{code.lower()}': {'cctvStatusRoadways': [], 'roadwayCctvStatuses': {'IH-10': [{
            'icd_Id': f'IH-10 @ Test {code}', 'name': f'IH-10 @ Test {code}', 'hasSnapshot': True, 'latitude': 29.76,
            'longitude': -95.36, 'statusDescription': 'Device Online', 'dirDescription': 'East',
            'equipLoc': {'roadway': 'IH-10'}}]}} for code, _name in TXDOT_DISTRICTS},
    }


def cars_payloads():
    """One CARS camera per state, with each state's real media hosts."""
    media = {
        'mn': [{'type': 'WMP', 'url': 'https://video.dot.state.mn.us/public/C608.stream/playlist.m3u8',
                'videoPreviewUrl': 'https://public.carsprogram.org/cameras/MN/C608'},
               {'type': 'STILL_IMAGE', 'url': 'https://public.carsprogram.org/cameras/MN/C30405-v1'}],
        'co': [{'type': 'WMP', 'url': 'https://publicstreamer4.cotrip.org:443/rtplive/070E22700CAM1RHS/playlist.m3u8',
                'videoPreviewUrl': 'https://cocam.carsprogram.org/Snapshots/070E22700CAM1RHS.flv.png'}],
        'ne': [{'type': 'STILL_IMAGE', 'url': 'https://dot511.nebraska.gov/images/vid-001077037-04.jpg'},
               {'type': 'STILL_IMAGE', 'url': 'https://dot511.nebraska.gov/images/vid-001077037-05.jpg'}],
        'in': [{'type': 'WMP', 'url': 'https://skysfs3.trafficwise.org/preroll/INDOT_232_r/playlist.m3u8',
                'videoPreviewUrl': 'https://public.carsprogram.org/cameras/IN/INDOT_232_r.flv.png'}],
        'ks': [{'type': 'STILL_IMAGE', 'url': 'https://kscam.carsprogram.org/KDOT_573004_IMAGE001.JPG'}],
        'ma': [{'type': 'MJPEG', 'url': 'https://api.trafficland.com/v2.2/json/stream/432525?system=castlerock&key=x',
                'videoPreviewUrl': 'https://public.carsprogram.org/cameras/MA/432525-fullJpeg.jpg'}],
    }
    return {f'cameras_cars_{state}': [{'id': 100 + index, 'public': True, 'name': f'{state.upper()} test camera',
                                       'location': {'latitude': 40.0, 'longitude': -95.0, 'routeId': 'I-80',
                                                    'cityReference': 'in Testville'}, 'views': views},
                                      {'id': 9, 'public': False, 'name': 'Hidden', 'views': views}]
            for index, (state, views) in enumerate(media.items())}


class ExtendedFetcher(Fetcher):
    def __init__(self):
        super().__init__()
        self.payloads.update(open_camera_payloads())
        self.payloads['cameras_london'] = camera_payload()
        self.payloads['disasters'] = {'features': [{'properties': {'eventtype': 'FL', 'eventid': 1,
            'name': 'Provider test flood', 'datemodified': '2026-10-05T00:00:00Z', 'alertlevel': 'Orange',
            'iscurrent': 'true', 'url': {'report': 'https://www.gdacs.org/report.aspx?eventid=1'}},
            'geometry': {'type': 'Point', 'coordinates': [20, 10]}}]}
        for district in range(1, 13):
            self.payloads[f'cameras_caltrans_{district}'] = {'data': [{'cctv': {'index': '1', 'inService': 'true',
                'location': {'locationName': 'California test road', 'latitude': '37.8', 'longitude': '-122.2'},
                'imageData': {'static': {'currentImageURL': f'https://cwwp2.dot.ca.gov/data/d{district}/cctv/image/test.jpg', 'currentImageUpdateFrequency': '5'},
                              'streamingVideoURL': 'https://wzmedia.dot.ca.gov/test/playlist.m3u8'}}}]}
        # A wildfire and one road event per CARS state (an urgent closure in Minnesota).
        self.payloads['wildfires_us'] = {'features': [{'id': 1, 'properties': {
            'UniqueFireIdentifier': '2026-CAFIX-000001', 'IncidentName': 'Fixture', 'IncidentSize': 12000,
            'PercentContained': 40, 'POOState': 'US-CA', 'ModifiedOnDateTime_dt': 1790000000000,
            'FireDiscoveryDateTime': 1791100000000}, 'geometry': {'type': 'Point', 'coordinates': [-120.5, 38.5]}}]}
        for state in ROAD_EVENT_STATES:
            icon = 'tg_closure_urgent.png' if state == 'mn' else 'tg_construction_routine.png'
            self.payloads[f'roads_cars_{state}'] = [{'id': f'{state}-1', 'tooltip': f'<b>{state.upper()} 10 closed</b>. Detour.',
                'representation': {'iconProperties': {'image': f'/images/{icon}'}},
                'bounds': {'minLat': 44.9, 'maxLat': 45.1, 'minLon': -93.4, 'maxLon': -93.2},
                'startTime': {'time': 1790000000000}, 'active': True, 'priority': 1}]
        # Existing camera/road scenarios use valid empty new report feeds;
        # tests/test_intelligence_osint.py exercises report contents separately.
        self.payloads.update({
            'news_bbc_world': b'<rss><channel/></rss>',
            'news_aljazeera': b'<rss><channel/></rss>',
            'news_un': b'<rss><channel/></rss>',
            'gdelt_security': {'articles': []},
            'storms_nhc': {'activeStorms': []},
            'airports_faa': b'<AirportStatus/>',
            'earthquakes_emsc': {'features': []},
            # Official hazard warnings (tests/test_intelligence_hazards.py exercises their contents).
            'tsunami_ntwc': b'<feed xmlns="http://www.w3.org/2005/Atom"><title>No bulletins</title></feed>',
            'tsunami_ptwc': b'<feed xmlns="http://www.w3.org/2005/Atom"><title>No bulletins</title></feed>',
            'volcanoes_usgs': [], 'earthquakes_jma': [], 'earthquakes_geonet': {'features': []},
            'volcanoes_geonet': {'features': []}, 'outbreaks_who': {'value': []}, 'aviation_sigmet': [],
            'floods_uk': {'items': []},
            'earthquakes_bmkg': {'Infogempa': {'gempa': []}}, 'fireballs_jpl': {'fields': ['date'], 'data': []},
            'asteroids_jpl': {'fields': ['des'], 'data': []}, 'geomagnetic_kp': [], 'solar_flares': [],
            'air_quality_sg': {'data': {'regionMetadata': [], 'items': []}}, 'warnings_hko': {},
        })
        output = BytesIO()
        Image.new('RGB', (32, 24), '#15253a').save(output, format='JPEG')
        self.image = output.getvalue()

    def fetch(self, url, *args, **kwargs):
        from app.intelligence.feeds import resolved_url
        url = url.split('&resultOffset=')[0]  # Paged ArcGIS layers: the fixture is one page.
        keyed = next((feed.url for feed in BY_KEY.values() if feed.key_env and resolved_url(feed) == url), None)
        if keyed:
            self.requested_keyed_urls = [*getattr(self, 'requested_keyed_urls', []), url]
            url = keyed
        raw = next((payload for key, payload in self.payloads.items() if BY_KEY[key].url == url and isinstance(payload, bytes)), None)
        if raw is not None:
            self.calls.append('xml')
            return SimpleNamespace(content=raw, retrieved_at=datetime.now(UTC).isoformat(), sha256='xml-hash', final_url=url)
        if url.endswith('/roadCameraImages'):  # Estonia's second DATEX document
            self.calls.append('xml')
            return SimpleNamespace(content=ESTONIA_IMAGES, retrieved_at=datetime.now(UTC).isoformat(), sha256='xml-hash',
                                   final_url=url)
        if url.lower().endswith(('.jpg', '.jpeg')) or url.endswith('/image') or '/api/images/' in url:
            self.calls.append('image')
            return SimpleNamespace(content=self.image, retrieved_at=datetime.now(UTC).isoformat(), sha256='image-hash', final_url=url)
        if url.lower().split('?')[0].endswith('.m3u8'):
            # Stream liveness checks: a playlist, unless the test marks that publisher stream dead.
            self.calls.append('playlist')
            if url in getattr(self, 'dead_streams', ()):
                raise RuntimeError('Publisher stream unavailable')
            return SimpleNamespace(content=b'#EXTM3U\n#EXT-X-VERSION:3\n', retrieved_at=datetime.now(UTC).isoformat(),
                                   sha256='playlist-hash', final_url=url)
        if url.startswith('https://api.met.no/'):
            self.calls.append('weather')
            return SimpleNamespace(content=json.dumps({'geometry': {'coordinates': [-.1, 51.5, 20]}, 'properties': {'timeseries': [
                {'time': '2026-10-05T00:00:00Z', 'data': {'instant': {'details': {'air_temperature': 16, 'wind_speed': 3.2}},
                 'next_1_hours': {'summary': {'symbol_code': 'cloudy'}, 'details': {'precipitation_amount': 0.0}}}}]}}).encode(),
                retrieved_at=datetime.now(UTC).isoformat(), sha256='weather-hash', final_url=url)
        return super().fetch(url, *args, **kwargs)


@pytest.fixture
def expanded(tmp_path):
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    knowledge = KnowledgeStore(tmp_path / 'knowledge.db')
    knowledge.initialize()
    manager = IntelligenceManager(tmp_path / 'intelligence.db', ExtendedFetcher(), registry, extended=True, knowledge=knowledge)
    register_intelligence_tools(registry, manager)
    manager.refresh()
    try:
        yield manager
    finally:
        manager.close()


def test_extended_catalogs_preserve_unknown_frame_time_and_publisher_provenance(expanded):
    assert len(expanded.store.states()) == len(expanded.feeds)
    assert all(source['status'] == 'ok' for source in expanded.dashboard()['sources'])
    cameras = expanded.cameras(limit=4)
    assert cameras['total_matching'] == 71 + len(LIVESTREAMS) + ADDED_CAMERAS and cameras['next_offset'] == 4
    assert all(row['observed_at'] is None for row in cameras['cameras'])
    assert expanded.cameras(query='California')['total_matching'] == 14  # 12 Caltrans + ALERTCalifornia + Monterey Bay Aquarium live
    assert expanded.cameras(feed='cameras_caltrans_4')['total_matching'] == 1
    assert expanded.cameras(bbox=[-.5, 51, .5, 52])['total_matching'] == 1
    disaster = expanded.dashboard(feed='disasters')['events'][0]
    assert disaster['details']['alert_level'] == 'Orange'
    assert 'not the full affected area' in disaster['details']['limitation']
    cached = expanded.knowledge.get_cached_source(BY_KEY['cameras_london'].url)
    assert cached.source_type == 'public_intelligence_feed' and cached.trust == 'untrusted_external_data'
    assert cached.metadata['inference'] is False and 'not a live-frame' in cached.text


def test_frame_preview_cooldown_permission_and_failure_leave_prior_image(expanded):
    identifier = 'cameras_london:JamCams_test'
    result = expanded.registry.execute('intelligence_camera_frame', {'event_id': identifier})
    assert result.success and not result.output['cached']
    second = expanded.camera_frame(identifier)
    assert second['cached'] and expanded.fetcher.calls.count('image') == 1
    assert second['fetched_at'] == result.output['fetched_at']
    assert 'not verified capture time' in second['basis']
    expanded.registry.permissions = PermissionManager({'default': 'ALLOW', 'permissions': {'public_web_read': 'DENY'}}, expanded.store.path.parent)
    denied = expanded.registry.execute('intelligence_camera_frame', {'event_id': identifier})
    assert not denied.success and expanded.fetcher.calls.count('image') == 1
    with pytest.raises(ValueError):
        expanded.camera_frame('earthquakes:q1')


@pytest.mark.parametrize('url', ['http://127.0.0.1/camera.jpg', 'https://localhost/camera.jpg',
    'https://evil.example/camera.jpg', 'https://cwwp2.dot.ca.gov.evil.example/camera.jpg',
    'https://user:password@cwwp2.dot.ca.gov/camera.jpg', 'https://cwwp2.dot.ca.gov:8443/camera.jpg',
    'https://cwwp2.dot.ca.gov/camera.svg', 'https://cwwp2.dot.ca.gov:wrong/camera.jpg'])
def test_camera_host_contract_rejects_arbitrary_private_or_active_content(url):
    assert camera_url(url, 'cameras_caltrans_4') == ''


def test_weather_cache_model_distinction_and_resources_are_realistic(expanded):
    first = expanded.weather(51.5, -.1)
    second = expanded.weather(51.5, -.1)
    assert first['weather']['air_temperature'] == 16 and second['cached']
    assert expanded.fetcher.calls.count('weather') == 1 and 'not a local camera measurement' in first['basis']
    resources = resource_catalog(category='cameras')
    assert any(row['integration'] == 'key_required' for row in resources['resources'])
    assert any(row['integration'] == 'directory' for row in resources['resources'])
    assert 'not proof of live coverage' in resources['basis']
    recall = expanded.recall(query='flood')
    assert recall['total_matching'] == 1 and recall['knowledge_cache_enabled']


def test_local_atlas_reads_only_public_data_and_guards_host_origin_and_paths(expanded):
    value = expanded.open_map()
    assert expanded.open_map()['map_url'] == value['map_url']
    url = value['map_url']
    with httpx.Client() as client:
        page = client.get(url)
        assert page.status_code == 200 and 'HAYAT World Atlas' in page.text
        assert "script-src 'self'" in page.headers['Content-Security-Policy']
        assert client.get(url + 'data').json()['total_matching'] == 79 + PLACED_LIVESTREAMS + ADDED_CAMERAS
        assert client.get(url + 'event?id=cameras_london%3AJamCams_test').json()['event']['kind'] == 'public_camera'
        assert client.get(url + 'frame?id=cameras_london%3AJamCams_test').status_code == 200
        assert client.get(url + 'vendor/manifest.json').status_code == 404
        assert client.get(url + 'data', headers={'Host': 'evil.example'}).status_code == 404
        assert client.get(url + 'data', headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.get(url.split('/')[0] + '//' + url.split('/')[2] + '/data').status_code == 404
        data = client.get(url + 'data').json()
        assert 'watches' not in data and 'preferences' not in data


@pytest.mark.parametrize('text,tool', [('Find public cameras in London', 'intelligence_cameras'),
    ('Show camera directories', 'intelligence_resources'), ('Open world atlas', 'intelligence_map'),
    ('Recall intelligence knowledge about floods', 'intelligence_recall')])
def test_camera_and_world_context_routes_do_not_require_a_model(text, tool):
    assert direct_plan(text, TOOL_NAMES)['tool'] == tool


def test_world_coverage_networks_parse_with_publisher_media(expanded):
    spain = expanded.cameras(feed='cameras_spain')['cameras'][0]
    assert spain['title'] == 'A-62 km 25.3 towards Burgos' and spain['details']['country'] == 'Spain'
    assert spain['details']['image_url'] == 'https://etraffic.dgt.es/camarasEtraffic/176130.jpg'
    madrid = expanded.cameras(feed='cameras_madrid')['cameras'][0]
    assert madrid['details']['image_url'] == 'https://informo.madrid.es/cameras/Camara06303.jpg'  # cache-buster dropped
    assert round(madrid['latitude'], 3) == 40.466
    oregon = expanded.cameras(feed='cameras_oregon')['cameras'][0]
    assert oregon['details']['image_url'].startswith('https://www.tripcheck.com/RoadCams/cams/Astoria')
    ottawa = expanded.cameras(feed='cameras_ottawa')['cameras'][0]
    assert ottawa['details']['image_url'] == 'https://traffic.ottawa.ca/beta/camera?id=49'  # camera number, not record id
    assert camera_url('https://traffic.ottawa.ca/beta/camera?id=49&x=1', 'cameras_ottawa') == ''
    assert camera_url('https://traffic.ottawa.ca/other?id=49', 'cameras_ottawa') == ''


def test_points_carry_the_whole_world_in_one_request(expanded):
    value = expanded.points()
    # All fixture networks plus the registered publishers; ISS has no fixed place.
    assert value['total'] == 79 + PLACED_LIVESTREAMS + ADDED_CAMERAS and value['cameras'] == 70 + PLACED_LIVESTREAMS + ADDED_CAMERAS
    by_id = {point[0]: point for point in value['points']}
    assert by_id['cameras_delaware:KCAM001'][5].startswith('s') and by_id['cameras_spain:176130'][5] == 'i'
    assert by_id['cameras_maryland:7a00a1dc01250075004d823633235daa'][5] == 'p'
    assert expanded.points(media='stream')['cameras'] < value['cameras']
    url = expanded.open_map()['map_url']
    with httpx.Client() as client:
        assert client.get(url + 'points').json()['total'] == 79 + PLACED_LIVESTREAMS + ADDED_CAMERAS
        assert client.get(url + 'points?media=bogus').status_code == 400


def test_volcano_webcams_drop_unplaced_records_and_accept_both_usgs_hosts(expanded):
    rows = expanded.cameras(feed='cameras_volcanoes')['cameras']
    assert [row['title'] for row in rows] == ['Mount Shasta - N (Mount Shasta)']  # the 0,0 record is not a camera
    assert rows[0]['details']['image_url'].startswith('https://volcview.wr.usgs.gov/')
    assert camera_url('https://avo-volcview.wr.usgs.gov/ashcam-api/images/webcams/x/current.jpg', 'cameras_volcanoes')


def test_us_state_networks_keep_public_cameras_and_working_media_only(expanded):
    minnesota = expanded.cameras(feed='cameras_cars_mn')['cameras']
    assert len(minnesota) == 1  # "public": false is not shown
    details = minnesota[0]['details']
    assert details['image_url'] == 'https://public.carsprogram.org/cameras/MN/C30405-v1'  # the still, not the 403 preview
    assert details['video_url'].endswith('C608.stream/playlist.m3u8')
    nebraska = expanded.cameras(feed='cameras_cars_ne')['cameras'][0]['details']
    assert nebraska['other_views'] == ['https://dot511.nebraska.gov/images/vid-001077037-05.jpg']
    massachusetts = expanded.cameras(feed='cameras_cars_ma')['cameras'][0]['details']
    assert massachusetts['video_url'] == '' and massachusetts['image_url'].endswith('432525-fullJpeg.jpg')
    assert expanded.cameras(feed='cameras_missouri')['total_matching'] == 1  # the refusing proxy is left out
    alabama = expanded.cameras(feed='cameras_alabama')['cameras']
    assert [row['title'] for row in alabama] == ['I-10 at McDonald Rd']  # first-responder cameras are not public
    assert alabama[0]['details']['page_url'] == 'https://www.algotraffic.com?cameraId=1845'
    texas = expanded.cameras(feed='cameras_txdot_hou')['cameras'][0]
    assert texas['details']['image_url'] == ('https://its.txdot.gov/its/DistrictIts/GetCctvSnapshotByIcdId'
                                             '?icdId=IH-10%20%40%20Test%20HOU&districtCode=HOU')
    assert texas['details']['region'] == 'Houston'


def test_txdot_snapshots_arrive_as_base64_json_and_are_decoded(expanded, monkeypatch):
    import base64

    row = expanded.cameras(feed='cameras_txdot_dal')['cameras'][0]
    jpeg = expanded.fetcher.image

    def fetch(url, max_bytes=0, accept=None, **_kwargs):
        assert accept == {'application/json'}
        return SimpleNamespace(content=json.dumps({'icd_Id': 'x', 'snippet': base64.b64encode(jpeg).decode()}).encode(),
                               retrieved_at=datetime.now(UTC).isoformat(), final_url=url, sha256='json')

    monkeypatch.setattr(expanded.fetcher, 'fetch', fetch)
    frame = expanded.camera_frame(row['id'])
    with open(frame['path'], 'rb') as handle:
        assert handle.read() == jpeg
