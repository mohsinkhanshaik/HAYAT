# Intelligence source and reuse records

Checked October 5, 2026. This is a record of concrete reuse and design study,
not a list of uninstalled capabilities.

| Project/source | Primary reference | Reuse in Hayat | Terms checked |
|---|---|---|---|
| Osiris | [Repository](https://github.com/simplifaisoul/osiris), [documentation](https://osirisai.live/docs), [license](https://github.com/simplifaisoul/osiris/blob/master/LICENSE) | Architectural study of feed adapters, source caches and map layers; original Python implementation | MIT; no application source or bundle copied |
| Natural Earth | [Official terms](https://www.naturalearthdata.com/about/terms-of-use/), [land GeoJSON](https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_land.geojson) | Packaged offline world-land geometry, rounded to 0.01 degrees | Public domain; provenance embedded in asset |
| CISA KEV | [Official data repository](https://github.com/cisagov/kev-data), [license](https://github.com/cisagov/kev-data/blob/develop/LICENSE) | Direct public catalog adapter and local change monitoring | Repository license notes US government public-domain work and CC0 treatment where applicable |
| USGS | [GeoJSON feed contract](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php) | Independent earthquake schema adapter | Official public programmatic feed |
| NASA EONET | [v3 API](https://eonet.gsfc.nasa.gov/docs/v3) | Independent natural-event adapter, with original source references | Public API; source-event references retained |
| NOAA SWPC | [Bulletin data](https://www.spaceweather.gov/products/alerts-watches-and-warnings) | Independent bulletin adapter using the documented JSON link | Official public bulletin feed |
| GDACS | [API quickstart](https://www.gdacs.org/Documents/2025/GDACS_API_quickstart_v1.pdf) | Documented event search across six disaster types; provider levels and representative points retained | Public event reporting API; report provenance retained |
| TfL JamCams | [Open data](https://tfl.gov.uk/info-for/open-data-users/our-open-data) | Public camera metadata, explicit still and short-video playback | TfL terms and branding apply; camera outages and image intervals retained |
| Caltrans | [CCTV contract](https://cwwp2.dot.ca.gov/documentation/cctv/cctv.htm), [conditions](https://dot.ca.gov/conditions-of-use) | Twelve district catalogs; publisher stills and video URLs | Public catalog does not grant arbitrary camera access; provider terms apply |
| Leaflet 1.9.4 | [Official download](https://leafletjs.com/download.html), [license](https://github.com/Leaflet/Leaflet/blob/v1.9.4/LICENSE) | Local interactive browser atlas, map controls and clustering presentation | BSD-2-Clause; bundled license, upstream SRI and source/hash manifest |
| HLS.js 1.7.3 | [Repository](https://github.com/video-dev/hls.js), [license](https://github.com/video-dev/hls.js/blob/v1.7.3/LICENSE) | Explicit publisher HLS playback with bounded buffer | MIT; local bundle and license, pinned version and source hash |
| OpenStreetMap | [Copyright](https://www.openstreetmap.org/copyright), [tile policy](https://operations.osmfoundation.org/policies/tiles/) | Optional street tiles after enabling the online layer | ODbL attribution; browser cache/referrer, no prefetch or offline bulk download |
| Open-Meteo | [API docs](https://open-meteo.com/en/docs) | On-demand point model conditions, ten-minute result cache | Free API non-commercial use; source attribution and model/observation distinction |
| HTTPX / SQLite / JSON Schema / Tk | Existing project requirements and Python runtime | Existing libraries used for bounded requests, local state, validation and native UI | Existing dependency licensing; no additional installation |

Do not assume a data source's redistribution, paid API access or third-party
content terms follow the host application's MIT license. New adapters should
record their actual source contract and usage terms here. Keep provider keys
optional, preserve external data as evidence and test outages and malformed
responses before enabling unattended polling.

`app/assets/intelligence-map/vendor/manifest.json` records bundled versions,
source URLs, byte counts and SHA-256 hashes. The corresponding license files
ship beside the JavaScript. `scripts/vendor_intelligence_map.py` is the explicit
rebuild path; Hayat does not install upstream releases automatically.

The resource catalog separately links worldwide camera publishers, NASA Worldview
and FIRMS, ReliefWeb, World Bank, OpenSky and OpenAQ. Those links are discovery
resources rather than enabled data adapters. Windy webcam access requires an
optional API key. The attempted NWS alerts endpoint was blocked by this
environment's robots policy and remains a directory entry. Publicly viewable
content and open-source software are different licensing categories.
The on-demand Open-Meteo tool is implemented, but its October 5 live check was
also blocked by the provider's robots policy here. Its schema/cache tests use
mocked provider responses; live weather remains unavailable in this environment.

## Open camera networks (added October 5, 2026)

Eleven more public camera catalogs are connected through original adapters in
`app/intelligence/providers.py`: New York City DOT, Fintraffic Digitraffic
(Finland, CC BY 4.0), Singapore LTA via data.gov.sg, the Hong Kong Transport
Department via data.gov.hk, the NZ Transport Agency, Delaware DOT, Maryland
CHART, the City of Austin open-data portal, Seattle DOT/WSDOT, Iowa DOT and
Québec 511. No code was copied from them, and none needs an API key. Each
publisher's own terms apply to its images and streams. A live check on
October 5, 2026 fetched 7,442 cameras from these networks (11,923 with London
and California). A snapshot loaded from each network that publishes stills.
Kentucky TRIMARC images were excluded because the publisher's robots.txt
disallows retrieval. The 511 traveler-information APIs and WSDOT's statewide
catalog need free developer keys and are listed as key-required resources only.

Second wave (same day): ALERTCalifornia (UC San Diego), DriveBC, Vegagerðin
(Iceland), City of Toronto, City of Calgary, Illinois DOT via Travel Midwest and
Michigan DOT Mi Drive add 7,192 cameras, for 19,115 in total. Illinois and
Michigan publish open catalogs but disallow automated image retrieval, so only
their publisher viewers are linked. The 511 and WSDOT adapters are built in and
activate with owner-supplied free keys in `.env`; keys are redacted from all
stored state.

Third wave (same day): DGT Spain (national DATEX II camera publication), the
Ayuntamiento de Madrid KML camera catalog, ODOT TripCheck (Oregon) and the City
of Ottawa add 3,899 cameras with stills; a live check of all 41 feeds found 25,496 cameras.

Fourth wave (2026-10-06): the CARS 511 traveler gateways of Minnesota, Colorado,
Nebraska, Indiana, Kansas and Massachusetts, MoDOT's stream list, TxDOT's 25
district camera services and ALDOT's ALGO Traffic API add 10,036 cameras. The
open catalog at github.com/bzsasson/traffic-camera-sources (LiveTrafficCam) was
used only as a map of which states publish feeds; every camera is fetched from
its publisher. Every catalog and one
image per network were fetched live through HAYAT's fetcher with robots.txt
honoured.
