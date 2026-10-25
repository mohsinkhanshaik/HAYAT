# Live intelligence network: everything around a place

Ask about a place and HAYAT gathers every public source that covers it at the
same time. It answers in one summary that names the source of every sentence
and says plainly what no source covers.

Code:
- [app/intelligence/fusion.py](../app/intelligence/fusion.py): combining sources around a place, and comparing over time.
- [app/intelligence/live.py](../app/intelligence/live.py): the on-demand providers.
- [app/intelligence/catalog.py](../app/intelligence/catalog.py): one description for every source.

## Ask it

| You say | What happens |
|---|---|
| "Show everything within 10 miles of Detroit" | All sources within 16.1 km of Detroit, combined (`intelligence_around`) |
| "Show aircraft, traffic and weather around Detroit" | Only those parts, plus the nearest cameras for the traffic |
| "What is happening in Paris?" | Everything around Paris |
| "What changed here during the last hour?" | Now compared with about an hour ago, yesterday, and what is usual at this hour (`intelligence_changes`) |
| "Which camera has the best view of Times Square?" | Cameras ranked, with the reasons (`intelligence_best_camera`) |
| "What sources do you have and why are they useful?" | Every source described the same way, and why each type helps (`intelligence_sources`) |
| "Makkah live", "mekkah cam", "madina cam", "kaaba live", "masjid nabawi live" | The official broadcast plays in chat |
| "Mekkah and madina cam" | Makkah plays; **Next camera** goes straight to Madinah |
| "Play Al Jazeera live", "play live news", "bear cam", "Kilauea volcano live", "NASA live" | That official channel; Next camera stays with news, or with webcams |

A place can be a name, coordinates ("42.33, -83.05") or "here", meaning this
computer's location, or the last place you asked about.

## When a camera will not play

**Smooth playback in the desktop player (2026-10-11).** "VLC lags a lot" had two
causes, found by measuring pictures actually shown per second:
- YouTube sends some broadcasts (Makkah, Madinah) as separate picture and sound.
  HAYAT attached the sound to the picture, and libVLC's sync then froze the
  picture: 5 to 16 frames a second with long stops. Played alone, the same
  streams show a steady 30. The sound now has its own player, which starts
  only when you press Unmute (30 fps measured with sound on).
- Some publishers stop sending: Norway's playlist sat on the same segment for
  over 30 s, and Alabama showed "Playing" with no picture at all. The card now
  shows the real frame rate ("· 15 fps", or "picture not changing"). It
  reconnects after 12 s of an unchanged picture, and a stream that never
  shows a picture counts as not playing.
- Also: hardware decoding (DXVA2), 1.5 s of network buffer instead of 4 s (closer
  to live), and the card no longer re-lays out the chat ten times a second.
  A busy HAYAT window did not slow the picture: 30 fps either way.

Measured on 2026-10-10 by playing 54 random cameras through the real player:
before the fixes three in 24 sat on "Reconnecting…" for ever; after them none
did.

- **A stream that sends nothing** for 20 seconds, or after three reconnects,
  is given up on. HAYAT shows the same camera's still image if it has one,
  otherwise the next matching camera, otherwise a plain sentence: "… is not
  sending video right now." A stream that does not answer the first check goes
  straight to its still.
- **Signed addresses.** Kansas (CARS) stream addresses carry a token that
  expires five minutes after the catalog is read, and the catalog is read
  hourly, so almost every Kansas stream failed with 401. Playing one now
  fetches fresh addresses first (one request, at most every four minutes),
  and a stream whose token runs out mid-play reloads the same camera.
- **A still that never arrives** says the publisher has no current image
  instead of "retrying; last image retained" over a black screen.
- **Michigan** (MDOT Mi Drive, 804 cameras) cannot be shown in HAYAT: its
  image host's robots.txt (`micamerasimages.net`) disallows all automated
  access, and HAYAT honours that. Asking for one gives the link to the camera
  on MDOT's own map.
- **The Cameras tab's "Watch video"** plays in HAYAT's own player window
  (up to 1280 px). It used to open the web atlas, and official livestreams
  (Makkah, Madinah) went to the browser.
- **The chat card** shows **● LIVE**, CLIP, STILL, RECONNECTING or NO VIDEO,
  the camera's place, wrapping controls and **Watch large** for a bigger
  window (the card lets go, so the publisher serves one connection).

### Sources from gods-eye-view (2026-10-11)

