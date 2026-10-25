/* Exercise actual atlas media controls through publisher outages and Stop races. */
const fs = require('fs'), vm = require('vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('app/assets/intelligence-map/map.js', 'utf8');
const timers = new Map(); let nextTimer = 1;
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.dataset = {}; this.attrs = {}; }
  append(...items) { this.children.push(...items); }
  prepend(...items) { this.children.unshift(...items); }
  setAttribute(key, value) { this.attrs[key] = value; }
  getAttribute(key) { return this.attrs[key] || (key === 'src' ? this.src : null); }
  removeAttribute(key) { delete this.attrs[key]; if (key === 'src') this.src = ''; }
  addEventListener(event, fn) { this.events[event] = fn; }
  querySelector(query) {
    return this.children.find(x => x instanceof Element && (x.tag === query || query === '[data-auto-still]' && x.dataset.autoStill))
      || this.children.map(x => x instanceof Element && x.querySelector(query)).find(Boolean) || null;
  }
  pause() {} load() {} play() { return Promise.resolve(); }
}
class Hls {
  static Events = {MANIFEST_PARSED: 'manifest', ERROR: 'error'};
  static ErrorTypes = {MEDIA_ERROR: 'media'};
  static isSupported() { return true; }
  constructor() { this.events = {}; this.loads = 0; this.urls = []; Hls.latest = this; }
  loadSource(url) { ++this.loads; this.urls.push(url); }
  attachMedia() {} on(name, fn) { this.events[name] = fn; }
  startLoad() {} recoverMediaError() { this.recovered = true; }
  destroy() { this.destroyed = true; }
}
const panel = new Element('div');
const context = vm.createContext({Element, Hls, URL, AbortController, console,
  window: {Hls}, document: {hidden: true, createTextNode: value => value},
  setTimeout: (fn, delay) => { const id = nextTimer++; timers.set(id, {fn, delay}); return id; },
  clearTimeout: id => timers.delete(id),
  fetch: async () => { throw new Error('publisher disconnected'); },
  panel, node: (tag, text, cls) => Object.assign(new Element(tag), {textContent: text, className: cls}),
  button: (text, action) => Object.assign(new Element('button'), {textContent: text, action}),
  link: text => new Element('a'), mediaKind: () => 'HLS stream', $: () => panel,
});
vm.runInContext('let frameTimer=null,frameRequest=null,videoTimer=null,mediaGeneration=0,hls=null,imageURL=null,detailsVersion=1;', context);
vm.runInContext(source.slice(source.indexOf('function stopMedia()'), source.indexOf('/* The whole world')), context);
vm.runInContext(source.slice(source.indexOf('function cameraControls('), source.indexOf('async function inspect(')), context);
vm.runInContext(`cameraControls(panel,{id:'test',details:{available:false,provider:'test',image_url:'https://example.org/camera.jpg',video_url:'https://example.org/live.m3u8'}},1)`, context);
function buttons(element) { return element.children.flatMap(x => x instanceof Element ? [x, ...buttons(x)] : []); }
const all = buttons(panel), auto = all.find(x => x.dataset.autoStill),
  load = all.find(x => x.textContent === 'Load latest snapshot'), play = all.find(x => x.textContent === 'Play publisher video');
(async () => {
  assert.equal(auto.disabled, false); assert.equal(load.disabled, false);
  auto.checked = true;
  await load.action();
  assert.equal(auto.checked, true, 'failure must not turn selected refresh off');
  assert.equal(timers.size, 1, 'timeout is removed and a reconnect is scheduled even when hidden');
  assert.equal([...timers.values()][0].delay, 30000);
  await play.action();
  assert.equal(timers.size, 0, 'switching media cancels stale snapshot retry');
  Hls.latest.events.error(null, {fatal: true, type: 'network'});
  assert.equal(timers.size, 1);
  const retry = [...timers.values()][0].fn;
  const player = Hls.latest;
  vm.runInContext('stopMedia()', context);
  assert.equal(timers.size, 0); assert.equal(player.destroyed, true);
  retry();
  assert.equal(player.loads, 1, 'already queued retry must not reopen media after Stop');
  panel.children = [];
  let addresses = 0;
  context.json = async () => ({video_url: `https://manifest.googlevideo.com/fresh-${++addresses}.m3u8`});
  vm.runInContext(`cameraControls(panel,{id:'livestreams:makkah',details:{provider:'official',live_page:'https://www.youtube.com/@SaudiQuranTv/live'}},1)`, context);
  const official = buttons(panel).find(x => x.textContent === 'Play publisher video');
  await official.action();
  assert.match(Hls.latest.urls[0], /fresh-1/);
  Hls.latest.events.error(null, {fatal: true, type: 'network'});
  let [timerId, timer] = [...timers][0]; timers.delete(timerId);
  await timer.fn();
  assert.match(Hls.latest.urls[0], /fresh-2/, 'official source is re-resolved after network failure');
  const second = Hls.latest;
  second.events.error(null, {fatal: true, type: 'network'});
  [timerId, timer] = [...timers][0]; timers.delete(timerId);
  context.json = async () => ({video_url: '', notice: 'Official channel offline'});
  await timer.fn();
  assert.equal(timers.size, 1, 'offline publisher keeps the selected broadcast reconnecting');
  [timerId, timer] = [...timers][0]; timers.delete(timerId);
  let release;
  context.json = () => new Promise(resolve => { release = resolve; });
  const pending = timer.fn();
  vm.runInContext('stopMedia()', context);
  release({video_url: 'https://manifest.googlevideo.com/late.m3u8'});
  await pending;
  assert.equal(Hls.latest, second, 'late source resolution cannot start a new player after Stop');
  assert.equal(second.destroyed, true);
  console.log('atlas media recovery passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
