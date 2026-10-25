# Public onion navigation

HAYAT can read a known public v3 onion URL and optionally follow links on that same origin. This is separate from the existing Onion (Tor) discovery search. No search or download starts when the window opens.

## Use the window

1. Start HAYAT with `python run.py` and open **Onion navigator** from the main GUI or Searches window.
2. Paste a publisher-listed `.onion` URL and press **Read page**. HAYAT checks the installed Tor service and fails closed if it is unavailable. Privacy, local-only mode and emergency stop still block network use.
3. Read **Page evidence**, including its actual final URL, retrieval time, HTTP status and SHA-256. Source citations establish that a response was retrieved; they do not establish that its claims are true.
4. Use **Links** to inspect destinations. **Read selected same-site link** starts a new authorized read. Other origins remain reference-only.
5. Optionally enable **Follow same-site links**, with 1–8 pages and depth 1–2. An optional topic ranks eligible links; it does not turn the topic into a new search request.
6. Use **Cancel** to discard unfinished results. Back and Forward display the last 20 reports already held by the window without making new requests. Clear history removes those reports from the window; transfer logs are separate.

The viewer works at normal and compact window widths. Its source pane and evidence pane can be resized using their divider. Text, source and link lists have scrollbars.

## Tool interface

`onion_navigate` uses the `public_web_read` permission and the normal tool registry authorization, execution interlock and location guard. Parameters are:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `url` | required | Public checksum-valid v3 onion URL; no credentials or session/auth query parameters |
| `max_pages` | 1 | At most 1–8 attempted page reads |
| `max_depth` | 0 | 0 reads only the selected page; up to 2 levels may be followed |
| `query` | empty | Optional topic, at most 500 characters, used to rank eligible source links |

The JSON result contains status, bounded extracted text, page metadata, citations, a link explorer, per-page errors, resource accounting and stop reason. Status is `complete`, `partial` or `unavailable`; unavailable sources do not produce invented findings.

No API credentials or paid services are required. Tor must already be installed using HAYAT's existing setup flow. Reading can start its installed daemon manually, as with the existing onion research tool.

## Boundaries and limits

- HTTP GET only, public HTML or plain text. No page scripts, account sign-in, form submission, downloads of executable content, access-control bypass or challenge solving.
- The URL, redirects and discovered links use existing public URL validation and v3 address checksum/version checks. Every transfer remains on the selected origin over the unchanged local Tor SOCKS route; switching the route discards results. No direct fallback or destination DNS preflight is used.
- Robots rules are respected. A robots access denial, redirect, oversized response or transient retrieval failure does not grant permission to read the page. Missing robots files (404/410) are handled as ordinary absence.
- Each page is capped at 1 MiB, robots at 128 KiB, accepted page bodies at 4 MiB total and report excerpts at 32,000 characters total. Failed transfers and robots traffic are not counted as accepted page bytes. The default whole-run navigation time is 90 seconds after Tor setup, configurable between 10 and 180 seconds; socket reads have shorter timeouts as that deadline approaches.
- Cancellation is checked between chunks, requests, extraction stages and final acceptance. An already running socket operation or Tor bootstrap can finish before cancellation is observed. Emergency stop/resume cannot revive an old execution ticket.
- Source instructions are untrusted. Existing injection assessment quarantines suspicious text and prevents following its links. Heuristics are not proof that source material is safe or accurate.
- Same-service pages are one publisher, not independent corroboration. External references are not automatically fetched or verified. No persistent page cache is created, but tool/transfer logs and clipboard content can retain requested URLs and selected citations.
- Public services can be offline, slow, blocked or interactive. Tor routing does not establish complete dark-web coverage or guarantee anonymity.

The implementation follows Tor's [v3 address encoding specification](https://spec.torproject.org/rend-spec/encoding-onion-addresses.html) and [onion-service guidance](https://support.torproject.org/tor-browser/features/onion-services/). Official public sample addresses come from the [Tor Project's own service directory](https://onion.torproject.org/).

## Verification on October 24, 2026

- 100 backend tests passed across `test_onion_navigation.py`, `test_tor_routing.py` and `test_wide_onion_research.py`, including the registered tool, permission denial, robots restrictions, retry/cooldown handling, bounds, DNS prevention, redirects, cancellation, stale stop tickets, source hashes and injection quarantine.
- The final navigator GUI passed 16 focused tests, including compact layout, local history, source/link selection, cancellation, live progress, privacy/permission denials and scroll preservation. Normal (980 × 760) and compact (650 × 640) captures were inspected; copy/clear controls remain visible in the wrapping action row.
- A live, owned temporary Tor daemon read the official Tor Project onion homepage: HTTP 200, 25,724 response bytes and 54 validated reference links, with source SHA-256 and timestamp, zero destination DNS lookups, then clean daemon shutdown. The first fresh-daemon attempt stopped at 50% bootstrap after 100 seconds; a bounded retry reused only that temporary descriptor cache and passed in 13.67 seconds. Both records are retained under `.test-runs/`; no access failure was presented as a successful source read.

Run focused backend verification with:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_onion_navigation.py tests/test_tor_routing.py tests/test_wide_onion_research.py -q
```

Run GUI tests separately from other Tk test processes:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_gui_onion_navigation.py -q
```

The live test requires a network connection and installed Tor, and can take several minutes:

```powershell
$env:HAYAT_LIVE_ONION_NAVIGATION='1'
.venv\Scripts\python.exe -m pytest tests/test_live_onion_navigation.py -q
```
