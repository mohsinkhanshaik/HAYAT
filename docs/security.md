# Security model

## Permission decisions

Every registered tool declares a permission category. `config/permissions.yaml` resolves it to `ALLOW`, `ASK`, or `DENY`. An `ASK` action is denied in unattended mode and receives a one-time console prompt in interactive mode.

Two layers decide every tool call: `config/permissions.yaml` (can deny outright) and `app/safety/authority.py` (risk tiers, Trusted Owner Mode, and the protected boundary). YAML `ALLOW` does not skip the authority kernel.

Live owner policy in `config/permissions.yaml` is intentionally broad: ordinary local tools default to `ALLOW`, including deletion, computer input, browser typing, unknown PowerShell, and administrator category lookup. That is not the whole story:

- High-consequence categories (`login_account`, `submit_form`, `external_post`, `send_message`, `connector_write`, `mcp_write`, `automation_manage`, `powershell_admin`) still require a human answer. Trusted Owner Mode cannot pre-authorise them.
- The owner's own words are that answer for sending, submitting and posting. When the message HAYAT received from the owner says to send, text, email, reply, submit, post or tweet — as a command, not a mention — the turn's authority envelope carries `send_message`, `submit_form` or `external_post` (plus `connector_write`/`mcp_write`) and it runs without a second prompt. That authority is bound to that exact turn and expires after five minutes. It is read from what the owner typed or said, never from a rewritten goal or anything a task reads, and it never reaches web content, background learning, scheduled jobs, internal maintenance or a remote device. Logging in, `automation_manage` and Administrator work still ask every time.
- Purchase, credentials, system modification, access-control bypass, and self-permission changes stay `DENY`.
- Restart and shutdown stay `ASK`.
- HAYAT never elevates itself; an already-elevated process is still classified before it runs.

## Filesystem confinement

Relative paths resolve beneath the project root. Canonical resolution rejects traversal and access outside the configured allowed roots. Critical Windows, program, browser-profile, credential, and SSH-key locations remain protected even when a broader root is allowed. This is an application boundary, not an operating-system security sandbox.

## Command safety

PowerShell runs with `-NoProfile -NonInteractive` and never requests elevation.

Shell text produced by a model is untrusted input. `app/safety/command_policy.py` parses it before any policy lookup: it splits the text into segments on `;`, `&&`, `||`, `|`, and `&` while respecting quoting, tokenises each segment, and extracts risk signals. The boundary is an **allowlist, not a denylist**.

A command is `ALLOW` only when *every* segment matches a named safe profile in `config/permissions.yaml` **and** no elevated-risk signal is present. Anything unrecognised is `ASK`. A hard-deny set is `DENY` regardless of profile:

- encoded PowerShell payloads (`-e`, `-enc`, `-EncodedCommand`, and any prefix)
- runtime code construction or evaluation — quoted-string concatenation, `[char]`, `[Convert]::FromBase64String`, `-join`, `iex`/`Invoke-Expression`
- any reference to a protected resource (`.env`, `id_rsa`, `.ssh`, `credentials`, `.pem`, and the rest of `protected_resources`), matched after lowercasing and separator normalisation so `.ENV`, `.\.env`, and `data/../.env` are equivalent
- retrieving remote content and executing it, in any ordering or piping arrangement
- proxy-execution and system-control binaries (`mshta`, `rundll32`, `certutil`, `bitsadmin`, `reg`, `netsh`, `vssadmin`, and similar)
- administrator elevation
- the legacy protected-system patterns, which are retained

Signals that do not deny but do escalate `ALLOW` to `ASK`: multiple segments, pipelines, redirects, command substitution, nested interpreters, and paths outside the project root.

Configuration can only *tighten* the classifier's decision, never loosen it: setting `powershell_safe: DENY` blocks even a profiled command. Unrecognised shell text is classed `powershell_unknown`; the live YAML currently `ALLOW`s that class, and Trusted Owner Mode may pre-authorise it because it is an EXTENDED-tier local action. High-consequence admin work still stops.

