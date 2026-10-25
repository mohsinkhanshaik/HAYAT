# World updates and context recovery

Updated October 10, 2026.

HAYAT now routes broad current-world requests covering multiple sectors through source-backed research. It separates evidence from conditional effects, retains the subject through diagnostic exchanges, and fits reference context to the selected model's real window.

## The reported problem

The owner requested detailed developments in industry, politics, finance and natural events, then asked how those developments could affect his future and the world. The first response repeated hazard-heavy event lists, included duplicate empty results and did not provide balanced sector analysis. It also said "showing 50" while rendering only 12 records. The follow-up failed before inference with an essential-context overflow. "Go ahead and fix it" became a system-information task; "try again now" did not retry the failed question.

The context builder placed full runtime and situation snapshots inside system-role messages. The final fitter treated every system message as mandatory, including large reference data and conversation recaps. It reserved output/headroom before measuring the actual essential prompt. Consequently, optional data could make a short request fail. The diagnostic path knew how to repeat an earlier tool task, but did not resolve these phrases to the recorded failed question. GUI error recording could also overwrite the shared boundary's more precise failure/request.

## Ask for an update

For example:

```text
Research current developments in industry, politics, finance and natural events around the world in deep detail. Explain the evidence, possible future effects and what to watch.
```

Requests naming at least two supported sectors, an update/current-news need, and a world/global/future scope use the world-brief route. Specific event lists, camera requests, historical essays and local file/document edits retain their existing routes.

The world brief checks each requested sector separately within one shared research budget. It uses the existing public search, retrieval, injection defense, provenance, claim verification and citation machinery. It does not grant new authority, enable paid inference or bypass privacy/local-only mode. A slow request still uses the existing background job and delivery mechanism.

Each sector includes source-backed observations when available and clearly labelled conditional transmission channels: production and hiring, trade and energy, household/borrowing conditions, or safety and supply disruption. Missing sources, exhausted budgets and missing publication dates become explicit gaps. Fetching a page today does not prove the event occurred today or remains active. Feed confidence labels and repetition do not establish independent verification.

Since October 14, 2026 each sector also lists the sources it used (up to five): the publisher's domain, the publication and update date, or "publication date not stated", the time it was retrieved and how many days old it is. When the newest dated source in a sector is more than 7 days old, the sector says "Stale data" and treats it as background, not as what is happening now. A claim the sources disagree on gets its own "Conflict:" line with the count of supporting and contradicting sources; neither side is presented as settled. A closing "Coverage:" line says how many sectors were checked with sources, names the gaps, the stale sectors and the number of disputed claims. A provider outage or exhausted budget remains a named gap, never a current-event statement.

The personal-effects section states that location, occupation, financial exposure and time horizon are not established by the reports. It suggests what evidence to monitor over different horizons and separates possible scenarios from observed outcomes. It does not invent personal circumstances, numeric forecast odds, guaranteed earnings or an investment recommendation. This is partial research coverage, not an exhaustive view of every world event.

## Follow up and recover

```text
Explain this and how it could affect my future and the world.
What was wrong?
Go ahead and fix it.
Try again now.
```

"Explain this" now resolves as a referential follow-up. For world-effect questions, the latest substantive world report remains the reference when intervening messages only explained an error or reported system status. Large reports are excerpted without changing the stored full conversation; structured world excerpts retain material from every covered sector.

If world research is still running, a referential follow-up reports that the evidence is not ready rather than inventing a completed analysis. It preserves the research subject so the finished job can update the evidence audit and deliver its report.

Runtime snapshots, situation summaries and generated recaps carry internal optional-context metadata. The final fitter can omit them while retaining identity/safety instructions and the current request. It measures mandatory content before fitting output space; a small window can use a smaller output ceiling/headroom while retaining a minimum margin. It never silently enlarges the provider's configured window or cuts the user's request.

If actual instructions plus the current request still cannot fit, the error is recorded as `RESOURCE_EXHAUSTED`, with dependency `model context`, instead of `UNKNOWN`. Smaller reference context cannot solve a genuinely oversized request or an incompatible model window.

For a recorded context failure in the current session, repair/retry phrases retry the failed read-only question or research request. They do not replay a side-effecting action or another session's request. A failed retry retains the substantive failed question; the GUI preserves the shared boundary's classified record. "Fix it" in this path means retry through the corrected runtime, not a claim that model prose rewrote code or retrained weights.

Repeated successful event/search reads of the same scope display the latest result. Different categories, places and queries stay separate. Identical rendered sections are deduplicated, and the event-list count now matches the number actually displayed.

Desktop layout now uses the window's current dimensions and stops an older resize handler from overwriting a newer layout. Resize handlers arrange rails without recursively pumping geometry updates. Short windows use primary controls with secondary actions in menus at normal and enlarged text sizes, accounting for Windows DPI scaling. The compact header shows the greeting and **Workspace** menu; hardware details remain in the overview/system views and wallet information in **Business**. These changes repair a compact-window failure exposed by the broader GUI checks. The short-window live-state strip is limited to two text lines so a wrapped sensor/status label cannot consume the conversation area. Hover over that strip or open **Activity** for the full camera, microphone, feed, learning and privacy state. Taller windows retain the expanded strip.

The audit follow-up also keeps complete
recent conversation exchanges together when fitting history. An oversized
exchange stops older history from being reintroduced with gaps; optional
state/recaps remain independent. Failed greeting repairs return an incomplete
answer notice and preserve cancellation.

## Verification and practical limits

[tests/test_world_followup_repair.py](../tests/test_world_followup_repair.py) reproduces the original request, oversized reports/snapshots, follow-up, diagnostic/retry phrases, cross-session protection, missing-sector evidence, shared budgets, event deduplication and displayed counts. Prompt tests cover 4K, 8K and 16K windows. Wider checks cover conversation, routing, providers, research, intelligence and failure handling.

The opt-in real local HAYAT check uses labelled fixture reports and an isolated temporary conversation database. It verifies that an oversized report follow-up completes through `hayat:latest` on port 11435 and acknowledges missing personal context/conditional effects. It does not verify current world-news facts or establish general model accuracy. Local JUnit evidence is saved under `.test-runs/` and may be absent from a source checkout.

The October 10 repair regression passed **458 tests**, with the opt-in model check skipped in that run. That real-model check passed separately after restoring the folder service. Evidence files are `.test-runs/world-repair-regression-20261010.xml` and `.test-runs/world-repair-live-20261010.xml`. GUI tests also clean up progress widgets left by stubbed workers and wait for Windows geometry to settle, while retaining their transcript-size and control-accessibility assertions.

The final combined GUI/documentation run passed **196 tests** in 72.16 seconds, including the compact-window and enlarged-text cases that failed during investigation. It also checks a stale resize event, full status text on hover, accessible action menus, navigation, themes and workspace state. A separate related layout/repair run passed **78 tests**, with one live-model skip; those tests overlap the other runs. GUI evidence: `.test-runs/world-repair-gui-docs-20261010.xml`.

Run the focused tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_world_followup_repair.py -q
```

For the separately opted-in local-model check, first ensure the prepared folder service and `hayat:latest` are available:

```powershell
$env:HAYAT_RUN_WORLD_LIVE = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_world_followup_repair.py::test_actual_local_hayat_handles_the_long_report_followup -q
Remove-Item Env:\HAYAT_RUN_WORLD_LIVE
```

Restart an already running HAYAT window to load the code. Then repeat the world-update request and follow-up. Model interpretations can still be wrong; check dates, source contents, coverage gaps and consequential conclusions. This repair prevents the reproduced routing/context failures and improves evidence handling; it cannot guarantee every answer is correct.