The open-source [gods-eye-view](https://github.com/bilawalsidhu/gods-eye-view)
(MIT) catalogues the camera endpoints it uses. Each was checked through
HAYAT's own fetcher, robots.txt included, and played in HAYAT's player:

| Source | Result |
|---|---|
| Statens vegvesen, Norway (open OGC catalog) | **Added**: 894 cameras, 138 with live HLS video; 6 of 6 sampled streams played in under 3 s. HAYAT had skipped Norway because its DATEX feed needs registration; this catalog does not |
| Transpordiamet / Tark tee, Estonia (two DATEX II documents) | **Added**: 176 road-weather cameras, joined from the location and image documents |
| City of Tallinn intersections | **Added**: 255 cameras; the curated list (positions and headings) is copied from the repo with its MIT notice ([THIRD_PARTY](../app/intelligence/data/THIRD_PARTY.md)) |
| Live Traffic NSW, Australia | **Not added**: the image host answers automated clients with a "Page not found" page instead of the frame. The repo gets around this by pretending to be a Chrome browser; HAYAT does not disguise itself to get past a publisher's block |
| Stadt Warendorf webcam | **Not added**: plain http only (its https port fails), and HAYAT plays camera media over https only |
| Ontario 511 | **Not added keyless**: the endpoint answers 400 without a key; it stays a keyed network (`HAYAT_KEY_511_ONTARIO`) |
| "Shinjuku" pilot pack | **Not added**: its "cameras" are sample demo videos, not live cameras |
| Launch Library 2, CelesTrak, adsbdb, nowCOAST, NASA Worldview and FIRMS, OSRM, GBFS | **Added** as the Explore features: launches, satellites and ISS passes, aircraft details, weather maps, satellite images, fire hotspots, directions, bike share ([explore](explore.md)) |
| Radio Browser | **Not added**: its robots.txt disallows the API |

Number-plate reading and issuing tickets are not provided: reading plates
from public cameras would let anyone track a person's car, and ticketing is
for the authorities. Counting vehicles on a camera still, without identifying
anyone, is available: **Cameras → Count vehicles** ([explore](explore.md)).

## Smarter discovery (stage 3)

The background source scout ([discovery.py](../app/intelligence/discovery.py),
[discovery_strategies.py](../app/intelligence/discovery_strategies.py)) now learns
how to search.

- **A strategy log.** Every way of searching records what it yielded: candidates,
  working catalogs and cameras. That covers a web search for a country, a web search
  for a kind of camera, and a dataset search on CKAN, Socrata or ArcGIS Online. The
  next search favours what has worked (epsilon-greedy, so new ways still get tried).
  A search that found nothing rests for two weeks. "Discovery report" shows the log.
- **Category gaps.** Cameras are counted by kind (traffic, weather, volcano, coast,
  mountain, city), and the kinds with the fewest are searched for first, not only
  the countries with none.
- **Open-data portals.** These are searches on government catalogs that list camera
  datasets web search rarely surfaces:
  - **CKAN:** Canada, Toronto, Japan, the Netherlands and Israel.
  - **Socrata:** its public catalog of US city data.
  - **ArcGIS Online:** its public feature services.

  The other portals checked on 2026-10-11 are left out, and HAYAT does not get around
  them:
  - refused by robots.txt: the UK, Australia, Ireland, Germany, British Columbia,
    Ontario, Alberta and Mexico;
  - blocks bots: Switzerland;
  - needs an account: Brazil;
  - moved their API: the US and Austria.
- **ArcGIS publishers told apart.** Thousands of publishers share arcgis.com, so
  "already connected" now compares the publisher's own service path. Before, one
  wildfire feed there made every ArcGIS camera dataset look known.
- **An isolated trial.** Before a found catalog goes live or is proposed, three of
  its own images or streams are fetched and checked for a real JPEG or PNG, or a real
  HLS playlist. Two of three must answer. A catalog that fails is recorded as
  "failed its trial", with the reason, and is never activated.

Live run on 2026-10-11 (scratch database): ArcGIS found 16 datasets and queued 15.
Three camera catalogs failed their trial:
- Kentucky TRIMARC: the images are refused by robots.txt.
- An Irish catalog: its image host did not answer.
- Austin's ArcGIS copy: 1 of 3 cameras answered (HAYAT has Austin's own feed).

The New Zealand copies serve http-only images from 2017 and were rejected; HAYAT
already reads NZTA's own feed. Socrata queued 13 datasets, all correctly found not
to be camera catalogs (crash and count data).

