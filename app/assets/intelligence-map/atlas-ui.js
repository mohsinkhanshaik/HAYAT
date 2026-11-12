/* Atlas workspace UI (2026-11-08): view codes, layer chips, request activity with cancel, location status
   and one predictable Escape. The ideas come from gods-eye-view (MIT, © 2026 Bilawal Sidhu: sharelink,
   mapStackChips/unavailable reasons, loadingFeedback, locationStatus, panelEscape); this is HAYAT's own
   implementation for a Leaflet atlas, no code copied. Loaded after map.js and shares its globals. */
'use strict';

/* ---------------------------------------------------------------- activity
   Slow requests are announced after a short delay (fast loopback answers never flash) with their
   elapsed time and a Cancel button. Cancelling stops the browser waiting; the caller then says so. */
const ACTIVITY_REVEAL_MS = 600, ACTIVITY_SLOW_MS = 15000;
const CANCELLED = new WeakSet();
const activity = new Map();
let activitySeq = 0, activityTimer = null;
function track(label, controller) {
  const id = ++activitySeq;
  activity.set(id, {id, label, controller, started: performance.now(), row: null, slow: false});
  renderActivity();
  return () => {
    const task = activity.get(id);
    if (!task) return;
    activity.delete(id);
    if (task.row) {
      const hadFocus = task.row.contains(document.activeElement);
      task.row.remove();
      if (hadFocus) focusFirstVisible(document.querySelector('#activity-list button'), $('map'));
    }
    renderActivity();
  };
}
function cancelTask(controller) {
  if (!controller || controller.signal.aborted) return;
  CANCELLED.add(controller); controller.abort();
}
function wasCancelled(controller) { return CANCELLED.has(controller); }
function renderActivity() {
  clearTimeout(activityTimer); activityTimer = null;
  const now = performance.now(), list = $('activity-list');
  let next = Infinity;
  for (const task of activity.values()) {
    const age = now - task.started;
    if (age < ACTIVITY_REVEAL_MS) { next = Math.min(next, ACTIVITY_REVEAL_MS - age); continue; }
    if (!task.row) {
      task.row = node('div', undefined, 'activity-row');
      task.text = node('span', task.label + '…', 'activity-label');
      task.clock = node('span', '', 'activity-time'); task.clock.setAttribute('aria-hidden', 'true');
      const cancel = button('Cancel', () => cancelTask(task.controller));
      cancel.setAttribute('aria-label', 'Cancel: ' + task.label);
      task.row.append(task.text, task.clock, cancel); list.append(task.row);
      $('activity-live').textContent = task.label + '…';
    }
    if (!task.slow && age >= ACTIVITY_SLOW_MS) {
      task.slow = true; task.row.classList.add('slow');
      task.text.textContent = task.label + ' · taking longer than usual';
      $('activity-live').textContent = `${task.label} is taking longer than usual. You can cancel it.`;
    }
    task.clock.textContent = Math.max(1, Math.round(age / 1000)) + ' s';
    next = Math.min(next, 1000 - (age % 1000));
  }
  $('activity').hidden = !list.children.length;
  if (activity.size) activityTimer = setTimeout(renderActivity, Math.max(50, next));
}

/* ---------------------------------------------------------------- view codes
   The address bar follows the view as "#v=1&c=lat,lon&z=…" (replaceState: no history spam), so a reload
   keeps it, and the same code can be copied and opened in any HAYAT atlas. Only the part after "#" is
   ever read or written: the session's private address, files and keys are never part of a code. */
