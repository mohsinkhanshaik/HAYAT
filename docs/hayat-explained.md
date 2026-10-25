# HAYAT explained: everything it does, how it works, and what makes it different

HAYAT is a private AI assistant that lives on your own Windows computer. It talks and listens, researches the web
and cites its sources, sees through your camera and screen, uses your apps, writes, makes images and videos,
watches markets and world events, finds jobs and clients, and remembers what matters — all with a local language
model, no cloud account and no cost. It was created by **Mohsin Khan Shaik** and is free, open-source software
([AGPL-3.0](../LICENSE)).

This page explains each part: what it does, how it works inside, how to use it, and where its limits are. For
step-by-step use of every area, see [How to use HAYAT](how-to-use-hayat.md).

**Contents:** [What makes it special](#what-makes-hayat-special) · [Working status and tests](#working-status-and-tests) ·
[The brain](#the-brain-how-a-request-becomes-an-answer) · [Many models](#many-models-one-voice) ·
[Chat](#chat-and-understanding) · [Memory and DNA](#memory-recall-and-dna) · [24/7 learning](#learning-around-the-clock) ·
[Predictions](#predictions-and-how-they-are-checked) · [Research and Tor](#research-the-dark-web-and-source-checking) ·
[People and clients](#people-search-client-search-and-faces) · [Jobs](#the-job-search-engine) ·
[Business](#business-money-and-decisions) · [Markets](#stock-and-crypto-trade-center) ·
[Global intelligence](#global-intelligence-cameras-and-the-world-map) · [Vision](#vision-cameras-ocr-and-person-detection) ·
[Images and video](#image-generation-video-generation-and-video-clips) · [Computer use](#using-your-computer-on-any-os) ·
[Planning](#planning-automation-and-decision-making) · [Voice](#voice-and-voice-matching) ·
[Wi-Fi sensing](#wi-fi-sensing-movement-and-patterns) · [Privacy and security](#privacy-security-and-authorization) ·
[REA](#reverse-engineering-with-rea) · [Web3](#web3-and-the-agentic-web) · [Displays](#3d-and-5d-displays) ·
[Limitations](#honest-limitations) · [Find and reach](#find-hayat-and-reach-its-creator)

---

## What makes HAYAT special

- **Truly local.** The language model, memory, files and camera stay on your computer. Nothing leaves unless you
  ask. HAYAT's own web reading can go through Tor.
- **It acts, and proves it.** HAYAT runs real tools — files, browser, apps, research, media — and a verifier (the
  "Truth Gate") checks each result. It may only say something was done when the tool result shows it; a claim
  guard catches replies that pretend otherwise.
- **You stay in charge.** Anything that touches the outside world — sending email, posting, buying, signing in,
  installing — waits for your approval. One **Emergency stop** halts everything.
- **Uncensored, but not reckless.** It answers every lawful topic plainly and completely, without lectures or
  disclaimers you did not ask for. The one line it keeps is about actions: it will not help commit a crime against
  a specific person or system, attack what you do not own, or hand over credentials.
- **It learns from its own record.** Every finished task becomes a lesson; failures are remembered with what fixed
  them; predictions are checked against what actually happened.
- **Zero cost, enforced.** Paid APIs and cloud fallbacks are blocked in code, not just turned off.
- **Plain answers.** Results are explained in words — never raw JSON or dictionaries.
- **Huge but tested.** About 166,000 lines of Python across 597 modules, with 7,865 automated tests.

## Working status and tests

Measured on 25 October 2026:

| Check | Result |
| --- | --- |
| Automated tests in this repository | **7,865** (7,738 offline + 127 opt-in live checks) |
| Full offline suite, fresh clone with no private data or runtime folder | **7,685 passed, 0 failed**, 53 skipped (optional engines or hardware not present) |
| Full offline suite on the development machine | **7,718 passed, 0 failed** (after one wording update) |
| Live checks with the real 35B model (brain, understanding, honesty, hard chat, team work, web routing, Tor, model hardening) | **32 passed** |
| GitHub automatic checks on every change | Passing (lint + 150 core tests on a clean Windows machine) |
| Memory "DNA" linked-recall benchmark (2,002 memories, 201 links, 100 recalls) | 100 % complete, 53 ms mean |
| Tor started on demand, then a real search through it | Ready in 59.5 s; results returned; Tor check confirmed |

Working today: conversation, voice, research with citations, Tor and onion reading, memory and recall, the Trade
Center, global intelligence, jobs, business toolkit, image and video generation, video download and clipping,
camera and screen reading with OCR, person detection, enrolled-face recognition, computer control, automation,
Wi-Fi sensing (with supported hardware), reverse engineering (with the optional REA engine) and the Web3 explorer.
Each needs its own setup where noted, and `python scripts/doctor.py` reports what is installed.

## The brain: how a request becomes an answer

HAYAT's "brain" is a mesh of **21 software subsystems joined by 31 explicit contracts**
(`app/brain_mesh.py`). They are real program parts, not neurons inside the language model:

```text
You (chat, voice, CLI)
  → Intent (what you mean)  → Context Budget (what fits the model)  → Model Router (which model)
  → Planner (typed steps with success criteria)  → Tool Judgment
  → Authority (ALLOW / ASK / DENY for every action)  → Tool Bus (the actual tools)
  → Truth Gate (verifies each result)  → Memory + DNA Graph (remember and link)
  → the answer, written in plain words
```

Around them sit Research, Learning, Network, Perception, Observability, Specialists (helper agents),
Self-Inquiry, and the executive layer: **Prefrontal Cortex** (goals and control), **Dual Brain** (a critic that
revises drafts) and **Invention** (an experiment workbench). `brain_mesh_self_check` tests every node
independently. A step that fails is re-planned with bounded retries, using what fixed that failure before.

## Many models, one voice

- **Default brain:** HAYAT 35B-A3B Uncensored — Qwen3.6-35B-A3B (HauhauCS uncensored build), a mixture-of-experts
  model with about 3 billion active parameters per token. On an 8 GB laptop GPU plus system RAM it ran at about 31
  tokens per second. It also handles vision.
- **Advisors:** other installed models can be consulted — a larger model in DEEP mode, or a specialist that the
  router has *measured* to be better at a kind of task. Their answers come back as a labelled second opinion; HAYAT
  keeps what is right, corrects what is wrong and writes the reply itself (`app/models/council.py`). Advice can
  never give instructions or change permissions.
- **Measured routing:** benchmark results decide which model handles which kind of task.
- **Colibri (optional):** an alternative local inference engine for side-by-side comparison
  (`app/models/colibri.py`). Off by default; starts only on request from **Models**.
- **HAYAT Open 4B:** a small model fine-tuned locally on public data, for lighter machines (experimental).

## Chat and understanding

Type or speak naturally. The intent router separates small talk, questions, research, actions and corrections, so
"how are you today?" is a greeting, not a web search, and "look again" retries a search rather than opening the
camera. Corrections replace only what they contradict. A one-line situation summary (what you have been doing,
what is waiting) rides along with every reply, so you rarely repeat yourself. Replies are plain language; when a
task fails, HAYAT says what failed and why.

## Memory, recall and DNA

Memory lives in a local SQLite database, in layers that never mix by accident:

- **Working memory** — the current goal and recent observations.
- **Episodic** — every finished or failed task with its outcome.
- **Semantic** — facts you told it, with source and importance.
- **Procedural** — reusable procedures with success counts and confidence.
- **Reflection** — what worked, what failed, and what to try next time.

**Recall** blends keyword search (FTS5), local neural embeddings, entity matching, recency, confidence and
importance. Then **DNA — Dynamic Neural Associations** — follows a graph of links between memories (`supports`,
`contradicts`, `depends_on`, `caused_by`, `same_entity`, `supersedes` and more), so remembering one fact brings
back the facts tied to it. Each link has a direction, weight, evidence and a reinforcement count. "DNA" is a name
for this association graph; it is not biology and it does not change model weights. Unverified web text cannot
create links. Every memory carries a SHA-256 checksum; tampered rows are quarantined from the model.

## Learning around the clock

- **Experience lessons** — before planning, similar past tasks become short lessons ("worked 3 times:
  research_topic"; "read_file failed twice here; what fixed it: search_files → read_file").
- **Background learning** — when you turn it on, HAYAT uses idle time to research open questions and likely follow-ups from public
  sources, within a budget, and discovers new official data sources it may use. It pauses while you are working.
- **Gap analysis** — the situation model lists what it cannot do right now (missing setup, recent failures, tools
  that fail more than they work); the learning atlas finds missing prerequisites in your own studies.
- **Teaching** — a learning atlas of 12 fields with quizzes, labs and spaced review based on your real answers.
- **Self-upgrade** — HAYAT counts its own failures and repeated manual work and turns them into improvement
  proposals (`app/selfimprove.py`). Small, reversible changes it may make and report; anything that changes its
  behaviour, reach or what you see waits for your approval. Code changes must pass the test suite and are reverted
  if they do not help.
- **Fine-tuning** — a local training path exists; a trained model is never promoted without passing held-out tests.

## Predictions, and how they are checked

HAYAT predicts in several places, and every prediction carries its own measured track record:

- **What you will ask next** — from your habits and recent focus; each guess is scored against your actual next
  request ("right 12 of the last 40 times"). Predictions are suggestions; HAYAT never acts on one by itself.
- **Market ranges** — hour, day and week ranges calibrated on past errors, with a walk-forward track record on
  HAYAT's own past forecasts.
- **Scenario odds** — "in situations like now, how often did this market rise a week later?", counted from history,
  with a plain verdict when the difference is just noise.
- **Possibilities by period** — chance of being higher after 1 hour to 1 year, with intervals counted on separate
  periods.

It never claims accuracy it has not measured, and it never promises income or price moves.

## Research, the dark web and source checking

- **Web research** runs in the background and reports back with the pages it actually read. Claims are checked
  against those pages; freshness and conflicts are shown. Free providers only (DuckDuckGo, Bing, Wikipedia and
  official data APIs), always respecting robots.txt.
- **Tor:** turn on "Tor for HAYAT" and its web reading goes through Tor; it starts on demand and never falls back to
  a direct connection silently.
- **Dark web (onion):** search public onion indexes and read known public `.onion` pages through Tor, with every page
  hashed and time-stamped as evidence. Read-only: no logins, forms, downloads or purchases.
- **OSINT source inspection:** paste a public page, GitHub repo or paper to see its text, publisher and provenance.

## People search, client search and faces

- **Person research** is limited to a named person's *public professional work* — publications, projects,
  organisations — with sources. Namesakes are shown as separate candidates; nothing private is gathered.
- **Contact discovery** finds published professional contact routes for a known name at a known organisation —
  never guessed emails, home addresses or data brokers.
- **Client search (leads):** "find dentists in Austin and make a lead list" builds a list of real businesses from
  web search, with the contact details they publish. Outreach drafts are honest and wait for your approval.
- **Face recognition** recognises only people you enrol, with their consent, in your own photos; unknown faces
  stay unknown. Templates are encrypted with your Windows account. There is no face search of the internet.
- **Person detection** finds people in an image or camera frame (boxes and confidence) without identifying anyone.

## The job search engine

HAYAT searches public job boards and employer career pages (Greenhouse, Lever, Ashby, Workday, and free APIs such as
Arbeitnow, The Muse, Jobicy and Himalayas), reads each posting and your CV, and scores the fit with every part of the
score shown. Normal, deep and maximum search depths; worldwide or any city. It prepares application packets, tracks
applications, and suggests what to learn to qualify for more roles. When a site asks for human verification, HAYAT
pauses so you can complete it yourself. **Sending an application always waits for your confirmation.**

## Business, money and decisions

- **Business toolkit:** clients, hours, quotes and invoices as PDFs, payments, expenses, monthly income goal, Excel
  export, and business ideas with a launch plan, website and marketing kit. It never collects money or logs into a
  bank.
- **HAYAT Wallet desk:** budgets, pots and bills, with approvals only you can give.
- **Portfolio:** net worth in USD across what you record.
- **Executive reasoning and the invention workbench:** goals, a skeptical critic, saved experiment rounds,
  numeric model fitting and simulation, so ideas are tested rather than just described.

## Stock and crypto Trade Center

Live crypto prices streamed from Kraken (about 100 ms), stocks and funds, candles, order book and recent trades,
heatmaps, a 3D view, signals and alerts, world events by industry, and the Command Center overview. Forecast ranges,
scenario odds and period possibilities come with their measured records (see Predictions). **Read-only:** there is
no order button, no wallet and no exchange account; requests to trade are refused before any tool runs.

## Global intelligence, cameras and the world map

Public feeds — earthquakes, weather and alerts, wildfires, rocket launches, satellites and ISS passes, news — on a
live world map, with about 37,200 public camera feeds (traffic, city and landmark cameras from official
publishers), watches and alerts, a hazard radar for your area, "explore" and "deep look" around any place, a video
wall, and AI-written briefs that separate confirmed reports from single-source claims. Unknown locations are never
plotted as if known.

## Vision, cameras, OCR and person detection

HAYAT looks at structure first and pixels last: browser DOM and Windows UI Automation, then OCR (Windows OCR, with
Tesseract as fallback), then screenshots, then the vision model. The camera is off by default, opens only when you
ask, and keeps no recording. It can read text from any image, describe your screen, watch a window live, and
compare images for exact duplicates.

## Image generation, video generation and video clips

- **Images:** SDXL on your own GPU via stable-diffusion.cpp, native 1024 px, with upscaling.
- **Videos:** short clips with AnimateDiff motion modules on a Stable Diffusion 1.5 model, with quality tiers and
  smoothing; every result is decoded and measured before it is reported.
- **Video downloads and clipping:** paste a link to inspect qualities, play it in chat, download, or analyse it and
  cut clips (square, vertical, any length) for an audience.

## Using your computer, on any OS

HAYAT opens and drives Windows apps by control name (UI Automation), finds files, reads and writes documents, runs
code, and browses with Playwright. Desktop actions are observed and verified, not assumed. Windows is the main
platform; the core is portable Python with `setup.sh`/`start.sh` for Linux and macOS, and Linux support for the
desktop app is on the [roadmap](https://github.com/mohsinkhanshaik/HAYAT/blob/main/ROADMAP.md).

## Planning, automation and decision making

The planner turns a goal into typed steps with success criteria, critiques complex plans, and re-plans after a
failure using past lessons. **Automation** runs scheduled jobs (once, daily, weekly, intervals or cron) and event
rules through the same permissions and verification as a request you type. **Worker teams** split big jobs between
temporary specialist agents (researcher, coder, reviewer), each with its own evidence. Decisions that act on the
outside world always come back to you.

## Voice and voice matching

Local speech recognition (Faster Whisper) and speech (Piper): push-to-talk, wake word or hands-free, interruption
while speaking, and a watchdog that recovers from microphone errors. The mic stays off until you allow it.
Optional **voice matching** (XTTS) can speak in a reference voice from a WAV you supply, for 17 languages; it runs
locally and needs consent from the person whose voice it is.

## Wi-Fi sensing, movement and patterns

With supported hardware (for example an ESP32 running a CSI tool), HAYAT reads Wi-Fi channel state information and
detects movement in a room from changes in the radio paths, then can confirm it with a short camera check. A normal
Windows Wi-Fi card gives only signal strength, which HAYAT labels honestly as "not CSI".

## Privacy, security and authorization

- **Authority kernel:** every tool call is checked (ALLOW, ASK or DENY) against permission classes and who is
  asking. Text from web pages and files is data, never instructions.
- **Confirmations and Windows Hello:** outward actions wait for your approval; sensitive steps can require your
  fingerprint, face or PIN through Windows Hello (HAYAT only gets yes or no).
- **What runs in the background:** background services are off unless you enable them; the Activity window and
  Operation view show every step HAYAT takes; privacy mode pauses the camera, microphone, feeds and learning.
- **Location privacy:** offline place names, a guard on what HAYAT sends, and an exposure check of who can learn
  where you are.
- **Network guard and Security:** new-device notices on your home network, and automatic blocking of suspicious
  remote sources for the web client.
- **Secrets:** API keys and passwords are redacted from memory and logs; `.env`, SSH keys and browser password
  stores are protected.

## Reverse engineering with REA

A local workbench around the open-source REA engine: inspect apps and binaries, JavaScript bundles and source maps,
call graphs in 3D, with every finding tied to evidence. Target code is never executed; anything that writes
needs confirmation.

## Web3 and the agentic web

Read-only views of Bitcoin, Ethereum and layer-2 networks, any address, transaction or ENS name, DeFi totals, and a
checker for how ready a website is for AI agents ("Web 4.0"). No wallet, no signing, no transactions.

## 3D and 5D displays

- **Brain 5D** shows HAYAT's 21 subsystems on a 3D brain: three dimensions of space, the fourth is **time** (a
  replayable timeline of activity) and the fifth is **evidence** (each part's checked or unchecked state).
- **Command Center** is a live matrix of tiles: markets, world pulse, trade routes, calendar, odds, chains, news and
  your accounts.
- **Neural workspace** on the home screen, 3D market views, and the 3D call graph in REA.

## Honest limitations

- It can be wrong, like any AI. Sources are cited so you can check; unverified items are labelled.
- A local 35B model needs about 22 GB of RAM; smaller machines should use a smaller model.
- Windows is the main platform today.
- Some areas need setup: models, Tor, REA, image/video models, Wi-Fi CSI hardware, voice matching.
- The background learner and self-upgrade never change its behaviour without your approval, so improvement is
  deliberate, not automatic.
- Market predictions are probabilities with measured records, not promises. HAYAT never trades.
- It will not identify unknown people, solve CAPTCHAs, bypass access controls, or act against systems you do not own.

See [limitations](limitations.md) for more.

## Find HAYAT and reach its creator

- **Code:** [github.com/mohsinkhanshaik/HAYAT](https://github.com/mohsinkhanshaik/HAYAT)
- **Models:** [HAYAT-35B-A3B-Uncensored-GGUF](https://huggingface.co/Mohsinkhanshaik/HAYAT-35B-A3B-Uncensored-GGUF) ·
  [HAYAT-Open-4B](https://huggingface.co/Mohsinkhanshaik/HAYAT-Open-4B)
- **Talk to the creator, ask questions, share ideas:** [Discussions](https://github.com/mohsinkhanshaik/HAYAT/discussions)
- **Help build it:** [roadmap](https://github.com/mohsinkhanshaik/HAYAT/blob/main/ROADMAP.md) · [contributing](https://github.com/mohsinkhanshaik/HAYAT/blob/main/CONTRIBUTING.md)
- **Mohsin Khan Shaik:** [GitHub](https://github.com/mohsinkhanshaik) ·
  [LinkedIn](https://www.linkedin.com/in/mohsin-khan-shaik-972817213/) · [X](https://x.com/MohsinKhanShai5) ·
  [mohsinkhanshaik.com](https://mohsinkhanshaik.com/)

**Keywords:** local AI assistant, private AI, offline AI, self-hosted AI, local LLM, Ollama, uncensored LLM,
Qwen3.6, mixture of experts, AI agent, autonomous agent, computer use, desktop automation, Windows AI assistant,
voice assistant, Whisper, Piper, RAG, long-term memory, knowledge graph, web research agent, Tor, onion, OSINT,
job search AI, CV matching, crypto tracker, stock market AI, market forecasting, global intelligence, public
cameras, OCR, Stable Diffusion, SDXL, AnimateDiff, video clipping, Wi-Fi sensing, CSI, reverse engineering, Web3,
open source, AGPL.
