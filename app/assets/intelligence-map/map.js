/* Source fields use textContent. Media starts only from an explicit camera action. */
'use strict';
const $ = id => document.getElementById(id);
const motionPreference = matchMedia('(prefers-reduced-motion: reduce)');
const map = L.map('map', {preferCanvas: true, worldCopyJump: true, zoomControl: false,
  zoomAnimation: !motionPreference.matches}).setView([25, 0], 2);
L.control.zoom({position: 'topright'}).addTo(map);
L.control.scale({position: 'bottomleft', imperial: false}).addTo(map);
map.attributionControl.setPrefix(false);
map.createPane('atlas-land').style.zIndex = '190';
const markers = L.layerGroup().addTo(map);
const cartography = L.layerGroup();
let rows = [], total = 0, loading = 0, pending = null, config = null, landLayer = null;
let hls = null, imageURL = null, selected = null, detailsVersion = 0, sources = [];
let frameTimer = null, frameRequest = null, videoTimer = null, mediaGeneration = 0;
const street = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, className: 'street-tiles', attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
  referrerPolicy: 'strict-origin-when-cross-origin'
});
street.on('tileerror', () => message('Some street tiles are unavailable; the atlas overview remains visible.'));
function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = String(text);
  if (cls) el.className = cls;
  return el;
}
function message(text) { $('status').textContent = text; }
// The control that opened the details panel gets focus back when the panel closes (Escape or ×).
let detailsOpener = null;
function showDetails(open = true) {
  if (open && document.body.classList.contains('map-focused')) setMapFocus(false);
  const panel = $('details-panel');
  if (open && panel.hidden && !panel.contains(document.activeElement)) detailsOpener = document.activeElement;
  panel.hidden = !open;
  $('toggle-details').setAttribute('aria-expanded', String(open));
  requestAnimationFrame(() => map.invalidateSize({pan: false}));
  if (typeof viewChanged === 'function') viewChanged();
}
function focusFirstVisible(...candidates) {
  for (const el of candidates) {
    if (el && el !== document.body && el.isConnected && el.getClientRects().length && !el.disabled) { el.focus({preventScroll: true}); return el; }
  }
  $('map').focus({preventScroll: true}); return $('map');
}
/* Closing the panel stops its media and stops waiting for its request (the panel then says it was cancelled). */
function closeDetailsPanel({focus = true} = {}) {
  if (detailsRequest) cancelTask(detailsRequest.controller);
  stopMedia(); showDetails(false);
  // Observation cards are redrawn as the map moves, often during the very click that opened the panel:
  // find the card again by its record id (the opener's, else the selected record's).
  const cardFor = id => id ? document.querySelector(`.observation-card[data-record-id="${CSS.escape(id)}"]`) : null;
  if (focus) focusFirstVisible(cardFor(detailsOpener?.dataset?.recordId), detailsOpener, cardFor(selected), $('toggle-details'));
  detailsOpener = null;
}
function longitudeNear(longitude, centre) { return centre + (((longitude - centre + 180) % 360 + 360) % 360) - 180; }
function rowEnabled(row) {
  return (row.kind === 'public_camera' ? $('cameras').checked : $('events').checked)
    && (!$('fresh-only').checked || !row.stale)
    && (!$('hide-failed').checked || row.kind !== 'public_camera' || !row.flags.includes('b'))
    && (!$('category').value || row.category === $('category').value)
    && (!$('severity').value || row.severity === $('severity').value);
}
function inBounds(row, bounds) {
  return row.latitude >= bounds.getSouth() && row.latitude <= bounds.getNorth()
    && (bounds.getEast() - bounds.getWest() >= 360
      || longitudeNear(row.longitude, bounds.getCenter().lng) >= bounds.getWest()
        && longitudeNear(row.longitude, bounds.getCenter().lng) <= bounds.getEast());
}
let browseSignature = '';
let browseLimit = 40;
function distanceFromCentre(row, centre) {
  const rad = Math.PI / 180, dLat = (row.latitude - centre.lat) * rad;
  const dLon = (longitudeNear(row.longitude, centre.lng) - centre.lng) * rad;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(centre.lat * rad) * Math.cos(row.latitude * rad) * Math.sin(dLon / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(Math.min(1, Math.max(0, a))));
}
// One Tab stop for the whole observation list (a keyboard user had to Tab through every record to reach the map);
// Arrow keys, Home and End move between records, Enter opens one.
function rovingList(list) {
  const cards = [...list.querySelectorAll('.observation-card')];
  const current = cards.find(card => card.getAttribute('aria-current') === 'true') || cards[0];
  for (const card of cards) card.tabIndex = card === current ? 0 : -1;
  list.setAttribute('aria-label', `${cards.length} observations. Use the arrow keys to move between them.`);
}
function moveInList(event) {
  const cards = [...$('browse-list').querySelectorAll('.observation-card')];
  const index = cards.indexOf(document.activeElement);
  if (index < 0) return;
  const next = {ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: cards.length - 1}[event.key];
  if (next === undefined) return;
  event.preventDefault();
  const target = cards[Math.max(0, Math.min(cards.length - 1, next))];
  for (const card of cards) card.tabIndex = card === target ? 0 : -1;
  target.focus();
}
function updateBrowse(visible) {
  countTo($('record-count'), visible.length);
  countTo($('stream-count'), visible.filter(row => row.flags.includes('s')).length);
  countTo($('still-count'), visible.filter(row => row.kind === 'public_camera' && row.flags.includes('i')).length);
  const centre = map.getCenter(), lon = longitudeNear(centre.lng, 0);
  $('view-coordinates').textContent = `${Math.abs(centre.lat).toFixed(1)}° ${centre.lat < 0 ? 'S' : 'N'} · ${Math.abs(lon).toFixed(1)}° ${lon < 0 ? 'W' : 'E'} · Z${map.getZoom()}`;
  const sort = $('sort').value;
  const distances = sort === 'nearest' ? new Map(visible.map(row => [row.id, distanceFromCentre(row, centre)])) : null;
  const sorted = [...visible].sort((a, b) => {
    // Cameras HAYAT saw fail recently (flag b) sort with stale records, after everything that works.
    const priority = row => row.stale || row.flags.includes('b') ? 5 : row.severity === 'high' ? 0 : row.severity === 'medium' ? 1 : row.flags.includes('s') ? 2 : row.kind === 'public_camera' ? 3 : 4;
    return (sort === 'nearest' ? distances.get(a.id) - distances.get(b.id) : sort === 'title' ? 0 : priority(a) - priority(b))
      || a.title.localeCompare(b.title) || a.id.localeCompare(b.id);
  }).slice(0, browseLimit);
  $('visible-count').textContent = `${visible.length.toLocaleString()} records`;
  const signature = sorted.map(row => `${row.id}:${row.stale}:${row.flags}:${row.title}:${row.media}:${distances?.get(row.id)?.toFixed(1)}`).join('|') + ':' + selected + ':' + sort;
  const completeSignature = signature + ':' + sorted.length + ':' + visible.length;
  if (completeSignature === browseSignature) return;
  browseSignature = completeSignature;
  const list = $('browse-list'); list.replaceChildren();
  if (!visible.length) list.append(node('p', 'No matching observations here. Zoom out or reset your filters to explore another view.', 'note'));
  for (const row of sorted) {
    const camera = row.kind === 'public_camera', stream = camera && row.flags.includes('s');
    const card = button('', () => {
      map.setView([row.latitude, longitudeNear(row.longitude, map.getCenter().lng)], Math.max(map.getZoom(), camera ? 10 : 6));
      inspect(row.id);
    });
    card.className = `observation-card ${stream ? 'stream' : camera ? 'camera' : 'event'}`;
    card.dataset.recordId = row.id; card.setAttribute('aria-current', String(selected === row.id));
    const text = node('span'); text.append(node('strong', row.title),
      node('small', (camera ? row.media : row.category.replaceAll('_', ' ') || 'Observation') + (row.stale ? ' · stale feed' : '')
        + (camera && row.flags.includes('b') ? ' · last check failed' : '')
        + (distances ? ` · ${distances.get(row.id).toFixed(1)} km` : '')));
    card.append(node('span', stream ? '▶' : camera ? '◉' : '◇', 'record-icon'), text); list.append(card);
  }
  rovingList(list);
  if (visible.length > sorted.length) {
    list.append(node('p', `Showing ${sorted.length} of ${visible.length.toLocaleString()}.`, 'note'));
    list.append(button('Show 40 more', () => { browseLimit += 40; browseSignature = ''; updateBrowse(visible); }));
  }
}
function addCartography() {
  for (let lon = -180; lon <= 180; lon += 30) L.polyline([[-80, lon], [80, lon]],
    {color: '#66848c', opacity: .12, weight: .6, interactive: false}).addTo(cartography);
  for (let lat = -60; lat <= 60; lat += 30) L.polyline([[lat, -180], [lat, 180]],
    {color: '#66848c', opacity: lat === 0 ? .22 : .12, weight: .6, interactive: false}).addTo(cartography);
  for (const [lat, lon, title, ocean] of [[47, -109, 'NORTH AMERICA'], [-17, -62, 'SOUTH AMERICA'],
    [57, 25, 'EUROPE'], [8, 22, 'AFRICA'], [42, 94, 'ASIA'], [-27, 134, 'OCEANIA'],
    [0, -133, 'PACIFIC OCEAN', true], [10, -37, 'ATLANTIC OCEAN', true], [-25, 75, 'INDIAN OCEAN', true]]) {
    L.marker([lat, lon], {interactive: false, keyboard: false, zIndexOffset: -1000,
      icon: L.divIcon({className: 'atlas-label' + (ocean ? ' ocean-label' : ''), html: title, iconSize: [160, 18]})}).addTo(cartography);
  }
}
const CATEGORY_COLOURS = {earthquakes: '#ff5a5f', volcanoes: '#ff8a3d', wildfires: '#ffb020', severe_weather: '#4fc3f7',
  floods: '#3d8bfd', space_weather: '#f5e663', cyber: '#b388ff', traffic: '#e8a33d', other: '#9fb3c8'};