const VIEW_VERSION = '1';
const VIEW_KEYS = ['v', 'c', 'z', 'l', 'q', 'f', 'm', 'cat', 'sev', 'sort', 'hide', 'only', 'sel'];
const MEDIA_VALUES = ['stream', 'video', 'image', 'page'], SEVERITY_VALUES = ['high', 'medium'];
const SORT_VALUES = ['priority', 'nearest', 'title'], HIDE_VALUES = ['cameras', 'events'], ONLY_VALUES = ['fresh', 'working'];
let viewLinkIsReady = false, applyingView = false, viewHashTimer = null, writtenHash = '';
/* Text that looks like a file path, a key or a long secret never goes into a code. */
function looksPrivate(text) {
  return /(^|[\s"'(])([a-z]:[\\/]|\\\\|~[\\/]|\/(users|home|etc|var|tmp|private)\/)/i.test(text)
    || /\b[0-9a-f]{32,}\b/i.test(text) || /\b(api[_-]?key|token|secret|password|passwd)\s*[=:]/i.test(text);
}
function currentViewState() {
  const centre = map.getCenter();
  return {lat: centre.lat, lon: longitudeNear(centre.lng, 0), zoom: map.getZoom(),
    layers: OPTIONAL_LAYERS.filter(id => $(id).checked), query: loadedQuery.trim(), feed: $('feed').value,
    media: $('media').value, category: $('category').value, severity: $('severity').value, sort: $('sort').value,
    hide: [['cameras', 'cameras'], ['events', 'events']].filter(([id]) => !$(id).checked).map(([, key]) => key),
    only: [['fresh-only', 'fresh'], ['hide-failed', 'working']].filter(([id]) => $(id).checked).map(([, key]) => key),
    selected: !$('details-panel').hidden && selected ? selected : ''};
}
function encodeView(state) {
  const digits = state.zoom >= 12 ? 5 : 4, parts = [['v', VIEW_VERSION],
    ['c', `${state.lat.toFixed(digits)},${state.lon.toFixed(digits)}`], ['z', String(Math.round(state.zoom))]];
  const omitted = [];
  if (state.layers.length) parts.push(['l', state.layers.join(',')]);
  if (state.query && looksPrivate(state.query)) omitted.push('the search text (it looks like a file path or key)');
  else if (state.query) parts.push(['q', state.query.slice(0, 500)]);
  for (const [key, value, fallback] of [['f', state.feed, ''], ['m', state.media, ''], ['cat', state.category, ''],
    ['sev', state.severity, ''], ['sort', state.sort, 'priority']]) if (value && value !== fallback) parts.push([key, value]);
  if (state.hide.length) parts.push(['hide', state.hide.join(',')]);
  if (state.only.length) parts.push(['only', state.only.join(',')]);
  if (state.selected) parts.push(['sel', state.selected]);
  const code = parts.map(([key, value]) => key + '=' + encodeURIComponent(value).replace(/%2C/gi, ',')).join('&');
  return {code, omitted};
}
function viewCodeFromHash(hash) {
  const text = String(hash || '').replace(/^#/, '');
  return /^v=\d+(&|$)/.test(text) ? text : null;
}
/* A pasted value may be a bare code, "#code" or a whole atlas link from any session: only the part after "#" counts. */
function extractViewCode(input) {
  const text = String(input || '').trim(), at = text.indexOf('#');
  return viewCodeFromHash(at >= 0 ? text.slice(at) : text);
}
function ownViewHash(hash) { return hash === writtenHash; }
function decodeView(code) {
  let params;
  try { params = new URLSearchParams(code); } catch { return {error: 'The view code could not be read.'}; }
  if (params.get('v') !== VIEW_VERSION) return {error: `This is a version ${params.get('v')} view code; this atlas reads version ${VIEW_VERSION}.`};
  const state = {}, problems = [];
  for (const key of new Set(params.keys())) if (!VIEW_KEYS.includes(key)) problems.push(`ignored the unknown part “${key.slice(0, 20)}”`);
  if (params.has('c')) {
    const parts = params.get('c').split(','), [lat, lon] = parts.map(Number);
    if (parts.length !== 2 || parts.some(part => !part.trim()) || !Number.isFinite(lat) || !Number.isFinite(lon)
      || Math.abs(lat) > 90 || Math.abs(lon) > 180) return {error: 'its centre must be latitude,longitude within ±90 and ±180.'};
    state.lat = lat; state.lon = lon;
  }
  if (params.has('z')) {
    const zoom = Number(params.get('z'));
    if (!Number.isInteger(zoom) || zoom < 1 || zoom > 19) return {error: 'its zoom must be a whole number from 1 to 19.'};
    state.zoom = zoom;
  }
  const list = (key, allowed, what) => {
    const values = (params.get(key) || '').split(',').filter(Boolean);
    const unknown = values.filter(value => !allowed.includes(value));
    if (unknown.length) problems.push(`ignored unknown ${what} ${unknown.slice(0, 3).map(value => `“${value.slice(0, 20)}”`).join(', ')}`);
    return [...new Set(values.filter(value => allowed.includes(value)))];
  };
  state.layers = list('l', OPTIONAL_LAYERS, 'layer');
  state.hide = list('hide', HIDE_VALUES, 'visibility option');
  state.only = list('only', ONLY_VALUES, 'filter');
  const query = params.get('q') || '';
  if (query.length > 500 || looksPrivate(query)) problems.push('ignored the search text (too long, or it looks like a file path or key)');
  else state.query = query;
  const feed = params.get('f') || '';
  if (feed && !(config?.feeds || []).some(item => item.key === feed)) problems.push(`ignored the unknown source layer “${feed.slice(0, 40)}”`);
  else state.feed = feed;
  for (const [key, field, allowed] of [['m', 'media', MEDIA_VALUES], ['sev', 'severity', SEVERITY_VALUES], ['sort', 'sort', SORT_VALUES]]) {
    const value = params.get(key) || '';
    if (value && !allowed.includes(value)) problems.push(`ignored the unknown ${field} “${value.slice(0, 20)}”`);
    else state[field] = value;
  }
  const category = params.get('cat') || '';
  if (category && !/^[a-z][a-z_]{0,39}$/.test(category)) problems.push('ignored an invalid event category');
  else state.category = category;
  const chosen = params.get('sel') || '';
  if (chosen.length > 200 || /[\u0000-\u001f\u007f]/.test(chosen)) problems.push('ignored an invalid selected record');
  else state.selected = chosen;
  return {state, problems};
}
function shareReport(text) { $('share-state').textContent = text; message(text); }
async function openViewCode(code, origin) {
  const decoded = decodeView(code);
  if (decoded.error) {
    shareReport('View code not opened: ' + decoded.error + ' The current view was kept.');
    if (origin === 'startup') { await load(); viewLinkReady(); }
    return false;
  }
  const view = decoded.state;
  applyingView = true;
  try {
    stopMedia(); closeWall(); cancelPlaceSearch();
    $('query').value = view.query || ''; $('feed').value = view.feed || ''; $('media').value = view.media || '';
    if (view.category && ![...$('category').options].some(option => option.value === view.category)) {
      $('category').append(new Option(view.category.replaceAll('_', ' '), view.category));
    }
    $('category').value = view.category || ''; $('severity').value = view.severity || ''; $('sort').value = view.sort || 'priority';
    $('cameras').checked = !view.hide.includes('cameras'); $('events').checked = !view.hide.includes('events');
    $('fresh-only').checked = view.only.includes('fresh'); $('hide-failed').checked = view.only.includes('working');
    document.querySelectorAll('[data-destination]').forEach(el => el.setAttribute('aria-pressed', 'false'));
    browseLimit = 40; browseSignature = '';
    // The code was written with the details panel open: open it first so the map has the same width.
    if (view.selected) { showDetails(true); map.invalidateSize({pan: false}); }
    if (view.lat !== undefined) map.setView([view.lat, view.lon], view.zoom ?? map.getZoom(), {animate: false});
    else if (view.zoom) map.setZoom(view.zoom, {animate: false});
    $('view-name').textContent = 'Shared view';
    // Layers after the move, so automatic street detail cannot overrule what the code says.
    const wanted = new Set(view.layers);
    for (const id of OPTIONAL_LAYERS) $(id).checked = wanted.has(id);
    baseLayers(); basemapStatus();
    for (const kind of Object.keys(LIVE)) refreshLive(kind);
    refreshISS(); refreshFires(); refreshGnss(); refreshTrade(); updateLayerSummary(); renderChips();
    await load();
    if (view.selected) await inspect(view.selected);
  } finally { applyingView = false; }
  shareReport((origin === 'pasted' ? 'Opened the view code.' : 'Opened the view in the address.')
    + (decoded.problems.length ? ' Note: ' + decoded.problems.join('; ') + '.' : '') + ' Sources were re-checked now; media starts only on request.');
  viewLinkReady();
  return true;
}
function viewLinkReady() { viewLinkIsReady = true; writeViewHash(); }
function viewChanged() {
  if (!viewLinkIsReady || applyingView) return;
  clearTimeout(viewHashTimer); viewHashTimer = setTimeout(writeViewHash, 400);
}
function writeViewHash() {
  clearTimeout(viewHashTimer); viewHashTimer = null;
  if (!viewLinkIsReady || applyingView) return;
  const {code, omitted} = encodeView(currentViewState()), hash = '#' + code;
  $('view-code').value = hash;
  const notes = ['Centre, zoom, layers, filters and the selected record. Never this session’s private address, files or keys.'];
  if (omitted.length) notes.push('Left out: ' + omitted.join(', ') + '.');
  if (locationFix && map.distance(map.getCenter(), [locationFix.latitude, locationFix.longitude]) < 25000) {
    notes.push('This view is centred near this computer’s location: anyone you give the code to can see roughly where that is.');
  }
  $('view-code-note').textContent = notes.join(' ');
  if (location.hash !== hash) history.replaceState(history.state, '', hash);
  writtenHash = hash;
}
async function copyViewCode() {
  writeViewHash();
  const text = $('view-code').value;
  try {
    await navigator.clipboard.writeText(text);
    shareReport('View code copied. Open it with “Open a view code” in any HAYAT atlas, or after the atlas address.');
  } catch {
    $('view-code').focus(); $('view-code').select();
    let copied = false;
    try { copied = document.execCommand('copy'); } catch { copied = false; }
    shareReport(copied ? 'View code copied.' : 'The browser did not allow copying: the code is selected, press Ctrl+C.');
  }
}
async function openPastedViewCode() {
  const code = extractViewCode($('open-view-code').value);
  if (!code) {
    shareReport('That is not an atlas view code. A code starts with #v=1 (a whole atlas link works too).');
    $('open-view-code').focus(); $('open-view-code').select(); return;
  }
  if (await openViewCode(code, 'pasted')) $('open-view-code').value = '';
}
$('copy-view-code').addEventListener('click', copyViewCode);
$('open-view-code-button').addEventListener('click', openPastedViewCode);
$('open-view-code').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); openPastedViewCode(); } });
$('share-desk').addEventListener('toggle', () => { if ($('share-desk').open) writeViewHash(); });
map.on('moveend', viewChanged);
document.addEventListener('change', viewChanged);

