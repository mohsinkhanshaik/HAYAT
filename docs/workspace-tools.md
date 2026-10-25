# Screen, computer and Replica workspace

**Studios → Local image tools** provides manual offline inspection and duplicate
comparison in a compact, scrollable window. **Image research** checks publication
evidence separately. See setup, chat examples and live checks.

Open **Studios → Screen & computer**, **Person detection**, or **Replica app studio**.
They share a resizable window with three tabs. Replica forms scroll at compact
window sizes; tool work runs in a background worker and results return through
the Tk event loop. Closing the workspace stops its screen session.
The themed progress indicator and temporarily disabled action buttons make
background work visible; **Stop** stays available during screen reads.

For the upgraded brain model, open **Brain 5D → Appearance → Detail**.
**Ultra** retains all 4,850 triangles from the reviewed source; **Auto** chooses
Normal or High for the available canvas. Dragging, coasting or camera easing
temporarily uses Low detail; the requested detail returns when the camera
settles, including with animation paused. **Reset 3D view** restores the camera.
At short window heights, **Show history / Show 3D** keeps the model and timeline
usable in separate views instead of squeezing the model out of the window.

The **System & self** page's **Refresh self**, or chat command
`show your self-view`, reports observed task evidence: current work separately
from the previous task, verified/failed/unchecked attempts, missing goal checks
and conflicting completion claims. Attempt counts include retries; they are
not invented completion percentages. This is operational self-description,
not a measurement of subjective experience.

## Live screen reading

Choose an optional window title and **Read once** or **Start live (10 min)**.
Auto mode tries Windows accessibility first and falls back to local OCR when
the app exposes no page content. OCR uses window-handle capture, so an editor
covering the target does not become the captured image. Native window lookup
also works across reader threads when a UI Automation COM object is unavailable.
Accessibility reads create their COM objects on the reading thread and release
them there, rather than reusing objects whose first worker has already exited.

Only the latest text is held by the reader, with timestamps, change counts
and a content hash. Temporary screenshots are removed after OCR. **Stop**,
expiry, privacy revocation and runtime shutdown clear its latest text. An
already-running capture is discarded if Stop arrives before it completes.
Errors do not reuse a previous frame's text. Sessions last at most one hour;
the default is ten minutes. Polling is bounded to 0.5–30 seconds.

Reading does not click or type. Screen content is untrusted data. Accessibility
reads exclude password controls. Text returned through chat or a tool may still
appear in HAYAT's normal conversation/audit history; this is not a claim that
all downstream logs are ephemeral. OCR can misread text and protected/minimized
windows may be unreadable.

Chat commands: `read my screen`, `start live screen reading`,
`screen reading status`, `stop screen reading`. Desktop tools retain their
existing fresh-observation checks and PyAutoGUI failsafe. A changing desktop
can reject an observed click; obtain new evidence before retrying.

## Person detection

Choose a local image or explicitly **Read camera once**. The pinned
[Torchvision SSDLite320 MobileNetV3 COCO model](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.detection.ssdlite320_mobilenet_v3_large.html)
runs on the CPU and returns person boxes, scores and a count. It does not
identify people or infer demographic traits. Small, dark, occluded and unusual
views can be missed; false positives are possible. A zero count does not
establish that nobody is present.