function categoryColour(category) { return CATEGORY_COLOURS[category] || '#e7bc64'; }
function motionOn() { return !document.body.classList.contains('motion-paused'); }
/* Counters glide to their new value instead of jumping; instant when motion is paused. */
function countTo(el, value, suffix = '') {
  const from = Number(el.dataset.value || 0), started = performance.now(), span = 650;
  el.dataset.value = String(value);
  if (!motionOn() || from === value) { el.textContent = value.toLocaleString() + suffix; return; }
  function step(now) {
    const t = Math.min(1, (now - started) / span), eased = 1 - Math.pow(1 - t, 3);
    if (el.dataset.value !== String(value)) return;  // a newer count took over
    el.textContent = Math.round(from + (value - from) * eased).toLocaleString() + suffix;
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
/* A thin shimmering bar while anything is being read. */
let busyCount = 0;
function busy(delta) { busyCount = Math.max(0, busyCount + delta); $('progress').classList.toggle('active', busyCount > 0); }
/* One request at a time feeds the details panel: a new one stops waiting for the last, and each can be
   cancelled from the panel or from the activity notice (track() and cancelTask() live in atlas-ui.js). */
let detailsRequest = null;
function detailsTask(label) {
  if (detailsRequest) detailsRequest.controller.abort();
  const controller = new AbortController(), untrack = track(label, controller);
  const task = {controller, signal: controller.signal, done() { untrack(); if (detailsRequest === task) detailsRequest = null; }};
  detailsRequest = task;
  return task;
}
function cancelButton(task) {
  const el = button('Cancel', () => cancelTask(task.controller)); el.classList.add('cancel-request'); return el;
}
/* What a details request shows when it ends without an answer: cancelled (with a retry) or the error. */
function failureNodes(task, error, unavailableTitle, retry) {
  if (wasCancelled(task.controller)) return [node('h1', 'Cancelled'),
    node('p', 'The atlas stopped waiting for this answer. Nothing on the map changed.'), button('Try again', retry)];
  return [node('h1', unavailableTitle), node('p', error.message)];
}
function button(text, action) {
  const el = node('button', text); el.type = 'button'; el.addEventListener('click', action); return el;
}
function link(title, url) {
  const el = node('a', title);
  try {
    const parsed = new URL(url);
    if (!['https:', 'http:'].includes(parsed.protocol)) return node('span', title);
    el.href = parsed.href; el.target = '_blank'; el.rel = 'noopener noreferrer';
  } catch { return node('span', title); }
  return el;
}
function mediaKind(details) {
  if (details.live_page) return 'HLS stream';
  if (details.video_url) return new URL(details.video_url).pathname.toLowerCase().endsWith('.m3u8') ? 'HLS stream' : 'Video clip';
  return details.image_url ? 'Refreshing still' : details.page_url ? 'Publisher player' : 'No media';
}
async function json(path, signal) {
  const timeout = path.startsWith('deep?') ? 180000 : path.startsWith('camera/play?') ? 90000 : path === 'land' || path === 'config' ? 15000 : 45000;
  return requestJSON(path, {signal}, timeout);
}
async function requestJSON(path, options = {}, timeout = 45000) {
  const controller = new AbortController(), source = options.signal;
  const abort = () => controller.abort();
  if (source?.aborted) abort(); else source?.addEventListener('abort', abort, {once: true});
  let timedOut = false;
  const deadline = setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
  try {
    const response = await fetch(path, {...options, signal: controller.signal});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
    return value;
  } catch (error) {
    if (timedOut) throw new Error('The request timed out. Try again.');
    throw error;
  } finally { clearTimeout(deadline); source?.removeEventListener('abort', abort); }
}
function stopMedia() {
  ++mediaGeneration; clearTimeout(videoTimer); videoTimer = null;
  clearTimeout(frameTimer); frameTimer = null;
  if (frameRequest) { frameRequest.abort(); frameRequest = null; }
  if (hls) { hls.destroy(); hls = null; }
  const panel = $('details'), video = panel.querySelector('video'), auto = panel.querySelector('[data-auto-still]');
  if (auto) auto.checked = false;
  if (video) { video.pause(); video.removeAttribute('src'); video.load(); video.hidden = true; }
  if (imageURL) { URL.revokeObjectURL(imageURL); imageURL = null; }
}
/* The whole world in one compact request: [id, lat, lon, 'c'|'e', title, flags].
   Paging full records stopped at 5,000, so a world view showed a quarter of the
   cameras and whole continents looked empty. */
function pointMedia(flags) {
  return flags.includes('s') ? 'HLS stream' : flags.includes('v') ? 'Video clip' : flags.includes('i') ? 'Refreshing still'
    : flags.includes('p') ? 'Publisher player' : 'No media';
}
let loadedQuery = '';
async function load() {
  const token = ++loading; if (pending) pending.abort(); pending = new AbortController();
  const request = pending, untrack = track('Reading observations', request);
  const query = new URLSearchParams({query: $('query').value, feed: $('feed').value, media: $('media').value});
  loadedQuery = $('query').value;
  $('refresh').disabled = true; $('load-error').hidden = true;
  busy(+1); message('Reading local source evidence for the whole world…');
  try {
    const data = await json('points?' + query, request.signal);
    if (token !== loading) return;
    rows = data.points.map(([id, latitude, longitude, kind, title, flags, category]) => ({id, latitude, longitude, title, flags,
      category: category || '', severity: flags.includes('!') ? 'high' : flags.includes('+') ? 'medium' : '',
      kind: kind === 'c' ? 'public_camera' : 'event', media: kind === 'c' ? pointMedia(flags) : '', stale: flags.includes('x')}));
    const chosenCategory = $('category').value;
    const categories = [...new Set(rows.map(row => row.category).filter(Boolean))].sort();
    if (chosenCategory && !categories.includes(chosenCategory)) categories.push(chosenCategory);
    $('category').replaceChildren(new Option('All categories', ''), ...categories.map(value => new Option(value.replaceAll('_', ' '), value)));
    $('category').value = chosenCategory;
    browseLimit = 40;
    total = data.total;
    sources = data.sources.filter(s => !$('feed').value || s.key === $('feed').value);
    const ready = sources.filter(s => s.status === 'ok' && !s.stale).length;
    $('loaded-count').textContent = `${total.toLocaleString()} records loaded`;
    $('source-count').textContent = `${ready} / ${sources.length}`;
    message(`${data.cameras.toLocaleString()} public cameras and ${(rows.length - data.cameras).toLocaleString()} events worldwide · ${data.monitor.running ? 'monitor running' : 'monitor stopped'} · media loads on request`);
    draw();
  } catch (error) {
    if (token === loading && error.name !== 'AbortError') {
      const detail = 'View could not load: ' + error.message + (rows.length ? ' Previous results remain visible; they do not represent this new search.' : ' Try loading again.');
      message(detail); $('load-error-text').textContent = detail; $('load-error').hidden = false;
      $('loaded-count').textContent = rows.length ? `${rows.length.toLocaleString()} previous records retained` : 'No records loaded';
    } else if (token === loading && wasCancelled(request)) {
      message('Stopped reading observations. ' + (rows.length ? 'The previous results remain visible; they do not represent this new search.' : 'Refresh to try again.'));
      $('loaded-count').textContent = rows.length ? `${rows.length.toLocaleString()} previous records retained` : 'No records loaded';
    }
  }
  finally { busy(-1); untrack(); if (token === loading) $('refresh').disabled = false; viewChanged(); }
}
function draw() {
  markers.clearLayers(); const groups = new Map();
  updateBrowse(rows.filter(row => rowEnabled(row) && inBounds(row, map.getBounds())));
  if (landLayer) {
    if (!$('streets').checked && !$('satellite').checked && map.getZoom() <= 4) cartography.addTo(map);
    else map.removeLayer(cartography);
  }
  // Only what is on screen (plus a margin) is clustered; the whole world stays loaded.
  const view = map.getBounds().pad(0.25), wide = view.getEast() - view.getWest() >= 360;
  for (const row of rows) {
    const camera = row.kind === 'public_camera';
    if (!rowEnabled(row) || !wide && !inBounds(row, view)) continue;
    const longitude = longitudeNear(row.longitude, map.getCenter().lng);
    const p = map.latLngToContainerPoint([row.latitude, longitude]);
    const key = (camera ? 'camera' : 'event') + ':' + (map.getZoom() < 15 ? `${Math.floor(p.x/45)}:${Math.floor(p.y/45)}` : row.id);
    if (!groups.has(key)) groups.set(key, []); groups.get(key).push(row);
  }
  for (const group of groups.values()) {
    const row = group[0], camera = row.kind === 'public_camera';
    if (group.length > 1) {
      const lat = group.reduce((sum, r) => sum + r.latitude, 0) / group.length;
      const lon = group.reduce((sum, r) => sum + longitudeNear(r.longitude, map.getCenter().lng), 0) / group.length;
      const icon = L.divIcon({className: 'cluster' + (camera ? '' : ' event-cluster'), html: String(group.length), iconSize: [36, 36]});
      const what = `${group.length} ${camera ? 'public cameras' : 'provider observations'}`;
      L.marker([lat, lon], {icon, title: `${what}, press Enter to zoom in`}).addTo(markers).bindTooltip(`${what} · click to zoom`)
        .on('click', () => map.setView([lat, lon], map.getZoom() + 2));
    } else if (!camera && (row.severity === 'high' || row.severity === 'medium') && !row.stale) {
      // Severe events pulse, coloured by what they are; motion pauses with the rest of the atlas.
      const icon = L.divIcon({className: `pulse ${row.severity}`, iconSize: [22, 22],
        html: `<span style="--c:${categoryColour(row.category)}"></span>`});
      L.marker([row.latitude, longitudeNear(row.longitude, map.getCenter().lng)],
        {icon, title: `${row.title}, ${row.severity} severity ${(row.category || 'observation').replaceAll('_', ' ')}, press Enter for evidence`}).addTo(markers)
        .bindTooltip(node('span', `${row.title} · ${row.severity} severity`)).on('click', () => inspect(row.id));
    } else {
      const stream = camera && row.media === 'HLS stream', broken = camera && row.flags.includes('b');
      L.circleMarker([row.latitude, longitudeNear(row.longitude, map.getCenter().lng)], {radius: row.id === selected ? 10 : stream ? 6 : camera ? 4 : 5,
        color: row.id === selected ? '#dfc694' : row.stale || broken ? '#74818b' : stream ? '#a9d0a8' : camera ? '#8cbecb' : categoryColour(row.category),
        fillOpacity: broken ? .35 : .8, weight: row.id === selected ? 3 : 1.5})
        .addTo(markers).bindTooltip(node('span', row.title + (camera ? ' · ' + row.media : '') + (broken ? ' · last check failed' : '')))
        .on('click', () => inspect(row.id));
    }
  }
}
function cameraControls(panel, row, version) {
  const id = row.id, details = row.details, kind = mediaKind(details);
  panel.append(node('div', kind, 'media-badge'), node('p', `${details.provider} · ${details.availability_basis || ('publisher-reported ' + (details.available ? 'available' : 'unavailable'))}`, 'note'));
  const stage = node('section', undefined, 'media-stage');
  const frame = node('img'); frame.className = 'preview'; frame.alt = 'Requested public camera snapshot'; frame.hidden = true;
  const stamp = node('p', 'Choose a media action to connect to the publisher.', 'media-status'); stamp.setAttribute('role', 'status');
  const auto = node('input'); auto.type = 'checkbox'; auto.dataset.autoStill = 'true';
  auto.disabled = !details.image_url;
  const autoLabel = node('label', undefined, 'check'); autoLabel.append(auto, document.createTextNode(' Refresh selected still'));
  let frameFailures = 0;
  async function fetchFrame() {
    clearTimeout(frameTimer); frameTimer = null;
    if (frameRequest || version !== detailsVersion) return;
    const generation = mediaGeneration;
    loadFrame.disabled = true; const controller = new AbortController(); frameRequest = controller;
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch('frame?id=' + encodeURIComponent(id), {signal: controller.signal});
      if (!response.ok) throw new Error((await response.json()).error);
      const blob = await response.blob();
      if (version !== detailsVersion || generation !== mediaGeneration || controller.signal.aborted) return;
      frameFailures = 0;
      if (imageURL) URL.revokeObjectURL(imageURL);
      imageURL = URL.createObjectURL(blob); frame.src = imageURL; frame.hidden = false;
      stamp.textContent = `Fetched ${response.headers.get('X-Frame-Fetched-At')} · capture time unverified · publisher caching applies`;
      if (auto.checked) frameTimer = setTimeout(fetchFrame, Math.max(30, Math.min(3600, details.refresh_seconds || 120)) * 1000);
    } catch (error) {
      if (version === detailsVersion && generation === mediaGeneration) {
        const delay = Math.min(300, 30 * 2 ** Math.min(frameFailures++, 4));
        stamp.textContent = 'Snapshot unavailable: ' + error.message + (auto.checked ? ` · retrying in ${delay}s; last image retained.` : '');
        if (auto.checked) frameTimer = setTimeout(fetchFrame, delay * 1000);
      }
    } finally {
      clearTimeout(timeout); if (frameRequest === controller) frameRequest = null;
      loadFrame.disabled = !details.image_url;
    }
  }
  const loadFrame = button('Load latest snapshot', fetchFrame); loadFrame.disabled = !details.image_url;
  auto.addEventListener('change', () => { if (auto.checked) fetchFrame(); else { clearTimeout(frameTimer); frameTimer = null; } });
  stage.append(loadFrame, autoLabel, frame);
  if (details.video_url || details.live_page) {
    let failures = 0, generation = -1;
    const video = node('video'); video.controls = true; video.muted = true; video.preload = 'none'; video.hidden = true; video.playsInline = true;
    video.setAttribute('aria-label', kind + ' from ' + details.provider);
    const active = () => version === detailsVersion && generation === mediaGeneration;
    function retry(reason) {
      if (!active() || videoTimer) return;
      if (kind !== 'HLS stream') { stamp.textContent = 'Publisher clip unavailable. Try the publisher link.'; return; }
      const delay = Math.min(60, 2 ** Math.min(failures++, 6));
      stamp.textContent = `${reason} · reconnecting in ${delay}s.`;
      videoTimer = setTimeout(() => { videoTimer = null; if (active()) return connect(); }, delay * 1000);
    }
    async function connect() {
      if (!active()) return;
      stamp.textContent = 'Connecting to publisher…';
      let url = details.video_url;
      try {
        if (details.live_page) {
          const prepared = await json('camera/play?id=' + encodeURIComponent(id));
          if (!prepared.video_url) throw new Error(prepared.notice || 'Official channel is offline');
          url = prepared.video_url;
        }
      }
      catch (error) { retry('Broadcast unavailable: ' + error.message); return; }
      if (!active()) return;
      const isHLS = !!details.live_page || new URL(url).pathname.toLowerCase().endsWith('.m3u8');
      function begin() { video.play().catch(() => { stamp.textContent = 'Video loaded. Press Play in the player.'; }); }
      if (isHLS && window.Hls && Hls.isSupported()) {
        if (hls) hls.destroy();
        const player = new Hls({maxBufferLength: 15, maxMaxBufferLength: 30, backBufferLength: 0}); hls = player;
        player.loadSource(url); player.attachMedia(video); player.on(Hls.Events.MANIFEST_PARSED, begin);
        player.on(Hls.Events.ERROR, (_event, data) => {
          if (data.fatal && hls === player) retry('Publisher stream interrupted');
        });
      } else { video.src = url; begin(); }
    }
    const play = button('Play publisher video', () => {
      stopMedia(); frame.hidden = true; video.hidden = false;
      generation = mediaGeneration; failures = 0;
      return connect();
    });
    video.addEventListener('playing', () => { stamp.textContent = kind === 'HLS stream' ? 'Playing publisher stream · delay and capture time unverified.' : 'Playing a short publisher clip · this is not continuous live video.'; });
    video.addEventListener('waiting', () => { stamp.textContent = 'Buffering publisher video…'; });
    function retryNative() {
      if (!hls && !video.hidden && video.getAttribute('src')) retry('Publisher stream interrupted');
    }
    video.addEventListener('error', retryNative);
    video.addEventListener('ended', retryNative);
    stage.prepend(play); stage.append(video, link('Open video at publisher', details.live_page || details.video_url));
    if (kind === 'HLS stream') stage.append(button('Go live', () => {
      if (!active()) return;
      if (video.seekable && video.seekable.length) {
        video.currentTime = Math.max(0, video.seekable.end(video.seekable.length - 1) - 1);
        video.play().catch(() => { stamp.textContent = 'Press Play to resume at the live edge.'; });
      } else { clearTimeout(videoTimer); videoTimer = null; connect(); }
    }));
  }
  stage.append(button('Stop media', () => { stopMedia(); frame.hidden = true; stamp.textContent = 'Media stopped. Select a media action to reconnect.'; }), stamp);
  panel.append(stage);
  if (details.page_url) panel.append(link('Open publisher camera player', details.page_url));
}
async function inspect(id) {
  showDetails();
  const version = ++detailsVersion; stopMedia(); selected = id;
  const task = detailsTask('Reading the record’s evidence');
  const panel = $('details'); panel.replaceChildren(node('h1', 'Loading evidence…'), cancelButton(task));
  try {
    const value = await json('event?id=' + encodeURIComponent(id), task.signal); if (version !== detailsVersion) return;
    const row = value.event;
    draw();
    panel.replaceChildren(node('div', row.kind === 'public_camera' ? 'PUBLIC CAMERA' : 'SOURCE OBSERVATION', 'eyebrow'), node('h1', row.title));
    if (row.kind === 'public_camera') cameraControls(panel, row, version);
    panel.append(node('h2', 'Source evidence'));
    for (const [label, text] of [['Layer', row.feed], ['Event time', row.observed_at || 'Not supplied'],
      ['Last catalog/event fetch', row.retrieved_at || 'Unknown'], ['Source freshness', row.stale ? 'Stale' : 'Current fetch'],
      ['Record status', value.in_current_feed ? 'Present in current feed' : 'Retained historical record']]) panel.append(node('div', `${label}: ${text}`, 'fact'));
    if (value.assessment) {
      const assessment = node('section', undefined, 'evidence-summary');
      assessment.append(node('h2', 'What this record establishes'), node('p', value.assessment.summary));
      for (const [key, label] of [['risk', 'Source severity'], ['confidence', 'Source confidence']]) {
        const item = value.assessment[key];
        if (item) assessment.append(node('p', `${label}: ${item.label}. ${item.basis}`, 'note'));
      }
      assessment.append(node('p', value.assessment.confidence?.scope || 'Source reporting is not independent verification.', 'note'));
      panel.append(assessment);
    }
    if (row.source_sha256) panel.append(node('div', 'Recorded source SHA-256: ' + row.source_sha256, 'fact'));
    if (Number.isFinite(row.latitude) && Number.isFinite(row.longitude)) {
      panel.append(button('Focus here', () => map.setView([row.latitude, longitudeNear(row.longitude, map.getCenter().lng)], row.kind === 'public_camera' ? 14 : 7)),
        button('Everything around here', () => around(L.latLng(row.latitude, row.longitude))),
        button('Deep look here', () => deepLook(L.latLng(row.latitude, row.longitude))));
    }
    panel.append(link('Provider source', row.url));
    if (row.kind !== 'public_camera') for (const [key, text] of Object.entries(row.details)) {
      if (text !== null && text !== '' && typeof text !== 'object') panel.append(node('div', `${key.replaceAll('_', ' ')}: ${text}`, 'fact'));
    }
    panel.append(node('h2', 'Retained revisions'));
    for (const revision of value.retained_revisions) panel.append(node('p', `${revision.captured_at} · ${revision.event.title}`, 'note'));
    panel.append(node('h2', 'Nearby observations'));
    for (const item of value.nearby_events.slice(0, 8)) panel.append(button(`${item.distance_km} km · ${item.title}`, () => inspect(item.id)));
    panel.append(node('p', value.basis, 'note')); if (row.details.limitation) panel.append(node('p', row.details.limitation, 'note'));
    return row;
  } catch (error) { if (version === detailsVersion) panel.replaceChildren(...failureNodes(task, error, 'Evidence unavailable', () => inspect(id))); }
  finally { task.done(); }
}
async function resources() {
  showDetails();
  const version = ++detailsVersion; stopMedia(); selected = null;
  const task = detailsTask('Reading resources');
  const panel = $('details'); panel.replaceChildren(node('h1', 'Resources and coverage'));
  try {
    const value = await json('resources', task.signal); if (version !== detailsVersion) return;
    panel.append(node('p', value.basis, 'note'));
    for (const resource of value.resources) {
      const box = node('div', undefined, 'resource'); box.append(link(resource.title, resource.url),
        node('small', `${resource.category} · ${resource.region} · ${resource.integration}`), node('p', resource.notes, 'note')); panel.append(box);
    }
  } catch (error) { if (version === detailsVersion) panel.replaceChildren(...failureNodes(task, error, 'Resources unavailable', resources)); }
  finally { task.done(); }
}
function eventReports(panel, items) {
  for (const item of items) {
    const box = node('div', undefined, 'resource');
    box.append(node('h2', item.title), node('div', item.status, 'eyebrow'),
      node('p', item.status_reason), node('p', `Location: ${item.location_precision}`, 'note'),
      node('p', `Last report: ${item.last_reported || 'Unknown'}`, 'note'),
      node('p', item.confidence_basis, 'note'));
    if (Number.isFinite(item.latitude) && Number.isFinite(item.longitude)) {
      box.append(button('Focus here', () => map.setView([item.latitude, item.longitude], 7)));
    }
    box.append(button('Compare nearby evidence', () => intelligenceEvidence('correlate?id=' + encodeURIComponent(item.id))));
    for (const source of item.sources.slice(0, 6)) box.append(link(source.outlet + (source.disputes ? ' · disputes report' : '') + (source.stale ? ' · stale source' : ''), source.url));
    panel.append(box);
  }
}
async function intelligenceEvidence(path) {
  showDetails();
  const version = ++detailsVersion; stopMedia(); selected = null;
  const task = detailsTask(path.startsWith('search?') ? 'Searching the evidence' : path.startsWith('correlate?') ? 'Comparing nearby evidence' : 'Reading event reports');
  const panel = $('details'); panel.replaceChildren(node('h1', 'Reading intelligence…'), cancelButton(task)); busy(+1);
  try {
    const value = await json(path, task.signal); if (version !== detailsVersion) return;
    panel.replaceChildren(node('h1', value.correlation ? 'Nearby evidence' : value.search ? 'Intelligence search' : 'Event reports'));
    if (value.correlation) {
      eventReports(panel, [value.object]); panel.append(node('p', value.caution, 'note'), node('p', value.alternatives));
      for (const phase of ['before', 'after']) {
        const aircraft = value.aircraft[phase] || [];
        panel.append(node('h2', `Aircraft ${phase} (${aircraft.length})`));
        for (const craft of aircraft) panel.append(node('p', `${craft.label || craft.ident} · ${craft.at} · ${craft.distance_km} km`, 'fact'));
      }
      panel.append(node('p', value.aircraft.basis, 'note'));
      for (const camera of value.cameras) panel.append(button(`${camera.distance_km} km · ${camera.title}`, () => inspect(camera.id)));
      for (const row of value.nearby || []) panel.append(button(`${row.distance_km} km · ${row.title}`, () => inspect(row.id)));
    } else if (value.search) {
      const found = value.sections; eventReports(panel, found.events || []);
      for (const row of found.records || []) panel.append(button(row.title, () => inspect(row.id)));
      for (const row of found.cameras || []) panel.append(button(row.title, () => inspect(row.id)));
      for (const craft of found.aircraft || found.aircraft_now || []) panel.append(node('p', `${craft.flight} · ${craft.kind}`, 'fact'));
      if (!Object.values(found).some(part => Array.isArray(part) && part.length)) panel.append(node('p', 'No matching observations. Missing data does not establish absence.'));
      for (const [key, error] of Object.entries(found)) if (key.endsWith('_error')) panel.append(node('p', error, 'note'));
    } else {
      eventReports(panel, value.objects);
      panel.append(node('p', `${value.total} merged reports in the last 72 hours; showing ${value.objects.length}.`, 'note'));
    }
    if (value.basis) panel.append(node('p', value.basis, 'note'));
  } catch (error) {
    if (version === detailsVersion) panel.replaceChildren(...failureNodes(task, error, 'Intelligence unavailable', () => intelligenceEvidence(path)));
  } finally { busy(-1); task.done(); }
}
$('reports-button').addEventListener('click', () => intelligenceEvidence('objects'));
$('search-button').addEventListener('click', () => {
  const query = $('query').value.trim();
  if (!query) { message('Enter a place, callsign, airport, event type or keyword in Search.'); $('query').focus(); return; }
  intelligenceEvidence('search?query=' + encodeURIComponent(query));
});
async function sourceHealth() {
  showDetails();
  const version = ++detailsVersion; stopMedia(); selected = null; const panel = $('details');
  const task = detailsTask('Reading source health');
  panel.replaceChildren(node('div', 'CONNECTION STATUS', 'eyebrow'), node('h1', 'Source health'),
    node('p', 'Reading local fetch history…', 'note'), cancelButton(task));
  busy(+1);
  try {
    const value = await json('health', task.signal); if (version !== detailsVersion) return;
    const all = value.sources || [], current = all.filter(source => source.status === 'ok' && !source.stale).length;
    panel.replaceChildren(node('div', 'COVERAGE & EVIDENCE', 'eyebrow'), node('h1', 'Source health'),
      node('p', `${current} current · ${all.length - current} need attention · ${all.length} connected feed definitions`, 'lead'),
      node('p', value.basis, 'note'), node('p', 'Status read: ' + value.checked_at, 'note'),
      button('Reload local status', sourceHealth));
    const filters = node('div', undefined, 'health-filters'), search = node('input'), state = node('select');
    search.id = 'source-search'; search.type = 'search'; search.placeholder = 'Find a source…'; search.setAttribute('aria-label', 'Find a source');
    state.id = 'source-state'; state.setAttribute('aria-label', 'Source state');
    state.append(new Option('All source states', ''), new Option('Needs attention', 'attention'), new Option('Current fetch', 'current'));
    filters.append(search, state); const list = node('div'); list.id = 'source-health-list'; panel.append(filters, list);
    function renderHealth() {
      list.replaceChildren();
      const query = search.value.trim().toLocaleLowerCase();
      const shown = all.filter(source => (!query || `${source.title} ${source.key}`.toLocaleLowerCase().includes(query))
        && (!state.value || (source.status === 'ok' && !source.stale) === (state.value === 'current')));
      for (const source of shown) {
        const box = node('article', undefined, 'resource'); box.dataset.sourceKey = source.key;
        box.append(node('h2', source.title || source.key), node('small', `${source.status} · ${source.stale ? 'stale or missing' : 'current fetch'}`),
          node('p', 'Last successful fetch: ' + (source.last_success_at || 'Never recorded'), 'note'));
        if (source.error) box.append(node('p', source.error, 'note'));
        if (Number.isFinite(source.age_seconds)) box.append(node('p', `Fetch age: ${Math.floor(source.age_seconds / 60)} min · expected interval ${source.interval_seconds} s`, 'note'));
        if (source.sha256) box.append(node('p', 'Recorded SHA-256: ' + source.sha256, 'note fingerprint'));
        if (source.documentation || source.url) box.append(link('Publisher / documentation', source.documentation || source.url));
        box.append(button('Show retained observations', () => {
          if (![...$('feed').options].some(option => option.value === source.key)) $('feed').add(new Option(source.title || source.key, source.key));
          $('feed').value = source.key; ++detailsVersion; showDetails(false); load();
        }));
        list.append(box);
      }
      if (!shown.length) list.append(node('p', 'No sources match these filters. This is not evidence that the region has no events.', 'note'));
    }
    search.addEventListener('input', renderHealth); state.addEventListener('change', renderHealth); renderHealth();
  } catch (error) {
    if (version === detailsVersion) panel.replaceChildren(...(wasCancelled(task.controller) ? failureNodes(task, error, '', sourceHealth)
      : [node('h1', 'Source health unavailable'), node('p', error.message), button('Try local status again', sourceHealth)]));
  } finally { busy(-1); task.done(); }
}
function setMotion(paused) {
  document.body.classList.toggle('motion-paused', paused); $('motion').setAttribute('aria-pressed', String(paused));
  $('motion').textContent = paused ? 'Enable motion' : 'Pause motion';
  map.options.zoomAnimation = !paused; map.options.fadeAnimation = !paused;
}
async function start() {
  try {
    config = await json('config');
    $('feed').replaceChildren(new Option('All layers', '')); $('region').replaceChildren();
    for (const feed of config.feeds) { const option = node('option', feed.title); option.value = feed.key; $('feed').append(option); }
    for (const region of Object.keys(config.regions)) { const option = node('option', region); option.value = region; $('region').append(option); }
    try {
      const land = await json('land');
      landLayer = L.polygon(land.polygons.map(polygon => [polygon.map(point => [Math.max(-85, Math.min(85, point[1])), point[0]])]),
        {color: '#66848c', fillColor: '#203640', fillOpacity: 1, weight: .7, interactive: false, pane: 'atlas-land',
          attribution: '<a href="https://www.naturalearthdata.com/">Natural Earth</a>'}).addTo(map);
    } catch { message('Overview geometry unavailable. Observations and street detail remain available.'); }
    addCartography(); baseLayers();
    // A view code in the address (a reload, a pasted link) is restored before anything else is drawn.
    const incoming = viewCodeFromHash(location.hash);
    if (incoming) await openViewCode(incoming, 'startup');
    else { await load(); await focusHash(); }
    refreshSavedViews(); viewLinkReady();
  } catch (error) {
    const detail = 'Atlas startup issue: ' + error.message;
    message(detail); $('load-error-text').textContent = detail; $('load-error').hidden = false;
    $('loaded-count').textContent = 'Atlas not loaded';
  }
}
let timer;
// The whole world is loaded once; panning only re-clusters what is on screen.
map.on('moveend', () => { clearTimeout(timer); timer = setTimeout(draw, 120); });
$('filters').addEventListener('submit', event => { event.preventDefault(); load(); });
$('media').addEventListener('change', load);
$('browse-list').addEventListener('keydown', moveInList);
$('skip-map').addEventListener('click', event => { event.preventDefault(); $('map').focus(); });
$('skip-details').addEventListener('click', event => {
  event.preventDefault(); showDetails(true); $('details').tabIndex = -1; $('details').focus();
});
$('region').addEventListener('change', () => { if (!config) return; const [w, s, e, n] = config.regions[$('region').value]; $('view-name').textContent = $('region').value + ' view'; map.fitBounds([[s, w], [n, e < w ? e + 360 : e]]); });
/* ---------------------------------------------------------------- live layers
   Radar and satellite are public tiles this browser loads directly. Aircraft, ships
   and transit come from HAYAT's own rate-limited providers around the map centre, and
   glide between refreshes along their reported heading and speed (dead reckoning). */
const yesterday = new Date(Date.now() - 864e5).toISOString().slice(0, 10);
const RADAR_URL = 'https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png';
const overlays = {
  radar: L.tileLayer(RADAR_URL, {opacity: .72, maxZoom: 12, zIndex: 5, className: 'radar-tiles',
    attribution: 'NEXRAD radar · Iowa Environmental Mesonet'}),
  satellite: L.tileLayer('https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/'
    + yesterday + '/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg', {maxNativeZoom: 9, maxZoom: 14, zIndex: 2,
    attribution: 'NASA GIBS · VIIRS true colour ' + yesterday}),
};
let radarTimer = null;
function baseLayers() {
  $('street-toggle').setAttribute('aria-pressed', String($('streets').checked));
  if ($('streets').checked) street.addTo(map); else map.removeLayer(street);
  if ($('satellite').checked) overlays.satellite.addTo(map); else map.removeLayer(overlays.satellite);
  if (landLayer) landLayer.addTo(map);
  clearInterval(radarTimer); radarTimer = null;
  if ($('radar').checked) {
    overlays.radar.addTo(map);
    // The radar mosaic is renewed every five minutes; so is this layer.
    radarTimer = setInterval(() => overlays.radar.setUrl(RADAR_URL + '?t=' + Math.floor(Date.now() / 300000)), 300000);
  } else map.removeLayer(overlays.radar);
  draw();
  for (const id of ['streets', 'satellite', 'radar']) setLayerStatus(id, $(id).checked ? 'enabled' : 'off',
    $(id).checked ? id === 'satellite' ? 'Daily imagery · date shown in map credit' : id === 'radar' ? 'US radar tiles · refreshes every 5 minutes' : 'Online street tiles' : 'Off');
}
for (const id of ['streets', 'satellite', 'radar']) $(id).addEventListener('change', baseLayers);

const LIVE = {
  aircraft: {every: 15000, minZoom: 6, label: 'aircraft', layer: L.layerGroup(), items: [], markers: new Map(), at: 0, timer: null},
  vessels: {every: 60000, minZoom: 5, label: 'ships', layer: L.layerGroup(), items: [], markers: new Map(), at: 0, timer: null},
  transit: {every: 30000, minZoom: 9, label: 'transit vehicles', layer: L.layerGroup(), items: [], markers: new Map(), at: 0, timer: null},
};
const GLYPHS = {
  aircraft: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2l2 7 7 4v2l-7-2-1 6 3 2v1.5L12 21l-4 1.5V21l3-2-1-6-7 2v-2l7-4z"/></svg>',
  vessels: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2l5 9v9l-5 2-5-2v-9z"/></svg>',
  transit: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="4" width="14" height="14" rx="4"/><circle cx="9" cy="20" r="1.6"/><circle cx="15" cy="20" r="1.6"/></svg>',
};
function liveIcon(kind, item) {
  const heading = kind === 'aircraft' ? item.track : kind === 'vessels' ? item.course : null;
  const alert = kind === 'aircraft' && item.emergency ? ' emergency' : '';
  return L.divIcon({className: `live-icon live-${kind}${alert}${item.on_ground ? ' grounded' : ''}`, iconSize: [22, 22],
    html: `<div style="transform:rotate(${Number.isFinite(heading) ? heading : 0}deg)">${GLYPHS[kind]}</div>`});
}
function liveTitle(kind, item) {
  if (kind === 'aircraft') return `${item.flight || item.id}${item.type ? ' · ' + item.type : ''} · ${item.on_ground ? 'on the ground'
    : Math.round(item.altitude_ft).toLocaleString() + ' ft'}${item.speed_kt ? ' · ' + Math.round(item.speed_kt) + ' kt' : ''}${item.emergency ? ' · EMERGENCY' : ''}`;
  if (kind === 'vessels') return `Ship MMSI ${item.mmsi}${item.speed_kn !== null && item.speed_kn !== undefined ? ' · ' + item.speed_kn + ' kn' : ''}`;
  return `${item.operator}${item.route ? ' route ' + item.route : ''}${item.label ? ' · ' + item.label : ''}`
    + `${Number.isFinite(item.speed_kmh) ? ' · ' + Math.round(item.speed_kmh) + ' km/h' : ''}`
    + `${item.stale ? ' · report ' + Math.round(item.age_seconds / 60) + ' min old' : ''}`;
}
async function refreshLive(kind) {
  const state = LIVE[kind]; clearTimeout(state.timer); state.timer = null;
  if (state.request) state.request.abort(); state.request = null;
  if (!$(kind).checked) {
    state.layer.clearLayers(); state.markers.clear(); state.items = []; state.at = 0;
    map.removeLayer(state.layer); setLayerStatus(kind, 'off', 'Off'); return;
  }
  state.layer.addTo(map);
  state.timer = setTimeout(() => refreshLive(kind), state.every);
  if (document.hidden) { setLayerStatus(kind, 'paused', 'Paused while this tab is hidden'); return; }
  if (map.getZoom() < state.minZoom) {
    state.layer.clearLayers(); state.markers.clear();
    setLayerStatus(kind, 'zoom', `Zoom to level ${state.minZoom} or closer`);
    message(`Zoom in further to see live ${state.label} (they are read around the map centre).`); return;
  }
    const centre = map.getCenter(), radius = map.distance(centre, map.getBounds().getNorthEast()) / 1000;
  const request = new AbortController(); state.request = request;
  state.lastFetch = performance.now(); busy(+1); setLayerStatus(kind, 'loading', 'Reading the public source…');
  try {
    const value = await json(`live?kind=${kind}&lat=${centre.lat.toFixed(4)}&lon=${longitudeNear(centre.lng, 0).toFixed(4)}&radius_km=${Math.round(radius)}`, request.signal);
    if (state.request !== request || !$(kind).checked) return;
    state.items = value.items; state.at = performance.now();
    state.stale = false;
    const seen = new Set();
    for (const item of value.items) {
      // Vehicle numbers repeat between operators (TTC and MiWay both run a 4631): the key carries the operator.
      const key = (item.operator ? item.operator + ':' : '') + String(item.id); seen.add(key);
      let marker = state.markers.get(key);
      if (!marker) {
        marker = L.marker([item.latitude, item.longitude], {icon: liveIcon(kind, item), keyboard: false, zIndexOffset: 1000}).addTo(state.layer);
        state.markers.set(key, marker);
      } else { marker.setLatLng([item.latitude, item.longitude]); marker.setIcon(liveIcon(kind, item)); }
      marker.item = item; marker.unbindTooltip(); marker.bindTooltip(node('span', liveTitle(kind, item)));
    }
    for (const [key, marker] of state.markers) if (!seen.has(key)) { state.layer.removeLayer(marker); state.markers.delete(key); }
    const emergencies = kind === 'aircraft' ? value.items.filter(item => item.emergency).length : 0;
    if (!value.items.length && value.outside_coverage) {
      // No source covers this centre: say so instead of reporting an empty sea.
      message(`Live ${state.label}: ${value.outside_coverage}`);
      setLayerStatus(kind, 'coverage', value.outside_coverage); return;
    }
    message(`${value.count.toLocaleString()} ${state.label} within ${Math.round(value.radius_km)} km · ${value.source}`
      + (emergencies ? ` · ${emergencies} squawking EMERGENCY` : ''));
    setLayerStatus(kind, value.items.length ? 'ready' : 'empty', `${value.items.length} in the returned area · checked ${new Date().toISOString().slice(11, 19)} UTC`);
  } catch (error) {
    if (state.request === request && error.name !== 'AbortError') {
      state.stale = true;
      setLayerStatus(kind, 'error', 'Unavailable' + (state.items.length ? '; last positions retained' : '') + ': ' + error.message);
      message(`Live ${state.label} unavailable: ${error.message}`);
    }
  }
  finally { busy(-1); if (state.request === request) state.request = null; }
}
/* Dead reckoning: move each craft along its heading at its reported speed until the next refresh. */
function destination(lat, lon, bearingDeg, km) {
  const R = 6371, d = km / R, b = bearingDeg * Math.PI / 180, p1 = lat * Math.PI / 180, l1 = lon * Math.PI / 180;
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
  const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return [p2 * 180 / Math.PI, ((l2 * 180 / Math.PI) + 540) % 360 - 180];
}
let lastGlide = 0;
function glide(now) {
  requestAnimationFrame(glide);
  if (now - lastGlide < 100 || !motionOn() || document.hidden) return;  // ten frames a second is smooth enough
  lastGlide = now;
  for (const kind of ['aircraft', 'vessels']) {
    const state = LIVE[kind]; if (!$(kind).checked || !state.at || state.stale) continue;
    const seconds = Math.min(90, (now - state.at) / 1000);
    for (const marker of state.markers.values()) {
      const item = marker.item, heading = kind === 'aircraft' ? item.track : item.course;
      const knots = kind === 'aircraft' ? item.speed_kt : item.speed_kn;
      if (!Number.isFinite(heading) || !Number.isFinite(knots) || knots <= 0 || item.on_ground) continue;
      marker.setLatLng(destination(item.latitude, item.longitude, heading, knots * 1.852 * seconds / 3600));
    }
  }
  moveISS(now);
}
requestAnimationFrame(glide);
let liveMoveTimer = null;
map.on('moveend', () => {
  // A new view reads its own surroundings once the map settles, and never faster than the providers allow.
  clearTimeout(liveMoveTimer);
  liveMoveTimer = setTimeout(() => {
    for (const kind of Object.keys(LIVE)) {
      if (!$(kind).checked) continue;
      const state = LIVE[kind]; clearTimeout(state.timer);
      state.timer = setTimeout(() => refreshLive(kind), Math.max(0, (state.lastFetch || 0) + 5000 - performance.now()));
    }
  }, 700);
});
for (const kind of Object.keys(LIVE)) $(kind).addEventListener('change', () => refreshLive(kind));

/* The International Space Station: where it is, and its next orbit, from published elements. */
const issLayer = L.layerGroup();
let issMarker = null, issFrom = null, issTo = null, issAt = 0, issTimer = null, issRequest = null;
function issPath(track) {
  const segments = [[]];
  for (let i = 0; i < track.length; i++) {
    if (i && Math.abs(track[i][1] - track[i - 1][1]) > 180) segments.push([]);
    segments[segments.length - 1].push(track[i]);
  }
  return segments;
}
async function refreshISS() {
  clearTimeout(issTimer); issTimer = null;
  if (issRequest) issRequest.abort(); issRequest = null;
  if (!$('iss').checked) { issLayer.clearLayers(); issMarker = null; map.removeLayer(issLayer); setLayerStatus('iss', 'off', 'Off'); return; }
  issLayer.addTo(map); issTimer = setTimeout(refreshISS, 10000);
  if (document.hidden) { setLayerStatus('iss', 'paused', 'Paused while this tab is hidden'); return; }
  const request = new AbortController(); issRequest = request; setLayerStatus('iss', 'loading', 'Computing the orbit…');
  try {
    const value = await json('iss', request.signal);
    if (issRequest !== request || !$('iss').checked) return;
    const firstLook = !issMarker;
    issLayer.clearLayers();
    L.polyline(issPath(value.track), {color: '#c9a2ff', weight: 1.6, opacity: .75, dashArray: '6 6', className: 'iss-orbit', interactive: false}).addTo(issLayer);
    issFrom = issMarker ? issMarker.getLatLng() : L.latLng(value.latitude, value.longitude);
    issTo = L.latLng(value.latitude, value.longitude); issAt = performance.now();
    issMarker = L.marker(issFrom, {icon: L.divIcon({className: 'live-icon live-iss', iconSize: [30, 30],
      html: '<div><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="6" height="6" rx="1"/><rect x="1" y="10" width="7" height="4"/><rect x="16" y="10" width="7" height="4"/></svg></div>'}), keyboard: false, zIndexOffset: 2000})
      .addTo(issLayer).bindTooltip(node('span', `International Space Station · ${value.altitude_km} km up · ${value.source}`));
    if (firstLook) message(`ISS over ${value.latitude.toFixed(1)}°, ${value.longitude.toFixed(1)}° at ${value.altitude_km} km · dashed line: the next orbit`);
    setLayerStatus('iss', 'ready', 'Computed orbit · ' + new Date().toISOString().slice(11, 19) + ' UTC');
  } catch (error) { if (issRequest === request && error.name !== 'AbortError') { setLayerStatus('iss', 'error', 'Unavailable: ' + error.message); message('ISS position unavailable: ' + error.message); } }
  finally { if (issRequest === request) issRequest = null; }
}
function moveISS(now) {
  if (!issMarker || !issFrom || !issTo) return;
  const t = Math.min(1, (now - issAt) / 10000);
  if (Math.abs(issTo.lng - issFrom.lng) > 180) { issMarker.setLatLng(issTo); return; }  // crossing the date line
  issMarker.setLatLng([issFrom.lat + (issTo.lat - issFrom.lat) * t, issFrom.lng + (issTo.lng - issFrom.lng) * t]);
}
$('iss').addEventListener('change', refreshISS);

/* Right-click (or "Everything around here"): every source around one place, combined by HAYAT. */
async function around(latlng) {
  showDetails();
  latlng = L.latLng(latlng.lat, longitudeNear(latlng.lng, 0));
  const version = ++detailsVersion; stopMedia(); selected = null;
  const panel = $('details');
  const radius = Math.max(2, Math.min(100, Math.round(map.distance(map.getCenter(), map.getBounds().getNorthEast()) / 4000)));
  const ring = L.circle(latlng, {radius: radius * 1000, color: '#e7c276', weight: 1.5, fillOpacity: .05, className: 'scan-ring', interactive: false}).addTo(map);
  const task = detailsTask('Reading everything around here');
  panel.replaceChildren(node('div', 'EVERYTHING AROUND HERE', 'eyebrow'), node('h1', `${latlng.lat.toFixed(3)}, ${latlng.lng.toFixed(3)}`),
    node('div', undefined, 'scanner'), node('p', `Reading cameras, events, weather, stations, air, rivers, aircraft, ships, transit and the ISS within ${radius} km…`, 'note'),
    cancelButton(task));
  busy(+1);
  try {
    const value = await json(`around?lat=${latlng.lat.toFixed(5)}&lon=${latlng.lng.toFixed(5)}&radius_km=${radius}`, task.signal);
    if (version !== detailsVersion) return;
    panel.replaceChildren(node('div', 'EVERYTHING AROUND HERE', 'eyebrow'), node('h1', value.place.name));
    const [first, ...rest] = value.interpretation.split('\n');
    panel.append(node('p', first, 'lead'));
    for (const line of rest) panel.append(node('div', line.trim(), line.startsWith('  ') ? 'fact indent' : 'fact'));
    if ((value.cameras || []).length) {
      panel.append(node('h2', 'Best camera views'));
      for (const camera of value.cameras.slice(0, 6)) panel.append(button(`${camera.distance_km} km ${camera.direction} · ${camera.title}`, () => inspect(camera.id)));
    }
    if ((value.events || []).length) {
      panel.append(node('h2', 'Events nearby'));
      for (const item of value.events.slice(0, 8)) panel.append(button(`${item.distance_km} km ${item.direction} · ${item.title}`, () => inspect(item.id)));
    }
    if ((value.changes || []).length) {
      panel.append(node('h2', 'What changed'));
      for (const change of value.changes) panel.append(node('div', `${change.label}: now ${change.now}${change.unit}`
        + (change.an_hour_ago !== undefined ? ` · an hour ago ${change.an_hour_ago}${change.unit}` : '')
        + (change.yesterday !== undefined ? ` · yesterday ${change.yesterday}${change.unit}` : '')
        + (change.unusual ? ' · UNUSUAL' : ''), 'fact'));
    }
    panel.append(node('p', value.basis, 'note'));
    message(`Combined ${Object.keys(value.metrics).length} measurements around ${value.place.name}.`);
  } catch (error) { if (version === detailsVersion) panel.replaceChildren(...failureNodes(task, error, 'Nothing could be gathered', () => around(latlng))); }
  finally { busy(-1); task.done(); setTimeout(() => map.removeLayer(ring), motionOn() ? 2500 : 0); }
}
map.on('contextmenu', event => around(event.latlng));
for (const id of ['cameras', 'events', 'fresh-only', 'hide-failed', 'category', 'severity', 'sort']) $(id).addEventListener('change', () => { browseLimit = 40; browseSignature = ''; draw(); });
$('feed').addEventListener('change', load);
const DESTINATIONS = {world: {name: 'World', query: '', bounds: [[-57, -170], [76, 170]]},
  india: {name: 'India', query: 'India', bounds: [[6, 67], [36, 98]]},
  makkah: {name: 'Makkah', query: 'Makkah', centre: [21.4225, 39.8262], zoom: 13}};
function destinationView(key) {
  cancelPlaceSearch();
  const place = DESTINATIONS[key];
  $('query').value = place.query; $('feed').value = ''; $('media').value = '';
  $('fresh-only').checked = false; $('cameras').checked = true; $('events').checked = true;
  $('hide-failed').checked = false;
  $('category').value = ''; $('severity').value = ''; $('sort').value = 'priority'; browseLimit = 40;
  $('region').value = 'World'; $('view-name').textContent = place.name + ' view';
  document.querySelectorAll('[data-destination]').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.destination === key)));
  if (place.bounds) map.fitBounds(place.bounds, {padding: [30, 30]}); else map.setView(place.centre, place.zoom);
  return load();
}
document.querySelectorAll('[data-destination]').forEach(el => el.addEventListener('click', () => destinationView(el.dataset.destination)));
$('reset-filters').addEventListener('click', () => destinationView('world'));
$('fit-observations').addEventListener('click', () => {
  const enabled = rows.filter(rowEnabled);
  if (!enabled.length) { message('No matching located observations. Reset the filters to explore more.'); return; }
  // Remove the largest gap on the longitude circle so dateline results fit together.
  const lons = enabled.map(row => ((row.longitude % 360) + 360) % 360).sort((a, b) => a - b);
  let gap = -1, west = lons[0];
  lons.forEach((lon, i) => { const next = i + 1 < lons.length ? lons[i + 1] : lons[0] + 360;
    if (next - lon > gap) { gap = next - lon; west = next % 360; } });
  map.fitBounds(enabled.map(row => [row.latitude, west + (((row.longitude - west) % 360) + 360) % 360]), {padding: [45, 45], maxZoom: 13});
  $('view-name').textContent = 'Matching observations';
});
$('around-centre').addEventListener('click', () => around(map.getCenter()));
$('street-toggle').addEventListener('click', () => { $('streets').checked = !$('streets').checked; baseLayers(); });
$('toggle-details').addEventListener('click', () => { if ($('details-panel').hidden) showDetails(true); else closeDetailsPanel({focus: false}); });
$('close-details').addEventListener('click', () => closeDetailsPanel());
$('about-map').addEventListener('click', () => $('map-information').showModal());
$('close-about').addEventListener('click', () => $('map-information').close());
document.addEventListener('keydown', event => {
  if (event.key === '/' && !event.ctrlKey && !event.altKey && !event.metaKey && !event.target.closest('input,textarea,select,[contenteditable=true]')) {
    event.preventDefault(); $('query').focus();
  }
});
$('refresh').addEventListener('click', load); $('resources-button').addEventListener('click', resources);
$('sources-button').addEventListener('click', sourceHealth);
$('motion').addEventListener('click', () => setMotion(!document.body.classList.contains('motion-paused')));
motionPreference.addEventListener('change', event => setMotion(event.matches)); setMotion(motionPreference.matches);
window.addEventListener('pagehide', stopMedia); window.addEventListener('hashchange', focusHash);
/* Satellite fire and heat detections (NASA FIRMS, last 24 h) in the current view, sized by radiative power. */
const fireLayer = L.layerGroup();
let fireTimer = null, fireRequest = null;
async function refreshFires() {
  clearTimeout(fireTimer); fireTimer = null;
  if (fireRequest) fireRequest.abort(); fireRequest = null;
  if (!$('fires').checked) { fireLayer.clearLayers(); map.removeLayer(fireLayer); setLayerStatus('fires', 'off', 'Off'); return; }
  fireLayer.addTo(map); fireTimer = setTimeout(refreshFires, 3600000);
  const bounds = map.getBounds(), clamp = value => Math.max(-180, Math.min(180, value));
  const query = `s=${Math.max(-90, bounds.getSouth()).toFixed(3)}&w=${clamp(bounds.getWest()).toFixed(3)}`
    + `&n=${Math.min(90, bounds.getNorth()).toFixed(3)}&e=${clamp(bounds.getEast()).toFixed(3)}`;
  const request = new AbortController(); fireRequest = request; busy(+1); setLayerStatus('fires', 'loading', 'Reading heat detections…');
  try {
    const value = await json('fires?' + query, request.signal);
    if (fireRequest !== request || !$('fires').checked) return;
    fireLayer.clearLayers();
    for (const item of value.items) {
      const radius = Math.max(3, Math.min(14, 3 + Math.sqrt(item.power_mw)));
      L.circleMarker([item.latitude, item.longitude], {radius, color: '#ff7a1a', weight: 1, fillColor: '#ffb020', fillOpacity: .75,
        className: 'fire-dot'}).bindTooltip(node('span', `Heat detection · ${Math.round(item.power_mw)} MW · ${item.detected} UTC`
        + ` · confidence ${({h: 'high', n: 'nominal', l: 'low'})[item.confidence] || item.confidence}`)).addTo(fireLayer);
    }
    message(`${value.count.toLocaleString()} fire/heat detections in view · ${value.source}. Not every hotspot is a wildfire.`);
    setLayerStatus('fires', value.items.length ? 'ready' : 'empty', `${value.items.length} heat detections · last 24 hours`);
  } catch (error) { if (fireRequest === request && error.name !== 'AbortError') { setLayerStatus('fires', 'error', 'Unavailable: ' + error.message); message(`Fire hotspots unavailable: ${error.message}`); } }
  finally { busy(-1); if (fireRequest === request) fireRequest = null; }
}
$('fires').addEventListener('change', refreshFires);
/* Navigation integrity reported by aircraft (gnss.py): 0.5° cells in gpsjam.org's colours. Low accuracy has
   several causes; jamming or spoofing is one possibility, so the wording never says more than that. */