/* ---------------------------------------------------------------- layer chips
   Each layer that is on gets a chip on the map saying what it is doing, and why it shows nothing when
   it shows nothing: zoom, no coverage here, none in view, source error, or paused. The chip's action is
   the remedy (zoom, go to the covered area, check again); × turns the layer off. */
const CHIP_NAMES = {streets: 'Street detail', satellite: 'Satellite', radar: 'Radar', aircraft: 'Aircraft', vessels: 'Ships',
  transit: 'Transit', iss: 'ISS', fires: 'Fires', gnss: 'GPS interference', trade: 'Chokepoints'};
const CHIP_WORDS = {loading: 'loading…', ready: 'on', enabled: 'on', empty: 'none here', coverage: 'no coverage here',
  error: 'unavailable', paused: 'paused', off: 'off'};
const ZOOM_MAX = {radar: 12, satellite: 14};
const RADAR_COVERAGE = L.latLngBounds([[20, -130], [55, -60]]);  // the NEXRAD mosaic: the contiguous United States
const REFRESH = {aircraft: () => refreshLive('aircraft'), vessels: () => refreshLive('vessels'), transit: () => refreshLive('transit'),
  iss: () => refreshISS(), fires: () => refreshFires(), gnss: () => refreshGnss(), trade: () => refreshTrade()};