Normal project workflows are preserved by profile: `git status`, `git log`, `git diff`, `pytest`, `python -m pytest`, `python -m pip check`, `python -m compileall`, `Get-ChildItem`, `dir`, `Get-Content` on unprotected paths. `python <script>.py` through the shell is **not** in a profile; arbitrary script execution belongs to the permissioned `python_execute_file` tool.

Execution no longer uses `shell=True`. Text needing builtins is wrapped in an explicit argument vector (`cmd.exe /d /s /c` on Windows, `/bin/sh -c` elsewhere) so the exact invocation is auditable, and the timeout path terminates the whole process tree on both platforms.

This classifier is defence in depth. It cannot prove arbitrary shell text safe — it refuses to guess instead. High-impact use should remain supervised.

## Approval binding

An approval authorises one thing, not a mood. `app/safety/approvals.py` issues an `ApprovalGrant` bound to:

- the exact tool name
- a SHA-256 fingerprint of the canonically normalized arguments (key order independent)
- the current session
- an expiry time (300 seconds by default)
- a single invocation, unless a wider scope was explicitly requested

A grant does not transfer to different arguments, a different tool, a different session, or a later time window. Two identical `ASK` calls prompt twice. The scheduler's `/approve ID` path issues one bound single-use grant and revokes it after the rerun, rather than temporarily replacing the approval callback.

The broker keeps a bounded audit trail of what was authorised. It records argument *fingerprints*, never argument values, so approving a command containing sensitive text does not persist that text.

`CallBudget` bounds runaway tool loops within one task: 200 total calls and 60 calls to any single tool by default. It resets at the start of each top-level task; it is not a process-lifetime counter. Exhaustion returns a failed `ToolResult` with permission `BUDGET` rather than continuing.

## Prompt injection

External content is labeled untrusted in agent context and browser state. Instructions embedded in websites, email, documents, PDFs, Git repositories, downloads, chat, and API results have no authority. They cannot override the user goal or tool permission policy. Secrets should never be pasted into untrusted pages without a specific approved action.

Research additionally scans evidence for instruction overrides, secret requests, tool commands, and role impersonation. Suspected lines are redacted from model synthesis prompts while the original source remains provenance-bearing untrusted data. This is defense in depth, not a proof that arbitrary hostile text is harmless.

Research citations are accepted only after the source URL, fetched content hash, exact excerpt presence, and claim support are checked. Search snippets never qualify as evidence. Imported summaries retain user-provided provenance and cannot masquerade as primary sources.

## Public-network boundary

Research accepts only HTTP(S), rejects credentials in URLs, resolves DNS before access, and blocks loopback, link-local, private, reserved, and other non-global addresses. Every redirect is revalidated. Requests have robots.txt handling, per-domain pacing, content/byte/time limits, and bounded concurrency. Public downloads are quarantined, scanned, and never executed. Public browsing does not load the user's Chrome or Edge profile, passwords, or cookies.

## Secrets and logs

`.env` is Git-ignored. Structured logging recursively redacts key names resembling API keys, tokens, passwords, secrets, authorization headers, and credentials. Avoid placing secrets in goals or filenames because free-form user content is retained as task history.

## Computer control and vision

PyAutoGUI fail-safe mode is enabled: moving the pointer to a screen corner can abort its actions. Input is an ordinary local action: live YAML allows it, and Trusted Owner Mode pre-authorises it. Safe usage is still observe → act → observe → verify. Application editing uses an exact UI Automation window handle when context supplies one; close verification requires that intended handle to disappear, not merely a successful tool return. Screenshot interpretation is disabled until a vision-capable provider is deliberately configured.

## Child processes and downloads

Python, PowerShell, and terminal processes receive a filtered environment without secret-like variables. Output is capped, timeouts terminate the exact child process tree, and Python starts with user-site and unsafe path prepending disabled while the approved project root is explicitly supplied. This is stronger containment, but it is not a VM security boundary.

Browser, research, and governed-download files use the installed Microsoft
Defender command-line scanner before release. HAYAT requests a custom scan with
`-ScanType 3 -File ... -DisableRemediation`; Microsoft documents that this
custom mode ignores exclusions and scans archive files. Only return code zero
is accepted as clean. A detection, timeout, scanner error, or missing scanner
fails closed. Downloaded evidence is renamed with `.blocked`; dragged files are
not moved or modified, but are refused before parsing. Clean downloads receive
a SHA-256 digest and are never opened or executed automatically. This uses the
Windows security provider already present, so it adds no Python package or
driver. See Microsoft's [Defender command-line reference](https://learn.microsoft.com/en-us/defender-endpoint/command-line-arguments-microsoft-defender-antivirus).

