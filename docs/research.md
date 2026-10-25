# Web research and knowledge acquisition

## Structured public data

Searches now offers **Public datasets**, **Public papers**, and **Data & papers**.
These query DataCite dataset DOI records and Crossref publication metadata.
**Life-science papers** adds credential-free Europe PMC metadata search,
including native publication IDs for papers with no DOI. It does not fetch
full text or turn publication metadata into verified findings.
In chat, try **find datasets about climate**, **find publication metadata about
batteries**, or **find public data and papers about ocean temperatures**.
Search-again preserves this mode and its subject. Results are labeled
**metadata** rather than given a scientific-confidence percentage.

The tools `public_data_sources`, `public_data_search` and
`public_indicator_read` expose the source catalog, paginated registry search
and World Bank annual indicator observations. Search has a 1–10 result limit
**per source**, pages 1–100 and a 2 MiB response cap per request. Quick/Normal/Deep
GUI discovery requests return up to 2/5/10 records per registry respectively.
Discovery does not retrieve dataset contents or verify a paper's findings;
DOI duplicates are merged as records and conflicting metadata is flagged.

For example, `public_indicator_read(country="USA", indicator="SP.POP.TOTL",
start_year=2020, end_year=2022)` reads the requested annual series. Missing,
invalid and conflicting observations stay empty; a real zero remains zero.
Each result includes API URLs, retrieval time, source update date when reported
and a response hash. Metadata failure leaves observations available with an
explicit error. A live October 17 check succeeded against all three sources.
This is source retrieval, not a market prediction or independent data audit.

