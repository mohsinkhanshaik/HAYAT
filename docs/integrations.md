# Integrations, MCP, secrets, and browser extension

## Capability model

HAYAT has one integration boundary shared by personal-service connectors,
Model Context Protocol servers, and the optional browser extension. Installing
a definition does not connect an account. Definitions start disabled, declare
their scopes and read-only operations, and must be enabled deliberately.

## Shared transport improvements (October 17, 2026)

MCP discovery now follows every pagination cursor, including in REA, rather
than returning only the first page. Discovery stops with a clear error on
cursor loops, conflicting duplicate definitions or limits of 100 pages,
2,000 unique tools and 8 MiB of catalog data. Identical repeated definitions
are deduplicated. The existing 30-second discovery deadline still applies.
`mcp_tools` accepts optional `query` and `limit` arguments, ranking tool names
before descriptions while preserving their parameter schemas. For example:

```json
{"server_id": "zero-cost-web-research", "query": "search", "limit": 5}
```

An MCP result marked `isError` / `is_error` now becomes a failed HAYAT tool
call, with a redacted diagnostic and a failed event. Results above 8 MiB are
rejected with a request for smaller output. Catalog redaction preserves JSON
schemas for arguments named `password` or `api_token`. This follows the
[MCP tool pagination and result contract](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).

HTTP connectors retry declared reads up to three times for transport errors
and HTTP 408/429/500/502/503/504. Normal backoff is one then two seconds;
short `Retry-After` values are honoured. Longer publisher cooldowns are
reported without retrying early. Calls use a 45-second budget and socket
timeouts of at most ten seconds; the streaming deadline is checked between
chunks, so a blocked read can extend it by a socket timeout. Responses are
limited to 4 MiB after decompression. Provider pagination remains available
through normal action parameters. Successful results/events include the
attempt count. Writes, authentication failures and other permanent HTTP errors
are not automatically retried. Scoped credentials echoed by a provider are
redacted from returned output.

Browser-extension requests now belong to a specific authenticated connection.
Disconnecting, replacing the connection or stopping the bridge wakes pending
callers immediately with a retryable error. Replies from another connection
are ignored, and duplicate replies cannot block the reader or stall later
snapshot/scroll commands.

Regression coverage is in `tests/test_integration_resilience.py`, including a
real paginated stdio MCP server, real WebSocket disconnect/replacement,
compressed responses, trickling downloads, retry limits and registry-level
failure reporting. The existing live REA check exercises local JavaScript
analysis and discovers its real operation catalog.

Verification: 167 targeted checks passed, including 29 new resilience checks;
two opt-in GUI screenshot checks were skipped. The static audit validated all
556 registered tool schemas/signatures with zero errors or warnings. Ruff and
the changed-file whitespace check passed.

Environment note: `pip check` still reports missing `onnxruntime` distribution
metadata for Faster Whisper, openWakeWord and Piper. This environment instead
has `onnxruntime-gpu` 1.30.0; its `onnxruntime` module imports and advertises
TensorRT, CUDA and CPU providers. This inspection did not run fresh model
inference and does not make the package metadata warnings disappear.

Read operations use the passive `connector_read` or `mcp_read` permission.
Anything that changes an external account uses `connector_write` or
`mcp_write`; those are high-consequence categories and require approval for
the exact call. When the owner's own message says to send, submit or post,
that message is the approval for the turn (see `docs/security.md`). Web pages,
documents, background learning, schedules, and event triggers cannot approve a
connector action.

The Developer Super-Agent is the `developer` specialist registered beside the
researcher, computer, reviewer, coder, and self-repair roles. It coordinates
code/build/test tasks through the same planner and verifier; it has no special
permission to bypass the sandbox or alter HAYAT's authority policy.

## Secret broker

`app/integrations/secrets.py` stores credential values in the operating
system's credential backend through Keyring. `data/security/secrets.db` holds
only a secret name, allowed scopes, timestamps, expiry, and a short digest.
Values are never returned by status/list tools, written to events or SQLite,
or placed in model context.

Enter a connector token from a local terminal with hidden input, never in chat
or as a command-line value:

```powershell
.\.venv\Scripts\python.exe main.py --secret-set github-token --secret-scope connector:github
```

For an MCP server use a scope such as `mcp:personal-server`. Remove an entry
with `main.py --secret-delete NAME`.

Connector and MCP code requests a short-lived, scope-bound, single-use lease.
Redeeming it injects the value directly into an HTTP authorization header or
the declared MCP server environment. Reuse, expiry, and scope mismatch fail
closed. There is intentionally no model-callable tool for entering or reading
a secret.