## Keys

**Studios → Keys** lists every free developer key HAYAT can use:
- the 18 keyed 511 networks (Ontario, Alberta, Arizona, Georgia, New York,
  Pennsylvania, Utah and others);
- WSDOT;
- Finnhub, for exchange stock prices.

Each has a **Get a free key** link to the provider's sign-up page. The sign-up is
yours to do, since HAYAT cannot register accounts. Paste the key and press **Save &
test**: it goes into the local, git-ignored `.env`, applies at once, and is tested for
real, either by refreshing that camera network and counting its cameras, or by
fetching a live quote. The key is never shown again in full.

## Your own cameras (stage 4)

**Studios → My cameras** plays cameras you own, or are allowed to view, in the same
player as public cameras. You can also say "play my front door camera" or "show my
cameras".

- **Exact addresses only.** For example `rtsp://192.168.1.20:554/stream1`,
  `http://192.168.1.30/video.mjpg`, or an `https://` HLS address. HAYAT never scans
  or searches your network, and the player opens only an address that is
  registered ([my_cameras.py](../app/intelligence/my_cameras.py)).
- **Logins stay secret.** An address that contains a login is refused. You type
  the login into the window, it goes to the Windows credential store, and the local
  player receives it as a libVLC login option. It never appears in the address, a
  file, the chat or a tool result. RTSP plays over TCP, which home Wi-Fi handles
  better.
- Your cameras stay off the browser atlas and out of public camera searches.
  Nothing is recorded.

Checked: a stand-in camera registered in a scratch registry played through the real
worker at 12 to 16 fps, and an unregistered address was refused.

## On the world atlas

Open the atlas ("show the world map"). The **Live layers** bar adds:

| Layer | What you see | Source |
|---|---|---|
| Weather radar (US) | rain and storms, renewed every 5 minutes | NEXRAD via Iowa Environmental Mesonet tiles |
| Satellite | yesterday's true-colour picture of the Earth | NASA GIBS (VIIRS) |
| Aircraft | planes around the map centre, rotated to their heading; emergency squawks blink red | adsb.lol, every 15 s |
| Ships | vessels with their course | Digitraffic AIS (Baltic), every 60 s |
| Transit | buses and trains | MBTA (Boston), Finnish trains, every 30 s |
| ISS orbit | the station and a dashed line for its next 95 minutes | CelesTrak + SGP4 |
| Fires · last 24 h | satellite heat detections in view, sized by fire power | NASA FIRMS (VIIRS) |

Getting around (2026-10-11):
- **Type a place** in the search box ("Denver", "Makkah", or "21.42, 39.83") and the map flies there.
  Words about what happened ("earthquake", "wildfire", "cameras", "traffic") still filter the records.
- **Street detail turns on by itself** at city zoom, so a zoomed-in map is never a blank plane. Switch
  it off once and the atlas remembers.
- **← Back** (or Alt+←) returns to the previous view; **My location** goes to this computer's location.
- **Video wall** plays the six live streams nearest the map centre side by side, muted. **Next 6**
  pages through the rest, a click on a tile opens that camera, and Escape or × stops them all.
  Measured: 6 of 6 Denver I-25 streams played within 20 s in Edge.

What moves on the map:
- Between refreshes, aircraft and ships **glide** along their reported heading at their reported speed (dead reckoning). The positions jump to the truth at each refresh.
- **High-severity events pulse** in their category's colour. Medium events pulse more slowly.
- Counters count up to their new values.
- A shimmering bar shows when data is being read.

Everything stops with **Pause motion**, or when the system asks for reduced motion.

The live layers are fetched only while the atlas is visible, and never faster than the providers allow.

**Right-click anywhere** on the map, or press **Everything around here** on any record, to get the same combined answer as `intelligence_around` for that spot. The spot is named from GeoNames on this computer when the place names are set up; otherwise by BigDataCloud's reverse geocoder, with a spot near you rounded to about 11 km first ([location privacy](location-privacy.md)).

## Looking through a camera

"Look at the camera near Times Square", "what do you see on camera cameras_nyc:…" or "look at Makkah live" makes HAYAT take one current picture and describe it with its vision model (`intelligence_camera_look`):
- weather and light;
- traffic: flow, congestion, crashes and closures;
- crowds, smoke, water and snow;
- what one picture cannot show.

The picture comes from:
- a camera's still image;
- one frame of its live stream or clip, taken with the bundled FFmpeg from the same allowlisted publisher address the player uses;
- one frame of the official livestream.