Source contracts: [DataCite queries](https://support.datacite.org/docs/api-queries),
[Crossref REST](https://www.crossref.org/documentation/retrieve-metadata/rest-api/),
[World Bank API](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures).

Search-provider errors are isolated by execution context, scoped domains are
checked against returned hosts and duplicate tracking URLs are removed. Deep
scholarly search can consult Europe PMC as an additional public metadata source.
Subscription search such as Brave is blocked by the zero-cost policy, including
when a saved key or stale permissive flag is present. Crawls count failed requests toward their
page-attempt budget, stop duplicate queue entries, account for received bytes,
and reject follow-up redirects outside the seed's final domain when scoped.
The crawl time budget is checked between requests; it cannot interrupt an
in-flight fetch. Each fetch still has its own timeout and download limits.

For Europe PMC, call `public_data_search(query="climate health", kind="literature")`.
Use the returned `next_cursor` as `cursor` for another explicit request, keeping
`page=1`; numbered pages are for DataCite/Crossref. At most ten Europe PMC
records and 2 MiB are accepted per request. Provider-supplied next-page URLs are
never followed. Source IDs are validated, author emails are omitted, duplicate
native IDs are dropped, and a publication year later than the retrieval year
gets a visible warning. An open-access flag is not a license grant.

Contract: [Europe PMC programmatic access](https://europepmc.org/RestfulWebService).

## Image research

Open **Chat → Actions → Research an image…**, or **Studios → Searches →
Image research…**. Choose a photo, screenshot or diagram. The window shows
a thumbnail and the available OCR/vision reading, using the existing file
admission checks and configured engines. Reading runs in a worker so the
window remains usable. A failed read is shown explicitly; it does not enable
research. OCR and vision descriptions can be mistaken.

Enter a specific topic or question, then press **Deep research**. Only the
topic you type is sent to the existing bounded research workflow; image bytes,
paths and extracted text are not automatically sent to web search. Results,
sources, cancellation and retry appear in Searches. This view does not store
the image reading in long-term memory. Ordinary **Add image…** still uses the
existing attachment-and-memory workflow.

For a person whose name you already know, **Public work by name** opens the
public professional research form. It does not infer or fill a name from an
image. Face identification, person tracking and private-detail dossiers are
outside this workflow. Privacy/local-only modes block its web-search action.

**Exact image sources** sends the selected file's SHA-1 hash to Wikimedia
Commons and downloads returned candidates to verify their file bytes. It does
not upload the image. If Commons is unavailable or has no exact file match,
the local metadata remains visible and the outcome is not treated as a match.
No API key is needed.

To check a page you already found, paste its public HTTP(S) URL beside
**Check source page**, then press that button. HAYAT reads one HTML page and
up to eight declared image URLs, including supported lazy-image, `srcset`,
Open Graph and Twitter image declarations. A match requires identical file
bytes or identical decoded, oriented RGBA pixels; a re-encoded lossless copy
can match even when its file hash differs. Results show the page and image
URLs, retrieval times, SHA-256 hashes, failed checks and coverage limits.
Different, unknown and failed checks remain distinct. These actions use the
existing public-URL, robots, redirect and active Tor-route guards. They need
no API key or paid service and never upload the selected image.

Source-page checks accept at most 1 MiB of HTML, 20 MiB per image and 32 MiB
of received content in total. Local and compared images must fit within
20 million pixels and 16,384 pixels per side before preview or pixel decoding;
local bounds are also checked before OCR/vision. Animated images can match by
whole-file bytes, but a single matching frame is not accepted as an exact
pixel match. Metadata omits GPS and identifying EXIF fields. Emergency stop,
closing the image window, changing its image or changing the privacy route
prevents subsequent requests; an in-flight download may finish.

The source check does not execute JavaScript, crawl linked pages, rank visually
similar images, find a person's identity, prove authorship or establish image
authenticity. A resized, recompressed or cropped image may remain unmatched.
Embedded data URLs are ignored; a `srcset` containing one is skipped entirely.
Results stay in the image window until replaced or closed; this window has no
report-export button and does not automatically save its report. Select and
copy the evidence text to retain it. The Python `check_source_page` and
`exact_sources` functions return structured dictionaries for callers that
explicitly save JSON. The October 24 public-source verification report is at
`.test-runs/image-source-live-public-20261024/report.json`; that is a test
artifact, not a default user-report location.

## Research from the desktop

Open **Studios → Searches**, enter a topic, question or public link, and choose
**Quick**, **Normal** or **Deep**. The window starts at Deep. Searches run in the
background; select a row to read the sourced answer, copy it, cancel delivery,
or search again at Deep level. Refreshes preserve your selected row, text
selection and reading position. Completed searches also report back to chat,
including searches that finish immediately.

In chat, requests such as **research solar panels in depth** or **search all
about the James Webb telescope** select the deep research budget. A subject
such as **deep learning papers** does not change the research level by itself.
All levels remain bounded by the configured search, page and time limits;
Deep cannot promise to cover the entire internet.

With the automatic search provider, scientific and dataset queries also consult
Crossref/DataCite metadata alongside the configured web indexes. These are DOI
discovery leads, not verified findings. Generic discovery terms are removed and
unrelated titles filtered before results are merged. Explicit Bing-only or
DuckDuckGo-only settings keep their selected provider.

**Browse** opens the entered or selected query for you to explore interactively.
Ordinary searches use DuckDuckGo; Onion searches use Ahmia with installed Tor.
An already active Tor route is retained for ordinary searches too. Complete any
publisher sign-in or human check yourself, then share a selected public source
URL. Opening the browser does not import its contents or grant automated access.
A proxied Edge/Chrome window does not provide Tor Browser fingerprint protection.

Long browser guidance wraps in compact windows. Search-table columns account
for heading fonts and can scroll horizontally; compact public-person forms
reduce the visible job rows to leave room for reading the result. Partial
registry results retain errors from unavailable sources rather than silently
presenting complete registry coverage.

**Add image…** opens the image picker and sends the selected image through the
existing chat attachment reader. It combines OCR and a configured vision
model's description. Image text is untrusted source material. The description
prompt excludes identification from faces and inference of private details or
sensitive traits. It does not upload the photo to a reverse-face-search service.
For research about a person, supply a known name and a scoped question about
their public professional work, publications or official profile.

### Public people research (2026-10-17)

In Searches, choose **Person (public work)**, enter a known name, and optionally
add a public role/organization and public profile URL. In chat, use
**research person Jane Example at Example Institute in depth**. The
`public_person_research` tool exposes the same workflow.

This search checks public biographies/profiles, published work, interviews and
dated role announcements within the normal search/page/time budgets. A supplied
profile is a retrieval candidate even when the search provider returns no hits.
Each accepted source must mention the name. Supported public-work claims stay
attributed to their individual sources; a name match never proves identity or
that two sources describe the same person. Old roles are not presented as
verified current employment.

**Scholarly records (OpenAlex).** After the web sources, the answer lists published work from
OpenAlex, an open catalogue of scholarly works and their authors (no key; it allows automated use):
- each matching author record's institution, topics, numbers of works and citations;
- its three most-cited works with their DOIs;
- its ORCID iD.

Quick shows one record, Normal two, Deep three. A record counts only when every part of the name
appears in it, and the family name must be in the author's main recorded name. OpenAlex's own search is
fuzzy, and its alternative spellings are noisy: live, a search for "Geoffrey Hinton" returned Yoshua
Bengio, whose record lists "Geoffrey Hinton" as a spelling. Namesakes stay separate records ([S1], [S2],
…), and a supplied institution or field only ranks them. No country, contact or photo is read. If
OpenAlex can't be reached, the web answer stands and the error is noted.

Known people-broker hosts are excluded before retrieval. Asks about where someone is or about their private life are refused before any
search, however they are worded. An attack run on 2026-10-17 found that "research person Jane Example where does she
live", "… kids", "… daily routine", "… license plate" and a street address given as context had all got through,
because the old check caught only phrases like "home address". The name is now checked against the full list.
The context is checked only against asks that can't be about work: someone's own family ("her kids"), routines,
whereabouts, plates and street addresses. "Partner at Baker Law LLP", "Daily Mail journalist", "Face Research
Lab" and "Children's Hospital" still pass. Text from OpenAlex is shortened (names 120 characters, titles 200). Private contact,
family, sensitive personal facts and unrelated colleagues' claims are omitted.
The workflow does not use face identification, cameras or person tracking.
It bypasses the research query/source cache and does not promote the results
into the knowledge store. Ordinary chat history and the session's search-job
list still contain the answer you requested. No whole-internet completeness
claim is made.

General **Deep** research also expands beyond the first query wording to check
background evidence, limitations and dated reports, subject to the configured
budget. The Searches table now says Finished rather than implying every
completed run found a reliable answer.

Privacy mode and PRIVATE/LOCAL/LOCAL_ONLY modes also block searches started
from this window.

## Pipeline

Research remains inside the existing agent and permission system:

```text
typed or spoken goal
→ local knowledge and freshness check
→ knowledge-gap and query planning
→ bounded search and safe retrieval
→ structured HTML, JSON, table, or PDF extraction
→ claim extraction and exact evidence validation
→ contradiction and source-independence analysis
→ answer-first cited synthesis
→ temporal claim graph and task memory
```

The agent automatically researches current, uncertain, version-sensitive, documentation-dependent, or unfamiliar technical questions. Stable facts and sufficient fresh local knowledge can skip the network.

Validated factual claims are rendered deterministically for straightforward
lookups. A language model is used for synthesis only when the question needs an
explanation, mechanism, cause, comparison, trade-off, or joined multi-part
reasoning and at least three supported claims survived validation.

## Modes and budgets

`QUICK`, `NORMAL`, `DEEP`, `PRIMARY_ONLY`, `LOCAL_KNOWLEDGE_ONLY`, and `ADVERSARIAL` are supported. Budgets cap searches, pages, elapsed time, bytes, and concurrency. Query planning creates a small set of primary, freshness, implementation, comparison, or counter-evidence searches and removes near-duplicates. Technical queries include detected installed package versions when available.

Research stops when validated evidence is sufficient, information gain falls, the budget expires, local-only knowledge is missing, or retrieval fails. It cannot browse indefinitely.

## Retrieval and extraction

Search is provider-neutral. `auto` tries configured Brave Search, public DuckDuckGo HTML, and Bing RSS fallbacks. Results preserve title, URL, snippet, domain, rank, provider, date, intent, and score. Search snippets are discovery data and never claim evidence.

## Downloaded public documents

`learn_public_document` connects safe public retrieval to document intelligence
and durable recall. It accepts a validated public HTTPS URL for PDF, OOXML Word,
Excel, PowerPoint, bounded text/code, or a validated image. Deep mode reads up
to 300 PDF pages, extracts at most one million characters, creates at most 64
overlapping 4,000-character chunks, verifies claims against the retained source,
and records URL, content type, SHA-256, quarantine path, and chunk provenance.

The raw file stays under `data/knowledge/sources` and is never executed. A
Microsoft Defender custom scan runs on the completed partial file before it is
renamed, extracted, or indexed. The scan ignores Defender exclusions; a
detection, scan error, timeout, or unavailable scanner fails closed and retains
the download as `.blocked` without parsing it. Exact content hashes prevent
duplicate recall chunks. Each digest also gets a compact
document index containing the requested topic, source URL, summary, and spaced
filename aliases such as `rfc8785` / `RFC 8785`. Hashed vectors and FTS are
available immediately; neural chunk embeddings are deferred and can be filled
with `/memory reindex`. This avoids serial local embedding calls delaying the
answer while retaining the stronger optional index.

A live official RFC 8785 download retained 41,879 bytes and passed Microsoft
Defender before extraction. Reprocessing now joins physical line wraps before
claim extraction, rejects incomplete fragments and bibliography entries, and
classifies RFC/IETF/W3C sources as primary standards. This removed the false
conflict caused by a wrapped normative sentence. The verified local query
`What requirements must RFC 8785 JSON canonicalization follow?` returned five
source-backed normative claims without web or model generation in a ten-run
mean of 86.293 ms on this machine. These are point-in-time measurements, not
universal performance guarantees.

This path accepts any public HTTPS host rather than only the publisher allowlist
used for installable software. It still rejects private/loopback targets,
unsafe redirects, robots exclusions, responses above 64 MiB, executable binary
signatures, generic archives, malformed images, and OOXML containers with more
than 10,000 entries or 256 MiB expanded size. These are integrity and machine
safety boundaries, not subject-matter restrictions.

The built-in `zero-cost-web-research` MCP server exposes this engine to MCP
clients through bounded read-only search, fetch, and deep-research tools. It also
accepts an owner-opened Google page snapshot for link/text inspection. It does
not automate unattended Google result pages and it cannot enable a billable
provider; background research uses the same free search/retrieval engine.

The fetcher permits public HTTP(S) only, rechecks redirects and public DNS, respects configured robots rules, and applies rate, timeout, and byte limits. HTTP is tried before isolated Playwright rendering. Cached pages support TTLs plus `ETag` and `Last-Modified` revalidation. Playwright has bounded retries, DOM readiness checks, crash recovery, and semantic role/name actions.

An explicit page-learning request uses rendered capture rather than a search
snippet. `capture_web_page` validates that the target is public, renders it in
the isolated browser, accepts either a full URL or a bare public domain,
performs a bounded top-to-bottom pass to reveal lazy-loaded sections, extracts
the main text/metadata/headings/tables, and can
save a full-page, viewport, or CSS-selector screenshot under
`data/artifacts/web-captures`. The research run, content-hashed source, claims,
and provenance are retained even when a single page is not enough to validate
its claims; only validated results become active knowledge summaries.
Interactive follow-up can use the registered browser accessibility, role/name
find, click, type, tab, and scroll tools; captures never reuse personal Chrome
profiles or bypass authentication.

An explicit public domain combined with `access`, `visit`, `browse`, `scrape`,
`summarize`, `analyze`, or similar page-reading language is a capability
invariant: it is routed to `capture_web_page` before conversational model
generation. This prevents a local model from incorrectly claiming that the
internet is unavailable. Short follow-ups such as `summarize it` reuse the
last successfully rendered public page.

Extraction supports HTML metadata and JSON-LD, embedded JSON, headings, links, structured tables with units and footnotes, JSON APIs, text, and page-numbered PDFs. Public GitHub inspection uses the official API for repository metadata, README, tags, releases, manifests, source structure, docs/examples, workflows, and bounded issues/pull requests. Paper analysis reports only fields found in the retrieved paper, including method, data/sample, results, limitations, conflicts, and authors' conclusion.

## Claims, citations, and confidence

Every stored claim has a stable ID, type, scope, status, timestamps, entities, freshness class, and evidence links. Each evidence edge records source URL, content hash, relevant excerpt, page/section location, stance, support type, directness, strength, and independence group.

A citation is valid only when its source was successfully fetched, its URL and content hash match, its excerpt exists in the fetched content, and the excerpt actually supports the claim. Redirect, hash, snippet, and source mix-ups fail closed. Statuses are `SUPPORTED`, `PARTIALLY_SUPPORTED`, `DISPUTED`, `UNVERIFIED`, and `SUPERSEDED`.

Confidence combines direct support, source fitness for this query, independent corroboration, freshness, and contradiction penalties. It is calibrated evidence strength—not a claim of truth. Different years or software versions are separate scopes and are not treated as contradictions.

Requirement questions prioritize complete normative statements (`MUST`,
`SHALL`, `REQUIRED`, and equivalents). A stored exact answer is reused only if
it still clears the requested confidence threshold and covers the question's
intent facet. Old incomplete fragments are marked superseded—not deleted—and
their evidence and history remain auditable.

### Answer order, recent events and source requests (2026-10-24)

- **The stated answer comes first.** For "what is / who is" questions, a claim
  that names the question's subject before "is" ("The capital city of
  Australia is Canberra") ranks as a direct answer. A claim that only reports a
  belief ("Many travellers guess that Sydney is Australia's capital") ranks
  lowest and is dropped from the answer once a source states the answer.
- **Recent-event questions stay recent.** "Last night", "yesterday",
  "tonight", "this morning/afternoon/evening", "earlier today", "this week" and
  "who won …" are live questions. Claims dated outside that window, and every
  undated claim from a page dated outside it, are left out. If nothing recent
  remains, the reply says so and names the newest date found instead of
  presenting an old result. Undated evidence is kept with a caveat that the
  date could not be confirmed. Live questions are cached for minutes, not weeks.
  The first search uses the real date ("who won the lakers game October 23,
  2026"), because "last night" matches every last-night article ever written.
- **Search queries drop a leading lowercase article.** Bing's fallback read
  "the capital city of Australia" as Capital University and Capital One; without
  "the" it returns Canberra. "The Hague" keeps its capital T and its article.
- **Asking for a URL, link, study, paper, citation or source is researched.**
  "Give me the URL of the study that …", "find me the study showing …" and
  "what is the source for the claim that …" retrieve real pages instead of
  links recalled from memory. "Give me examples of irony", "a study plan" and
  "sources of vitamin D in food" are unaffected.
- **An unsupported premise is named first.** "The study that proves coffee
  cures cancer" opens with: none of the retrieved sources supports the premise
  "proves coffee cures cancer" — then what they do report. If a source does
  state the cure, no note is added.
- **A result needs a named event.** "Who won the match last night?" asks which
  match (teams, sport or competition) before any search or model can answer it.
  Any naming word, capitalised or not ("who won the lakers game last night",
  "the IPL final"), makes it a search.
- **Sources list what the answer cites.** Pages that were read but not cited
  are counted on one line instead of being listed as if they supported the
  answer; the citation numbers stay the same.

## Contradiction-aware knowledge

Contradictions are detected both within one research run and across facts
learned in separate sessions. Opposing same-scope propositions are stored as
separate claims joined by a durable `claim_conflicts` record; high lexical
similarity never causes an opposing statement to overwrite the earlier one.
Numeric disagreement is conservative and requires incompatible values with an
explicit shared unit, avoiding false conflicts from years, model numbers,
resolutions, or sample sizes.

An unresolved conflict marks both claims and their cached summaries disputed.
Consequently, exact-question reuse cannot serve either side as settled truth.
Online mode requests fresh evidence; local-only mode shows both claims, their
sources, the reason they conflict, and reduced confidence while explicitly
declining to choose a winner. `knowledge_conflicts` lists the durable conflict
graph, and `explain_knowledge` includes conflicts with each recalled claim.
Temporal facts with different years or versions remain separate history;
newer scoped evidence can supersede an older fact without erasing it.

## Durable knowledge and reuse

`data/knowledge/knowledge.db` stores runs, sources, source versions, claims, claim history, evidence, entities, claim/entity links, relationships, query/page caches, usage, failures, source/provider performance, and explicitly monitored pages. Retrieval combines lexical, semantic-hash, entity, freshness, confidence, status, and usage signals.

New temporal evidence can supersede a prior claim while preserving its history and provenance. Repeated successful use raises usefulness; repeated task failures can mark a claim disputed for review. The local-only mode never calls the network and labels stale cached results. Raw pages are not indiscriminately promoted into memory.

After three similar successful runs, the system may write an untrusted YAML skill candidate for review. It never activates generated code or promotes a skill automatically.

## Commands and tools

```text
/research [quick|normal|deep|primary|local|adversarial] QUERY
/knowledge QUERY
/why QUERY
/knowledge-export [markdown|json] QUERY
/knowledge-import PATH :: TOPIC
/knowledge-refresh [LIMIT]
/source-track URL [LABEL]
/source-check URL
/research-status
```

Model-facing tools include `web_search`, `public_web_fetch`, `public_api_get`, `public_download`, `bounded_crawl`, `research_topic`, `capture_web_page`, `knowledge_search`, `github_research`, `paper_research`, `explain_knowledge`, `export_knowledge`, `import_knowledge`, `track_public_source`, and `check_public_source_change`.

Natural typed and voice phrases such as “research this deeply,” “use only primary sources,” “learn this page and remember it https://…,” “check whether this is current,” and “what sources support that?” route through the same pipeline. Spoken output is shortened; full claims, caveats, and sources remain visible in the CLI/log.

## Safety and privacy

All web and imported content is labeled untrusted data. Online text cannot alter the user goal, grant tool permission, request credentials, or trigger a local action. Suspected prompt injection is removed from model synthesis input while its presence remains observable. Private/local network URLs, URL credentials, authentication bypass, CAPTCHAs, paywall bypass, personal browser profiles, and unrestricted crawling are not supported.

Downloads are hash-addressed and quarantined without execution. Imports require an explicitly selected project file and retain user-provided provenance. Change monitoring is opt-in per public URL. Query logging can be disabled, and raw web content is not copied to ordinary task logs.

## Troubleshooting and measurement

- Provider markup and rate limits can change; the provider chain records failures and falls back.
- JavaScript-only pages require a working Playwright browser channel.
- GitHub's unauthenticated API limit applies unless a future approved credential workflow is configured.
- Offline answers may be stale and are labeled accordingly.
- Missing paper sections stay `null`; the analyzer does not invent them.

Run quality and reuse measurements with:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_research_quality.py --label after
.\.venv\Scripts\python.exe scripts\benchmark_research_reuse.py
```

See [research-quality.md](research-quality.md) for the checked-in milestone measurements and their limitations.

## Background jobs, trust verdicts and catalogues

`ResearchJobs` runs each search in its own thread and returns a quick answer in the turn (`research.inline_wait_seconds`); a slower one is delivered by `AssistantService.deliver_notice` as an `assistant_notice` output event (window, terminal, voice) and recorded in the conversation, so follow-ups have it. A turn replaced before its inline answer was shown triggers `redeliver`. `compose_answer` appends `verdict()` (independence-grouped confirmation, contradictions, single-source claims, confidence) and, for book requests, Open Library and Internet Archive catalogue entries filtered by fuzzy title relevance. `resolve_query` turns a vague request into a standalone query from the conversation. Research status, cancel and repeat are routed controls answered from the job list.


## Professional investigation workspace

Searches → **Professional research…** opens the evidence dashboard. It supports
public names/profile seeds, scoped public business contact source discovery,
candidate comparison, timelines, source relationships, cited JSON/PDF exports,
encrypted investigation history and consent-based local document access.
Launch, files, live checks and limitations.
