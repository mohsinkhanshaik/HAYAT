# Teaching, labs, and mentor state

The learning subsystem stores user progress in `data/memory/learning.db`. It tracks topics, concepts, prerequisite graph, mastery estimate, confidence, last study/test, correct and incorrect answers, hint use, forgetting estimate, next review, quiz/lab evidence, weaknesses, events, and mentor profile. Levels are `UNKNOWN`, `BEGINNER`, `LEARNING`, `UNDERSTOOD`, `WEAK`, `MASTERED`, and `NEEDS_REVIEW`.

The initial AI-infrastructure graph covers Linux, Python, Bash, networking, Git, Docker, Kubernetes, Terraform/cloud, GPU/CUDA/NCCL, PyTorch distributed methods, inference stacks, GPU scheduling/operator tooling, observability, distributed systems, SRE, storage, RDMA/InfiniBand, capacity, and cost.

Natural commands include `Teach me GPU scheduling`, `Teach me NCCL by asking me questions`, `Quiz me`, `Test me on CUDA streams`, interview requests, hint-ladder requests, `What are my weaknesses?`, review, and labs. The transparent scheduler increases review intervals with mastery and reduces them for errors, hints, and forgetting risk; it is a practical heuristic, not a claim of perfect cognitive modeling. Lab scores include completion, verification, recovery, hints, errors, and duration evidence. Labs remain declarative project-local JSON plans.

The 50-case teaching evaluation passed prerequisite adaptation, Socratic prompting, exam behavior, interview prompting, and real weakness-state access. Mean deterministic teaching-control latency was 2.056 ms. A verified NCCL lab scored 0.9425 and changed mastery/review state.

Use `/learn status`, `/learn topics`, `/learn review`, and `/learn weaknesses` to inspect teaching evidence.

## Unified learning architecture and alignment

`app/learning/architecture.py` inventories six deliberately separate layers:
durable memory/DNA, researched knowledge, measured teaching progress, the
background queue, promoted procedures/skills, and model-training examples.
Keeping them separate prevents a scraped page from becoming a trusted personal
memory, a quiz score from becoming a factual claim, or an ordinary conversation
from silently becoming training data.

```text
/learn inventory
/learn alignment
/learn optimize
```

`inventory` reports compact counts, recall/index health, per-database bytes and
free pages, storage pressure, and the exact trust boundary of each layer. Its
snapshot has a ten-second in-process cache so the GUI/CLI can inspect it without
reopening and rescanning every database repeatedly. `alignment` checks durable
memory integrity, full-text/hashed-index coverage, claim evidence, summary
sources, unresolved contradictions, stale active knowledge, training-set
isolation, automatic-training state, and the background principal's write
scope. A stale item is a visible warning and remains in history; it is not
presented as current.

`optimize` is a governed maintenance operation, not erasure. It rebuilds only
missing derived recall indexes, resolves exact duplicate active summaries,
expires query/source cache rows past their TTL, invokes SQLite planner
optimization, and uses a passive WAL checkpoint that does not wait for active
readers or writers. It does not run `VACUUM`, compact episodic content, delete
evidence, or change model weights. Its audit is written to
`data/benchmarks/learning/architecture.json`. Content compaction remains a
separate, reviewable memory operation because it rewrites stored text even when
meaning is preserved.

The owner configuration already approves bounded public background learning
and validated local knowledge writes. It does not approve self-permission
changes. Private training remains local-only and requires explicit consent for
the actual selected dataset; learning in general is not consent to train on
private material.

## Language courses

`app/learning/language.py` teaches built-in language courses; Arabic is the first ([app/learning/courses/arabic.py](../app/learning/courses/arabic.py): 28 letters with positional forms, vowel marks, and ten vocabulary units, 109 items in 16 units). Progress lives in `language_progress` and `language_state` inside `data/memory/learning.db`, so a lesson resumes after a restart and is shared by the chat and the Learn window.