const gnssLayer = L.layerGroup();
const GNSS_COLOURS = {low: '#3fb950', medium: '#e3b341', high: '#f85149'};
let gnssTimer = null, gnssRequest = null;
async function refreshGnss() {
  clearTimeout(gnssTimer); gnssTimer = null;
  if (gnssRequest) gnssRequest.abort(); gnssRequest = null;
  if (!$('gnss').checked) { gnssLayer.clearLayers(); map.removeLayer(gnssLayer); setLayerStatus('gnss', 'off', 'Off'); return; }
  gnssLayer.addTo(map); gnssTimer = setTimeout(refreshGnss, 120000);
  if (map.getZoom() < 4) { gnssLayer.clearLayers(); setLayerStatus('gnss', 'zoom', 'Zoom to level 4 or closer'); message('Zoom in to a region to read GPS interference (aircraft within about 460 km).'); return; }
  const centre = map.getCenter();
  const request = new AbortController(); gnssRequest = request; busy(+1); setLayerStatus('gnss', 'loading', 'Reading aircraft navigation reports…');
  try {
    const value = await json(`gnss?lat=${centre.lat.toFixed(3)}&lon=${longitudeNear(centre.lng, 0).toFixed(3)}`, request.signal);
    if (gnssRequest !== request || !$('gnss').checked) return;
    gnssLayer.clearLayers();
    for (const cell of value.cells) {
      L.rectangle([[cell.south, cell.west], [cell.north, cell.east]], {color: GNSS_COLOURS[cell.level], weight: 1,
        fillColor: GNSS_COLOURS[cell.level], fillOpacity: cell.level === 'low' ? .12 : .38, className: 'gnss-cell'})
        .bindTooltip(node('span', `${cell.degraded} of ${cell.aircraft} aircraft report degraded navigation · ${cell.level}`
          + ` (${cell.percent}%)`)).addTo(gnssLayer);
    }
    const flagged = value.cells.filter(cell => cell.level !== 'low').length;
    message(`${value.cells.length} cells, ${flagged} with low navigation accuracy · ${value.area.degraded} of ${value.area.aircraft} aircraft`
      + ` degraded in the area${value.not_refreshed ? ' (not refreshed: ' + value.not_refreshed + ')' : ''} · ${value.source}. ${value.caveat}`);
    setLayerStatus('gnss', value.cells.length ? 'ready' : 'empty', `${value.cells.length} report cells · low accuracy has several possible causes`);
  } catch (error) { if (gnssRequest === request && error.name !== 'AbortError') { setLayerStatus('gnss', 'error', 'Unavailable: ' + error.message); message(`GPS interference unavailable: ${error.message}`); } }
  finally { busy(-1); if (gnssRequest === request) gnssRequest = null; }
}
$('gnss').addEventListener('change', refreshGnss);
/* ---------------------------------------------------------------- world trade
   Shipping chokepoints, ringed by the events HAYAT's feeds report near each (red: a high-severity
   report, gold: others, green: none), with what passes there and the funds it links to. */
