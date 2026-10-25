# Camera

## Camera browsing improvements (2026-10-17)

Global Intelligence → **Cameras** now offers **Name**, **Playable first**,
**Newest catalog**, and **Nearest event** sorting. Nearby searches accept a
radius up to 2,000 km. Choose **More camera actions → Near selected event**
after selecting a located event; this searches public cameras near the event,
not cameras tracking a person.

**Update catalogs** refreshes up to eight due, registered public camera networks
at a time. Select a network in Layers or enter a matching region to narrow the
batch. With no filter, unloaded/older networks are preferred; repeated updates
advance through other due networks. Publisher cooldowns and access-key
requirements still apply. This expands the loaded catalog; it does not add
unverified new providers or prove that every camera is currently playing.

The table preserves its selection and scroll while refreshing. A result for old
filters is discarded and the current filters load automatically. Unavailable
cameras are excluded from playback alternatives. Imagery still loads only on
request; updating catalogs does not start a stream or webcam.

**My cameras** now filters by name, host or media type, has both scrollbars,
and preserves selection/scroll on refresh. Partial names that match more than
one registered camera require a full name. Passwords preserve spaces exactly,
while the password field is cleared after an add attempt. Existing exact-address
registration and credential-store handling remain in effect.

## Beginner

The camera starts **OFF**. Check it without opening it:

```text
/camera status
/camera devices
```

Use `/camera on`, `/camera observe`, and `/camera off` only when you intend to. The latest hardware check found one integrated camera. Camera OFF releases the OpenCV device handle.

## Consent

Asking for a look **is** the consent for that look. Saying "Look at me", "Can you access the camera and look at me?", or "What do you see?" performs one observation immediately -- you are not asked to approve the thing you just asked for.

That consent is deliberately narrow. It authorises exactly one bounded `camera_observe` call (open, capture four spaced frames, compare clarity/OCR, interpret the clearest three locally, delete every frame, release the device), is single-use, expires after 120 seconds, and is recorded in the approval audit trail with `scope=explicit_user_request`. It does **not** cover:

- `camera_capture` -- writes a retained image file
- `camera_watch_change` -- samples continuously
- `camera_on` -- leaves the device open past the observation

Those actions remain explicit user operations. `camera_use` is `ALLOW` for the
local owner under Trusted Local Owner mode, while privacy mode overrides it and
background/untrusted principals cannot inherit it. A retained capture or watch
still has to be the requested action; allowing the category is not permission
for hidden activation.

Follow-up questions reuse what was already seen rather than reopening the device: "How do I look?" answers from the last observation, while "Look again" or "What do I look like now?" forces a fresh capture.

## Privacy behavior

- No hidden activation or background recording exists.
- Raw video retention is off.
- Observation frames use temporary files and are deleted after OCR/vision.
- An explicit still capture may be retained as the requested artifact.
- Agent tool activation is governed by `camera_use: ALLOW` for the local owner,
  principal isolation, explicit intent, session bounds, and privacy override.

## Intermediate: processing pipeline

`CameraManager` uses `CameraDeviceDiscovery`, `CameraCapture`, local OCR, and the existing `VisionAnalyzer`. A normal observation ranks four frames by sharpness, sends at most three to local vision, and exposes all frame-level interpretations so the answering model can compare stable details and changes. `watch_change` first computes a cheap grayscale frame difference and invokes expensive interpretation only after a meaningful change.

```text
camera -> short frame sequence -> clarity ranking + enhanced OCR -> local vision comparison -> observation -> event
```

The current local vision route is configured.

### Movement or light? (2026-10-14)

`camera_watch_change` used to average the pixel difference against the first frame and call anything over the threshold a change. A lamp switching on and the webcam re-exposing both counted, while a person crossing one corner of the view could fall below the threshold. Each sample is now compared by [app/perception/camera/change.py](../app/perception/camera/change.py), in five steps:

1. The frames are reduced to 160 px wide and softened.
2. The whole picture's brightness is matched, which undoes auto-exposure.
3. Smooth lighting, such as a lamp or a screen's glow, is subtracted. This is the same method the motion tracker uses.
4. Lone noisy pixels are removed.
5. Changed pixels count only inside 10-pixel blocks whose structure changed (a correlation under 0.7). A brightness jump clips highlights and shadows, which no gain undoes, but it leaves the shapes the same.
6. The changed area is measured, and so is how many regions of a 4 x 4 grid it covers.

The result names one of four kinds:

- `movement`: 0.2 % or more of the picture changed. The result gives where (for example "bottom left"), the box and the share of the picture that changed.
- `lighting`: the brightness moved but no detail did.
- `whole_picture`: 80 % or more of the grid's regions changed, meaning the camera moved or the scene was replaced.
- `none`: nothing changed.

`changed` is true only for `movement` and `whole_picture`. `peak_difference` (the old average) is still reported. Wi-Fi sensing's camera cross-check uses the same answer, so it can say "both saw it, in the bottom left of the picture" or "the camera saw only a change of light" ([Wi-Fi sensing](csi.md)).

## OCR backend

`app/perception/camera/ocr.py` tries two local, $0, network-free backends in order and reports which one is actually serving reads via `/camera status` and `MemoryDatabase`-independent `LocalOCR.status()`:

1. **`windows_media_ocr`** -- `Windows.Media.Ocr` through the official Microsoft `winsdk` WinRT projection. This ships inside Windows 10/11 itself: no download, no install, no subprocess per call. It is preferred whenever the current Windows user profile has a recognizer language installed, which is the default on essentially every real machine.
2. **`tesseract`** -- used only if a `tesseract` executable is found on `PATH`. Optional; not required for OCR to work.

Each read also tries contrast-enhanced and adaptive-threshold variants, merges
unique lines, and reports the best variant. Across a camera observation those
results are merged again across frames. If neither backend is available,
`read()` returns `{"available": False, ...}` rather than pretending to have
text -- callers must check `available` before trusting `text`.

```powershell
.\.venv\Scripts\python.exe scripts\ocr_benchmark.py
```

Renders eight synthetic images with known ground truth (log lines, error messages, file paths) at a realistic on-screen text size, reads each back through the active backend, and scores character/word accuracy (Levenshtein-based) and per-call latency to `data/benchmarks/ocr/latest.json`. On this machine with `windows_media_ocr`: ~89% mean character accuracy, ~86% mean word accuracy, ~16ms mean latency, $0 cost. Accuracy is weakest on strings with backslashes/paths and dense bracket-and-comma syntax; it is strong on natural sentences and plain log lines. Treat this as a directional benchmark, not a certified accuracy figure -- it covers eight synthetic cases, not a production OCR test corpus.

## Advanced: hardware check

```powershell
.\.venv\Scripts\python.exe scripts\camera_hardware_check.py
```

The script opens the configured camera, captures one frame, records timing/basic image statistics under `data/benchmarks/camera/hardware.json`, releases the handle, and removes the raw image. A dark frame may be valid if the lens is covered or room lighting is low.

Add another backend by implementing the same open/read/release contract and retaining explicit state/error reporting. Never infer device names; enumerate Windows PnP metadata.