const TILES = {streets: () => street, satellite: () => overlays.satellite, radar: () => overlays.radar};
function minZoom(id) { return LIVE[id]?.minZoom ?? (id === 'gnss' ? 4 : null); }
function viewTouches(bounds) {
  const view = map.getBounds();
  return [-360, 0, 360].some(shift => view.intersects(L.latLngBounds(
    [bounds.getSouth(), bounds.getWest() + shift], [bounds.getNorth(), bounds.getEast() + shift])));
}
/* Tile layers have limits Leaflet does not announce: radar stops at zoom 12 and covers the US only,
   satellite imagery stops at zoom 14 and is enlarged past zoom 9. */
function basemapStatus() {
  const zoom = map.getZoom(), status = id => $(id + '-status')?.dataset.state;
  if ($('radar').checked) {
    if (zoom > ZOOM_MAX.radar) setLayerStatus('radar', 'zoom', 'Radar tiles stop at zoom 12 · zoom out to see them');
    else if (!viewTouches(RADAR_COVERAGE)) setLayerStatus('radar', 'coverage', 'No radar here · the mosaic covers the contiguous United States');
    else if (status('radar') !== 'error') setLayerStatus('radar', 'enabled', 'US radar tiles · refreshes every 5 minutes');
  }
  if ($('satellite').checked) {
    if (zoom > ZOOM_MAX.satellite) setLayerStatus('satellite', 'zoom', 'Satellite imagery stops at zoom 14 · zoom out to see it');
    else if (status('satellite') !== 'error') setLayerStatus('satellite', 'enabled', zoom > 9
      ? 'Daily imagery · enlarged past zoom 9, its finest level' : 'Daily imagery · date shown in map credit');
  }
}
function chipRemedy(id, state) {
  const lowest = minZoom(id);
  if (state === 'zoom' && ZOOM_MAX[id] && map.getZoom() > ZOOM_MAX[id]) return {label: `Zoom out to level ${ZOOM_MAX[id]}`, run: () => map.setZoom(ZOOM_MAX[id], {animate: motionOn()})};
  if (state === 'zoom' && lowest) return {label: `Zoom in to level ${lowest}`, run: () => map.setZoom(lowest, {animate: motionOn()})};
  if (state === 'coverage' && id === 'vessels') return {label: 'Go to the Baltic Sea', run: () => { map.setView([59.5, 21], 6, {animate: motionOn()}); $('view-name').textContent = 'Baltic Sea'; }};
  if (state === 'coverage' && id === 'radar') return {label: 'Go to the United States', run: () => { map.fitBounds([[25, -124], [49, -67]], {animate: motionOn()}); $('view-name').textContent = 'United States'; }};
  if (['error', 'paused', 'empty'].includes(state) && REFRESH[id]) return {label: 'Check again now', run: REFRESH[id]};
  if (state === 'error' && TILES[id]) return {label: 'Reload the tiles', run: () => { setLayerStatus(id, 'enabled', 'Reloading tiles…'); TILES[id]().redraw(); basemapStatus(); }};
  return {label: 'Show it in the layer desk', run: () => {
    setMapFocus(false); $('layer-desk').open = true; $(id).scrollIntoView({block: 'nearest', behavior: 'instant'}); $(id).focus();
  }};
}
function chipState(id) { return $(id + '-status')?.dataset.state || 'enabled'; }
/* Chips are updated in place, never replaced: a status change mid-click (layers refresh every few seconds)
   must not swallow the click or move keyboard focus. The remedy is chosen when the chip is pressed. */