Antimalware scanning reduces risk; it cannot prove that every novel malicious
file is harmless or that a machine already controlled by an attacker is safe.
HAYAT therefore combines scanning with inert-type checks, archive expansion
limits, public-network restrictions, untrusted-content labeling, no automatic
execution, and fail-closed parser admission.

Defensive automation is strictly non-retaliatory. It may block, quarantine,
isolate its own work, preserve evidence, alert the owner, and support recovery.
It cannot attack, exploit, damage, or probe back against another machine.

## Scheduled approvals

An unattended `ASK` never becomes an implicit allow. The failed action is persisted in `approval_requests`; an explicit `/approve ID` grants one matching tool call during one rerun. Requests can also be denied. The optional Windows startup task runs as the current user with limited privileges.

## Voice input

Microphone text is user input, not elevated authority: nearby speakers, media, or another person could trigger recognition. Spoken actions use the same permission manager and sandbox as typed input. Voice approvals accept only explicit phrases; vague `yes` is insufficient, and `DENY` policies cannot be overridden.

Audio stays in memory except for short operating-system temporary wave files required by local recognition or synthesis; those files are deleted immediately. Raw retention and transcription-content logging are disabled. Push-to-talk is the privacy-first default. Wake mode is opt-in and exposes its listening state. Unsaved close commands pause for an explicit save/discard/cancel decision.

## Supervised child mode

Child mode is enforced before planning and permits only conversation, questions, teaching, and immediate stop/mute controls. Computer, browser, filesystem, coding, research, memory, and private-resource intents are blocked and entered into a redacted parent journal. If `HAYAT_PARENT_PIN` is configured, leaving the mode requires that PIN in either CLI or GUI. The journal and mode state persist across restart. This is an application guardrail, not a replacement for a Windows child account, network filtering, or adult supervision.

## Self-state and learning safety

Self-referential questions about this running instance's active provider, model, project, activity, tools, internet access, memory, voice, vision, hardware, files, or completed actions bypass generative self-narration and use `AgentStateService`. The boundary is anchored to the assistant (“you,” “your,” “HAYAT,” or equivalent), so ordinary external questions containing words such as model, memory, voice, research, or tools continue through normal question answering. The read-only `get_agent_state` tool cannot confer permissions. Teaching and labs store declarative state only; labs do not automatically install software or request administrator access. Training examples require verification and provenance, are split deterministically, and cannot trigger automatic fine-tuning.

The built-in web-research MCP server is read-only, rejects private/local-network
targets, respects retrieval limits and robots policy, labels returned text
untrusted, and forcibly disables paid search. Unattended Google result-page
automation is not enabled; Google text may be inspected only when the owner
explicitly supplies an opened-page snapshot through the browser bridge.

The zero-cost policy is also a safety boundary: configured cloud credentials cannot silently activate paid model/search/embedding/speech paths. Camera activation is explicit and raw observations are deleted by default. Clipboard content is tagged ephemeral and redacted before actions, logs, episodic memory, and reflection persist. Governed downloads recheck redirects, size/quota, and hashes and never execute automatically.

Embedding of private memory is local-only by default. Cloud-compatible embedding endpoints require explicit configuration and `allow_cloud`; the shipped configuration is false. The retrieval index rejects secret-like content and never indexes raw logs or binaries. Training data recursively redacts common credentials before hashing or export, rejects cross-split duplicate hashes, and provides no accidental one-word training execution command.

Private training examples require an explicit owner-consent flag plus verified
correctness. They are tagged `PRIVATE_LOCAL`, separated from the standard JSONL
corpus, recursively credential-redacted, and rejected if their metadata permits
cloud export or fails to declare local-only use. Automatic collection and
automatic fine-tuning remain disabled. Private retrieval learning and private
weight-training data are intentionally separate choices.

