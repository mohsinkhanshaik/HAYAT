# Explore: launches, satellites, maps, fires, routes and bikes

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
  model reading) and **Growing fires**. The text answer
  shows on the left and any picture on the right. Double-click the picture to
  open it full size. A failure says why in the tab.
- **Cameras tab → Count vehicles:** counts the cars, trucks, buses,
  motorcycles and bicycles in the selected public camera's current still,
  and rates the traffic as empty, light, moderate or busy. The first count
  downloads a 120 MB detector into `data\models\vision\`; after that a count
  takes about 0.1 s on the CPU.
- **World atlas → Fires · last 24 h:** a layer of satellite heat detections in
  the current view, sized by fire power. It follows the map as you pan.

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

**Not used:** Radio Browser. Its robots.txt disallows the API, and HAYAT
honours that refusal.

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
- **Aircraft owners are not shown.** adsbdb sends a `registered_owner` field,
  and HAYAT drops it before anything is shown or kept.
- Pictures are saved under `data\memory\explore\images\` (the newest 200),
  and caches under `data\memory\explore\cache\`. Both are git-ignored.

Code: [explore](../app/intelligence/explore.py),
[Explore tab](../app/gui_intelligence_explore.py), atlas layer in
[map.js](../app/assets/intelligence-map/map.js). Tests:
[test_explore](../tests/test_explore.py).
