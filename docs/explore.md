# Explore: launches, satellites, maps, fires, routes, bikes, transit, GPS interference and airspace

Added 2026-10-11, after comparing HAYAT with the open-source
[gods-eye-view](https://github.com/bilawalsidhu/gods-eye-view) (MIT). That
project's tool list covers these features. HAYAT already had live aircraft,
ships, transit, the ISS orbit, radar and satellite map layers and cameras;
this adds the rest. Every source was checked through HAYAT's own fetcher,
including robots.txt, and each one needs no account or key.

## Ask in chat

| You say | You get |
|---|---|
| "When is the next rocket launch?", "next SpaceX launch", "latest launches" | Rocket, mission, provider, pad, time (with "in 2 days"), status, and webcast if live |
| "What satellites are overhead?", "which Starlink satellites are above London now" | Satellites above 10°: elevation, direction, distance, and whether they could be seen |
| "When can I see the ISS?", "when will the space station pass over Hyderabad" | The next passes over 3 days, and the next one visible to the eye |
| "What plane is UAE1?", "aircraft info for 896450" | A flight's usual route by callsign, or the aircraft's type and registration by hex or registration |
| "Show me the weather radar over Chicago", "cloud map over Hyderabad", "lightning near Miami" | A current weather map picture, shown in chat |
| "Satellite image of Dubai", "satellite view of Karachi on 2026-10-01" | A true-colour satellite picture of the place for that day |
| "Any fire hotspots near Los Angeles?", "show fire hotspots" | Satellite heat detections from the last 24 hours, strongest first |
| "Directions from Detroit to Ann Arbor", "walking directions from Westminster to Camden Town" | Distance, time and turn-by-turn steps by car, bike or foot |
| "Bike share near Chicago", "where can I get city bikes in Paris?" | The nearest stations with bikes and free docks right now |
| "Where are the buses in Toronto?", "live trams near Amsterdam", "where is bus 504 in Toronto?" | Live buses, trams and trains nearby: route, distance, speed, whether stopped, how full, and stale reports marked |
| "Is there GPS jamming near Gdansk?", "where is GPS being jammed right now?" | Cells where aircraft report poor satellite navigation, with gpsjam.org's levels and the caveat |
| "Can I fly a drone in Washington DC?", "any TFRs near Las Vegas?", "no fly zones near Seattle" | Whether a US place is inside or near prohibited or restricted airspace or a temporary flight restriction |
| "Show fire perimeters near Fresno", "how big is the fire near Paradise?" | Mapped US fire perimeters: acres, containment, cause, crews, distance to the edge, or that you are inside one |
| "Any orca sightings near Friday Harbor?", "are there whales near Seattle?" | Recent marine-mammal sightings in the Salish Sea and Pacific Northwest |
| "Street view of the Eiffel Tower", "what does Times Square look like from the street?" | The nearest open street-level photo, shown in chat, with its date, direction and licence |

"Here", "me" and an empty place mean this computer's location, read from
Windows. A place can also be typed as `latitude, longitude`.

## A deep look at a place

"Deep look at Denver", "sitrep for London", or **Explore → Deep look** reads every
source HAYAT has for one place at once. That covers reported events and road reports,
the nearest cameras with vehicle counts, the weather forecast and air quality, river
gauges, aircraft, fires, the ISS and nearby launches, compared with HAYAT's own
earlier observations of the place. HAYAT then reasons over the results. A Denver look
takes about 3 to 10 s, and longer the first time the vehicle detector loads.

The answer is a list of **findings**, each with a level (alert, watch,
information), the **evidence** behind it, and a **confidence**:

- **Hazards by kind:** earthquakes, wildfire incidents, weather warnings, floods,
  each with the nearest one. Road reports (closures, construction) form a separate
  finding.
- **Fires and wind:** a fire within 150 km is checked against the forecast wind.
  When the wind blows from the fire toward the place, the finding says smoke is
  possible. Single small heat detections (one detection, under 10 MW) are summed
  up in one low-confidence line. They are often industry, gas flares or field
  burning, and are never reported as fires.
- **Weather and air:** rain within the hour, strong wind and extreme temperature
  come from the forecast model, at medium confidence. PM2.5 uses the US EPA levels.
  Its confidence is low because citizen sensors are uncalibrated.
- **Change:** anything HAYAT measured as unusual for this hour, against its own
  earlier looks at the place.
- **Traffic:** counts on the nearest cameras, and whether each is busier or quieter
  than usual for this hour.
- **The sky:** the next visible ISS pass within 36 hours, and any launch within 800 km.
- **GPS interference overhead:** when aircraft in the place's cell (or across the area)
  report degraded satellite navigation at a medium or high level. Always low confidence,
  and always worded as one possible cause.
- **US places only:** being inside a mapped fire perimeter is an **alert**, and an
  uncontained one within 30 km is worth watching. Being inside a temporary flight
  restriction is worth watching; a permanent restricted area is information.
- **Blind spots are findings too:** every source that could not be read, and
  everything with no coverage here (ships, transit). Missing data is never taken
  to mean "all clear".

The findings decide the situation: **ALERT**, **WATCH** or **CALM**. "Deep analysis
of Chicago" also asks HAYAT's local model for a short reading of the findings. It is
labelled as inference, told to use only the findings, and told to mark any link
between them as "possible". Measured: Los Angeles in 36 s with the model.

The rules are written out in [deep.py](../app/intelligence/deep.py), so every
conclusion can be checked against its evidence.

**On screen** ([gui_deep_look.py](../app/gui_deep_look.py)):
- A **situation gauge** (calm, watch, alert).
- The count of findings at each level, and the measurements behind them
  (temperature, wind, rain, PM2.5, events, closures, cameras, aircraft).
- One **card per finding**: its level colour, its confidence, and its evidence,
  folded until you open it. Filter chips show alerts, watch, information or blind
  spots.
- **Card actions:** an event opens on the map with its record, a camera plays in
  HAYAT's player, and a fire or launch site centres the map.
- **HAYAT's reading** asks the local model to read the findings, **Look again**
  repeats the look, and **Open in atlas** opens the browser atlas.
- In a narrow window, the gauge and counts become a strip above the cards.

**In the browser atlas:**
- **Deep look here**, on the map bar or on any record, shows the same gauge and
  cards in the side panel.
- Cards open their event or camera, or fly the map to a fire or launch site.
- "Ask HAYAT's model to read this" adds the labelled reading.

**From the main window:**
- The home Global intelligence card has a **Deep look** button, which looks at
  this computer's location.
- **Studios → Explore & deep look** opens the Explore tab, and **Studios → World
  atlas & video wall** opens the atlas.

## Fires: joined, and followed

"Which fires are growing near Los Angeles?" or **Explore → Growing fires** joins
hotspots in touching 5 km grid squares into fires. Each fire gets its detections,
total and peak power, size and latest detection. Every hourly FIRMS download keeps
the previous one, so each fire is marked **new**, **growing** (half again as many
detections or as much power), **steady** or **shrinking**. Fires no longer detected
are counted too. Satellites pass a few times a day, so a fire can look quiet between
passes.

## Second wave (2026-11-06): transit, GPS interference, airspace, perimeters, whales, street photos

Taken from the much larger gods-eye-view of 2026-11-06 (commit `6e4da23`), whose data
sources list quotes each provider's licence. Every source was fetched again through
HAYAT's own fetcher, robots.txt honoured, before it was added.

**Live transit** ([transit.py](../app/intelligence/transit.py)). Fifteen operator feeds
in GTFS-realtime: CapMetro (Austin), Metro Transit (Minneapolis–St Paul), TTC (Toronto),
MiWay, Hamilton HSR, Durham Region Transit, Guelph Transit, Kingston Transit, Burlington
Transit, HSL (Helsinki), OVapi (the whole Netherlands), Entur (all of Norway), TransLink
(South East Queensland), and Rio de Janeiro's buses and BRT. MBTA (Boston) and Finland's
trains were already connected. Measured on 2026-11-06: 11,000 vehicles across the fifteen
feeds. A question about one city reads only the feeds whose area covers it, in 1 to 5 s
(about 11 s for the very first question, which also reads each host's robots.txt).
- The format is protobuf. A small decoder reads only the vehicle-position fields HAYAT
  shows. It never reads the licence-plate field the format allows.
- Hamilton, Guelph, Burlington and Rio publish internal route keys ("5688", "O0232AAA0A").
  HAYAT shows the number riders know (2, 232) from each operator's own `routes.txt`
  tables ([data/transit_routes.json](../app/intelligence/data/transit_routes.json)).
  Entur's `VYG:Line:R22` becomes R22; an opaque id is left blank rather than shown.
- A report older than 10 minutes is shown as stale; one older than 2 hours is dropped.
  Each feed is read at most every 20 s (OVapi every 60 s, as its README asks) and at most
  four times a minute. Entur gets the `ET-Client-Name` header it requires.
- "Show buses near Toronto" (intelligence_around), the deep look and the atlas's
  **Transit** layer all read the same feeds.

**GPS interference** ([gnss.py](../app/intelligence/gnss.py)). Aircraft broadcasting
ADS-B report how far they trust their own position (NIC and NACp). An aircraft counts as
degraded when NACp < 8, NIC < 7 (except NIC 0 with NACp ≥ 10), or its receiver says it lost
GPS. Aircraft are binned in 0.5° cells over a rolling 30 minutes. A cell's level uses
gpsjam.org's published formula, 100 × max(0, degraded − 1) / aircraft: under 2 % low,
2–10 % medium, over 10 % high, and only cells with at least 3 aircraft get a level.
- At night traffic is often too thin for any cell to reach 3 aircraft. The answer then
  gives an area-wide reading with the same formula, labelled as coarser than cells. On
  2026-11-06 near Gdańsk at midnight, 6 of 9 aircraft reported degraded navigation.
- "Where is GPS being jammed?" samples five regions (the Baltic, the Eastern
  Mediterranean, the Black Sea, the Persian Gulf, and Central Europe as a comparison),
  one 250 NM read each. It is not the whole sky.
- adsb.lol is volunteer-run: at most five area reads a minute, one per area a minute, and
  a 429 starts a pause of the length it asks for. During a pause the answer comes from
  the 30-minute window and says it was not refreshed.
- **What it does not say:** that anyone is jamming. Low accuracy is what the aircraft
  report. Jamming or spoofing is one possible cause; faulty avionics and old transponders
  report the same, and the per-aircraft rule has never been validated against known events.

**US airspace** ([airspace.py](../app/intelligence/airspace.py)). The FAA's prohibited (P)
and restricted (R) areas, with floor, ceiling, times of use and controlling agency, and
the live temporary flight restrictions from tfr.faa.gov (VIP visits, stadiums, fires,
security zones), each with its NOTAM page. A place is checked for being **inside** a zone
and for zones whose edge is within the radius. One NOTAM drawn as several shapes is
listed once.
- The FAA's ArcGIS organisation enforces a shared per-minute quota and refused HAYAT on
  2026-11-06. The table changes on the 56-day aeronautical cycle, so it is kept on disk for
  a day, and when the FAA refuses, the last copy (up to 30 days old) is used and the answer
  says how old it is.
- When one half cannot be read, the answer claims only the half that was checked, and
  says the other is unknown, not clear.
- Places outside US airspace get a plain "covers the US only". This is not a flight
  briefing: controlled airspace around airports is not included, and drone pilots in the
  US still check B4UFLY or LAANC.

**Fire perimeters** ([ground.py](../app/intelligence/ground.py)). NIFC's current
interagency perimeters: acres, containment, cause, crews, county, when the perimeter was
last drawn, the distance to its edge, and whether the place is inside. Prescribed burns
are labelled. A perimeter not redrawn for 14 days is marked stale.

**Whale sightings.** Acartia's marine-mammal reports for the Salish Sea and the Pacific
Northwest coast (CC BY 4.0), by species and how long ago. Observers' comments, names and
identifiers are dropped, and each place is rounded to about 1 km.

**Street photos.** The nearest Panoramax picture within 150 m, saved and shown in chat
with its date, the direction it faces, its licence and its producer. Panoramax blurs
faces and number plates. A producer whose account name is an e-mail address is
credited as "a Panoramax contributor".

**Cameras.** Four more networks, about 5,000 cameras: FL511 (Florida, about 3,790),
Houston TranStar (about 1,080), King County roads in Washington (about 120) and the A22
Brenner motorway in Italy (13). FL511 gives the same camera number to different cameras
in different districts (1,340 of 4,057 rows on 2026-11-06), so HAYAT keys each by its image
channel instead. King County's layer also holds staff names, so only the location, image
and status fields are requested. See [Global Intelligence](global-intelligence.md#open-camera-networks).

**Not added:** iNaturalist (its robots.txt disallows the API), KartaView (did not answer),
Taiwan's freeway cameras (did not answer), Catalonia's cameras (their hosts still fail TLS:
the server's Diffie-Hellman key is too small), and Barrie and York Region transit. Those two
operators ask for a licence to be accepted before use. That decision is the owner's, so
HAYAT does not accept it for them.

## Traffic counts that remember, and that say when they cannot see

- Every vehicle count is kept (camera, time and the numbers; no image). After
  three earlier counts near the same hour of day, HAYAT says whether traffic is
  **busier than usual**, **about usual** or **quieter than usual**. Counts are kept
  for 60 days.
- **A placeholder is not an empty road.** A camera showing a card ("Temporarily
  Unavailable"), a blank or a test pattern is refused. The check: most pixels
  within one narrow shade band (86% for the Caltrans card, 11 to 49% for 13 real
  road scenes).
- **In the dark, counts are lower bounds.** HAYAT works out the Sun's height at
  the camera. Once it is 6° below the horizon, the count says that vehicles seen
  only as headlights are missed, and the deep look lowers its confidence.

## In the Global Intelligence window

- **Explore tab:** type a place (or press **Use map center**), and optionally a
  destination and an aircraft callsign. Pick a weather-map kind, a satellite
  group and a travel mode, then press a button: Launches, Recent launches,
  Satellites overhead, ISS passes, Fires nearby, Fires worldwide, Weather map,
  Satellite image, Directions, Bike share or Aircraft info, and **Deep look** (with the
  model reading) and **Growing fires**. Since 2026-11-06 there are also **Live transit**,
  **GPS interference**, **Airspace (US)**, **Fire perimeters (US)**, **Whale sightings**
  and **Street photo**. The buttons sit in four labelled rows: This place, Fires, Sky &
  space, and Maps & travel. When an answer has a picture, the text beside it leaves out
  the picture's file path. The text answer
  shows on the left and any picture on the right. Double-click the picture to
  open it full size. A failure says why in the tab.
- **Cameras tab → Count vehicles:** counts the cars, trucks, buses,
  motorcycles and bicycles in the selected public camera's current still,
  and rates the traffic as empty, light, moderate or busy. The first count
  downloads a 120 MB detector into `data\models\vision\`; after that a count
  takes about 0.1 s on the CPU.
- **World atlas → Fires · last 24 h:** a layer of satellite heat detections in
  the current view, sized by fire power. It follows the map as you pan.
- **World atlas → GPS interference · aircraft reports:** 0.5° cells around the map
  centre in gpsjam.org's colours (green low, amber medium, red high), each with how many
  aircraft reported degraded navigation. It follows the map from zoom 4.
- **World atlas → Transit** now shows all the operators above, named in the status line;
  a stale report is marked in the vehicle's label.

## World trade in the browser atlas

**Map & live layers → World trade · Shipping chokepoints** rings the 11 main sea-lane
chokepoints:
- red: a high-severity report nearby, pulsing unless motion is paused;
- gold: other reports;
- green: none.

A ring grows with the number of reports. Its popup shows what passes there, the nearest
reports, and the linked funds with today's moves. **Explore here** zooms in. These are event
reports, not ship traffic (see [markets](markets.md#world-trade-chokepoints)).

## Sources

| Feature | Source | Limits HAYAT keeps to |
|---|---|---|
| Rocket launches | Launch Library 2 (The Space Devs) | Free tier allows 15 requests an hour: answers are cached for 30 minutes, also on disk |
| Satellites, passes | CelesTrak GP elements, computed with SGP4 | One group at most every 2 hours, as CelesTrak asks |
| Aircraft details | adsbdb | Registered owners are never shown |
| Weather maps | NOAA nowCOAST WMS | Infrared, visible and water-vapour imagery is global; radar covers the US and its territories; lightning covers the Americas. Radar and lightning are drawn over NASA Blue Marble relief |
| Satellite image | NASA Worldview snapshots (GIBS), VIIRS Suomi NPP | One pass a day; yesterday by default |
| Fire hotspots | NASA FIRMS, VIIRS NOAA-20, last 24 hours | One 6 MB file, downloaded at most once an hour |
| Directions | OSRM on routing.openstreetmap.de (FOSSGIS) | Occasional personal use, as its policy asks; answers cached 10 minutes |
| Bike share | GBFS feeds listed by MobilityData | Only systems that need no key; docked schemes first |
| Vehicle counts | RF-DETR small, COCO (Apache 2.0), via open-image-models | Counts only |
| Live transit | The 15 operators' GTFS-realtime feeds, each under its own open-data licence (credited in every answer) | Each feed at most every 20 s (OVapi 60 s) and four times a minute |
| GPS interference | adsb.lol aircraft (ODbL); gpsjam.org's cell formula | Five 250 NM reads a minute; honours 429 pauses |
| US airspace | FAA Special Use Airspace (P and R) and tfr.faa.gov (public domain) | Restricted areas kept a day on disk; TFRs 15 minutes |
| Fire perimeters | NIFC WFIGS current interagency perimeters (public domain) | 15-minute cache |
| Whale sightings | Acartia (CC BY 4.0) | 10-minute cache; comments and names dropped |
| Street photos | Panoramax (each picture's own licence, usually CC BY-SA or Etalab) | Pictures only from Panoramax storage hosts |
| Heights anywhere | Re:Earth Terrain heights (Mapterhorn DEM, CC BY 4.0; EGM2008) | At most 64 points a question; answers kept 30 days on disk |
| Hurricane forecasts | NHC CurrentStorms.json and NOAA's tropical weather summary MapServer (public data) | Status 5 minutes, map layers 10 minutes |
| Data centres, dams, military areas | OpenStreetMap extracts (ODbL), bundled | Read from disk; no network |
| Licence-plate readers (US, Canada) | DeFlock's hourly OSM extract (ODbL) | At most 9 tiles a question, each kept an hour; locations only |

The last four arrived on 2026-11-08; see the third wave for what each
answer means, the live checks and the sources that were checked and refused.

**Not used:** Radio Browser, iNaturalist, InciWeb, public Overpass and ECCC's AQHI items. Their
robots.txt files disallow the API, and HAYAT honours that refusal.

## What the answers mean

- Launch times are the provider's "no earlier than" estimates. Launches slip.
- Satellite positions come from published orbital elements and are accurate
  to a few kilometres. "Visible" means the sky is dark (Sun 6° below the
  horizon) and the satellite is in sunlight. Clouds are not considered.
- A flight route is the callsign's usual schedule, not proof of today's
  flight.
- Weather maps are the latest mosaic NOAA has published, which can lag by
  minutes. Where radar has no coverage the map is empty, which does not
  mean the sky is clear.
- A fire hotspot is a satellite heat detection in a 375 m pixel. It can be a
  wildfire, but also a gas flare, a factory or a burning field.
- Travel times assume no live traffic.
- Vehicle counts come from one still. Distant or hidden vehicles are missed.

## Boundaries

- **Vehicle counting identifies no one.** It labels vehicles by kind. It does
  not read plates, faces or people, and it stores no results. Plate reading
  stays limited to the owner's own footage and authorized cameras
  ([anpr](anpr.md)).
- **Plate readers are a map of devices.** `plate_readers` says where volunteers have mapped
  licence-plate cameras. It never reads a plate, an image or a record; the map holds none.
- **Aircraft owners are not shown.** adsbdb sends a `registered_owner` field,
  and HAYAT drops it before anything is shown or kept.
- **No one is identified by the second wave either.** Transit decoding never reads the
  licence-plate field. Whale-sighting observers' comments, names and identifiers are
  dropped. E-mail-address account names on street photos are not repeated, and King
  County's staff-name field is never requested.
- Pictures are saved under `data\memory\explore\images\` (the newest 200),
  and caches under `data\memory\explore\cache\`. Both are git-ignored.

Code: [explore](../app/intelligence/explore.py), [transit](../app/intelligence/transit.py),
[gnss](../app/intelligence/gnss.py), [airspace](../app/intelligence/airspace.py),
[ground](../app/intelligence/ground.py), [Explore tab](../app/gui_intelligence_explore.py),
atlas layers in [map.js](../app/assets/intelligence-map/map.js). Tests:
[test_explore](../tests/test_explore.py), [test_world_layers](../tests/test_world_layers.py).