System-driver and Windows-tool removal is a permanent owner invariant. `pnputil /delete-driver`, device disable/removal commands, driver removal through DISM, and Windows feature/tool removal are hard-denied by both code and configured command patterns. Read-only inventory and diagnostics are still possible. This boundary does not claim the application is unhackable: an attacker who already controls Windows can alter application code or storage. HAYAT instead provides defense in depth through least privilege, protected paths, principal isolation, untrusted-content provenance, secret redaction, poisoning-resistant DNA auto-links, and tamper-evident memory rows.

Software cracking, license/DRM bypass, credential theft, exploit delivery,
unauthorized network access, and retaliatory attack are not operator
capabilities. Legal and ethical boundaries do not disappear in an owner mode.
An authorized satellite, robot, industrial controller, or other remote service
may be integrated only through a documented API/connector for a system the
owner is permitted to operate. High-consequence commands require exact scope,
target allowlists, owner approval, observable results, and an audit trail.

## Trusted Owner Mode and the authority engine

`app/safety/authority.py` is the single answer to "may this action execute?".
The model proposes actions and never grants them; a web page is evidence, never
instruction; a tool cannot vote on whether it may run.

The old model had two states -- ALLOW or ASK -- so anything not explicitly
allowed produced a prompt. Being asked to approve the camera *after* saying
"look at me" is not security: it is friction that trains you to approve
reflexively without reading, while genuinely consequential operations sit in the
same undifferentiated bucket as taking a screenshot.

Authority now separates risk from authorization across four tiers:

| Tier | Examples | Trusted Owner Mode |
|---|---|---|
| 0 PASSIVE | read state, search, observe windows | always allowed |
| 1 ORDINARY | launch apps, type, camera observation, workspace files | pre-authorised |
| 2 EXTENDED | delete files, downloads, unknown shell | pre-authorised only in Trusted Owner Mode; still bounded and recoverable |
| 3 HIGH_CONSEQUENCE | account login, form submit, external post, send message | **asks**, except send/submit/post the owner's current message told HAYAT to do |
| 4 PROHIBITED | credentials, financial transaction, protected-system mutation, permission bypass | **always denied** |

Turning on Trusted Owner Mode (`/authority owner on`) removes repeated prompts
for ordinary and extended recoverable local work (tiers 0-2). It does **not** cross the protected boundary: tier 3 still stops unless the
owner's current message itself said to send, submit or post, and
tier 4 is refused under any mode. `grant()` itself refuses to pre-authorise
anything at tier 3 or above, because a grant is a promise made in advance and
those decisions have to be made in the moment.

### Money: always stopped, always explained

Payments, purchases and money transfers are tier 4 in code ([app/safety/authority.py](../app/safety/authority.py)): no mode, grant, approval or remote device can allow them, and they cannot even be pre-granted. Since 2026-10-10 every stop gives the same plain explanation (`MONEY_EXPLANATION`): nothing was paid, why it is blocked, and what HAYAT can do instead -- find and compare, fill in everything except the payment, and stop on the payment page so the owner checks the amount and recipient and pays. A chat request to buy, pay, order online or send money is recognised in the intent router before any model or tool runs ([app/safety/money_requests.py](../app/safety/money_requests.py)), so the answer is fixed text rather than model wording. That reply hides nothing: it quotes the request; lists what was read from it (action, amount and currency, recipient or shop, service, item or bill -- each "not stated" when absent, never guessed); says what HAYAT did (nothing outside the chat: no page, form, card, account, wallet or password touched, no model or tool asked) and what was recorded (the request and reply in the local chat history only -- the request is not queued for background learning or remembered as feedback); gives the reason and where the rule lives; and lists what HAYAT can prepare. Questions about money ("how do I buy a house", "explain how PayPal works") are answered normally. The browser's payment-form guard uses the same explanation.

Other guarantees, each covered by a test:

- **Scope is a boundary.** A grant on `D:\AI` does not authorise
  `C:\Users\...\Documents`. Paths are resolved before comparison, so `..`,
  symlinks and alternate spellings cannot walk out of a granted root.
- **Session grants do not leak** into another session.
- **Expiry and revocation take effect immediately.**
- **Privacy mode outranks grants.** `/privacy on` blocks camera, clipboard and
  network even where a persistent grant exists -- otherwise the switch would be
  theatre.