People are never identified. Each look is remembered, so the next look at the same camera shows the previous description beside the new one.

Checked live on 2026-10-06:
- A Delaware road camera stream was described as night, dry and with no traffic.
- The Makkah broadcast was described as midday at 34 °C with an extremely dense crowd.

## What is gathered

Everything is read in parallel. If one provider fails or reaches its rate
limit, it is listed under `unavailable` and the rest of the answer still
comes back.

| Part | Source | Covers |
|---|---|---|
| Cameras | the ~35,900 connected public cameras, ranked | world |
| Events | earthquakes, disasters, NASA events, US wildfires (NIFC), road closures, incidents and construction (CARS: MN, CO, IN, KS, MA) | world / US |
| Weather forecast | MET Norway Locationforecast | world |
| Weather stations | Iowa Environmental Mesonet ASOS | US |
| Air quality | Sensor.Community citizen sensors (PM2.5 median) | world, densest in Europe |
| River gauges | USGS Water Services | US |
| Aircraft | adsb.lol (ADS-B), emergency squawks called out | world |
| Ships | Digitraffic AIS | Baltic Sea |
| Transit vehicles | MBTA; Finnish trains | Boston; Finland |
| Elevation | USGS EPQS | US |
| ISS | CelesTrak orbital elements + SGP4 | world |
| Official livestreams (26) | Saudi Quran TV, Saudi Sunnah TV, NASA ISS, temple trusts, Monterey Bay Aquarium, explore.org, EarthCam, Sen, See Jackson Hole, USGS Kilauea, and 13 live news channels (Al Jazeera, Sky, DW, ABC, NBC, CBS, WION, NDTV, India Today, TRT, CGTN, euronews, NHK) — each the publisher's own channel | — |

**Not used, because their robots.txt refuses automated access:** Open-Meteo's
forecast, air and elevation APIs, RainViewer, api.weather.gov, Overpass and
wheretheiss.at. airplanes.live answers 403. HAYAT honours these refusals.

**No keyless source yet:**
- news (GDELT disconnects);
- worldwide ships;
- transit outside Boston and Finland.

The answer says so instead of leaving gaps unexplained.

## Ranking cameras

Each nearby camera gets a score from:
- distance;
- media (live video, then a recorded clip, then a refreshing still, then the publisher's player);
- the network's measured reliability, from its last 50 refreshes;
- freshness of the publisher's catalog;
- whether the publisher is official (government, `.gov` and similar).

The reasons are shown with the score, for example "1.2 km away, live video,
network 98% reliable, official publisher".

## Change over time

Each look at a place is stored as a small observation, keyed by place and
radius:
- temperature, wind, rain expected in the next hour;
- station temperature, PM2.5;
- counts of aircraft, ships, transit vehicles, events and road closures;
- river gauge height.

Up to 500 observations are kept per place. The next look compares now with
about 10 minutes ago, an hour ago, yesterday, and the median at this hour on
earlier days. A value more than two standard deviations from usual is marked
**unusual**. Comparisons come only from HAYAT's own stored observations, so the
first look at a place has none.

## One source catalog

Built-in feeds, catalogs HAYAT discovered and approved, and the on-demand
providers are all described with the same fields:

- provider, types, country, coverage (bounding box) and records;
- media types, URL and documentation;
- refresh rate, authentication and licence;
- official or not;
- reliability: the share of successful refreshes, measured from feed health history;
- health, last checked and last success;
- restrictions and confidence.

A developer key is never shown: keyed URLs are listed without their query.

The 14 source types:
- live video and snapshot;
- satellite;
- sensor and location;
- traffic, aircraft, vessel and transit;
- weather and event;
- news;
- map and history.

Each type carries a plain sentence on why it helps, and HAYAT uses those
sentences when asked.

## Limits and safety

- Every provider is rate-limited per minute and cached for its own refresh
  interval. For example, aircraft are fetched at most 6 times a minute and
  cached for 15 seconds.
- Road events are fetched every two hours, about 13 MB a round. Nebraska's 50 MB
  file is left out.
- Fetches use HAYAT's public fetcher:
  - robots.txt is honoured;
  - only public addresses are used, never private networks;
  - size and time are capped;
  - nothing logs in.
- Restricted sources are recorded as `authentication_required` or
  `access_restricted`, and never worked around.
- "Here" uses Windows location only when you ask about "here". Reverse
  geocoding sends only the coordinates.
