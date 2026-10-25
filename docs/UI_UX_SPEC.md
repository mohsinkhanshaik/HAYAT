# D:\AI — UI/UX specification

Design contract for the local desktop interface. Written before implementation so the UI is built to a plan rather than assembled ad hoc. Nothing here is implemented yet; this document is the agreement, not a status report.

## 1. Product principle

The interface exists to let someone talk to their assistant. It is not a control panel for the Python modules underneath. A person should be able to use it without knowing what Ollama, VAD, a tool registry, an approval broker, or VRAM are.

The normal path is: open → type or speak → the assistant understands → it acts → it shows the result. Everything technical is reachable in one click and visible in none by default. Progressive disclosure is the rule, not a nice-to-have.

## 2. Framework decision

**Not yet decided.** It must be chosen with evidence, and the comparison must be run on this machine rather than argued from familiarity. Candidates and the axes that actually matter here:

| Axis | Why it matters for this project |
|---|---|
| Startup time | The runtime already takes seconds to warm; the window must appear before it |
| Threading model | The runtime is thread-heavy (voice listener, task worker, TTS). The UI must integrate with an event bus without blocking |
| Camera preview | A live preview must not fight the capture pipeline for the device |
| High DPI | This machine drives a 1920x1200 and a 3840x2160 display simultaneously |
| Accessibility | Screen-reader labels and keyboard navigation need native support |
| Packaging | Must stay inside the existing `.venv` discipline and zero-cost policy |
| Idle cost | The UI must not consume GPU that inference needs |

Candidates to measure: PySide6/Qt Widgets, PySide6/QML, and a localhost-bound web UI. The decision and its measurements get recorded here before any UI code lands.

## 3. Information architecture

```
┌──────────────────────────────────────────────────────────────┐
│  AI                    Local • Ready              ⚙  ◑  ⋯    │
├──────────┬───────────────────────────────────────┬───────────┤
│          │                                       │           │
│ ▸ Chat   │           Conversation                │ Context   │
│ ▸ Tasks  │           (primary surface)           │ (collapsed│
│ ▸ Memory │                                       │  by       │
│ ▸ Research                                       │  default) │
│ ▸ Settings                                       │           │
│          │                                       │           │
│ ──────── │                                       │           │
│ Privacy  │                                       │           │
├──────────┴───────────────────────────────────────┴───────────┤
│  [+]  Ask anything…                    [Camera] [Mic] [Send] │
└──────────────────────────────────────────────────────────────┘
```

Conversation gets the majority of the width. Both side panels collapse. The right panel is closed until something needs it (sources, provenance, task detail).

### Voice active

```
│                                                              │
│   ●  Listening                                    [Stop]     │
│      ▁▃▅▇▅▃▁                                                 │
```

State is named in words, never by colour alone, and never as an internal enum (`SPEECH_DETECTED` belongs to Developer Mode).

### Camera observation card

```
┌──────────────────────────────────────────────┐
│ 📷  CAMERA · just now                        │
│                                              │
│ I can see you sitting in front of an open    │
│ closet. The lighting is a bit dim.           │
│                                              │
│ [View details]                               │
└──────────────────────────────────────────────┘
```

Details (collapsed): captured time, resolution, vision backend, local-only processing, network used, raw frame retained.

### Research result

```
┌──────────────────────────────────────────────┐
│ Answer                                       │
│ Python 3.13 was released on 7 October 2024.  │
│                                              │
│ Sources                              3 used  │
│ ┌────────────────┐ ┌────────────────┐        │
│ │ docs.python.org│ │ python.org     │        │
│ │ retrieved today│ │ retrieved today│        │
│ └────────────────┘ └────────────────┘        │
│                                              │
│ Confidence: moderate · [Show evidence]       │
└──────────────────────────────────────────────┘
```

### Approval

```
┌──────────────────────────────────────────────┐
│ Permission required                          │
│                                              │
│ AI wants to modify 2 project files.          │
│ Risk: Low · Reversible via checkpoint        │
│                                              │
│ [Show changes]        [Reject]  [Approve]    │
└──────────────────────────────────────────────┘
```

Approval cards appear only for actions that genuinely need one. An explicitly requested camera look, a requested web lookup, or a status check must never produce one — the request is the consent.