Every (item, skill) pair has a spaced-repetition box: boxes 0 and 1 keep it inside the session (immediately, then after a minute), box 2 is ten minutes, and boxes 3–6 are one, three, seven, and twenty-one days. Correct answers move up, wrong answers go back to box 1, and close answers stay. Reading and writing are always offered; listening when the Arabic Piper voice is installed; speaking when the multilingual Whisper model and a microphone are present and privacy mode is off. Grading is deterministic. Arabic is compared without vowel marks, with alef, yaa, and taa-marbuta variants unified. Latin answers ignore accents, apostrophes, and doubled letters. Speaking needs an 88% match to count as correct and 60% to count as close. `scripts/setup_arabic.py` downloads the voice and speech model once.

Topics without a built-in course are taught by the local model one idea at a time (`teach me docker`). Each step ends with a `Question:` line, the model judges the answer, and the lesson stays open in `pending_learning` until `stop lesson`. An open lesson no longer captures unrelated requests: the router hands obvious new requests (`open notepad`, long questions) to their normal path and keeps the lesson open. Since 2026-10-15 the same goes for talking to HAYAT instead of answering: a question ("how do you feel now?", "where are we?"), an instruction ("learn and understand", "tell me…") or a complaint. These were graded "Not quite" before. A complaint ("I didn't ask you that", "stop teaching") also sets the lesson aside. Arabic text in a language lesson is still taken as an answer.

## Durable background learning

Public-safe substantive questions, research requests, and teaching topics can enter a persistent SQLite learning queue at `data/learning/learning.db`. Greetings, immediate controls, trivial computer actions, secrets, private paths/context, child-mode speech, privacy-mode speech, code, and diagnostics do not. Queue items survive restart, are content-hash deduplicated, have bounded retry counts, and move through `pending`, `learning`, `learned`, or `failed` states. `/learn queue` shows that evidence; `/learn retry TOPIC` explicitly resets one reviewed failed item.

The learner consumes that queue before its curated seed topics, and it now runs
continuously rather than only when the machine has been still for two minutes.

It stands aside for exactly as long as it should. Each conversational turn
pauses it and the end of the turn resumes it, released even if the turn fails,
so an answer never competes with background reading and a crash cannot strand
the loop. This replaced a keyboard-idle rule that was the first and strongest
test in `machine_is_busy()`: requiring 120 seconds of stillness meant the
learner sat out every session the owner was present for, reported honestly as
"the machine is busy with your work" while the CPU was idle. Idle time is now
consulted only as a tiebreaker once the machine is genuinely under load.

Ceilings are 60 cycles per hour, 300 public requests per hour, 300 seconds per
cycle, and 4 GiB of writes per day. Every source is free -- DuckDuckGo HTML and
direct public fetches, no API key and no account -- so the rate costs nothing
but disk and electricity.

The limits that matter are unchanged. It remains restricted to public
search/read and validated knowledge writes under the `BACKGROUND_LEARNER`
principal. It cannot type, click, launch programs, access private resources,
or silently extend its own authority. It yields when RAM or CPU is genuinely
contended, stops dead at the daily byte ceiling, and `/learn stop` and
`data/learning/STOP` remain kill switches that work from outside the process.
The 1 TB learning allocation is a storage ceiling, not a target or
preallocation.

Its queue is also filtered for what belongs in a public search box. Questions
about the owner himself do not go into it -- his face, his money, his family,
and his own self-improvement. "What would make me better" was typed into a
search engine and came back with life-coaching articles that were then stored
as sourced claims; the object of the question decides, so "how can I improve
query performance in postgres" is still researched.

With `idle_reflection` enabled, an otherwise empty queue receives at most
`reflection_candidates` jobs derived from real unresolved contradiction pairs,
then stale active knowledge. Topic proposal is deterministic and model-free.
The resulting jobs still pass through the same idle/resource/rate budgets,
public-only retrieval, evidence validation, contradiction handling, and
restricted `BACKGROUND_LEARNER` authority. This is bounded knowledge maintenance,
not an unconstrained self-prompting or self-modification loop.

