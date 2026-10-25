# Easier navigation and visible activity

The main window now keeps **All features**, **Activity** and **Stop task** in
sight, including in Focus Mode. The strip beside them shows the latest cached
camera and microphone states, public-feed monitoring, background learning,
privacy mode, live tool-call count and the latest recorded action.
On shorter screens, the Activity button carries the live-call count and the
latest recorded action stays in the Activity window, preserving room for chat.

## Find and use a feature

Open **All features** or press **Ctrl+Shift+A**. Search in ordinary words or by
an exact tool name. Filter Features, Workspaces, Controls, Studios or Tools.
The inventory comes from Hayat's capability catalog, declared GUI actions and
live tool registry, so newly registered tools are included automatically.

Select a feature to see what it does, how to start and examples. **Open feature**
uses its existing workspace. Features without a dedicated window instead offer
**Prepare request in chat**; the request remains editable until sent.

The Tools section also supports direct operations. It shows each tool's
description, permission scope, risk label and JSON argument contract. Enter
arguments and choose **Run selected tool** to execute that one operation.
Browsing, filtering and selecting do not execute tools. Invalid arguments stay
in the form with an explanation. Results appear in chat, even if the feature
window closes before the tool finishes. Each manual operation has its own call
budget and preserves the normal permission and authority checks.

**Ctrl+Shift+P** still opens Commands. Its search now includes the same feature
inventory; choosing a tool opens its details rather than running it immediately.
Tab, arrow keys, Enter and Escape work in the new windows. There are explicit
empty-search states and scrollbars. Local vector icons replace font-dependent
sidebar symbols and add distinct activity, globe, income, feature-grid, book and
task icons. Text labels remain beside icons; icons follow theme colors and the
configured text scale when rendered.

## Workspace tabs, suggestions and the intelligence map

The bar under the title has tabs straight to **Global Intelligence**, **Business**,
**Voice & Models**, **Research** and **Automation**. In a window narrower than
1,380 px they hide, so the live-state line stays readable; the sidebar and
**Studios ▾** reach the same places.

Above the message box, suggestion chips (World now, AI news, Business brief,
Write a report, Plan my day, Public cameras) put a ready request in the box.
Nothing is sent until you press **Send**. A window under 800 px tall hides them
to keep the conversation readable.

The **Global intelligence** card now sits in the right column, under system
resources. It shows a small world map of up to 150 of the newest located
events (cameras excluded), coloured and shaped by category and sized by
severity, followed by the three newest events and any watch alerts. Clicking
the map opens the intelligence workspace. It reads local snapshots only and
starts no network request. Moving it out of the centre gives the 3D workspace
more room.

## See unattended work

Open **Activity** or press **Ctrl+Shift+L**. It lists recorded tool starts,
completions and failures, model/research/service activity, connector calls,
authority decisions, sensor events and local intelligence alerts. Select a row
to inspect its recorded arguments, event identity, task correlation and scope.
Credentials are redacted. The time column is explicitly UTC. Search filters the
recorded details as well as the visible description.

The view retains the latest 500 relevant live events from the current GUI
session, with bounded details. The existing durable event journal remains the
history source; this view does not claim to audit unrelated applications or
unrecorded operating-system actions. A missing event bus is shown as unavailable.
Live tool counts distinguish overlapping operations. Background work remains
visible when its workspace closes.

**Export incident bundle** saves one zip to `data/artifacts/incidents/` for
looking into a problem later or handing to a helper. It holds the activity rows
the search box currently shows (`activity.json`), the latest 50 recorded
failures, the live camera/microphone/feed/learning/privacy line, the last
200 KB of `data/logs/hayat.log` and a read-only `incident_snapshot` of
resources and AI components. Every part goes through the same redaction as the
logs. `manifest.json` names where each part came from, whether it was
collected, its size and SHA-256; a part that cannot be read is listed as not
collected instead of stopping the export. While it runs the button reads
**Cancel export**; a cancelled export leaves no file. Collecting it changes
nothing on the PC.

**Stop task** requests cancellation of the current agent task and speech.
Individual synchronous tool calls finish or reach their own timeout; cancelling
an agent plan cannot undo a completed action. Public feeds have a separate
start/pause control in Activity. **Background learning** opens its existing
queue and pause controls. Closing Activity leaves monitoring running.

This upgrade changes visibility and navigation without adding an approval
step to routine unattended monitoring. UI event subscriptions are released when
Hayat closes, and worker threads deliver results through the GUI queue.

Regression checks cover inventory completeness, keyboard handlers, search-only
behavior, validated tool execution, isolated budgets, denied permissions,
concurrent tool counts, credential redaction, selection preservation, bounded
history, scaled icons and normal window lifecycle.