Weights are downloaded only during setup, verified by SHA-256, then loaded
locally with `weights_only=True`. They are installed at
`data/models/people/ssdlite320_mobilenet_v3_large_coco-a79551df.pth`.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-perception.txt
.venv\Scripts\python.exe scripts/setup_person_detection.py
```

Camera inference deletes its temporary frame even on failure and releases a
camera that was idle before the request. It does not start background recording.
The separate one-shot camera check captured 1280×720, completed local inference
and shut down without errors. That frame returned zero detections; this does
not validate recall for a person standing in that camera view. The temporary
frame was deleted. Report: `.test-runs/camera-person-verification.json`.

Chat: `person detection status`, `detect people in camera`, or
`detect people in "D:\AI\data\work\photo.jpg"`.

The reply reads as plain detail, not raw JSON: how many people, each one's
confidence as a percentage (a 0.9973 score reads as 99.73%, never a misleading
100%), roughly how much of the frame it fills, and where (centre, top-left and
so on). It states plainly that this finds *that* a person is present, not who.
To compare people you have **enrolled**, open **Studios → Enrolled people (local)**
and explicitly confirm consent for enrollment. The encrypted gallery gives raw
cosine similarity and keeps close ties ambiguous; HAYAT never identifies an unknown person from a
face. To find where an exact image file is published publicly, use image source
search. These are three separate, bounded tools, and none of them searches for
a person's identity.

See the local recognition upgrade for
storage, legacy-gallery migration, model checks and the limits of these scores.

## Replica on HAYAT

The [Replica pack](https://github.com/Jakeschincariol/replica-skill), pinned at
`77c9436fb3d18c3d58169efb8caf4fe906b0dc51`, is bundled under
`app/vendor/replica_skill/`. Original MIT license, files and per-file hashes are
preserved. HAYAT's adapter lives in `app/replica.py`; no Claude installation,
Claude account, Claude executable or Claude-specific skill directory is required.

All eleven workflows are available: recon, architect, design, build, backend,
test, diff, entrepreneur, brand, launch and deploy. **Prepare workflow** writes
adapted instructions and upstream format templates in the chosen project, and
reports missing prerequisites. **Generate files** calls HAYAT's configured model
with a JSON schema, local prerequisite context and, optionally, one bounded
public reference page. Privacy and network policy can block that reference.

Use **Browse...** to choose a project. The workflow goal starts empty and must
contain 1–8,000 characters; invalid goals do not create workflow files or call
the model. Selecting a checker shows its relevant inputs and default project
path, while remembering a custom path for each checker. Checker file pickers
accept files inside the chosen project. A failing checker displays **Checker
needs attention**, including when the checker process itself ran successfully.

The adapter validates all planned paths and formats before writing any generated
artifact. Explicit HTML IDs requested in the goal receive one bounded repair
attempt if missing, before generated files are written. Files stay within the chosen project; repository metadata, credentials
and dependencies are excluded. Existing files receive backups. Each generation
has a manifest listing hashes, backups, model, pinned source and status. A
truncated response is rejected. A filesystem failure can leave partial writes;
the manifest records them rather than claiming a successful run.

Generation produces code and planning artifacts. It does not execute generated
code, run an app's tests, prove feature claims, send messages, commit changes or
publish an app. The test workflow writes test plans/specs. Deploy writes a
preflight document, with unperformed checks left pending. Build large projects
in bounded slices rather than expecting one generation to finish an entire app.

**Run checker** actually executes one of six pinned local checkers: feature
parity, image comparison, contrast, review themes, brand sweep and store listing.
Parity requires a score of 95 and all declared must-have rows complete; this
checks the matrix, not whether its claims are true. Image comparison requires
95 and offers layout mode (which deliberately tolerates palette changes) or
pixel mode. Contrast/listing/brand findings are reported as failures. A clean
review-analysis run does not independently authenticate the reviews supplied.
These are the pinned upstream rules; store/platform rules may change later.

Chat examples:

```text
replica workflows
prepare replica recon in D:\AI\data\work\my-app
run replica recon in D:\AI\data\work\my-app: Plan an original offline task list
```

## Verification

- `tests/test_workspace_tools.py`: real checker subprocesses, screen lifecycle,
  chat routing/execution, permissions, path constraints, camera failure cleanup,
  artifact backup/validation and model-output limits.
- `tests/test_gui_workspace_tools.py`: actual compact window captures, scrolling,
  worker delivery, a late read discarded after Stop, and destruction cleanup.
- `scripts/verify_workspace_tools.py`: actual local OCR on a generated window,
  a guarded click received by its button, a live read/stop, and installed-model
  inference on a supplied pedestrian fixture and three variants. Report:
  `.test-runs/workspace-hardware.json`. Three people were detected in the original,
  mirrored and reduced-size fixture; the blank fixture returned zero. This is
  four fixture checks, not a general accuracy benchmark.
- `tests/test_live_workspace.py`: opt-in generation with HAYAT's real model,
  followed by an isolated browser check of the generated counter app's actual
  mouse and keyboard behavior, with networking blocked. Its result is separate
  from the generation manifest's honest `tests_run=false` status.
  The actual run passed: the generated app started at zero, three button clicks
  produced three, and keyboard Enter produced four, with no browser errors.
  Reproduce with `python scripts/verify_live_replica.py`; each new run keeps its
  own report directory. The original successful run is under
  `.test-runs/live-replica/` and `.test-runs/live-replica-build.json`.

The upstream Replica checker suite also passed all 57 tests before integration.
See pending checks for the broader work and final reports.