The self-improvement review (the **Improve** view) raises the same two gaps in
bulk. Applying "resolve contradictions between stored claims" queues every
unresolved conflict pair (up to 50) as a "Resolve with current primary
evidence…" research job; applying "refresh knowledge that has passed its
validity horizon" queues up to 50 stale topics, including ones learned before
(a refresh relearns them). Neither resolves or deletes anything directly: the
learner's validated pipeline decides. A stale query that was never a real topic
is retired instead -- kept with its sources, marked `stale` with a
`retired: <reason>` resolution, and never refreshed again -- when, after its
"Refresh with…:" wrappers are removed, it runs past 360 characters (a pasted
prompt), fails the public-search filter above (a remark or instruction to
HAYAT), or is a phrase cut out of a longer message the owner wrote. The stale
count covers only entries still in use, so superseded and retired rows no
longer keep the proposal open forever.

When `anticipate_followups` is enabled, one public-world question produces at
most `anticipated_per_request` deterministic follow-up research candidates.
They explore bounded failure/diagnostic, best-practice/trade-off, or mechanism
angles; they are labelled as predictions and never represented as the owner's
words. Learned candidates are RAM-indexed for a low-latency check but remain
durable in SQLite. Reuse requires strong subject overlap plus compatible
question facets. Same subject with a different facet is recorded as a mismatch
and causes fresh research, unless privacy, local-only, or child-safe mode
forbids network work. This keeps anticipation a latency optimization rather
than permission to force a prefetched answer onto a different question.

Validated, fresh, sufficiently relevant knowledge can be answered directly from the local knowledge store without another web search or language-model call. Exact duplicates refresh their existing record. Newer or stronger conflicting summaries supersede older active records while preserving the old evidence and provenance. `/knowledge optimize` resolves exact active duplicates and expires cache rows; it does not erase evidence history.

The background cycle now performs this same freshness-aware recall before any
web tool call. If validated, non-stale knowledge already covers the queued
topic, the cycle records a `reuse` result with zero requests and zero knowledge
writes and marks the topic learned. Stale entries, incomplete answers, disputed
claims, and different question facets do not take this shortcut. This prevents
24/7 operation from repeatedly scraping what HAYAT already understands while
still refreshing facts whose validity window has expired.

### What the learner will and will not study (2026-10-04)

On 2026-10-04 the learner's journal showed it researching chat as written. Examples were "great what have learn about today" and "ok good now look for ways to make money". It also built follow-up questions from them, such as "failure modes ... for money required elite", which stored claims about mining ships in a video game. Three changes stop that:

| Change | What it does |
| --- | --- |
| HAYAT reads each message first | Before researching a topic that came from someone's words, HAYAT's own model rewrites it as one standalone research question, or answers NONE for small talk, remarks to HAYAT, questions about what it learned, private matters, and word salad. Seed, reflection and refresh topics pass unchanged, and so does every topic when the model is unreachable. |
| Follow-ups only from real questions | Predicted follow-up questions are made only when the message is a well-formed question ("Why does...", "How do...", "Explain..."). |
| Off-topic claims are not stored | A claim its source supports is still dropped when it shares no meaningful word with the question. Only Latin-script questions are judged, so Arabic questions answered from English sources are untouched. |

In a live check with HAYAT's brain, the meta question, "cool, anything new you picked up?" and "HAYAT HAYAT HAYAT HAYAT." all came back NONE. "ok good now look for ways to make money" became "What are realistic ways to earn extra income online?" Genuine questions came back as cleaner versions of themselves.

## Resilient learning and improvement

The learner now persists retry deadlines across restarts. Failed topics wait
60 seconds, then 120 seconds, with a three-attempt ceiling; other ready topics
can proceed. Every topic producer uses the same public-topic filter. Privacy
mode yields without spending an attempt. Learned jobs and exhausted retries
cannot bypass the queue through reflection or tool-failure suggestions.

While running and admitted by the learning gates, the worker reviews small
local maintenance every 15 minutes. It can verify/rebuild memory indexes and
queue evidence refreshes. Proposal IDs stay stable, completed outcomes survive
restart, and failed maintenance remains visible. Code upgrades retain their
existing evaluation and promotion requirements. Repair now rejects new failing
tests or reduced test coverage and restores changed pre-existing untracked
files when a repair attempt crashes.

