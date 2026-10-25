# Tools

Every tool has a name, description, JSON input schema, risk, permission category, and handler. The registry validates arguments before permission checks and execution.

Implemented groups:

- Filesystem: list, read, write, append, create folder, copy, move, search, exists, info, and approval-gated deletion. A file named without its folder ("read settings.yaml") is found by name inside the allowed folders and the answer says where it was ("found it at config/settings.yaml"); several matches are offered as "Did you mean …", never guessed. Reading a folder or listing a file says which tool fits instead of failing.
- Execution: Windows command shell, Windows PowerShell, generated Python, and existing project Python scripts with timeout/stdout/stderr/exit code, secret-filtered environments, bounded output, and process-tree termination on timeout.
- Browser: launch, navigate, DOM-visible text, structured headings/links/tables/metadata, accessibility snapshot, tabs, find, click, type, scroll, URL/title state, screenshot, quarantined download with SHA-256, and close.
- Research: provider-neutral search, safe public fetch/API GET/download, bounded crawl, claim-validated synthesis, durable knowledge search/explain/export/import, original-paper analysis, public GitHub deep inspection, and opt-in source change monitoring.
- Windows: screenshot, mouse movement/click, typing, key press, hotkey, and scroll via PyAutoGUI.
- Desktop controls by name (Windows UI Automation, the layer screen readers use): `ui_read` reads a window's title and visible text, `ui_elements` lists its buttons, menu items, boxes, checkboxes and tabs with position and whether they are enabled (both read-only, `computer_observe`), `ui_click` presses a control by its name and `ui_type` types into a named box and reads it back (both `computer_input`, approval required, exactly like the coordinate tools). Names work wherever the window is and whatever the display scale, unlike screen coordinates. An exact name wins over a partial one; several matches are listed and an index is asked for; a greyed-out control is reported, not pressed. A single-line box (a file name, a search field) is replaced; a document or multi-line editor only ever gets text at its cursor and is never wiped. With no window named, the target is the topmost window that is not HAYAT. Plain requests route straight to them: "click the Save button in notepad", "type 'report.txt' into the file name box", "read the notepad window", "what buttons are in the calculator app". "Click here" or "click 500, 300" stay with the pointer tools.
- Applications: discover and launch only approved Chrome, Edge, VS Code, Notepad, Calculator, File Explorer, WhatsApp, Word, Excel, PowerPoint, Outlook, OneNote, Teams, Discord, Zoom, Telegram, Signal, and Spotify targets (an app with no plain executable -- a Store package such as WhatsApp, or a Squirrel install such as Discord -- is launched through its Start-menu entry in `shell:AppsFolder`; only the desktop window counts, never the web version in a browser tab; an app that is not installed is reported as unavailable). Names that are also ordinary words (Word, Excel, Teams, Zoom, Signal, Edge) are recognised only after a verb or preposition such as "open" or "in", so "zoom in" and "edge cases" are not requests to open an app; observe exact window state; edit and read back a targeted Notepad document; close one exact window while preserving unrelated instances and pausing for unsaved-work choice. Arbitrary executables are rejected.
- Contacts and messaging: `contact_save`, `contact_find`, `contact_list` and `contact_delete` keep a local contact book in `data/contacts/` (never committed). Numbers are stored with their country code; a number without one is refused rather than guessed. "Save mom's number as +44 7700 900123" saves a contact. `whatsapp_send` opens `whatsapp://send` so WhatsApp Desktop shows that chat with the message filled in, then presses Enter only if the WhatsApp window is confirmed in front; otherwise the message is left typed and unsent with the reason. It reports that the message was sent, not that it was delivered. It is `send_message`, so it runs only when the owner's own message asked to send ("send mom a message on WhatsApp saying I'm late"), which the router plans straight to this tool. `email_send` sends from the owner's own account over SMTP to a contact or an address, with optional attachments read through the same path permission check as every file tool; the password is leased from the Windows credential store for one send (see `email.*` in configuration). A request with explicit words ("email Ahmed: meeting moved to 5", "... saying ...") is planned straight to it; "email Ahmed the report" goes to the planner, because it means attach the report.
- Isolated execution: `sandbox_run_python` runs untrusted Python in a disposable `python:3.13-slim` Docker container (`--network none`, `--read-only`, `--cap-drop ALL`, `no-new-privileges`, user 65534, 512 MB, 1 CPU, 128 processes, a time limit enforced inside the container, removed afterwards). It refuses when Docker Desktop is not running; it never falls back to the host.
- Vision: `describe_screen` captures a fresh screenshot, submits it to the configured local vision model, and verifies both the image and non-empty interpretation before completion.
- Images: `generate_image` uses the installed local diffusion engine with fast/balanced/quality sampling presets and photo/cinematic/illustration/concept guidance; `image_generation_status` reports the actual engine/model; `image_generation_history` lists retained, hashed artifacts. Completion requires a real PNG on disk, not only a successful wrapper return. The shipped checkpoint remains the actually installed SD 1.5 model; quality controls were improved, but no unsupported replacement model is claimed.
- System: read-only platform, Python, processor, and disk status.

Playwright DOM selectors are preferred over coordinates. Browser click results observe URL and title. On the desktop, named controls (`ui_click`) are preferred over coordinates for the same reason. Windows coordinate actions are approval-gated and should follow a new screenshot observation.

A failed step that cannot succeed by being repeated (`FileNotFoundError`, `NotADirectoryError`, `IsADirectoryError`, an address that is really a file name) goes straight to re-planning instead of using its retries; a passing failure such as a timeout keeps them. A web address whose "host" is a file name (`https://settings.yaml`) is refused with a pointer to `read_file`.

Downloads require approval, remain under `data/artifacts/downloads/quarantine`, and are never executed by the download tool.

Research downloads use the separate `public_download` permission, are hash-addressed under `data/knowledge/sources`, and are likewise quarantined and non-executable. Research URL validation blocks private/local networks; browser navigation remains a distinct tool and permission boundary.

The `/tools` command displays live registry metadata. Counts vary with configured vision/model capabilities; `/tools` is authoritative for the current process. The model-independent list, risks, and permissions are generated in [runtime-reference.md](generated/runtime-reference.md). Model-facing schemas contain no Python handler objects.

Recent governed groups include camera lifecycle/observation, Win32 displays, ephemeral clipboard operations, public hashed downloads, read-only incident snapshots, and safe local lab operations. They reuse the same registry and permission boundary; they are not side-channel automation.

## Tool contract audit (2026-10-17)

`audit_tool_contracts` (also part of `/tools benchmark`) reads every registered tool's declaration
without running anything. It checks that:
- the schema is valid JSON Schema, and each declared default fits it;
- every argument the schema allows is one the handler accepts, and every argument the handler needs is required;
- every handler that rejects unknown arguments declares that, so a bad call fails before authorization.

It also checks each tool against the authority (`app/safety/authority.py`):

| Check | Level | Why |
|---|---|---|
| Permission category unknown to the authority | error | It falls back to the extended tier. A sending tool with a misspelt category (`send_mesage`) would silently lose its always-ask protection. Shell categories (`terminal`, `powershell…`) are resolved per command, and are accepted. |
| Risk not `low`, `medium` or `high` | error | Unknown risk |
| A refused-outright category (`purchase`, …) | error | The tool could never run |
| An always-ask category marked low risk | warning | The risk understates it |
| A description under 25 characters | warning | Too thin for the planner to choose the tool well |

The full-runtime test requires no errors and no warnings. On 2026-10-17, 551 tools passed after five
one-line descriptions (`browser_scroll`, `contact_list` and three wallet "remove" tools) were written out.
