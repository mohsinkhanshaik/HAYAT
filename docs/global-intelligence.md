# Independent global intelligence in Hayat

October 23: NSW and Lyon public camera catalogs now connect to real still previews with guarded URLs, bounded media and explicit unverified capture freshness. The Resources browser now has 68 entries. Actual checks and launch steps.

October 19 update: **Resources** now searches 66 curated entries by words,
category, access state and region. Filtering uses local metadata, keeps stable
selection/scroll on refresh, and explains empty results. **Manual / key** means
setup is required; it does not configure a service or assume free billing.

The València city camera-location catalog is connected with exact official
viewer links and CC BY 4.0 location-data attribution. It does not provide direct
stills/streams here. The catalog endpoint passed a live read; the viewer host
refused this environment's connection, so playback and capture freshness remain
unverified. Conflicting locations for one publisher camera ID remain unplaced,
with both reported locations visible in metadata. US National Park Service and Traffic Scotland links are discovery
resources, not imported camera networks.

The Cameras browser now supports bounded catalog updates, media/freshness sorting,
configurable nearby-event radii and stable selection/scroll during refresh.
Less frequent actions are under **More camera actions**. See the
[camera browsing improvements](camera.md#camera-browsing-improvements-2026-10-17).

Makkah and Madinah use registered official broadcasts with renewed signed
addresses on reconnect. India has four verified publisher entries: Siddhivinayak
in Mumbai, Shirdi Saibaba, Somnath, and the Arunachala mountain still camera.
Publisher pages are read as data; advertising embeds are ignored and the player
uses direct media. **Go live** moves playback to the live edge. Ads inserted
into the broadcast itself remain publisher-controlled. Offline broadcasts keep
retrying while selected, with pause and stop available during connection.

Discovery prioritizes India and Makkah, rotates through India's 36 states and
union territories, and keeps searching global coverage gaps. It recognizes
public camera JSON catalogs and RSS news feeds, records publisher access needs,
and retries transient unreachable sources after six hours. Coverage remains
limited to connected public publishers; a region search is not proof of camera
coverage. See the regional completion record.

Open the front-page **Open intelligence** card, **Studios → Global intelligence**,
or ask **Show global intelligence**. The native workspace combines an offline
zoomable map, regional searches, paged events, deep evidence, retained alerts,
watch editing, source status and a factual assessment.
The catalog contains 131 feed definitions, including 19 camera networks that require
publisher keys. Counts do not establish configured access, free billing or uptime.
The current profile leaves optional background startup manual. Public camera and road-event networks, news outlets, official
earthquake/storm/airport reports, Cameras, Resources and Context tabs join a
local browser **World atlas** with street maps and public publisher media.
It runs inside Hayat without an Osiris account, server, Docker installation,
browser map service or paid model key.

Selected camera streams keep reconnecting after publisher drops, with retry
delays capped at 60 seconds. Snapshot refresh retains the last image and keeps
retrying after failures; the atlas keeps selected media connected when its tab
is hidden. Stop, Pause, switching cameras and closing the player still cancel
the appropriate refresh or playback work. A publisher availability flag or a
failed short playlist probe no longer blocks an attempt to play a camera.
Public publisher URLs remain validated; publisher keys and login requirements
still come from the publisher. Feed GET requests retry transient connection
failures up to twice within their request time budget; denials and publisher
cooldowns keep their existing behavior.

## Workspace layout

### Card beside the main chat

Conversation details start folded so the intelligence card remains visible
when a chat begins. **Details** opens its tabs; long answer provenance scrolls
inside the panel, and incoming messages preserve the source list's selection
and reading position.

Drag the divider between chat and the overview column to adjust the column's
width. Focus the divider and use Left/Right for keyboard adjustment, or
double-click to reset it. On compact windows the overview opens over the chat
without narrowing it; **Layout → Show / hide overview** controls it. The layout
keeps separate open/hidden choices for wide and compact windows during the
session. **Workspaces** keeps research, markets, REA and other tools reachable
when their direct navigation buttons no longer fit.

The Global intelligence card in the main window's right column has its own
scrollbar and a bounded content area, so long summaries and alerts do not take
over the column. Drag **Resize panel** along its lower edge to change its
height; with keyboard focus on that handle, Up/Down adjusts it too.

Drag the card's **Global intelligence** heading to float that same card
anywhere inside the main HAYAT window. Use its **Dock** button or
**Panel → Dock beside chat** to return it to its previous place in the column.
While floating, the lower handle resizes both width and height. It stays inside
the main window when the window shrinks. **Panel → Reset panel size** restores
the compact size. Moving the card keeps its current content and refresh loop;
it does not open the separate Global Intelligence workspace.

Double-click the heading or use **Panel → Collapse / expand** to fold its
content. The main column's **Collapse all / Expand all** controls include this
card, and its fold preference is remembered. Position and size apply to the
current session; a fresh session starts docked beside chat.

### Separate workspace window

The workspace opens at a smaller size within the usable screen and has normal
minimize, maximize and resize controls. Drag the **Global Intelligence** header
or the Windows title bar to move it; dragging the header of a maximized window
restores it first. Double-click the header to maximize or restore. Resize with
an edge or the bottom-right grip. **Window → Fit window to screen** brings an
oversized or partly hidden window back onto its current monitor; the menu also
offers maximize/restore, minimize and close. It is an independent window, so
you can work in the main HAYAT window alongside it. Moving and resizing keep
the current filters and selected observation. Drag inside the map to pan it.
On very short screens, the filter bar folds to leave room for the map and
details. **Window → Show filter bar** or **Ctrl+F** brings it back; folding it
does not clear the active filters.

The window follows a mission-control layout:

- **Header**: live/paused monitor state, time since the last successful fetch,
  **Refresh feeds**, **Add watch** and **Pause/Start monitor**.
- **Filter bar**: search, **Category** (earthquakes, volcanoes, wildfires,
  severe weather, floods, space weather, cyber, other events, public cameras),
  **Region**, **Severity** (high only, medium and above, low and above),
  **Time range** (last hour to last 30 days, or any time) and **Source feed**.
- **Map**: glowing markers whose colour and shape show the category and whose
  size and halo show severity. An **Event layers** panel turns categories on or
  off on the map; public cameras start hidden so 4,000+ catalog points don't
  bury incidents. The selected event gets a callout. The corner shows the map
  centre and a distance scale. Dense views keep the most severe markers first.
- **Selected Event** card: severity badge, previous/next navigation through
  the current results, category/provider chips and a fact table. The table
  holds location, provider fields, event time with age, source link, last
  fetch, verification, matching watches, revisions and feed status.
  **Copy location** copies the coordinates.
- **AI Assessment** card: **Risk level**, **Relevance** and **Confidence**
  tiles plus Summary, Risk analysis, Context and Related events views.
  Recommended actions are clickable: track the area with a watch, check model
  weather at the point, open the source, or jump to related observations.
- **Live Global Feed**: the newest dated observations as cards with category
  chips, age and severity badges (for example `M5.0`, `G2`, `ORANGE`).
  **View all events** opens the paged Events table.
- **Assessment** shows category bars and the severity mix for every matching
  item. **Reports** generates a brief for the current filters or shows the
  monitor's saved brief.

### Map navigation and coverage (2026-10-11)

- **Expand map** (F11) gives the map the space used by the detail cards and live
  feed. **Show details**, F11 or Escape restores the layout; selecting an
  observation also opens the inspector. Escape closes the workspace only when
  the map is already in its normal layout.
- **Fit results** frames the loaded observations, including pairs across the
  international date line. **Reset** returns to the world position without
  changing filters. **Clear filters** resets the search, region, severity,
  time, source and map layers; cameras return to their initially hidden state.
- **In view** opens a scrollable, keyboard-accessible list of visible loaded
  observations. Select a row and press Enter to inspect its evidence. A cluster
  zooms in at overview scale; at close zoom it opens its member list. Right-click
  a cluster to inspect it at any scale. Reports sharing a coordinate remain
  accessible even at maximum zoom.
- The coverage strip distinguishes **In view**, **Map loaded** (loaded / all
  located matches in enabled layers), **No coordinates** and **Feeds current**.
  Narrow maps keep the first two; expand the map to see all four. A gold loaded
  count identifies a partial subset. The existing 1,000-marker limit remains;
  **Filter to visible map** (**View only** on narrow windows) queries the current
  area so observations beyond the world subset can be found. Unknown locations
  stay available in Events. A current feed does not prove an event is active.
- **Layers** starts folded and scrolls when opened on a short map. Geographic
  labels and the coordinate grid can be toggled under **Map options**. The same
  menu contains Fit region, World atlas, Weather here, Watch this area, Save view,
  Saved views… and Customize.
- Hover cards show a report's priority, provider and age or stale-source state.
  **Ctrl+F** focuses search; **/** does so when the map has focus. Arrow keys pan,
  **+ / -** zoom, **Home** resets the view, and **Enter** opens In view.

Changing filters clears the previous event's assessment while the next matching
record loads. Hidden layers also hide a selected marker's highlight.
The map upgrade record documents tests and visual review.

### What the window shows first (2026-10-10)

Reviewed on a copy of the owner's real feeds (5,901 events, 35,219 cameras):

- **Events are events.** Public cameras stay on their map layer and in
  **Cameras**; "All events", the counts, the live feed and the Assessment
  chart no longer include them. The status bar says "5,901 events (+35,219
  public cameras in Cameras)".
- **Now before later.** Newest reports come first. Items dated in the future
  (road works scheduled for 2027) follow them, soonest first, so they no
  longer head every list.
- **The window opens on the day's most severe report**, for example a
  major hurricane, rather than whichever row sorts first.
- **Clusters.** At world zoom, dense markers group into numbered bubbles in
  the commonest kind's colour. A red ring means a high-severity report is
  inside. Hovering lists what is in a cluster; a click zooms in. The selected
  event always stays its own marker.
- **Live feed.** "All" holds at most 15 of any one category, so road works
  can't fill a world feed. Each category chip shows that category's own
  newest items, even when they aren't among the 60 newest overall. Six chips
  plus **More** replace six rows of chips. Titles and badges are measured in
  the real font ("RESTRICT", not "RESTRI").
- **Readable tabs.**
  - **Alerts:** "M5.3 · southeast of Easter Island · 3 h ago · 2026-10-10
    22:04 UTC".
  - **Sources:** failing feeds first, with a summary of what is current,
    failed or needs a key, and a readable record for the selected feed.
  - **Watches:** On/Paused, and areas as latitude/longitude ranges.
  - **Assessment:** fills itself when opened, with one line for all camera
    catalogs instead of seventy.
- **Room for the map.** The map area takes roughly two thirds of the main
  vertical split, with bounded, scrollable detail cards below it. The hover hint
  is drawn on the map, and **Filter to visible map** sits in the toolbar.
  The subtitle folds below 960 px. The split follows the window until you drag
  it; Expand map makes the whole workspace available to the map.
- **Icons.** Every button keeps its own icon, even after HAYAT re-applies
  icons across the app (before, about twenty fell back to the same generic
  symbol).

Windows narrower than 1,180 px hide the live feed. Short or narrow windows
switch to a compact layout: inspector beside the map, one card at a time,
and icon-only toolbars with tooltips. **Save view** and **Customize** remember
the category, severity, time range and hidden map layers as well.

### How severity, relevance and confidence are decided

Severity is a fixed rule over fields the provider publishes, and every label
shows the rule that produced it:

| Source | High | Medium | Low |
|---|---|---|---|
| USGS earthquakes | M6.0+ or PAGER orange/red | M4.5–5.9 or PAGER yellow | below M4.5 |
| GDACS disasters | Red alert | Orange alert | Green alert |
| NOAA space weather | scale G/S/R 4–5 | scale 2–3 | scale 1, no scale, or a cancellation |
| CISA KEV | known ransomware use | every other exploited entry | — |
| NASA EONET storms | 64 kt+ winds | 34–63 kt | below 34 kt |
| NASA EONET wildfires | 100,000+ acres | 10,000+ acres | smaller or unsized |

Public cameras are informational. NOAA K-index alerts map to G-levels (K5 = G1).
Space-weather titles now come from the bulletin's own ALERT/WARNING/WATCH
line instead of the message code.

**Relevance** is High when one of your watches matches, Medium when a watch
covers the same layer but not this area or threshold, and Low otherwise.
**Confidence** is High when the issuing agency publishes the record itself
(USGS, CISA, NOAA) in a current fetch, or when another connected source
reports the same kind of event within 300 km and 72 hours. A single current
aggregator report (EONET, GDACS) is Medium. A stale source or an item no
longer in the feed is Low. None of these are impact forecasts or probabilities.

**Analyze with HAYAT** sends the selected event's retained evidence to the
local brain model. It writes a short Summary / Risk / What to watch reading.
The prompt marks provider text as untrusted and forbids invented casualties,
damage, forecasts or sources. Reasoning tags are removed, and the result is
labelled as local-model inference. Without a loaded model, the card says so and
the evidence-based assessment still works. Chat: **Analyze intelligence event
earthquakes:EVENT_ID**.

The workspace keeps parsed and classified rows per feed in memory until a new
snapshot arrives, and redraws only when the stored data or filters change.
One filtering pass serves the table, map, live feed and counts. On the October
5 data set (6,625 records) a full refresh of the view takes about 3 ms.

## Public views and richer context

**World atlas** opens a local, token-addressed Leaflet map. Zoom with the wheel
or controls, drag to pan, select a region/layer, and search places or events.
Dense observations form numbered clusters; selecting one zooms closer. Selecting
a marker opens provider facts, fetch/event times, revisions and nearby items.
At most 5,000 located records load per view; narrow the filters for larger sets.
The native map remains available offline. **Street map (online)** requests
OpenStreetMap tiles only after it is enabled, with visible attribution.

**Cameras** pages 200 publisher catalog entries at a time, with query, layer,
region, media (still images, live video, publisher player) and availability
filters. The table shows each camera's network, media type, availability,
country and, for nearest searches, distance. A summary line counts stills,
live streams and publisher players and lists the matching networks. Select a
camera to load its latest snapshot, open it in the atlas (live HLS video plays
there) or **Open publisher page** for networks that only offer their own
player. **Near selected event** lists the cameras closest to the selected
event within 150 km. On any event, the AI Assessment's first recommended action
is **View the nearest public camera** when one lies within 100 km, and Related
events lists the nearest cameras separately from other observations. The atlas also offers explicit publisher video
controls, using native MP4 or HLS.js playback. Playback stops when a different
observation is selected or the page is hidden. Videos may be short clips; outages,
browser codecs and provider CORS can prevent playback. Fetch time is displayed
separately from unverified image capture time. Provider availability alone never
establishes that an image is live.
Delayed responses cannot replace a newer evidence or Resources view. The narrow
atlas layout scrolls the page and evidence panel so media controls stay reachable.

**Resources** contains 35 curated entries covering worldwide camera directories,
disasters, satellite imagery, weather, humanitarian reports, economy, aviation,
air quality and the reused map/video libraries. Connected adapters, on-demand
tools, external directories and key-required APIs are labeled separately.
[EarthCam](https://www.earthcam.com/network/),
[SkylineWebcams](https://www.skylinewebcams.com/en/webcam.html) and
[Explore.org](https://explore.org/livecams/currently-live) extend discovery
beyond the connected networks through their own players and terms. They are
links, not a claim of verified worldwide camera coverage. The 511
traveler-information APIs and WSDOT's statewide catalog add tens of thousands
more cameras once you supply free developer keys (see **Turning on keyed camera
networks** below).

**Context** retrieves [Open-Meteo](https://open-meteo.com/en/docs) current model
conditions for a coordinate, selected event or native map center. Results include
model time, units, grid location and a ten-minute cache. The free endpoint's
non-commercial terms apply. Model conditions are distinct from sensor readings.
The October 5 live check was denied by the provider's robots policy in this
environment. The tool reports that failure and keeps the existing public-fetch
rules; a successful mocked contract test does not establish live availability.
Recall searches retained provider observations with freshness and provenance.
Successful feed refreshes also store bounded normalized evidence in Hayat's
research source cache, expiring at the feed interval. External text remains
untrusted evidence; it is not promoted into independently verified claims.
This improves source access and situational context, not literal consciousness.

## Map, evidence and front page

- Wheel over the map to zoom around the pointer; drag to pan. Click a marker
  to inspect it. Arrow keys move the map, `+`/`−` zoom and `Home` resets it.
  Zoom is bounded to 1–32×; longitude wraps across the date line.
- Choose a region, layer and search phrase. **Filter to visible map** restricts
  both events and alerts to the viewport. **Fit region** and **Reset** control
  the view. The map labels how many located items are shown; at most 1,000
  matching markers are drawn. Use filters to narrow denser sets.
- **Events** has horizontal/vertical scrolling and Previous/Next pages of
  200 items, so every matching cached record is accessible. Unknown locations
  remain visible in unbounded searches and cannot be focused on the map.
- The inline inspector shows provider facts, event/fetch times, source links,
  watch matches and a revision timeline. **Focus on map**, **Ask Hayat in chat**,
  **Open source** and **Full raw evidence** provide explicit actions. Nearby
  observations are limited to 300 km and do not establish a relationship.
  Retained events absent from the current feed are labeled historical.
- **Alerts** provides scrolling access to matching retained local alerts.
  **Watches** adds, edits and deletes rules; the advanced editor supports
  multiple feeds, keywords, date-line bounds, magnitude and enabled state.
  **Sources** shows successful fetch times, cooldowns, errors, changes and
  source hashes. **Assessment** analyzes all matching cached items, with
  paged examples and explicit coverage gaps.
- **Customize** saves front-page visibility, compact/detailed summaries,
  alert visibility and 0–5 alert rows. It also saves the current region,
  search, layer and map position; **Save view** saves those view choices alone.
  A hidden card can be restored through Studios or All features.
- **Saved views…** keeps up to 30 named views (region, search, category,
  severity, time range, feed, layers and map position). Saving one records the
  sources behind it: how many items matched, and for each contributing feed its
  item count, last successful fetch and whether it was stale. **Open on the map**
  applies the filters and re-checks them against today's cache, then lists what
  changed: the matching count then and now, feeds that went stale or came back,
  new sources and sources that no longer contribute. Old counts are never shown
  as current. The evidence check uses the region preset; the map-area-only
  switch is restored but not applied to the counts. The same actions are the
  `intelligence_views` tool (`list`, `save`, `open`, `delete`), so each one is
  recorded in Activity.

The home card reads local snapshots without initiating network requests. It
shows source freshness, monitor state and retained alert counts. Its alert
buttons open the corresponding event. Routine updates open no dialogs and
source links open only after a click. Manual operations run in background
workers through the registry and appear in Activity; forms remain open if a
save fails. Preferences persist in the intelligence database and do not change
permissions or monitor authority.

## Automatic operation

The owner's requested unattended mode is enabled in `config/settings.yaml`:

```yaml
intelligence:
  enabled: true
  tick_seconds: 60
  extended_sources: true
```

The monitor starts during ordinary Hayat startup. Every tick checks which sources
are due; individual sources refresh at their own intervals. It continues when
the map window is closed, stops with Hayat and does not prompt on every cycle.
Safe mode, tests, `start_background_services=False` and `HAYAT_DISABLE_BACKGROUND`
disable automatic startup. Chat or the window can start/pause the monitor.

Reads use the existing public-URL, robots, byte-limit and permissions boundaries.
A separate registry budget prevents monitoring from resetting a chat task's
budget. The background principal receives public-read authority, and routine
source failures back off automatically. The monitor stores snapshots and local
alerts; source text cannot request code execution or grow its authority.
Background camera refreshes fetch catalog metadata only. Snapshots and video
load when requested; there is no continuous camera recording. At most 30 latest
preview files are retained, with publisher refresh cooldowns and image limits.
Set `extended_sources: false` to restore the four core feeds for automatic scans.

Two starter watches are created once: M5+ earthquakes and new/revised CISA
catalog entries. The initial fetch establishes a baseline, avoiding alerts for
every historical item. Deleted starter watches stay deleted after restart.
Add a region, keyword phrase or feed-specific watch to customize monitoring.

Each scan updates `data/memory/intelligence/latest-brief.md` beside the configured
intelligence database. The report records successful fetch times, stale sources,
source URLs, content hashes and recent observations. It replaces one file
atomically. Twelve snapshots per feed and 1,000 local alerts bound storage.

## Working sources

| Layer | Primary source | Interval | Meaning |
|---|---|---|---|
| Earthquakes | [USGS GeoJSON](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php) | 5 minutes | M2.5+ reports from the past day; source magnitude, location and timestamp |
| Natural events | [NASA EONET v3](https://eonet.gsfc.nasa.gov/docs/v3) | 30 minutes | Up to 200 open natural events; includes source links and point geometry where available |
| Cyber | [CISA KEV catalog](https://github.com/cisagov/kev-data) | 60 minutes | Exploited-vulnerability catalog entries; not proof that the owner's devices are exposed |
| Space weather | [NOAA bulletins](https://www.spaceweather.gov/products/alerts-watches-and-warnings) | 15 minutes | Source-issued space-weather notices and issue dates; notices are not all currently active |
| Disaster reports | [GDACS API quickstart](https://www.gdacs.org/Documents/2025/GDACS_API_quickstart_v1.pdf) | 30 minutes | Six disaster types, representative event points and provider alert levels; can overlap other feeds |
| Tsunami bulletins (2 feeds) | [NOAA tsunami.gov](https://www.tsunami.gov/) NTWC and PTWC Atom feeds | 10 minutes | Each warning centre's latest bulletin: category (information, advisory, watch, warning), region, preliminary magnitude and bulletin text |
| US volcano alert levels | [USGS HANS](https://volcanoes.usgs.gov/hans-public/) | 60 minutes | Volcanoes above normal, with USGS alert level and aviation colour code; each volcano is placed once by a second USGS call and cached |
| Japan earthquakes | [JMA earthquake list](https://www.jma.go.jp/bosai/map.html#contents=earthquake_map&lang=en) | 15 minutes | One record per quake (the newest report with a hypocentre), English area names, depth and the strongest shaking measured (shindo) |
| New Zealand earthquakes and volcanoes | [GeoNet API](https://api.geonet.org.nz/) | 15 / 60 minutes | Felt earthquakes (MMI 3+) and the volcanoes at alert level 1 or above |
| Disease outbreaks | [WHO Disease Outbreak News](https://www.who.int/emergencies/disease-outbreak-news) | 60 minutes | The 30 newest reports, placed at the centre of the country they name (and labelled as country-level) |
| Airspace hazards | [NOAA Aviation Weather Center](https://aviationweather.gov/data/api/) | 15 minutes | SIGMETs for thunderstorms, severe turbulence, icing and volcanic ash, at the centre of the warned area, with altitudes and validity |
| England flood warnings | [Environment Agency flood monitoring](https://environment.data.gov.uk/flood-monitoring/doc/reference) | 15 minutes | Severe flood warnings, flood warnings and flood alerts in force, placed by their flood area (cached) |

| Indonesia earthquakes | [BMKG open data](https://data.bmkg.go.id/gempabumi/) | 15 minutes | The latest M5+ quakes with BMKG's own tsunami-potential statement, translated ("29 km NW of Pohuwato-Gorontalo") |
| Fireballs | [NASA/JPL CNEOS fireball API](https://ssd-api.jpl.nasa.gov/doc/fireball.html) | 6 hours | Bright meteors detected by US government sensors: place, altitude, speed, radiated and impact energy; reported days to weeks later |
| Asteroid close approaches | [NASA/JPL close-approach API](https://ssd-api.jpl.nasa.gov/doc/cad.html) | 6 hours | Every pass within 0.05 AU in the next 60 days, in lunar distances and km, with an estimated size; a close approach, never called an impact |
| Geomagnetic activity | [NOAA SWPC planetary K-index](https://www.swpc.noaa.gov/products/planetary-k-index) | 30 minutes | The latest Kp, the NOAA G-storm level (Kp 5 = G1 … Kp 9 = G5), the last day's maximum and how far south aurora may reach |
| Solar flares | [NOAA SWPC GOES X-ray](https://www.swpc.noaa.gov/products/goes-x-ray-flux) | 30 minutes | The latest flare's class (A, B, C, M, X) and what that class can do |
| Singapore air quality | [data.gov.sg PSI](https://data.gov.sg/) | 60 minutes | 24-hour PSI and PM2.5 for the five regions, banded good / moderate / unhealthy / very unhealthy / hazardous |
| Hong Kong warnings | [Hong Kong Observatory open data](https://data.weather.gov.hk/weatherAPI/doc/HKO_Open_Data_API_Documentation.pdf) | 15 minutes | Warnings in force (tropical cyclone signals, rainstorm, thunderstorm, fire danger, heat, cold), placed at the centre of Hong Kong |

### Around me: the hazard radar (2026-10-15)

"What's happening around me?", "am I safe?", "any hazards near Dubai", "anything dangerous near me today" and "hazard radar" all ask `intelligence_radar` ([app/intelligence/radar.py](../app/intelligence/radar.py)). It does one pass over the events HAYAT already holds and sends no new requests:

- **What counts:** everything within the radius (500 km by default) from the last 72 hours. Cameras and news reports are left out, and road events count only within 50 km.
- **Order:** results are sorted most serious first, then nearest. Each one has its distance, compass bearing and the publisher-based severity.
- **Where you are:** this computer's location (Windows location, otherwise the approximate IP location), or the place or coordinates you name.
- **Caveats:** disease reports are marked as placed at a country's centre. Scheduled items dated in the future, such as next year's road works, never count as serious.

The **main window** shows the radar beside what HAYAT senses locally, under the Wi-Fi sensing panel. Your location sits in the middle, north is up, and each hazard is a blip at its real bearing. Distance is drawn on a square-root scale, so close things are not crushed into the centre. Colour shows severity, and high-severity blips pulse. Hovering over a blip names it. The sweep line is decoration only: blips come only from the last reading. The panel reads every 10 minutes. A medium or high hazard within 300 km that was not shown before gives one chat note ("Heads-up: …"), with at most three per reading. Country-level and scheduled items never trigger one. Privacy mode pauses the panel, because it uses this computer's location.

**Studios → Around me (hazard radar)** opens the full window. It has the large scope, the list beside it, a place box, radius (100–2000 km) and time window (24 hours to 30 days), and **Fetch latest feeds**, which refreshes the hazard feeds that are due before reading.

### Second wave (2026-10-15)

Seven more feeds were added, and all seven were checked live on a temporary database (0 failures, 3.9 seconds). They bring two new categories: **Asteroids & fireballs** and **Air quality**. Their severity rules:

- **BMKG quakes:** high when BMKG says the quake has tsunami potential or it is M7+, and medium at M6+.
- **Fireballs:** high at 10 kt or more, medium at 1 kt or more. For scale, Chelyabinsk in 2013 was about 440 kt.
- **Asteroids:** never higher than low. One passing inside the Moon's distance is low, and the rest are informational.
- **Kp:** high at 7 or more (G3+), and medium at 5 or more (G1–G2).
- **Solar flares:** X class is high, M is medium and C is low.
- **Singapore PSI:** over 200 is high, and 101–200 is medium.
- **Hong Kong:** typhoon signal 8 or above, a red or black rainstorm, or red fire danger is high. Signal 3, amber rain, thunderstorm, heat, cold and similar warnings are medium.

"Any asteroids passing earth this week", "air quality in Singapore right now" and "any earthquakes in Indonesia today" read the matching feeds first. Space-weather questions now also refresh the Kp and flare feeds.

Checked and not used: Italy's INGV and ReliefWeb (robots.txt), Turkey's AFAD (server errors), Copernicus EMS and the old Canadian feed (gone), and the Philippines' PHIVOLCS and Chile's CSN (heavy web pages only, with no data feed).

### Official hazard warnings (2026-10-14)

The nine feeds above come from the agencies that issue the warnings, so they count as official sources when events are merged and verified. JMA and GeoNet quakes also merge with the USGS and EMSC reports of the same quake, which gives independent confirmation. Severity follows each publisher's own scale, and the reason is shown:

- **Tsunami:** a warning is high. An advisory, watch or threat is medium. An information statement, which says no threat exists, is informational.
- **Volcanoes:** a USGS alert of WARNING or RED is high, and WATCH or ORANGE is medium. GeoNet level 3 or above is high, and level 2 is medium.
- **JMA quakes:** shaking of shindo 6- or above, or M7+, is high. Shindo 5- or above, or M6+, is medium.
- **GeoNet quakes:** MMI 7 or above, or M7+, is high. MMI 5 or above, or M6+, is medium.
- **Outbreaks:** every WHO outbreak report is medium.
- **Airspace:** volcanic ash is high, and other SIGMETs are medium.
- **England floods:** a severe flood warning is high, a flood warning is medium and a flood alert is low.

Quiet states are not shown as events: volcanoes at level 0, flood warnings no longer in force, and an empty tsunami feed. "Any tsunami warnings right now?", "any disease outbreaks in the world right now", "any volcano alerts right now" and "any earthquakes near Tokyo" read the matching feeds first ([app/intelligence/world_requests.py](../app/intelligence/world_requests.py)). The map gains two categories: **Tsunami** and **Disease outbreaks**.

Checked and not used: Germany's DWD warnings (robots.txt disallows), Smithsonian volcano reports and CISA advisories (both refuse automated clients with 403), IODA internet outages (server errors) and MeteoAlarm (megabytes per country with no coordinates). JTWC typhoon warnings were also left out, because their positions appear only inside per-storm text and GDACS already covers those storms. The parsers are in [app/intelligence/hazards.py](../app/intelligence/hazards.py).
| London cameras | [TfL open data](https://tfl.gov.uk/info-for/open-data-users/our-open-data) | 60 minutes for catalog | Public JamCam metadata; publisher stills update separately, roughly every three minutes |
| California cameras (12 feeds) | [Caltrans CCTV contract](https://cwwp2.dot.ca.gov/documentation/cctv/cctv.htm) | 60 minutes for catalogs | Twelve district catalogs, public stills and video URLs where supplied |

### Open camera networks

Fifty-six more catalogs from agencies and institutions publish cameras with no API key (counting Texas's 25 districts). Each was checked
live on October 5, 2026 through HAYAT's own fetcher, robots.txt rules included:

| Network | Cameras (live check) | Media | Catalog refresh |
|---|---|---|---|
| [New York City DOT](https://webcams.nyctmc.org/) | 973 | still images | 60 minutes |
| [Fintraffic Digitraffic, Finland](https://www.digitraffic.fi/en/road-traffic/) | 810 stations | road-weather stills, several views each | 60 minutes |
| [Singapore LTA (data.gov.sg)](https://data.gov.sg/) | 8 (count varies) | still images | 10 minutes |
| [Hong Kong Transport Department](https://data.gov.hk/en-data/dataset/hk-td-tis_2-traffic-snapshot-images) | 1,013 | still images | 60 minutes |
| [NZ Transport Agency](https://www.nzta.govt.nz/traffic-and-travel-information/) | 313 | still images | 60 minutes |
| [Delaware DOT](https://deldot.gov/map/) | 361 | live HLS video | 60 minutes |
| [Statens vegvesen, Norway](https://www.vegvesen.no/trafikk/) (added 2026-10-11) | 894 (859 available) | stills; 138 with live HLS video | 60 minutes |
| [Transpordiamet (Tark tee), Estonia](https://tarktee.transpordiamet.ee/) (added 2026-10-11) | 176 | road-weather stills (each address is one capture) | 15 minutes |
| [City of Tallinn](https://ristmikud.tallinn.ee/) (added 2026-10-11) | 255 | intersection stills, about every minute | list ships with HAYAT |
| [Maryland CHART](https://chart.maryland.gov/) | 552 | publisher player page | 60 minutes |
| [City of Austin](https://data.austintexas.gov/Transportation-and-Mobility/Traffic-Cameras/b4k4-adkb) | 820 active | still images | 60 minutes |
| [Seattle DOT and WSDOT](https://web.seattle.gov/Travelers/) | 654 | still images | 60 minutes |
| [Iowa DOT](https://www.511ia.org/) | 1,258 | stills, plus about 700 live HLS streams | 60 minutes |
| [Québec 511](https://www.quebec511.info/) | 680 | publisher player page | 60 minutes |
| [ALERTCalifornia (UC San Diego)](https://alertcalifornia.org/) | 1,289 | wildfire-watch stills and publisher viewer | 60 minutes (26 pages of 50) |
| [DriveBC, British Columbia](https://www.drivebc.ca/) | 1,066 | still images | 60 minutes |
| [Vegagerðin, Iceland](https://www.vegagerdin.is/) | 496 | still images | 60 minutes |
| [City of Toronto](https://open.toronto.ca/dataset/traffic-cameras/) | 336 | still images | 60 minutes |
| [City of Calgary](https://data.calgary.ca/) | 216 | still images | 60 minutes |
| [Illinois DOT (Travel Midwest)](https://travelmidwest.com/) | 2,985 | publisher viewer | 60 minutes (3 pages) |
| [Michigan DOT (Mi Drive)](https://mdotjboss.state.mi.us/MiDrive/map) | 804 | publisher viewer | 60 minutes |
| [DGT, Spain national roads (DATEX II)](https://nap.dgt.es/) | 1,955 | still images | 60 minutes |
| [Ayuntamiento de Madrid](https://datos.madrid.es/portal/site/egob/) | 357 | still images | 60 minutes |
| [ODOT TripCheck, Oregon](https://www.tripcheck.com/) | 1,159 | still images | 60 minutes |
| [City of Ottawa](https://traffic.ottawa.ca/) | 428 | still images | 60 minutes |
| [USGS volcano observatories (AshCam)](https://volcview.wr.usgs.gov/) | 370 (238 with an image in the last 3 days) | volcano webcam stills | 60 minutes |
| [Minnesota DOT (511mn, CARS)](https://511mn.org/) | 1,532 | live HLS video (1,251), stills (281) | 60 minutes |
| [Colorado DOT (COtrip, CARS)](https://www.cotrip.org/) | 1,029 | stills, live HLS video (815) | 60 minutes |
| [Indiana DOT (CARS)](https://511.in.gov/) | 749 | stills and live HLS video | 60 minutes |
| [Kansas DOT (KanDrive, CARS)](https://www.kandrive.gov/) | 608 | stills, live HLS video (200) | 60 minutes |
| [Nebraska DOT (CARS)](https://new.511.nebraska.gov/) | 347 | stills, several views each | 60 minutes |
| [MassDOT (Mass511, CARS)](https://mass511.com/) | 308 | stills | 60 minutes |
| [MoDOT Traveler (Missouri)](https://traveler.modot.org/) | 555 | live HLS video | 60 minutes |
| [TxDOT, 25 districts (Texas)](https://its.txdot.gov/) | 4,371 (4,168 online) | snapshots (base64 JSON, decoded) | 60 minutes |
| [ALDOT ALGO Traffic (Alabama)](https://algotraffic.com/) | 537 | live HLS video and stills | 60 minutes |

Together with London and California that is 25,496 public cameras in a live check of
all 41 feeds (48 seconds, none failed). The
third wave (Spain, Madrid, Oregon and Ottawa) added 3,899, all with stills, and a
snapshot loaded from each of the four. Spain's national catalog is DATEX II XML
(3.7 MB, accepted up to 16 MB) and Madrid's is KML; both are parsed with
document-type and entity declarations refused. Checked and left out on the same
day: Germany's Autobahn API (it now publishes no webcams), NSW Live Traffic
(its image server refuses automated requests), Catalonia (its HTTPS is too weak
to connect, and HAYAT fetches camera media over HTTPS only), the Basque Country
(20 records per page, most without an image), Taiwan's freeway catalog (not
reachable from here), and Vancouver (robots.txt disallows it).

The fourth wave (2026-10-06) added 10,036 US cameras from nine states. Six of them
(Minnesota, Colorado, Nebraska, Indiana, Kansas, Massachusetts) run the Castle Rock
CARS 511 platform, whose traveler gateway (`<state>tg.carsprogram.org`) is keyless
and allowed by robots.txt. One adapter reads all six, and each network allowlists
only the stream and image hosts its own catalog used. The others are MoDOT's
open stream list, TxDOT's per-district camera service and ALDOT's ALGO Traffic
API. A live refresh of all 26 Texas and Alabama feeds took 10 seconds, and
frames from every network that publishes stills decoded. Left out on purpose:
South Dakota (CARS gateway disallowed by robots.txt), Arkansas (its streams are
marked protected and answer 403, with no snapshots), Massachusetts' MJPEG video
(served through a vendor key, so its plain stills are used instead), Minnesota's
video previews (403), the IBI 511 sites' internal map endpoints (their documented
API needs a developer key, which is already supported), Norway (DATEX needs
registration and the catalog API is disallowed) and Ireland (no open camera
endpoint found). For Hyderabad no
government or publisher offers an open camera catalog; search results there were
aggregators re-hosting other feeds and a directory of unsecured private cameras,
which HAYAT does not use. Michigan DOT images are still disallowed by robots.txt,
so its 804 cameras stay viewer links. USGS AshCam serves the same images from two
hosts (volcview and avo-volcview), and both are allowlisted. Webcams it lists at
0,0 are dropped as unplaced. Earlier totals:
19,115 cameras, 13,727 with stills, 4,253 with live video and 6,310 opening in a
publisher viewer (some offer more than one).

The World atlas now loads every located camera and event in one compact request
(`points`: id, position, kind, title and media flags) and clusters only what is
on screen, so the whole globe is visible at once. It used to page full records
and stop at 5,000, so a world view showed about a quarter of the cameras, and
whole continents looked empty. The second wave of seven networks refreshed in 14 seconds.
A snapshot loaded from every network that publishes stills. ALERTCalifornia's
wildfire cameras pair with the wildfire layer, so a fire event can open the
closest fire-watch camera. The Illinois and Michigan catalogs are open, but their
image servers' robots.txt disallows automated retrieval. HAYAT therefore stores
no image URL for them and opens the publisher's own viewer in your browser. Media URLs are allowlisted per network (host, HTTPS, and port
443, or 8888 for Iowa's video servers), so a catalog cannot point HAYAT at
another site. Kentucky's camera images were left out because the publisher's
robots.txt disallows retrieval. Austin's planned or removed entries are skipped.
Hong Kong's XML catalog is parsed with document-type and entity declarations
refused. These are public publisher cameras only. HAYAT does not search for
private or unsecured cameras, does not record continuously and does no face or
plate recognition.

### Turning on keyed camera networks

Nineteen more networks are built in and switch on as soon as you add a free
developer key. Register on the network's developer page (for example
`https://511on.ca/developers/doc`), then add a line to `.env` in the HAYAT folder
and restart:

```text
HAYAT_KEY_511_ONTARIO=your-key
HAYAT_KEY_WSDOT=your-access-code
```

The 511 networks are `ONTARIO`, `ALBERTA`, `SASKATCHEWAN`, `MANITOBA`,
`NEWBRUNSWICK`, `NOVASCOTIA`, `ARIZONA`, `GEORGIA`, `NEWYORK`, `PENNSYLVANIA`,
`WISCONSIN`, `IDAHO`, `NEVADA`, `LOUISIANA`, `ALASKA`, `UTAH`, `CONNECTICUT` and
`NEWENGLAND` (variable `HAYAT_KEY_511_<NAME>`). Without a key, a network stays
off and a manual refresh reports which variable to add. The key is used only in
the request URL. It is replaced by `***` in stored source state, error messages,
the saved brief and the research cache, and `.env` is never committed. The 511
and WSDOT adapters follow the publishers' documented formats. They were tested
against recorded-shape fixtures, not live, because no key was available.

An October 5, 2026 live check fetched and normalized all 18 feeds: 6,625 provider
records, including 890 TfL and 3,591 Caltrans camera catalog entries. These are
catalog counts, not confirmed simultaneously live streams. Future
availability depends on the providers and network. Failed or offline feeds keep
their last snapshot, visibly marked stale. Unknown locations remain unknown and
are not plotted at (0, 0). Event time and fetch time are distinct. Items leaving
a rolling feed are reported as no longer present, not resolved.

## Chat and tools

```text
Show global intelligence
Refresh intelligence feeds
Show intelligence alerts
Show intelligence brief
Analyze global intelligence
Analyze intelligence in Europe
Analyze intelligence about Microsoft
Explain intelligence event earthquakes:EVENT_ID
Analyze intelligence event earthquakes:EVENT_ID
Check intelligence monitor
Stop intelligence monitor
Start intelligence monitor
Open world atlas
Find public cameras in London
Find public cameras in Hong Kong
Find cameras near 40.71, -74.0 within 20 km
Show the nearest cameras near event earthquakes:EVENT_ID
Show camera directories
Recall intelligence knowledge about floods
```

### Everyday questions

Ordinary phrasings reach the same tools
([world_requests](../app/intelligence/world_requests.py)):

| Ask | Tool used |
|-----|-----------|
| Which planes are flying over Dubai? · Air traffic over London · Planes near me | `intelligence_aircraft` (live ADS-B) |
| Is it going to rain in Lahore tomorrow? · How windy is Chicago today? · Do I need an umbrella in London? | `intelligence_around` with weather: now, plus today / tomorrow / the day after (MET Norway) |
| Any earthquakes near Tokyo right now? · Any storms near Florida? · Is there a hurricane anywhere? · Show me floods in Pakistan | `intelligence_objects` for that hazard, after refreshing its feeds |
| Space weather today · Any cyber attacks reported today? | `intelligence_brief` for that feed, after refreshing it |
| What happened near Karachi in the last week? | `intelligence_region_changes` |
| Give me a world brief · What are the latest alerts? · What is happening in the world today? · Show me the map of current events | brief, alerts, assessment, map |
| Watch Istanbul for earthquakes · Stop watching Istanbul · List my watches | `intelligence_watch` (a bounded area and that hazard's feeds) |

A hazard question first refreshes the feeds it depends on (cooldowns apply),
so "0 reports" means none were reported, not that the feed was never fetched.
If no feed can be read, the answer says it is from older stored reports.
Questions *about* a subject ("what causes earthquakes", "how do hurricanes
form", "tell me about fires") stay ordinary questions, and "stop watching the
video" stays with the player: only a known city or country is a watch.

**Show Osiris status** and **Show Osiris playbook** now route to this workspace
and its evidence brief in a full Hayat runtime. The earlier income-only helper
remains compatible in isolated business workspaces.

```text
intelligence_watch {"action":"save","key":"pacific-region","data":{"title":"Pacific earthquakes M5+","feeds":["earthquakes"],"bbox":[170,-30,-170,30],"min_magnitude":5}}

intelligence_watch {"action":"save","key":"product-monitor","data":{"title":"Microsoft catalog changes","feeds":["cyber"],"keywords":["Microsoft"]}}

intelligence_dashboard {"feed":"earthquakes","bbox":[170,-30,-170,30],"limit":100}

intelligence_brief {"query":"wildfire"}

intelligence_history {"feed":"earthquakes"}

intelligence_dashboard {"limit":200,"offset":200}

intelligence_dashboard {"category":"earthquakes","min_severity":"medium","since_hours":24}

intelligence_assess {"region":"East Asia","min_severity":"high"}

intelligence_analyze {"event_id":"EVENT_ID_FROM_CATALOG"}

intelligence_alerts {"feed":"earthquakes","limit":100,"offset":100}

intelligence_preferences {"action":"save","data":{"home_detail":"detailed","home_alert_limit":2}}

intelligence_cameras {"feed":"cameras_caltrans_4","available_only":true,"limit":200}

intelligence_cameras {"near":[40.71,-74.0],"radius_km":20,"media":"image"}

intelligence_cameras {"near_event":"EVENT_ID_FROM_CATALOG","radius_km":100}

intelligence_camera_frame {"event_id":"CAMERA_ID_FROM_CATALOG"}

intelligence_resources {"category":"satellite"}

intelligence_weather {"latitude":51.5,"longitude":-0.12}

intelligence_map {"event_id":"EVENT_ID_FROM_CATALOG"}
```

Bounds use `[west, south, east, north]`. A west value greater than east crosses
the date line. Geographic rules skip items without valid point coordinates.
Magnitude thresholds apply only to earthquakes because different sources use
different magnitude units. Alerts deduplicate a watch and event revision across
refreshes. Rules and alerts survive restarts.

## What was learned and reused

The owner identified [Osiris](https://www.osirisai.live/) and its
[MIT-licensed repository](https://github.com/simplifaisoul/osiris). Its public
documentation and earthquake adapter informed the layered-feed architecture:
normalize upstream formats, cache by source cadence, separate map presentation
from data retrieval and treat outages as ordinary operational conditions.
Hayat's Python adapters, monitoring, storage and Tk interface are original code;
there is no Osiris runtime dependency or copied application bundle.

The offline basemap derives from [Natural Earth's public-domain land data](https://www.naturalearthdata.com/about/terms-of-use/).
Its source URL, content hash, transformation and terms are embedded in
`app/assets/world-land.json`. `scripts/build_intelligence_map.py` rebuilds it.
Existing open-source HTTPX, SQLite, JSON Schema validation and Tk provide
networking, persistence, tool contracts and the native interface. See
[source and reuse records](open-source-intelligence-sources.md).
Leaflet 1.9.4 (BSD-2-Clause) and HLS.js 1.7.3 (MIT) are bundled locally with
licenses and source/hash manifests; runtime pages have no CDN dependency.
`scripts/vendor_intelligence_map.py` rebuilds these assets.

This implementation adds a practical autonomous intelligence foundation; it
does not reproduce Osiris's entire product. Public ADS-B aircraft, Baltic AIS
vessels and a computed ISS orbit are connected as on-demand atlas layers.
Sanctions and speculative forecasts are not connected.
Connected cameras cover London, California, New York City, Finland, Iceland,
Singapore, Hong Kong, New Zealand, British Columbia, Toronto, Calgary, Québec,
Delaware, Maryland, Austin, Seattle, Iowa, Illinois and Michigan; many places
have no public camera. The native map uses an equirectangular projection, while the atlas uses
Leaflet's street-map projection. No fabricated entities or prediction
probabilities fill missing layers.

## Verification

Tests exercise real isolated SQLite persistence, feed schemas, stale retention,
cooldowns, automatic monitoring, registry denials, independent call budgets,
deduplicated alerts, date-line regions, unknown locations, snapshot limits,
chat routing, source-linked replies, pointer-centered zoom, geographic bounds,
pagination, revision inspection, preferences, home alerts and native tab/form
navigation, severity/category/time filters, the classification rules, cached
row reuse, corroboration and relevance scoring, local-model analysis, the live
feed, the compact layout, extended publisher contracts, camera preview cooldowns, expiry-aware
source caching, point weather and the loopback atlas boundary. Browser checks
verified street tiles, a real TfL snapshot/video, resource links and a smaller
viewport without JavaScript errors. Public-feed live
checks are separate from mocked regression tests.

Run `python scripts/verify_public_intelligence.py --extended` for all configured extended feeds
(omit `--extended` for the four core feeds).
Its database, brief and `verification.json` stay in a separate `data/work`
workspace rather than the owner's live intelligence records.

See the October 5 recovery completion
for the final fixes, regression results and live-weather limitation.

## Sorting and filters

The atlas adds category/severity filters, priority/name/distance sorting and
Show 40 more. Camera queries can sort by title, distance, media or freshness;
merged objects can also sort by time and confidence. Geographic queries retain
their constraints when empty. See behavior and limits.
