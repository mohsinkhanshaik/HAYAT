# Reverse engineering with REA

Open **Studios → Reverse engineering (REA)**, use the command palette, or open the
reverse-engineering entry in HAYAT's tools guide. The window can be moved and
resized using its normal title bar and borders. Its split panes let you give
the operation list or analysis more room.

HAYAT runs [REA](https://github.com/morluto/rea) as a persistent local MCP process.
The integration currently pins **rea-agents 6.7.0**, with its dependency integrity
hashes preserved in `integrations/rea/package-lock.json`. It does not silently
follow upstream's latest release. See upstream's
[installation guide](https://github.com/morluto/rea/blob/main/docs/installation.md)
for current provider and platform requirements; newer upstream features may be
absent from this pinned version.

## Use the workbench

### Start with an app link

Paste a public URL into **Target or app link** and press **Inspect link**
(or Enter). Chat also accepts `reverse engineer https://example.com/app`.

- A web-app page collects inline JavaScript and same-origin script URLs and
  runs REA's actual static JavaScript analysis on those local sources. A direct
  `.js`/`.mjs`/`.cjs` link works too. Page scripts are not executed.
- A native package/download URL, such as `.exe`, `.apk`, `.wasm` or `.asar`,
  prepares the existing **Open target** confirmation flow. It does not run the
  downloaded application or installer.
- A landing page or app-store listing exposes only its public page source.
  It does not provide private backend code or the native app binary.

Collection respects HAYAT's public fetcher, privacy mode, path permissions,
robots rules and cancellation checks between requests. It collects at most
eight scripts, makes at most eight script requests, reads a page/direct source
under 1 MB, limits each collected script to 500 KB and total source bytes to
5 MB. Literal same-origin imports/exports are parsed to depth two, and at most
two advertised same-origin source maps are retained as evidence. Bare/computed
imports, cross-origin scripts and login flows remain outside collection.
Source-map names are never used as filesystem paths or automatically fetched.
Skipped resources and analysis coverage remain
visible in the report. A directory's synthetic `package.json` identifies the
collection; it does not install dependencies.

The **Source evidence** tab lists the retained scripts/maps. **Verify selected
saved file** checks its size and SHA-256 against recorded evidence; **Cancel
link** stops subsequent collection and discards late results. See the
October 24 upgrade and checks.

The unique local workspace contains a `sources/` directory, its URL/size/SHA-256
manifest (`hayat-link-source.json`) and `analysis.json`. Larger returned reports
use an explicitly labelled display excerpt that points to the saved analysis.
Provenance and earlier analysis reports stay outside `sources/`, so they do
not become input app code in the next analysis.
If REA completed an analysis but its full result exceeds the MCP response
budget, HAYAT requests a bounded summary using that exact retained Evidence ID
and verifies the parent operation/reference. The summary preserves coverage
limits and unknowns. Full retained Evidence lasts only for that REA session;
the fetched sources and saved summary remain on disk for inspection/reanalysis.

Actual public checks passed on the official jQuery source and the jQuery UI
accordion demo page. The jQuery check exercised the transport-limit fallback;
the demo exercised HTML/inline source collection. These are static source
checks, not proof of the app's runtime behavior. Evidence:
`.test-runs/rea-public-links.json` and `.test-runs/rea-public-http.xml`.

### Start with a local target

1. Choose a file or extracted application folder.
2. Filter operations by name or description. For a JavaScript/Electron folder,
   choose `analyze_javascript_application` and press **Use target**.
3. Fill the guided **Inputs** fields. Required fields have an asterisk and
   descriptions appear on hover. **JSON arguments** retains all nested and
   conditional options. **Operation reference** shows the server's actual
   schema and description. Inputs are validated before dispatch; non-finite
   numeric values are rejected.
4. Press **Run analysis**. A worker performs the call through HAYAT's governed
   tool registry. Reading, selecting text and scrolling remain available.
5. Read **Summary** or **Full report**, use **Find next** / Ctrl+F to search,
   and **Save report…** to save the displayed report. The
   display is capped at 500,000 characters and explicitly marks truncation.

Small windows collapse the parameter editor to leave room for results. Toggle
**Show inputs** to edit it; starting a run in a small window folds it again.
Ctrl+Enter runs the selected operation. A progress indicator and elapsed time
remain visible while work runs.

**Engine status → Check engines** checks the bundled Node/REA installation and
the configured Ghidra/JDK versions, headless launcher, native decompiler and
Windows native controls. Missing or incompatible components include the
provider's repair instructions. The check runs in a worker, stops after 45
seconds, and does not open a target or replace your current report, history or
inputs. **Copy setup commands** copies the three project setup commands.
Chat/tool clients can request the same check through
`reverse_engineer_status` with `{"check_engine": true}`. The default status
remains a quick local inventory.

The recent-results selector keeps up to ten analyses within a two-million-character
report/summary budget for the current HAYAT session. It includes failures and
prepared actions. **Restore** loads a run's arguments without submitting it.
Results that finish after closing the window remain available when reopening
it. Switching operations preserves up to twenty argument drafts, including
unfinished numeric edits. This history and these drafts stay in memory; only
an explicit save writes a report to disk. Quitting HAYAT clears them.

Reading an older result while another run finishes preserves your displayed
text, selection and scroll position. Summaries report only fields and counts
returned by REA; they do not assert complete coverage or verified findings.

Chat and the workbench share one REA session. If an analysis is already active,
a second operation is refused with a busy message instead of changing its
target midway. Retry after the active operation finishes. Failed startup can
be retried once its worker exits; shutdown closes an unfinished session too.

Operations that open/close targets, execute/capture behavior, extract/export
files or change notes use **Prepare action** and the existing confirmation
window. Preparing an action does not execute it. Native sessions persist
between calls and close when HAYAT shuts down. Closing the workbench does not
cancel an already dispatched call or close a target being used by chat.

### Limits on what goes in and comes out

- **Arguments** must be plain JSON objects of at most 64,000 bytes. Anything
  else is refused before it reaches REA, with one plain error.
- **Results** larger than 8 MiB are refused. Narrow the request instead: one
  procedure, a search filter, or a smaller address range.
- **Credential-shaped values** in results are redacted with the same rules the
  MCP gateway uses. A key embedded in an analysed file shows as
  `[REDACTED:kind len=… sha256=…]`, so you can tell it is there without the
  value being kept in chat or memory. Ordinary strings and code are kept.
- **Environment:** REA runs with HAYAT's filtered environment, so API keys,
  tokens and passwords from HAYAT's own settings are not passed to it.
- **Errors:** an unknown tool, a broken connection, a call after shutdown, or a
  second call while one is running each returns a single plain error. The
  session stays usable.

Chat supports **Reverse engineer C:\apps\thing.exe**, **List the functions**,
**Show me the strings**, **Decompile main**, and **Reverse engineering tools**.
For operations requiring additional arguments, use the workbench or the
`reverse_engineer_read` / `reverse_engineer_act` tool schemas.

## Setup and limits

Run `python scripts/setup_rea.py` from the repository for a fresh install. This
uses the checked-in npm lock, disables package lifecycle scripts and installs
inside `runtime/rea`; it does not register REA with unrelated agents. HAYAT
uses its bundled Node executable. The setup script can copy a supported Node
installation from PATH and checks REA's pinned Node requirement before installing.
Setup bundles both Node and npm into `runtime/node`; later setup uses their
explicit local paths rather than requiring a system npm command. Node 25 and
other unsupported release lines are rejected. Installed version reporting
refreshes after setup; restart HAYAT when replacing an engine used by a running
session.

Static JavaScript analysis does not need a native decompiler. The October 17
dependency completion installed project-local Ghidra 12.1.4, Temurin JDK 21,
WABT 1.0.42 and JADX headless MCP 0.7.1. `ReaSession` discovers their verified
local paths and preserves explicitly configured alternatives. The real Windows
Ghidra workflow opened a generated x64 PE, listed its procedure and decompiled
its expected return value, with unchanged input bytes after cleanup. The
installed 6.7.0 MCP process exposes 207 operations. Other target families and
platform-specific operations have separate engine and authorization requirements.
Inspect only software you own or are authorized to examine.

Reproduce these installations with:

```powershell
.\.venv\Scripts\python.exe scripts/setup_native_analysis.py
.\.venv\Scripts\python.exe scripts/setup_analysis_extras.py
.\.venv\Scripts\python.exe scripts/setup_rea.py
.\.venv\Scripts\python.exe scripts/verify_native_analysis.py
```

The provisioners verify publisher SHA-256 hashes and retain source/identity
records beside each engine. The former 3.2.1 release refused Windows native
analysis despite installed Ghidra/Java; upgrading to the pinned 6.5.0 package
supplied its required native controls. Mutating debugger, Android, Frida,
annotation and extraction operations remain on HAYAT's confirmation path.

Initial 3.2.1 verification on October 17, 2026: the real local REA process exposed **114
operations**, analyzed a generated JavaScript fixture without executing it,
returned its required input schema, and reported a nonexistent target as an
error. A separate live check successfully inspected PE/CLI metadata in the local
Windows `System.Configuration.Install.dll` without executing it. This
demonstrates the bridge and these static workflows; it does not establish
that every operation, target format, runtime capture or native engine works.

The combined REA, public-data, research, routing, registry, permissions and GUI
regression run passed **588 tests** with one opt-in screenshot test skipped.
That screenshot test was run separately and reviewed at normal and compact
sizes. The passive contract audit checked **556** HAYAT tool declarations with
zero errors or warnings; that audit does not execute handlers or establish
live readiness for every tool. The local regression report is
`.test-runs/rea-public-data-regression.xml`.

The follow-up workbench enhancement passed **158 checks**, including a real
JavaScript analysis through the guided GUI and separately reviewed normal and
compact screenshots. It covers draft/schema refresh, numeric validation,
history retention after closing a running window, preserving older results
during completion, concurrent-call refusal and startup retry. Its report is
`.test-runs/rea-workbench-enhancement.xml`. Native decompiler prerequisites
were subsequently supplied and verified by the dependency completion above.

The October 18, 2026 follow-up adds a separate Engine status tab with bounded
Ghidra/JDK diagnostics and copyable setup commands, preserving the active
report, inputs and history. The local readiness check passed all ten scoped
checks. The expanded native verification passed thirteen read operations on
the generated x64 PE, including decompilation, assembly, instructions, callers,
callees and references. It did not execute the sample, and its SHA-256 stayed
unchanged. Results are in `.test-runs/native-analysis/verification.json`.

The serial regression run passed **189 checks**, including the opt-in studio
captures, real Ghidra workflows, Accounts tests and tool contracts. Final
provisioning and documentation checks passed **32 tests**; these overlap the
regression run and are not an additional count of unique tests. Ruff passed.
Accounts test waits now observe completed UI states and isolate clipboard
assertions from the shared desktop clipboard. No Accounts product code changed.
The regression report is `.test-runs/ghidra-upgrade-regression.xml`.

### October 18 release update

REA is now pinned and installed at **6.7.0**. The upstream
[release notes](https://github.com/morluto/rea/releases/tag/rea-agents-6.7.0)
include provider startup, cancellation/cleanup and client configuration fixes.
Ghidra **12.1.4** and the bundled Replica source already matched their upstream
latest release/commit when checked; neither was unnecessarily reinstalled.

`setup_rea.py` now installs the locked dependency tree into a separate local
stage, with lifecycle scripts disabled. It checks the package and actual
launcher version before activation. The previous installation is retained as
`runtime/rea-backup-<timestamp>-<id>`. An activation failure restores it; failed
stages are retained for diagnosis. A setup lock prevents overlapping installs.
If setup was interrupted, inspect `runtime/.rea-install.lock` before removing
that stale lock and retrying. Restart HAYAT after an upgrade.

The upgraded process passed actual JavaScript analysis and all thirteen native
Ghidra read operations on the generated x64 PE. Its expected return value was
decompiled and source bytes stayed unchanged. This verifies those workflows,
not every exposed operation or optional provider. Installation provenance is
in `runtime/rea/hayat-install.json`; see the update report
for checks and remaining limits.
