# Privacy

## Defaults

| Data | Default behavior |
|---|---|
| Microphone audio | Process locally; discard after transcription |
| Transcription logs | Disabled |
| Camera | OFF; no background recording; temporary observations deleted |
| Clipboard content | Explicit read only; usable in current task but redacted from persistence |
| Screenshots | Created only for requested tools/verification; stored under project data paths |
| Local models | Input remains on configured local endpoints |
| Cloud adapters | Present for compatibility but blocked by default zero-cost policy |
| Browser | Isolated research context by default; no personal profile/cookies |

Memory and logs store sanitized task evidence. `.env`, credentials, SSH keys, browser password stores, and private keys are protected. Raw external content remains untrusted data.

Private mode further restricts routing/logging, but it does not make unsafe instructions safe. Review `config/settings.yaml`, `config/permissions.yaml`, and `.env` before changing defaults.

## Tor routing and onion sources

`hayat_mask_on` requires Tor immediately for the public fetcher and built-in
search providers. Missing setup, bootstrap failure, daemon loss and remembered
Tor intent in a runtime without background services keep the proxy requirement;
only an explicit unmask restores direct routing. Status distinguishes a required
route from a ready Tor daemon. It suppresses its direct public-IP probe while
Tor is required.

`public_web_fetch` can read a valid v3 onion URL through this route. The address
version and checksum are checked without local DNS. Ordinary destinations also
avoid destination DNS preflight locally when routed through Tor; literal private
addresses and local hostnames remain blocked, and HAYAT's Tor configuration
rejects internal addresses resolved through Tor. Redirects remain checked.
Route changes interrupt the current retrieval rather than silently retrying
directly. An application-owned HTTP transport bounds SOCKS handshake reads that
the installed HTTP dependency otherwise leaves untimed. Public Tor reads do not
retain response cookies; caller-supplied headers are dropped on a change of
origin. Untrusted source handling and the authority kernel still apply.

Research browser-rendering fallbacks stop while Tor is required. The explicit
masked Edge/Chrome launcher uses a separate profile and sets SOCKS, DNS, QUIC,
WebRTC and bypass flags. This configuration is not Tor Browser and has not been
certified against browser fingerprinting. The Tor Project explains why
[using Tor in another browser lacks Tor Browser's protections](https://support.torproject.org/tor-browser/security/using-tor-with-other-browsers/).

External connectors/MCP services, ordinary browser automation, native media
players and download/extractor subprocesses, and other Windows applications are
outside this verified routing coverage. Adapter-based VPN/DNS/IPv6 observations
are exposure indicators, not a packet-level leak test. Tor does not guarantee
untrackability: accounts, identifying content, fingerprinting and traffic
correlation remain relevant. See the
deep verification report for actual test
evidence, observed failures and scope.

## Sensitivity labels

`app/safety/sensitive.py` defines five handling labels used across the system.

| Label | Meaning | Stored | Trained on | Transmitted |
|---|---|---|---|---|
| `PUBLIC` | ordinary operational data | yes | yes | yes |
| `PROJECT` | project-internal content | yes | yes | not by default |
| `PRIVATE` | user-private content | redacted | no | only with exact authorization |
| `SECRET` | credentials and keys | never | never | never |
| `EPHEMERAL` | clipboard, audio, session-only | never | never | only with exact authorization |

`assert_persistable` refuses to write `SECRET` or `EPHEMERAL` payloads, and refuses any payload in which credential material is detected regardless of its declared label. `assert_transmittable` refuses `PRIVATE`, `SECRET`, and `EPHEMERAL` payloads unless an exact authorization was granted.

## Secret detection and redaction

One detector is shared by logs, events, task history, action records, checkpoints, memory, conversation history, training data, retrieval indexing, and clipboard handling. It inspects **values**, not only dictionary key names — a credential appearing in `stdout`, in a goal string, or inside a file the agent read has no helpful key attached to it.

Detected shapes include OpenAI, Anthropic, GitHub, AWS, Google, Slack, and Stripe key formats; JWTs; `Authorization` header material; private-key blocks; Azure storage keys; connection-string passwords; credentials embedded in URLs; and generic `name = value` assignments for key, token, password, secret, and credential names. Values loaded from `.env` are additionally redacted by exact match, so a credential that matches no pattern is still removed.

Redaction stays diagnostic. A redacted value becomes `[REDACTED:<kind> len=<n> sha256=<12 hex>]`, which preserves enough to investigate a failure without the value being recoverable. Placeholders such as `${OPENAI_API_KEY}`, `changeme`, `<redacted>`, and empty assignments are recognised and left readable.

Training data is the one exception: it uses value-independent placeholders (`[REDACTED:<kind>]`). A digest is still derived from the secret, training corpora may be exported, and value-derived placeholders would prevent two otherwise-identical samples from deduplicating.

Private-local training examples are a separate opt-in lane. They require an
explicit owner-consent flag and verified correctness, are tagged
`PRIVATE_LOCAL`, remain under `data/training/<category>/private`, and cannot
enable cloud export. Automatic collection is disabled: remembering or
researching a private document does not silently turn it into weight-training
data.

Approval audit records store an argument *fingerprint*, never argument values, so approving a command containing sensitive text does not persist that text.