## 4. Design tokens

Defined once, referenced everywhere. No arbitrary pixel values in components.

- **Spacing**: 4, 8, 12, 16, 24, 32
- **Radius**: 6 (controls), 10 (cards), 16 (surfaces)
- **Motion**: 120ms (state), 200ms (surface), 240ms max; all suppressed under reduced-motion
- **Control height**: 32 (compact), 40 (default)
- **Type scale**: display 24, section 18, body 14, secondary 13, caption 12, mono 13

**Surfaces** are layered rather than pure black: `background` → `surface` → `surface-elevated` → `border`. **Text** is `primary` / `secondary` / `muted`. **Status** is `accent`, `success`, `warning`, `error` — each paired with an icon and a word, because colour alone must never carry meaning.

One accent colour. Active controls and selected navigation use it; nothing else does.

## 5. Component inventory

`AppShell` · `Sidebar` · `TopBar` · `ConversationView` · `MessageBlock` · `Composer` · `VoiceStatus` · `CameraStatus` · `TaskStatus` · `ApprovalCard` · `ResearchCard` · `SourceCard` · `ProvenancePanel` · `SystemPanel` · `ErrorCard` · `Toast` · `CommandPalette`

No single-file UI. Each component owns its own states: default, hover, focus, active, disabled, loading, empty, error.

## 6. State model

The UI is a renderer of authoritative runtime state. It must never infer state by parsing the assistant's natural-language output — camera on/off, voice state, task state, and approval state all come from structured events.

```
runtime → event bus → UI state store → components
```

`app/output.py::OutputCoordinator` already exposes `add_listener(source, text)`, which is the seam a UI subscribes to for conversation content. Structured lifecycle events (task, approval, camera, voice) still need to flow through the same coordinator or a sibling channel before the UI can be built — see §9.

## 7. Keyboard

| Key | Action |
|---|---|
| `Enter` | Send |
| `Shift+Enter` | Newline |
| `Esc` | Stop / cancel current activity |
| `F11` / `Ctrl+Shift+F` | Toggle full-width Focus Mode / restore both rails |
| `Ctrl+K` | Toggle camera |
| `Ctrl+L` | Look through the camera |
| `Ctrl+D` | Describe the current screen |
| `Ctrl+T` | Open tools |
| `Ctrl+M` | Open memory |
| `Ctrl+P` | Toggle privacy mode |
| `Ctrl+H` | Open health |
| `Ctrl+E` | Export the transcript |
| `Tab` | Move focus (visible focus ring required) |

Everything important is reachable without a mouse. Slash commands keep working in the composer for parity with the CLI, but are never required.

## 8. Copy rules

Write what happened, not that something happened.

| Never | Instead |
|---|---|
| "Done. I verified the requested result." | "Notepad is open." / "I found three relevant sources." |
| "Execute camera_observe operation?" | "Use the camera for one look?" — and not at all if the user just clicked Look |
| "An unknown error occurred." | "The camera couldn't start." + Details |
| "Task has been successfully completed." | Name the deliverable |

One canonical term per concept: **Chat, Voice, Conversation Mode, Camera, Gesture Control, Research, Memory, Tasks, Privacy, Settings, Developer**. No synonyms.

## 9. Prerequisites before UI code

The UI is blocked on plumbing, deliberately. A polished shell over a broken conversation core is a worse outcome than no shell.

- [x] **OutputCoordinator** — one serialized owner of console output; worker threads publish rather than print. *Done; 150-event concurrent stress test green.*
- [ ] **TurnRecord** — a canonical per-turn record (ids, intent, tools, provenance, latency, status).
- [ ] **AssistantResponse** — one structured response object that CLI, voice, and UI all render, replacing today's divergent response paths.
- [ ] **Structured lifecycle events** — task/approval/camera/voice transitions on the bus, not just text.

## 10. Definition of done

Milestone 1 is complete only when the UI launches against the *same* runtime as the CLI, typed conversation works, messages never duplicate or disappear, Stop works, system state is authoritative, the window stays responsive under load, dark and light both work, and the CLI is unbroken.

"Buttons exist and the background is dark" is not the bar.