function layerChip(id) {
  const group = node('span', undefined, 'layer-chip'); group.dataset.layer = id;
  // While a background refresh is loading, the remedy still answers the last settled reason.
  const main = button('', () => { const state = chipState(id); chipRemedy(id, state === 'loading' ? group.dataset.settled || state : state).run(); });
  main.className = 'chip-main'; main.dataset.part = 'main';
  const off = button('×', () => turnLayerOff(id));
  off.className = 'chip-off'; off.dataset.part = 'off'; off.setAttribute('aria-label', `Turn ${CHIP_NAMES[id]} off`);
  group.append(main, off);
  return group;
}
function updateChip(group) {
  const id = group.dataset.layer, state = chipState(id), text = ($(id + '-status')?.textContent || '').trim();
  const remedy = chipRemedy(id, state), main = group.querySelector('.chip-main');
  const count = state === 'ready' && /^(\d[\d,]*)\s/.exec(text);
  const word = state === 'zoom' ? (ZOOM_MAX[id] && map.getZoom() > ZOOM_MAX[id] ? 'zoom out' : 'zoom in')
    : count ? count[1] : CHIP_WORDS[state] || state;
  group.dataset.state = state;
  if (state !== 'loading') group.dataset.settled = state;
  main.textContent = `${CHIP_NAMES[id]} · ${word}`;
  main.setAttribute('aria-label', `${CHIP_NAMES[id]}: ${text || word}. ${remedy.label}.`);
  main.title = `${text || word} · ${remedy.label}`;
}
function renderChips() {
  const box = $('layer-chips'), on = OPTIONAL_LAYERS.filter(id => $(id).checked);
  const existing = new Map([...box.querySelectorAll('.layer-chip')].map(chip => [chip.dataset.layer, chip]));
  for (const [id, chip] of existing) if (!on.includes(id)) chip.remove();
  on.forEach((id, index) => {
    const chip = existing.get(id) || layerChip(id);
    if (box.children[index] !== chip) box.insertBefore(chip, box.children[index] || null);
    updateChip(chip);
  });
  box.hidden = !on.length;
}
let chipFrame = 0;
function layerStatusChanged() { if (!chipFrame) chipFrame = requestAnimationFrame(() => { chipFrame = 0; renderChips(); }); }
function turnLayerOff(id) {
  const chips = [...$('layer-chips').querySelectorAll('.layer-chip')], index = chips.findIndex(chip => chip.dataset.layer === id);
  $(id).checked = false; $(id).dispatchEvent(new Event('change', {bubbles: true}));
  renderChips();
  const rest = [...$('layer-chips').querySelectorAll('.chip-main')];
  focusFirstVisible(rest[Math.min(index, rest.length - 1)], $('open-layers'));
  message(`${CHIP_NAMES[id]} turned off.`);
}
for (const id of ['streets', 'satellite', 'radar']) $(id).addEventListener('change', basemapStatus);
for (const id of OPTIONAL_LAYERS) $(id).addEventListener('change', layerStatusChanged);
map.on('zoomend moveend', () => { basemapStatus(); layerStatusChanged(); });
// A layer paused while the tab was hidden checks again as soon as the atlas is visible.
document.addEventListener('visibilitychange', () => {
  if (document.hidden) return;
  for (const id of Object.keys(REFRESH)) if ($(id).checked && $(id + '-status')?.dataset.state === 'paused') REFRESH[id]();
});