const tradeLayer = L.layerGroup();
let tradeTimer = null, tradeRequest = null;
const TRADE_COLOURS = {alert: '#f05d5e', watch: '#e8c46a', calm: '#4ade80'};
function signedPct(value) { return value === null || value === undefined ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`; }
function tradePanel(row) {
  const panel = node('div', '', 'trade-popup');
  panel.append(node('strong', row.name), node('p', row.passes, 'note'));
  panel.append(node('p', `${({alert: 'High-severity report nearby', watch: 'Reports nearby', calm: 'No reports nearby'})[row.status]}`
    + ` · ${row.event_count} in the last days`));
  if (row.events.length) {
    const list = node('ul', '');
    for (const event of row.events.slice(0, 4)) list.append(node('li', `${event.title} · ${event.distance_km} km`));
    panel.append(list);
  }
  panel.append(node('p', 'Linked: ' + row.markets.map(item => `${item.symbol} ${signedPct(item.change_today_pct)}`).join(' · '), 'note'));
  panel.append(button('Explore here', () => { map.closePopup(); map.setView([row.latitude, row.longitude], 7); }));
  return panel;
}
async function refreshTrade() {
  clearTimeout(tradeTimer); tradeTimer = null;
  if (tradeRequest) tradeRequest.abort(); tradeRequest = null;
  if (!$('trade').checked) { tradeLayer.clearLayers(); map.removeLayer(tradeLayer); setLayerStatus('trade', 'off', 'Off'); return; }
  tradeLayer.addTo(map); tradeTimer = setTimeout(refreshTrade, 300000);
  const request = new AbortController(); tradeRequest = request; busy(+1); setLayerStatus('trade', 'loading', 'Reading retained context…');
  try {
    const value = await json('trade', request.signal);
    if (tradeRequest !== request || !$('trade').checked) return;
    tradeLayer.clearLayers();
    for (const row of value.chokepoints) {
      const colour = TRADE_COLOURS[row.status];
      L.circleMarker([row.latitude, row.longitude], {radius: 9 + Math.min(9, row.event_count), color: colour, weight: 2,
        fillColor: colour, fillOpacity: .28, className: 'trade-ring trade-' + row.status})
        .bindTooltip(node('span', `${row.name} · ${row.event_count} report${row.event_count === 1 ? '' : 's'} nearby`))
        .bindPopup(() => tradePanel(row), {maxWidth: 320}).addTo(tradeLayer);
    }
    const busyLanes = value.chokepoints.filter(row => row.status !== 'calm').length;
    message(`${value.chokepoints.length} shipping chokepoints · ${busyLanes} with reports nearby. ${value.basis}`);
    setLayerStatus('trade', 'ready', `${value.chokepoints.length} mapped chokepoints · retained event context`);
  } catch (error) { if (tradeRequest === request && error.name !== 'AbortError') { setLayerStatus('trade', 'error', 'Unavailable: ' + error.message); message(`World trade layer unavailable: ${error.message}`); } }
  finally { busy(-1); if (tradeRequest === request) tradeRequest = null; }
}
$('trade').addEventListener('change', refreshTrade);
map.on('moveend', () => { if ($('fires').checked) { clearTimeout(fireTimer); fireTimer = setTimeout(refreshFires, 700); } });
map.on('moveend', () => { if ($('gnss').checked) { clearTimeout(gnssTimer); gnssTimer = setTimeout(refreshGnss, 1500); } });
/* ---------------------------------------------------------------- navigation
   The search box takes the map to a typed place ("Chicago", "21.42, 39.83") and filters
   records only when the words name no place. Street detail joins in once a city is in view,
   unless the owner switched it off; Back returns to the previous view. */
const STREET_ZOOM = 6;
let streetChoice = null;
try { streetChoice = localStorage.getItem('hayat-street-detail'); } catch { streetChoice = null; }
function rememberStreets(on) {
  streetChoice = on ? 'on' : 'off';
  try { localStorage.setItem('hayat-street-detail', streetChoice); } catch { /* a private window keeps no choice */ }
}
$('streets').addEventListener('change', () => rememberStreets($('streets').checked));
$('street-toggle').addEventListener('click', () => rememberStreets($('streets').checked));
function autoStreets() {
  if (streetChoice === 'off') return;
  const wanted = map.getZoom() >= STREET_ZOOM || streetChoice === 'on';
  if (wanted !== $('streets').checked) { $('streets').checked = wanted; baseLayers(); }
}
map.on('zoomend', autoStreets);
const viewHistory = [];
const forwardHistory = [];
let restoring = false, lastView = null;
function viewKey() { const c = map.getCenter(); return {lat: c.lat, lng: c.lng, zoom: map.getZoom(), name: $('view-name').textContent}; }
map.on('movestart', () => { if (!restoring) lastView = viewKey(); });
map.on('moveend', () => {
  if (restoring) { restoring = false; return; }
  if (!lastView) return;
  const now = viewKey();
  const moved = Math.abs(now.zoom - lastView.zoom) >= 1
    || map.distance([now.lat, now.lng], [lastView.lat, lastView.lng]) > 2000 * 2 ** (12 - now.zoom);
  if (moved) { viewHistory.push(lastView); if (viewHistory.length > 30) viewHistory.shift(); forwardHistory.length = 0; }
  $('view-back').disabled = !viewHistory.length;
  if ($('view-forward')) $('view-forward').disabled = !forwardHistory.length;
  lastView = null;
});
function restoreView(view) {
  if (!view) return;
  restoring = true; $('view-name').textContent = view.name;
  lastView = null;
  map.setView([view.lat, view.lng], view.zoom, {animate: false});
  restoring = false; lastView = null;
  $('view-back').disabled = !viewHistory.length;
  $('view-forward').disabled = !forwardHistory.length;
}
function goBack() {
  if (!viewHistory.length) return;
  forwardHistory.push(viewKey()); restoreView(viewHistory.pop());
}
function goForward() {
  if (!forwardHistory.length) return;
  viewHistory.push(viewKey()); restoreView(forwardHistory.pop());
}
$('view-back').addEventListener('click', goBack);
const forwardButton = button('Forward →', goForward);
forwardButton.id = 'view-forward'; forwardButton.disabled = true; forwardButton.title = 'Next view (Alt+→)';
$('view-back').after(forwardButton);
function flyTo(place, zoom) {
  // The move is recorded for Back under the old name, so the label changes after it starts.
  if (motionOn()) map.flyTo([place.latitude, place.longitude], zoom, {duration: .9});
  else map.setView([place.latitude, place.longitude], zoom);
  $('view-name').textContent = place.name;
}
/* Words about what happened, not where: these filter records and never move the map. */
const RECORD_WORDS = /\b(earthquakes?|quakes?|wildfires?|fires?|floods?|volcano(es)?|eruptions?|storms?|hurricanes?|cyclones?|typhoons?|tornado(es)?|tsunamis?|cameras?|webcams?|cctv|streams?|cyber|outages?|road ?works?|roadworks|accidents?|crash(es)?|incidents?|closures?|alerts?|traffic|weather|aurora|space weather|vulnerabilit(y|ies)|cve-\S+)\b/i;
let placeRequest = null, placeGeneration = 0;
function cancelPlaceSearch() { ++placeGeneration; if (placeRequest) placeRequest.abort(); placeRequest = null; }
function searchFeedback(text, error = false) { $('search-feedback').textContent = text; $('search-feedback').classList.toggle('search-error', error); }
function rejectPlaceSearch(text) {
  searchFeedback(text + ' The previous map remains visible. Edit the query and try again.', true);
  $('query').focus(); $('query').select();
}
$('filters').addEventListener('submit', async event => {
  event.preventDefault(); event.stopImmediatePropagation();
  cancelPlaceSearch(); const generation = placeGeneration;
  const text = $('query').value.trim(), mode = $('search-mode').value;
  if (mode === 'place' && text.length > 120) { rejectPlaceSearch('Place searches accept up to 120 characters.'); return; }
  if (text && text.length <= 120 && mode !== 'records' && (mode === 'place' || !RECORD_WORDS.test(text))) {
    const request = new AbortController(); placeRequest = request;
    const untrack = track('Finding the place', request);
    busy(+1);
    let place = null;
    searchFeedback('Finding the place…');
    try { place = await json('place?name=' + encodeURIComponent(text), request.signal); }
    catch (error) {
      if (wasCancelled(request)) searchFeedback('Place search cancelled. The map did not move.');
      else if (generation === placeGeneration && error.name !== 'AbortError') rejectPlaceSearch('Place lookup unavailable: ' + error.message);
      return;
    }
    finally { busy(-1); untrack(); if (placeRequest === request) placeRequest = null; }
    if (generation !== placeGeneration || $('query').value.trim() !== text) return;
    if (place?.found) {
      flyTo(place, /^(?:[+\-\d.]|geo:)/i.test(text) ? 13 : 11);
      $('query').value = '';
      if (loadedQuery) await load();  // records were filtered before: show everything around the place
      else draw();
      searchFeedback(`Went to ${place.name}. Choose Observation text to filter records instead.`);
      message(`Went to ${place.name}.`);
      return;
    }
    if (place?.error || mode === 'place') { rejectPlaceSearch(place?.error || 'No matching place was found.'); return; }
  }
  searchFeedback(text ? 'Filtering retained observations by this text. Choose Places & coordinates to navigate instead.' : 'Showing observations with the current filters.');
  return load();
}, {capture: true});
$('query').addEventListener('input', cancelPlaceSearch);
$('search-mode').addEventListener('change', cancelPlaceSearch);
// "My location" (locate) and Escape are handled in atlas-ui.js.
document.addEventListener('keydown', event => {
  if (event.altKey && event.key === 'ArrowLeft' && !event.target.closest('input,textarea,select')) { event.preventDefault(); goBack(); }
  if (event.altKey && event.key === 'ArrowRight' && !event.target.closest('input,textarea,select')) { event.preventDefault(); goForward(); }
});

/* ---------------------------------------------------------------- video wall
   The live streams in view, side by side: nearest the map centre first, six at a time.
   Each tile is its own small hls.js player; everything stops when the wall closes. */
const WALL_SIZE = 6;
let wallPlayers = [], wallQueue = [], wallGeneration = 0;
function stopWall() {
  ++wallGeneration;
  for (const player of wallPlayers) { try { player.destroy(); } catch { /* already gone */ } }
  wallPlayers = [];
  for (const video of document.querySelectorAll('#wall-grid video')) { video.pause(); video.removeAttribute('src'); video.load(); }
  $('wall-grid').replaceChildren();
}
function closeWall() { stopWall(); $('wall').hidden = true; $('video-wall').setAttribute('aria-pressed', 'false'); }
function isPlaylist(url) { try { return new URL(url).pathname.toLowerCase().endsWith('.m3u8'); } catch { return false; } }
async function wallTile(row, generation) {
  const tile = node('div', undefined, 'wall-tile'), state = node('span', 'Connecting…', 'wall-state');
  const video = node('video'); video.muted = true; video.playsInline = true; video.autoplay = true;
  video.setAttribute('aria-label', row.title);
  tile.append(video, state, node('span', row.title, 'wall-title'));
  tile.title = 'Open this camera in Details';
  tile.addEventListener('click', () => inspect(row.id));
  $('wall-grid').append(tile);
  const fail = text => { tile.classList.add('failed'); state.textContent = text; };
  try {
    const value = await json('event?id=' + encodeURIComponent(row.id));
    if (generation !== wallGeneration) return;
    const details = value.event.details;
    let url = details.video_url;
    if (details.live_page) {
      const prepared = await json('camera/play?id=' + encodeURIComponent(row.id));
      if (generation !== wallGeneration) return;
      url = prepared.video_url;
    }
    if (!url) { fail('No stream right now'); return; }
    video.addEventListener('playing', () => { state.textContent = 'LIVE'; tile.classList.remove('failed'); });
    video.addEventListener('waiting', () => { state.textContent = 'Buffering…'; });
    if (window.Hls && Hls.isSupported() && (details.live_page || isPlaylist(url))) {
      const player = new Hls({maxBufferLength: 8, maxMaxBufferLength: 16, backBufferLength: 0});
      wallPlayers.push(player);
      player.loadSource(url); player.attachMedia(video);
      player.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => fail('Click to open')));
      player.on(Hls.Events.ERROR, (_event, data) => { if (data.fatal) fail('Stream stopped'); });
    } else { video.src = url; video.play().catch(() => fail('Click to open')); }
  } catch (error) { if (generation === wallGeneration) fail(String(error.message).slice(0, 60)); }
}
function fillWall() {
  stopWall();
  const generation = wallGeneration, batch = wallQueue.splice(0, WALL_SIZE);
  $('wall-next').disabled = !wallQueue.length;
  $('wall-note').textContent = batch.length
    ? `${batch.length} live streams nearest the map centre · ${wallQueue.length} more in view · muted · click a tile for details`
    : 'No live streams in this view. Zoom out or move the map, then open the wall again.';
  for (const row of batch) wallTile(row, generation);
}
$('video-wall').addEventListener('click', () => {
  if (!$('wall').hidden) { closeWall(); return; }
  stopMedia();
  const centre = map.getCenter(), bounds = map.getBounds();
  wallQueue = rows.filter(row => row.kind === 'public_camera' && row.flags.includes('s') && rowEnabled(row) && inBounds(row, bounds))
    .sort((a, b) => distanceFromCentre(a, centre) - distanceFromCentre(b, centre));
  $('wall').hidden = false; $('video-wall').setAttribute('aria-pressed', 'true');
  fillWall();
});
$('wall-next').addEventListener('click', fillWall);
$('wall-close').addEventListener('click', closeWall);
window.addEventListener('pagehide', stopWall);
/* ---------------------------------------------------------------- deep look
   The same reasoning as the desktop's Deep look: every source for one spot, as findings with
   evidence, confidence and blind spots. A finding about an event, a camera or a fire opens it. */
const LEVELS = {alert: ['ALERT', '#f05d5e'], watch: ['WATCH', '#e8b04b'], info: ['INFO', '#6cb6ff'], gap: ['BLIND SPOT', '#7d8a96']};
const SITUATIONS = {calm: ['NO ALERT IN READ SOURCES', '#57c785', .17], watch: ['WATCH', '#e8b04b', .5], alert: ['ALERT', '#f05d5e', .83], unknown: ['LIMITED COVERAGE', '#9aa4ae', null]};
function svg(tag, attributes) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) el.setAttribute(key, value);
  return el;
}
function gauge(situation) {
  const [label, colour, share] = SITUATIONS[situation] || SITUATIONS.unknown, box = node('div', undefined, 'deep-gauge');
  const drawing = svg('svg', {width: 120, height: 70, viewBox: '0 0 120 70', 'aria-hidden': 'true'});
  ['calm', 'watch', 'alert'].forEach((key, index) => {
    const a0 = Math.PI * (1 - index / 3) - .03, a1 = Math.PI * (1 - (index + 1) / 3) + .03, r = 50;
    drawing.append(svg('path', {d: `M ${60 + r * Math.cos(a0)} ${62 - r * Math.sin(a0)} A ${r} ${r} 0 0 1 ${60 + r * Math.cos(a1)} ${62 - r * Math.sin(a1)}`,
      fill: 'none', stroke: key === situation ? SITUATIONS[key][1] : '#33475a', 'stroke-width': 9}));
  });
  if (share !== null) {
    const angle = Math.PI * (1 - share);
    drawing.append(svg('line', {x1: 60, y1: 62, x2: 60 + 38 * Math.cos(angle), y2: 62 - 38 * Math.sin(angle), stroke: colour, 'stroke-width': 3, 'stroke-linecap': 'round'}));
  }
  drawing.append(svg('circle', {cx: 60, cy: 62, r: 5, fill: colour}));
  const text = node('span', label, 'deep-level'); text.style.color = colour;
  box.append(drawing, text);
  return box;
}
function findingCard(item) {
  const [name, colour] = LEVELS[item.level] || LEVELS.info, card = node('article', undefined, 'finding' + (item.level === 'gap' ? ' gap' : ''));
  card.style.setProperty('--c', colour);
  const head = node('small', name); head.append(node('span', `confidence ${item.confidence}`, 'conf'));
  card.append(head, node('h3', item.title), node('p', item.detail));
  if (item.evidence.length) {
    const fold = node('details'), list = node('ul');
    fold.append(node('summary', `Evidence (${item.evidence.length})`));
    for (const entry of item.evidence) list.append(node('li', entry));
    fold.append(list); card.append(fold);
  }
  const target = item.target || {};
  if (target.kind === 'event' || target.kind === 'camera') card.append(button(target.kind === 'camera' ? 'Open camera' : 'Open event', () => inspect(target.id)));
  else if (target.kind === 'point') card.append(button('Fly there', () => flyTo({latitude: target.latitude, longitude: target.longitude, name: item.title}, target.zoom || 9)));
  return card;
}
async function deepLook(latlng, reason = false) {
  showDetails();
  latlng = L.latLng(latlng.lat, longitudeNear(latlng.lng, 0));
  const version = ++detailsVersion; stopMedia(); selected = null;
  const panel = $('details'), task = detailsTask(reason ? 'Deep look with HAYAT’s model' : 'Deep look');
  panel.replaceChildren(node('div', 'DEEP LOOK', 'eyebrow'), node('h1', `${latlng.lat.toFixed(3)}, ${latlng.lng.toFixed(3)}`), node('div', undefined, 'scanner'),
    node('p', reason ? 'Reading every source, then asking HAYAT’s local model to read the findings…' : 'Reading every source for this spot and reasoning over it…', 'note'),
    cancelButton(task));
  busy(+1);
  try {
    const value = await json(`deep?lat=${latlng.lat.toFixed(5)}&lon=${latlng.lng.toFixed(5)}&radius_km=25${reason ? '&reason=1' : ''}`, task.signal);
    if (version !== detailsVersion) return;
    panel.replaceChildren(node('div', 'DEEP LOOK', 'eyebrow'), node('h1', value.place.name),
      node('p', `within ${value.radius_km} km · ${value.observed_at.slice(11, 16)} UTC`, 'note'), gauge(value.situation));
    const counts = node('div', undefined, 'deep-counts');
    for (const level of ['alert', 'watch', 'info', 'gap']) {
      const cell = node('div'); cell.append(node('strong', value.findings.filter(item => item.level === level).length), level === 'gap' ? 'blind' : level);
      counts.append(cell);
    }
    panel.append(counts);
    if (value.reading && value.reading.text) panel.append(node('h2', 'HAYAT’s reading · model inference'), node('p', value.reading.text, 'lead'));
    if (value.reading?.note) panel.append(node('p', value.reading.note, 'note'));
    const filters = node('div', undefined, 'deep-filters'), list = node('div');
    let chosen = 'all';
    function render() {
      list.replaceChildren(...value.findings.filter(item => chosen === 'all' || item.level === chosen).map(findingCard));
      if (!list.children.length) list.append(node('p', 'Nothing in this group; see the blind spots before taking that as all clear.', 'note'));
      for (const chip of filters.children) chip.setAttribute('aria-pressed', String(chip.dataset.level === chosen));
    }
    for (const [level, label] of [['all', 'All'], ['alert', 'Alerts'], ['watch', 'Watch'], ['info', 'Information'], ['gap', 'Blind spots']]) {
      const chip = button(label, () => { chosen = level; render(); }); chip.dataset.level = level; filters.append(chip);
    }
    render();
    panel.append(filters, list);
    if (!reason) panel.append(button('Ask HAYAT’s model to read this', () => deepLook(latlng, true)));
    panel.append(node('p', value.basis, 'note'));
    message(`Deep look · ${value.place.name}: ${value.situation.toUpperCase()} · ${value.findings.length} findings.`);
  } catch (error) { if (version === detailsVersion) panel.replaceChildren(...failureNodes(task, error, 'Deep look unavailable', () => deepLook(latlng, reason))); }
  finally { busy(-1); task.done(); }
}
$('deep-centre').addEventListener('click', () => deepLook(map.getCenter()));
async function focusHash() {
  const code = viewCodeFromHash(location.hash);
  if (code) { if (!ownViewHash(location.hash)) await openViewCode(code, 'address'); return; }
  if (location.hash === '#query') { setMapFocus(false); $('query').focus(); return; }
  if (location.hash.length > 1) {
    const row = await inspect(decodeURIComponent(location.hash.slice(1)));
    if (row && row.latitude !== null) map.setView([row.latitude, row.longitude], row.kind === 'public_camera' ? 13 : 6);
  }
}
const focusMapButton = button('Focus map', () => setMapFocus(!document.body.classList.contains('map-focused')));
focusMapButton.id = 'focus-map'; focusMapButton.setAttribute('aria-pressed', 'false');
focusMapButton.title = 'Expand map; Escape restores the panels';
document.querySelector('.map-actions').append(focusMapButton);
function setMapFocus(enabled) {
  document.body.classList.toggle('map-focused', enabled);
  focusMapButton.textContent = enabled ? 'Show panels' : 'Focus map';
  focusMapButton.setAttribute('aria-pressed', String(enabled));
  requestAnimationFrame(() => map.invalidateSize({pan: false}));
}
$('retry-load').addEventListener('click', () => config ? load() : start());
document.addEventListener('keydown', event => {
  if (event.key === '/' && !event.target.closest('input,textarea,select,[contenteditable=true]')) { setMapFocus(false); $('query').focus(); }
});
async function refreshSavedViews() {
  try {
    const value = await json('views');
    const choice = $('saved-atlas-views'), previous = choice.value;
    choice.replaceChildren(new Option('Choose a view', ''), ...value.views.map(view => new Option(view.name, view.name)));
    if ([...choice.options].some(option => option.value === previous)) choice.value = previous;
  } catch (error) { $('saved-view-state').textContent = 'Saved views unavailable: ' + error.message; }
}
async function atlasViewAction(action) {
  const name = action === 'save' ? $('view-title').value.trim() : $('saved-atlas-views').value;
  if (!name) { $('saved-view-state').textContent = action === 'save' ? 'Name the view first.' : 'Choose a saved view first.'; return; }
  const controls = ['save-atlas-view', 'open-atlas-view', 'delete-atlas-view'];
  controls.forEach(id => { $(id).disabled = true; });
  const arguments_ = {action, name};
  if (action === 'save') {
    const centre = map.getCenter();
    arguments_.data = {region: $('region').value, query: $('query').value, layer: $('feed').value,
      center_lat: centre.lat, center_lon: longitudeNear(centre.lng, 0), zoom: Math.min(32, Math.max(1, 2 ** (map.getZoom() - 2))),
      category: $('category').value, min_severity: $('severity').value,
      atlas: {zoom: map.getZoom(), media: $('media').value, cameras: $('cameras').checked, events: $('events').checked,
        fresh_only: $('fresh-only').checked, hide_failed: $('hide-failed').checked,
        layers: OPTIONAL_LAYERS.filter(id => $(id).checked)}};
  }
  try {
    const value = await requestJSON('views', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(arguments_)});
    if (action === 'open') {
      stopMedia(); closeWall();
      const options = value.view.options, browser = options.atlas || {};
      for (const [id, field] of [['query', 'query'], ['feed', 'layer'], ['region', 'region']]) $(id).value = options[field] || (id === 'region' ? 'World' : '');
      if (options.category && ![...$('category').options].some(option => option.value === options.category)) $('category').append(new Option(options.category.replaceAll('_', ' '), options.category));
      $('category').value = options.category || ''; $('severity').value = options.min_severity || '';
      $('media').value = browser.media || '';
      for (const [id, field, fallback] of [['cameras','cameras',true], ['events','events',true], ['fresh-only','fresh_only',false], ['hide-failed','hide_failed',false]]) $(id).checked = browser[field] ?? fallback;
      const zoom = browser.zoom ?? Math.min(19, Math.log2(options.zoom || 1) + 2);
      map.setView([options.center_lat || 0, options.center_lon || 0], zoom, {animate: false});
      if (Array.isArray(browser.layers)) {
        // After the move, as for a share code, so automatic street detail cannot overrule the saved layers.
        const wanted = new Set(browser.layers);
        for (const id of OPTIONAL_LAYERS) $(id).checked = wanted.has(id);
        baseLayers(); basemapStatus();
        for (const kind of Object.keys(LIVE)) refreshLive(kind);
        refreshISS(); refreshFires(); refreshGnss(); refreshTrade(); updateLayerSummary(); renderChips();
      }
      $('view-name').textContent = name + ' view'; await load();
      $('saved-view-state').textContent = `Opened ${name}. Sources re-checked now. ` + (value.changes || []).join(' ');
    } else {
      await refreshSavedViews();
      if (action === 'save') $('saved-atlas-views').value = name;
      $('saved-view-state').textContent = `${action === 'save' ? 'Saved' : 'Deleted'} ${name}.`
        + (action === 'save' && OPTIONAL_LAYERS.some(id => $(id).checked) ? ' Its map layers are saved too.' : '');
    }
  } catch (error) { $('saved-view-state').textContent = 'View could not ' + action + ': ' + error.message; }
  finally { controls.forEach(id => { $(id).disabled = false; }); }
}
for (const [id, action] of [['save-atlas-view','save'], ['open-atlas-view','open'], ['delete-atlas-view','delete']]) $(id).addEventListener('click', () => atlasViewAction(action));
const OPTIONAL_LAYERS = ['streets', 'satellite', 'radar', 'aircraft', 'vessels', 'transit', 'iss', 'fires', 'gnss', 'trade'];
function updateLayerSummary() {
  const active = OPTIONAL_LAYERS.filter(id => $(id).checked).length;
  $('layer-summary').textContent = 'Map & live layers' + (active ? ` · ${active} on` : '');
  $('open-layers').textContent = active ? `Layers · ${active}` : 'Layers';
}
function setLayerStatus(id, state, text) {
  const status = $(id + '-status');
  if (status) { status.dataset.state = state; status.textContent = text.slice(0, 180); }
  if ($('open-layers')) updateLayerSummary();
  if (typeof layerStatusChanged === 'function') layerStatusChanged();
}
const layerButton = button('Layers', () => {
  setMapFocus(false); $('layer-desk').open = true;
  $('layer-desk').scrollIntoView({block: 'nearest', behavior: 'instant'});
  $('layer-summary').focus();
});
layerButton.id = 'open-layers'; layerButton.setAttribute('aria-controls', 'layer-desk');
document.querySelector('.map-actions').insertBefore(layerButton, focusMapButton);
for (const id of OPTIONAL_LAYERS) {
  const label = $(id).closest('label'), status = node('small', 'Off', 'layer-status');
  status.id = id + '-status'; status.dataset.state = 'off';
  $(id).setAttribute('aria-label', label.textContent.trim());
  $(id).setAttribute('aria-describedby', status.id);
  label.classList.add('layer-with-status'); label.append(status);
  $(id).addEventListener('change', updateLayerSummary);
}
for (const [id, layer] of [['streets', street], ['satellite', overlays.satellite], ['radar', overlays.radar]]) {
  layer.on('tileerror', () => { if ($(id).checked) setLayerStatus(id, 'error', 'Some tiles are unavailable; try another basemap.'); });
}
$('clear-overlays').addEventListener('click', () => {
  for (const id of OPTIONAL_LAYERS) $(id).checked = false;
  rememberStreets(false); baseLayers();
  for (const kind of Object.keys(LIVE)) refreshLive(kind);
  refreshISS(); refreshFires(); refreshGnss(); refreshTrade(); closeWall();
  updateLayerSummary(); message('Optional overlays and video wall stopped. Camera and event observations remain available.');
});
updateLayerSummary();
// start() runs at the end of atlas-ui.js, which this file's start-up depends on (view codes, activity, chips).