- **Every tool reaches authority.** Static `ALLOW` is not a bypass: principal isolation, privacy, envelopes and protected boundaries evaluate every registered invocation. The resolved command category, including Administrator reclassification, is what authority receives.
- **Self-repair preserves owner work.** A repair captures the exact pre-repair working tree. Failed attempts restore only files created or changed by the repair; they never run `git checkout -- .` over unrelated edits.
- **Explicit request is consent** for one ordinary reversible action, and only
  up to tier 1.
- **Every withheld decision is audited** with its tier, source and reason;
  routine allows are not published, or the event bus would drown in them.

Inspect with `/authority status`, `/authority grants`, `/authority why`, and
revoke with `/authority revoke [capability]`.

### Persistence, envelopes, and convergence

**Owner Mode and grants survive restart.** Authority state lives in
`data/memory/authority.db` rather than a config file, for two reasons: a grant
is security state rather than a preference and should not sit hand-editable
beside cosmetic settings, and the model has file tools -- keeping authority in a
store only `AuthorityManager` writes to removes any path where generated text
could widen its own permissions by editing YAML.

Enabling for one session and enabling permanently are different acts:

```
/authority owner on              # this session only
/authority owner on --persistent # survives restart
```

**Grants are re-evaluated, not grandfathered.** A restored grant is checked
against the *current* tier table, so a capability reclassified upward between
versions loses its old authority instead of inheriting it. Grants written under
an older `POLICY_VERSION` are held back entirely and remain only as audit
history.

**Privacy mode persists deliberately.** If the owner turned privacy on and the
process later crashed, coming back with sensors live is the wrong failure
direction; it stays on until explicitly turned off.

**Task authority envelopes** remove per-substep prompting. "Open Notepad and
write hello" is one request and several tool calls -- launch, focus, type, read
back. The envelope is derived once from what the owner actually asked for, and
substeps run inside it. It can only narrow: capabilities above tier 1 are
dropped when the envelope is created, it is bound to one turn, it expires, and
it is closed when the task ends. A planner deciding on its own that it needs
something the owner never requested gets no envelope and meets the normal
boundary.

**One authorization system.** The bespoke camera-consent path is gone. Intent
maps to capabilities, an envelope is derived, and `AuthorityManager` decides --
rather than a general engine plus a camera exception. The behaviour users care
about is unchanged: "look at me" does not then ask permission to look.

Roles are now distinct: `PermissionManager` holds static category policy,
`AuthorityManager` makes the runtime decision, and `ApprovalBroker` only
resolves the cases where a decision is `REQUIRE_APPROVAL`.

## Owner-confirmed outward actions

Publishing the business website, registering an account or a business, signing in to a website, and submitting any web form act as the owner toward the outside world. At the owner's request (2026-10-03), HAYAT may do them, and only in this order (`app/safety/confirmations.py`):

1. **Prepare.** `form_fill_prepare` fills the form and stops; `publish_website_prepare` packages the site. Each proposes an action with every value that will be sent and evidence (a screenshot of the filled page, or the file list). Nothing has left the machine.
2. **Confirm.** Only the owner confirms: in the confirmation window, after ticking "I have checked these details", or by saying "confirm action N" at this computer. No tool can confirm, so neither the model nor web content can approve its own action. A remote device cannot confirm either.
3. **Secrets stay with the owner.** HAYAT never fills a password, PIN, one-time code or ID number, even if one is given to it. The owner types it into the confirmation window; it is used once, cleared, and never stored, logged or taken from chat.
4. **Act once, re-checked.** An action runs once, within 10 minutes. Before pressing the button, the executor re-reads every field and refuses if anything changed since the owner looked.
5. **Verify.** The result comes back with evidence: the final address, success or error words on the page, and an after-screenshot. A publish fetches the live page and compares it with the local file. The owner marks it "Looks right" or "Something's wrong".

The plain browser tools refuse anything that would submit a form: a click on a submit, sign-up, sign-in, publish or similar button, and Enter in a form field. They point to `form_fill_prepare` instead. Search forms and ordinary links still work. Payment and card forms are refused outright (`purchase` stays prohibited), and HAYAT never solves CAPTCHAs. Each step is written to `data/memory/confirmed-actions.jsonl` with field names, never values or secrets.

