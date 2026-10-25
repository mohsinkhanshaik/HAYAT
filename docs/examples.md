# Prompting guide and complete examples

Natural language is expected. State the goal, evidence, and boundaries—not implementation trivia.

Weak: `FIX IT`

Better: `Inspect this project, reproduce the failure, fix only the verified root cause, rerun tests, and summarize changed files.`

## Research

```text
Research the latest Kubernetes GPU scheduling documentation. Use official sources first, compare at least two independent sources for changing claims, cite retrieved pages, and remember the durable concepts.
```

The agent checks memory/freshness, plans bounded searches, ranks sources, extracts evidence, validates claims/citations, stores distilled knowledge, and answers.

## Coding

```text
Open my active Python project, run its tests, explain the first failure, apply the smallest fix, and verify the complete suite.
```

## Voice and Windows

```text
User: Open Notepad.
AI: Done.
User: Write hello in it.
AI: Done.
User: Close it.
AI: You have unsaved changes. Save them?
```

The session retains the exact Notepad window; UI Automation types; verification reads observed UI; close logic detects the dialog and requests a choice.

## Camera plus research

```text
Look at this device, read its label if confidence is sufficient, then find its official documentation. Do not retain the raw frame.
```

Camera activation -> temporary frame -> OCR/local vision -> confidence check -> bounded official-source research -> citation. Current limitation: local OCR is unavailable, so the vision path must say when text confidence is inadequate.

## Deep incident diagnosis

```text
/mode deep+incident
Diagnose why my local inference server is slow. Gather evidence and propose a fix; do not change configuration yet.
```

Compared with FAST, this uses a stronger suitable local route, more context, resource/model state, hypotheses/counter-hypotheses, and stronger verification.

## Learn Kubernetes

```text
/mode teach
Teach me Kubernetes scheduling. Ask diagnostic questions first.
Quiz me without hints.
/learn weaknesses
/mode teach+lab
Give me a safe GPU scheduling lab.
```

## Resume a remembered project

```text
/remember Project Atlas refers to D:\Projects\Atlas.
...later...
/recall Project Atlas
Open Project Atlas and run its tests.
```

Memory resolves the fact; filesystem approval boundaries still apply.

## Prompt library

- Windows: `Move the active window to monitor two and verify its new bounds.`
- Vision: `Capture monitor one and describe only what is visibly supported.`
- Research: `Check whether this API behavior is still current using version-matched official docs.`
- Learning: `Teach me NCCL Socratically; reveal hints one level at a time.`
- Project: `Use my active project; list uncommitted files and do not edit anything.`
- Incident: `Investigate this Docker networking failure read-only, then rank hypotheses.`
- Private: `/mode private+local` followed by the task.
