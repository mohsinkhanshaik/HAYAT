# Complete command reference

Commands are handled by `app/cli.py`. Plain text goes through the unified intent router; `/goal TEXT` explicitly requests execution.

## Core and state

| Command | Result |
|---|---|
| `/help` | Show current CLI help. |
| `/status` | Live provider, model, memory, tools, voice, vision, camera, project, network, and storage. |
| `/brain [status|topology|check|benchmark]` | Runtime state, brain-node graph, independent/team self-check, or coordination benchmark. |
| `/health` | Component health and local resource snapshot. |
| `/system` | CPU, RAM, GPU/VRAM, disk, and timestamp. |
| `/performance` | Local tool/action/benchmark performance. |
| `/tools benchmark [deep]` | Audit every tool contract and historical behavior; deep runs only explicit passive probes. |
| `/tools optimize` | Refresh the audit and show the evidence-ranked optimization queue without automatic code changes. |
| `/doctor` | Run the subsystem doctor. |
| `/cost` | Show the enforced zero-cost policy and reachable local providers. |
| `/exit` | Close resources and exit. |

## Interaction and modes

```text
/chat TEXT
/goal TEXT
/mode [auto|fast|balanced|deep|research|coder|computer|vision|voice|local|private|adversarial|teach|lab|mentor|incident]
/response [normal|concise|detailed|debug]
/clear
```

Modes may be composed with `+`, for example `/mode deep+research`. `/chat` never requests tools; `/goal` does.

## Models, memory, projects, and learning

```text
/models
/models benchmark
/models route TEXT
/memory
/memory status
/memory benchmark
/memory reindex
/memory sync
/memory consolidate
/remember TEXT
/recall TEXT
/project list
/project status
/project use NAME
/project clear
/learn status
/learn inventory
/learn alignment
/learn optimize
/learn topics
/learn review
/learn weaknesses
```

Languages, persona and software, in plain words:

```text
install urdu | learn spanish yourself      learn a language: voice + study (asks first)
talk to me in urdu | english please        switch the reply language
act like a real person | back to assistant mode
install vlc | download and install ffmpeg  find with winget, explain, install on "yes"
```

Language lessons are started in plain words rather than slash commands:

```text
learn arabic                 start or resume the Arabic course
continue arabic              pick up where you stopped
arabic progress              reading/writing/listening/speaking progress
hint | skip | repeat | record | stop lesson     during a lesson
teach me docker              a model-taught lesson on any other topic
```

Reindexing may take time. Consolidation reports exact duplicates but does not silently delete them.
Learning optimization preserves learned records and evidence; it only maintains
derived indexes/planner statistics, checkpoints WAL files passively, resolves
exact active-summary duplicates, and removes expired caches.

## Research and knowledge

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

`primary` favors first-party sources; `adversarial` actively seeks contradictory evidence. Imports must name an explicit local file.

## Windows, browser, vision, camera, and clipboard

```text
/vision PATH [PROMPT]
/camera [status|devices|on|off|capture|observe]
/monitors [status|capture NUMBER]
/clipboard [status|read|write TEXT|analyze]
```

Natural commands drive the registered browser and Windows tools. Camera activation and clipboard content use the permission system. `/clipboard status` returns only type/size metadata.

## Voice

```text
/voice on
/voice off
/voice status
/voice devices
/voice probe
/voice test
/voice mute
/voice unmute
/voice mode push
/voice mode wake
/voice input ID|auto
/voice output ID|auto
```

## Files, downloads, dependencies, and storage

```text
/downloads status
/downloads recent
/downloads models
/downloads cleanup
/dependencies
/dependencies check
/dependencies outdated
/backup status
/backup create before-upgrade
/storage status
/storage largest
/storage clean
/storage models
/storage knowledge
```

The actual public download is a permissioned agent tool. It accepts only configured authoritative HTTPS sources and never executes the file.

## Tasks, jobs, approvals, and events

```text
/tasks
/events
/jobs
/job executions
/job add ID TYPE SCHEDULE_JSON :: GOAL
/job run ID
/job enable ID
/job disable ID
/job remove ID
/approvals
/approve ID
/deny ID
```

Schedule types are `once`, `daily`, `weekly`, `interval`, and `cron`. The scheduler is enabled for persisted jobs, but unattended runs have restricted background authority rather than owner authority. Approval is one-time and scoped.

## Skills, agents, incidents, labs, and security

```text
/skills
/skill propose TASK_ID [NAME]
/skill promote NAME
/skill run NAME INPUTS_JSON
/delegate ROLE GOAL
/incident [system|ai|gpu|ollama|docker|kubernetes|network]
/labs
/lab create SCENARIO
/lab restore ID
/security audit
```

Generated skills remain untrusted until explicit promotion. Incident snapshots diagnose without changing the system. Labs are simulations under `data/work/labs`.

## Evaluation and training readiness

```text
/eval status
/eval quick
/eval full
/eval compare
/training status
/training dataset
/training validate
/training private
/training estimate PROFILE
/training gate
/training candidate [PROFILE METHOD]
/hayat-model status
/hayat-model build
```

`/training candidate` prepares and audits a private-local adapter candidate;
it never starts training. `/hayat-model build` explicitly recreates the local
Ollama identity from its checked-in Modelfile and already-installed foundation
weights. Neither command downloads a foundation model or promotes an adapter.

No one-word command starts model training. Actual fine-tuning is disabled.