## Connector marketplace

The local catalog is `config/connectors.yaml`. `connector_marketplace_search`
searches definitions; `connector_install` installs one disabled;
`connector_enable` changes activation after approval. `connector_read` calls
only actions declared read-only in that connector's manifest, while
`connector_action` is the approval-gated write path.

Catalog definitions are templates. GitHub, Notion, or any other external
account still needs its own provider-side application/token setup and a
properly scoped secret. HAYAT never fabricates OAuth consent or marks an
account connected merely because a template exists.

Remote equipment, including a satellite service, follows the same connector
model; there is no universal-control switch. A real integration needs the
provider's documented API, proof that the account/asset is authorized,
least-privilege credentials, explicit telemetry and command schemas, target
allowlists, simulation or dry-run support where available, exact approval for
commands, response verification, and an audit record. HAYAT does not bypass a
ground station, radio authorization, license, or access control.

## MCP gateway

`app/integrations/mcp_gateway.py` uses the official Python MCP client. A server
must be registered with an installed executable, arguments, a read-only tool
allowlist, and optional environment-variable-to-secret mappings. It is started
without a shell and receives a secret-filtered environment plus only its
declared leases. Tool results pass through the shared secret redactor.

The gateway exposes discovery, read, and approval-gated write paths. It does
not accept arbitrary URLs, command strings, or an unregistered server from
model output.

HAYAT registers one built-in stdio server named `zero-cost-web-research` with
five read-only tools:

- `search_web` uses the configured free provider chain;
- `fetch_public_page` applies public-network, robots, size, timeout, and rate limits;
- `deep_web_research` expands a bounded set of search angles, ranks results,
  fetches public pages, and returns provenance-bearing excerpts without a model;
- `inspect_google_snapshot` parses a Google page that the owner explicitly
  opened and supplied through the browser bridge;
- `google_search_status` reports the connector boundary and available paths.

This server forcibly disables paid search even when credentials exist. Direct
unattended automation of Google result pages is disabled: background research
uses free search fallbacks, while an owner-opened Google tab can be inspected
through a snapshot. All returned content remains untrusted external data and
cannot grant permissions or execute instructions.

## Browser Extension Bridge

The unpacked Manifest V3 extension lives in `browser_extension/`. It requests
only active-tab, scripting, storage, tabs, and public HTTP(S) host access; it
does not request cookies, browsing history, downloads, or password access.

The Python bridge binds to `127.0.0.1:8765` only. A random pairing token is
stored by the secret broker and must be presented as the first WebSocket
message. The bridge supports:

- active-tab URL, title, visible text, viewport and document height;
- bounded link and form-field structure extraction;
- controlled up/down/top/bottom scrolling;
- live page snapshots retained only as bounded runtime state and sanitized
  event metadata.

Setup:

```powershell
cd D:\AI
.\.venv\Scripts\python.exe main.py --browser-bridge-token
```

In Chrome or Edge, open the extensions page, enable Developer mode, choose
**Load unpacked**, select `D:\AI\browser_extension`, and paste the printed
token into the extension options page. Keep HAYAT running so the localhost
bridge remains available.

## Event-driven proactivity

`EventTriggerEngine` maps a supported sanitized event to either a visible
suggestion or a background goal. Rules have exact payload filters, cooldowns,
hourly limits, persisted counters, and start disabled when created through a
tool. Enabling or creating automation is approval-gated.

Both event-triggered goals and scheduled goals execute as
`BACKGROUND_LEARNER`, never as `LOCAL_OWNER`. That principal may inspect local
state and research public evidence, and may store validated knowledge. It may
not click, type, modify external accounts, install software, use sensors, or
write arbitrary files. Proactivity therefore increases initiative without
silently increasing authority.

## Document Intelligence Suite

Dropped `.pdf`, `.docx`, `.xlsx`, `.xlsm`, `.pptx`, text/code, and image files
use one attachment pipeline. Word paragraphs/tables, bounded spreadsheet
sheets/rows, slide text/tables/notes, PDF pages, image OCR, and local vision are
normalized into searchable text. HAYAT stores a compact understanding,
entities, numbers, provenance, path, and content hash in hybrid memory rather
than copying the original file into memory.

## Dependencies

All directly imported integration/document packages are pinned by compatible
version ranges in `requirements.txt`: the Office readers, Keyring,
Cryptography, WebSockets, official MCP SDK, Watchdog, RapidFuzz, MSS, pandas,
and lxml. The setup remains reproducible without installing speculative
packages that no code calls.
