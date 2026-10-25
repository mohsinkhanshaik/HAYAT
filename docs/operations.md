# Operations, backup, and recovery

## Safe update sequence

```powershell
cd D:\AI
git status --short
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\doctor.py
git log --oneline -10
```

Back up mutable databases under `data/memory` and `data/knowledge`, make one scoped change, rerun tests/doctor, review `git diff`, then commit. Do not blindly update every dependency.

```text
/backup status
/backup create before-upgrade
```

The backup manager uses SQLite's consistent backup API, hashes every output, writes a manifest under `data/backups`, excludes `.env`, and never restores automatically. Restore is deliberately manual and reviewable.

## Recovery

Git restores tracked code; it does not automatically restore ignored databases/audio/browser state. Copy database files while the assistant is stopped. For an unwanted local code change, inspect it first with `git diff`; avoid destructive reset commands when work is unreviewed.

Durable job executions and task checkpoints show the last verified step after interruption. Jobs never silently resume a high-risk/manual-review state. `/events`, `/tasks`, and `/job executions` help reconstruct what happened.

## Strict release gate

```powershell
.\.venv\Scripts\python.exe scripts\release_gate.py
```

The gate compiles code, runs every automated test, checks generated documentation, runs the doctor, and reasserts zero-cost policy. Each run is bound to the source it tested: the manifest records the commit and a content id of the tested files (the git tree of every tracked and untracked, non-ignored file as it was on disk, built in a temporary index), each check's exact command and real exit status, the JUnit outcomes read from the test cases themselves, and SHA-256 hashes of the artifacts. It writes `data/benchmarks/release/latest.json` plus `runs/<id>/manifest.json` and `pytest.xml` (runs are not committed). Promotion is blocked if any check fails, the JUnit file is missing or has failures or errors, an artifact is missing, or the source changed while the checks ran.

```powershell
.\.venv\Scripts\python.exe scripts\release_gate.py --verify
```

`--verify` (optionally with a manifest path) re-checks a recorded run against today's tree: it recomputes every outcome instead of trusting the stored flag, re-reads artifact hashes, and refuses evidence for other files, an edited JUnit file or a deleted artifact. Because the id depends only on content, committing the tested files unchanged keeps the evidence valid.

## Windows validation control plane

```powershell
.\scripts\windows_validation.ps1 -Quick      # compile, tests, doctor, docs check (~1 min)
.\scripts\windows_validation.ps1 -Standard   # + release gate, local model round trip, browser, pywinauto window control
.\scripts\windows_validation.ps1 -Full       # + camera capture, real speaker-to-microphone voice loopback, OCR accuracy
```

This wraps the existing check scripts rather than duplicating their logic, and classifies each real outcome as `PASS`, `FAIL`, `SKIPPED`, `UNAVAILABLE` (hardware/dependency not present on this machine), or `NOT_CONFIGURED` (a required service, e.g. Ollama, is not reachable) -- it never upgrades a script that didn't run into a passing result. `-Full` exercises the camera and speaks audio through the system output while listening on the microphone; run it somewhere that is fine to briefly hear and be recorded transiently (nothing is retained by default). Writes `data/benchmarks/windows_validation/latest.json` and `latest.md`. A `FAIL` on a hardware-loopback check (voice, camera) can be genuinely flaky run-to-run because it depends on physical speaker-to-microphone coupling -- re-run once before treating it as a regression; a `FAIL` on `compile`/`pytest`/`release_gate`/`windows_window_control` is not expected to be flaky and should be investigated.

## Startup helper

Optional login startup scripts, if present under `scripts`, must be run explicitly. The project never silently adds itself to Windows startup.