/* ---------------------------------------------------------------- location status
   "My location" asks HAYAT for this computer's own position (Windows Location, else a public-IP estimate,
   the same reading as before) over the loopback connection. The page shows how it was read and how good it
   is; the browser's own location permission is never requested. */
let locationFix = null, locationProblem = null, locationRequest = null;
const locationLayer = L.layerGroup().addTo(map);
function distanceText(metres) { return metres < 1000 ? `${Math.round(metres)} m` : `${(metres / 1000).toFixed(metres < 10000 ? 1 : 0)} km`; }
function accuracyText(fix) {
  if (!fix.precise) return 'approximate · city level at best';
  return fix.accuracy_m ? `within about ${distanceText(fix.accuracy_m)}` : 'precise · accuracy not reported';
}
function methodText(fix) {
  return {windows_location_service: 'Windows Location service', public_ip_lookup: 'public IP address estimate'}[fix.method] || 'no method succeeded';
}
function drawLocation() {
  locationLayer.clearLayers();
  if (!locationFix) return;
  const point = [locationFix.latitude, locationFix.longitude];
  if (locationFix.precise && locationFix.accuracy_m && locationFix.accuracy_m <= 100000) {
    L.circle(point, {radius: locationFix.accuracy_m, color: '#a9d0a8', weight: 1, fillOpacity: .08, interactive: false, className: 'location-accuracy'}).addTo(locationLayer);
  }
  L.circleMarker(point, {radius: 7, color: '#0d1c23', weight: 2, fillColor: locationFix.precise ? '#a9d0a8' : '#dfc694', fillOpacity: 1, className: 'location-dot'})
    .bindTooltip(node('span', `This computer’s location · ${methodText(locationFix)} · ${accuracyText(locationFix)}`))
    .on('click', locationDesk).addTo(locationLayer);
}
function showLocationStatus() {
  const chip = $('location-status');
  if (!locationFix && !locationProblem) { chip.hidden = true; return; }
  chip.hidden = false;
  if (locationFix) {
    chip.dataset.state = locationFix.precise ? 'precise' : 'approximate';
    chip.textContent = `◎ Your location · ${locationFix.precise ? (locationFix.accuracy_m ? '±' + distanceText(locationFix.accuracy_m) : 'precise') : 'approximate'}`;
    chip.setAttribute('aria-label', `Location status: ${methodText(locationFix)}, ${accuracyText(locationFix)}. Open location details.`);
  } else {
    chip.dataset.state = 'failed'; chip.textContent = '◎ Location unavailable';
    chip.setAttribute('aria-label', `Location status: unavailable. ${locationProblem.reason || ''} Open location details.`);
  }
}
async function locate() {
  cancelPlaceSearch();
  if (locationRequest) cancelTask(locationRequest);
  const request = new AbortController(), untrack = track('Reading this computer’s location', request);
  locationRequest = request; busy(+1); message('Reading this computer’s location…');
  try {
    const value = await requestJSON('location', {signal: request.signal}, 45000);
    if (locationRequest !== request) return;
    if (!value.found) {
      locationProblem = value; locationFix = null; drawLocation(); showLocationStatus();
      message('My location: ' + (value.reason || 'not available') + ' Select “Location unavailable” for details.');
      return;
    }
    locationProblem = null; locationFix = {...value, read_at: new Date()};
    flyTo({latitude: value.latitude, longitude: value.longitude, name: 'Your location'}, value.precise ? 14 : 11);
    drawLocation(); showLocationStatus(); viewChanged();
    message(`Your location · ${methodText(value)} · ${accuracyText(value)}. It stays on this computer.`);
  } catch (error) {
    if (wasCancelled(request)) message('Stopped reading the location. The map did not move.');
    else if (error.name !== 'AbortError') { locationProblem = {reason: error.message}; showLocationStatus(); message('My location: ' + error.message); }
  } finally { busy(-1); untrack(); if (locationRequest === request) locationRequest = null; }
}
function clearLocation() {
  locationFix = null; locationProblem = null; drawLocation(); showLocationStatus(); viewChanged();
  closeDetailsPanel({focus: false}); focusFirstVisible($('locate'));
  message('Location cleared from the map.');
}
async function browserPermission() {
  try { return (await navigator.permissions.query({name: 'geolocation'})).state; } catch { return 'unknown'; }
}
async function locationDesk() {
  showDetails();
  const version = ++detailsVersion; stopMedia(); selected = null;
  const panel = $('details'), fix = locationFix, problem = locationProblem || {};
  const source = fix || problem;
  panel.replaceChildren(node('div', 'LOCATION STATUS', 'eyebrow'), node('h1', fix ? 'Your location' : 'Location unavailable'));
  if (fix) panel.append(node('p', `${fix.label ? fix.label + ' · ' : ''}${fix.latitude.toFixed(5)}, ${fix.longitude.toFixed(5)}`, 'lead'));
  else panel.append(node('p', problem.reason || 'No position has been read yet.', 'lead'));
  const facts = [['Method', fix ? methodText(fix) : source.method ? methodText(source) : 'Not read yet'],
    ['Accuracy', fix ? accuracyText(fix) : '—'], ['Source', source.source || '—'],
    ['Read at', source.observed_at || (fix ? fix.read_at.toISOString() : '—')],
    ['Windows location permission', source.windows?.permission || 'not reported'], ['Windows location status', source.windows?.status || 'not reported']];
  if (source.why_not_precise) facts.push(['Why not precise', source.why_not_precise]);
  if (source.how_to_get_precise) facts.push(['How to get a precise position', source.how_to_get_precise]);
  for (const [label, text] of facts) panel.append(node('div', `${label}: ${text}`, 'fact'));
  const permission = node('div', 'Browser location permission: checking…', 'fact');
  panel.append(permission, node('p', 'Read by HAYAT on this computer and passed to this page over the loopback connection. The atlas never asks the browser for its location and sends this position to no new service. Map tiles and live layers you turn on are requested for whatever area is on screen, including this one; a shared view code holds the map centre.', 'note'));
  const actions = [button('Read location again', locate)];
  if (fix) actions.push(button('Centre on it', () => flyTo({latitude: fix.latitude, longitude: fix.longitude, name: 'Your location'}, fix.precise ? 14 : 11)),
    button('Clear from map', clearLocation));
  else if (locationProblem) actions.push(button('Clear', clearLocation));
  panel.append(...actions);
  const state = await browserPermission();
  if (version === detailsVersion) permission.textContent = `Browser location permission: not requested by the atlas (browser state: ${state})`;
}
$('locate').addEventListener('click', locate);
$('location-status').addEventListener('click', locationDesk);