## Remote-access intrusion defence

`app/security/intrusion.IntrusionShield` gates every request to the web server (`app/web/server.py`) before any endpoint runs. It scores offences per source (failed logins, path traversal, secret-file theft, scanner probes, injection markers, oversized bodies, floods), bans past `BAN_THRESHOLD` for an escalating time (`BAN_LADDER`, 15 min to 7 days), and persists bans to `data/security/intrusion-bans.json`. A request for credentials such as `/.env`, `/.git`, `/.aws` or `/.ssh` immediately crosses the threshold.

Banned GET/HEAD requests receive harmless synthetic data before any application endpoint runs. Secret-file probes receive an invented key and an `example.invalid` database host; other reads receive an empty synthetic gateway response. The decoy generator reads no private file or runtime state, its key cannot authenticate to HAYAT, and responses contain no executable payload or callback. POST requests still receive the existing denial/tarpit and never create a fake authenticated session. These decoys are a defensive deception response, not an attack against another computer. The first decoy response per ban emits `INTRUSION_DECOY` with `real_data_access=False`.

Loopback and the bound LAN subnet retain their existing exemption from scoring and bans; authentication still protects real API data. A correct passphrase clears a source's score. Bans emit `INTRUSION_OFFENCE`/`INTRUSION_BANNED` events. The Security GUI window lists bans (with Lift ban) and runs the local watch self-scan. This shield protects the HAYAT web server; it cannot exclude someone who already controls Windows or prevent theft through another application.

The October 18 hardening also gates WebSocket handshakes and checks an existing
socket's ban before each new message. It does not cancel a turn already executing.
Request inspection decodes a bounded copy of URL escapes without changing routing.
Source tracking and failed-login history have fixed limits; new untrusted sources
are refused at capacity rather than evicting active bans. Saved bans use bounded,
validated loading and atomic replacement. Persistence failures are visible in the
Security window; an unavailable or corrupt store cannot restore its lost bans.
JSON bodies are size checked while streaming even without Content-Length.
Malformed credentials, message shapes and JSON fail without reaching the runtime.
Security response headers also cover early refusals and decoys.

Open **Studios → Security** for **Remote defense**, **Machine scan**, and
**Poisoning & decoys**. Reports and ban-file access run off the Tk thread. The
source table scrolls in both directions, preserves its position on unchanged
refreshes, and shows complete selected reasons. Live counters refer only to the
current shield instance; saved bans are not an attack history. Local defense
checks use an isolated memory-only shield and generated decoys, leaving the live
shield and learning stores unchanged. Closing cancels UI polling; an in-flight
observation may finish without updating the destroyed window.

## Source-poisoning containment

Research and attachment retention inspect a normalized copy of external text:
Unicode compatibility forms, invisible format characters and line splitting do
not hide the coordinated instruction patterns. Original source evidence stays
unchanged. Sources that exceed the two-million-character inspection limit are
quarantined rather than treated as fully inspected. Coordinated instruction
signals also quarantine the source and yield an empty synthesis excerpt, so
neither malicious text nor a redaction notice becomes a factual claim. Research
retains provenance; quarantined attachments are excluded from durable memory.

These are bounded pattern heuristics, not a probability of compromise or proof
that other text is safe. A single phrase in educational material does not by
itself trigger coordinated-source quarantine. The authority kernel independently
denies tool authority to untrusted external content. Defensive responses remain
blocking, quarantine and inert synthetic data; HAYAT sends no retaliation or
executable payload. Windows observation failures are now reported as incomplete
coverage, rather than being converted to empty successful observations.

## Third-party advisories closed in HAYAT

- **NLTK PYSEC-2026-3740** (no fixed release yet): six NLTK model save/load functions bypass NLTK's file sandbox. HAYAT routes their paths through NLTK's own validator, and refuses any path outside the allowed folders. See [Text analysis](text-analysis.md#nltks-security-fix).
- **multidict CVE-2026-104874**: upgraded to 6.9.1 on 2026-10-15.