### Why background learning kept "failing" (fixed 2026-10-06)

The journal showed three causes, now fixed:

- **Budget exhaustion was charged to the topic.** Background research shared
  the foreground tool-call budget (60 `research_topic` calls), so topics
  ended "failed" with *Repeated-call budget exhausted* through no fault of
  their own.
  - Each background cycle now has its own budget (`registry.budget_scope()`).
  - HAYAT's own limits (budget, rate limit, privacy mode, a cycle already
    running) never use up one of a topic's three attempts.
  - Topics that failed this way are put back in the queue when the queue opens.
- **Refreshed knowledge was born stale.** A page the publisher re-confirmed
  with HTTP 304 kept its first fetch time, so claims about anything "live"
  expired on arrival. The same three topics were re-researched every 15
  minutes, all night.
  - A revalidated page now counts as verified at that moment.
  - A learned topic is not refreshed again within 6 hours.
- **Topics that were never topics.**
  - The learner researched "find current location of <an email address>",
    and follow-up angles on a command ("how brief today actually works").
  - Locating or profiling a person (email addresses, phone numbers,
    "location of", "where does X live", "track this person") is now never
    background research.
  - Follow-up angles are not built on commands or changing facts ("today",
    "latest", "news").
  - Stored entries like these are retired when they go stale, instead of
    being refreshed.

Continuous operation requires HAYAT to be running and the PC awake. Restart
HAYAT after updating its source to load the new worker and interface. The stop
file, resource budgets, privacy mode, and foreground-turn yielding still apply.

### Refreshable official reference data

Open **Studios → Knowledge Library** (also available in **Commands**) to search
retained claims, filter fresh supported evidence, inspect verification dates and
source excerpts, and copy a fact with its citations. **Learn this topic** sends
the entered public topic to the existing governed queue. Search work runs off
the UI thread, outdated responses are discarded, and closing the window
cancels pending searches. The library displays unverified and conflicting data
counts separately from freshness.

Recall now adds matching candidates from the entire retained claim corpus to
its recent semantic candidates. Older facts no longer disappear solely because
more than 1,000 newer claims have been stored. Maintenance also detects claims
labeled supported without retained sources: it withdraws that label, preserves
history, and expires affected summaries for fresh research.

`config/official-knowledge.json` contains a curated technical reference pack
covering local model tools, structured responses, running-model inventory,
SQLite durability, Python concurrency, transcription, and microphone access.
The expanded pack adds asynchronous tasks, monotonic timing, JSON parsing,
thread shutdown, caching, path resolution, deque ordering, and subprocess results.
Refresh it with:

```powershell
.venv\Scripts\python.exe scripts/refresh_official_knowledge.py
```

Each fact must still match a retrieved excerpt and pass the existing citation
verifier before the entry is stored. The importer restricts sources to approved
official hosts, records retrieval time and content hash, and sets a seven-day
refresh deadline. Its report is `data/knowledge/official-pack-report.json`.
Failed entries leave prior freshness deadlines untouched and cause a nonzero
exit status. Successful repeats refresh the same knowledge record. Expired
entries are available to the normal stale-knowledge maintenance pipeline; the
catalog importer itself runs when invoked.

This pack adds reference knowledge available to recall. It does not change
model weights or certify that all of HAYAT's knowledge is current. Dataset
checks separately detect altered content, invalid verification, malformed
records, split contamination, and missing provenance. Rejected examples are
removed from exported splits; training still needs sufficient reviewed data,
held-out splits, and a matching local trainable checkpoint.

## Parent-controlled child mode

`/learn child on [young|child|teen]` restricts the shared runtime to age-appropriate conversation and teaching before planning. Computer, browser, filesystem, coding, memory, and private-resource actions are blocked and journalled with secret redaction. Set `HAYAT_PARENT_PIN` in `.env` to require a parent PIN when leaving the mode, then inspect `/learn child report`. This is a supervision boundary, not a substitute for a Windows child account or attentive adult.