/* ---------------------------------------------------------------- Escape
   One press closes one thing, the one you are in first, else the topmost: an open fold in the side panel,
   the video wall, the details panel, a map popup, then focus-map mode. Focus returns to what opened it.
   A native dialog closes itself, so Escape is left to it. */
function shown(el) { return !!el && !el.hidden && el.getClientRects().length > 0; }
function focusInside(el) { return el.contains(document.activeElement) || document.activeElement === document.body; }
function closeWallByKey() { const back = focusInside($('wall')); closeWall(); if (back) focusFirstVisible($('video-wall')); }
function closeDetailsByKey() { closeDetailsPanel({focus: focusInside($('details-panel'))}); }
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing || document.querySelector('dialog[open]')) return;
  const target = event.target instanceof Element ? event.target : null;
  const fold = target?.closest('.explorer details[open]');
  let handled = true;
  if (fold) { fold.open = false; fold.querySelector('summary').focus(); }
  else if (target?.closest('#wall') && shown($('wall'))) closeWallByKey();
  else if (target?.closest('#details-panel') && shown($('details-panel'))) closeDetailsByKey();
  else if (document.querySelector('.leaflet-popup')) map.closePopup();
  else if (shown($('wall'))) closeWallByKey();
  else if (shown($('details-panel'))) closeDetailsByKey();
  else if (document.body.classList.contains('map-focused')) setMapFocus(false);
  else handled = false;
  if (handled) event.preventDefault();
});

start();
