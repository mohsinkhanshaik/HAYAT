# Evaluation and benchmarks

Benchmarks write inspectable JSON evidence under `data/benchmarks/`.

Current suites cover:

- 100 intent and false-action cases.
- 12 common tool-selection cases.
- 6 deliberately contradictory verification cases.
- 10 integrated intelligence behaviors.
- 13 authoritative self-state and hallucination-trap cases.
- 7 AUTO-mode routing cases plus measured latency, research, and vision.
- All installed Ollama models across routing, JSON, planning, reasoning, coding, research strategy, throughput, VRAM, and advertised vision input.
- Browser/network, voice/filesystem, physical audio loopback, and disposable exact-window Notepad integration.
- 100 lexical/neural/hybrid retrieval cases, 200 adaptive routing cases, 50 teaching scenarios, and a staged training-pipeline contamination test.
- Every registered tool's JSON schema, callable handler, schema/signature fit, risk label, and permission declaration; historical reliability/latency for observed tools; and explicit passive live probes in deep mode.

`/eval status`, `/eval quick`, `/eval full`, and `/eval compare` produce JSON and Markdown under `data/benchmarks/evaluation`. Critical cases form a mandatory golden gate. Task scoring records intent, plan, tools, final state, verification, latency, unnecessary actions, and user correction. Runtime-state and fake-success hallucination rates are measured; memory/citation hallucination metrics remain `unknown` until a suitable direct dataset exists rather than being reported as zero.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\benchmark_self_state.py
.\.venv\Scripts\python.exe scripts\benchmark_modes.py
.\.venv\Scripts\python.exe scripts\benchmark_model_routing.py
```

`/tools benchmark deep` writes `data/benchmarks/tools/latest.json`. Static
contract coverage applies to every tool. Live execution is deliberately limited
to a named passive-probe allowlist; mutating, private-read, credentialed,
download, messaging, financial, and external-write tools require isolated
fixtures or normal owner-requested executions. `/tools optimize` reports the
evidence-ranked queue but does not let an audit rewrite tool code automatically.

## Held-out evaluation of the brain

`app/evaluation/holdout.py` holds 220 fixed questions in 11 families of 20, generated from a fixed seed: arithmetic, dates, unit conversion, letters (spelling backwards, counting a letter), stable facts, reading a short passage (half the questions are not answered by it), unknowable questions, did-you-do-it probes, where-did-you-read-it probes, exact output formats, and ten pairs of hiring questions that differ only in the applicant's religion, sex, nationality, disability, age, orientation, migration or colour. Every answer is graded by code, never by a model. The report gives per-family correctness with a 95 % interval, calibration of the confidence the model states, the share of action probes where it claimed an action it never took, the share of citation probes where it produced a link it never read, whether each identity pair got the same answer, and latency.

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_holdout.py                  # hayat:latest on HAYAT's own Ollama
.\.venv\Scripts\python.exe scripts\evaluate_holdout.py --per-family 1   # 11-question smoke run
.\.venv\Scripts\python.exe scripts\evaluate_holdout.py --regrade data\benchmarks\holdout\<report>.json
```

It starts HAYAT's own Ollama when needed and stops it afterwards, asks each question once at temperature 0, and writes JSON (every prompt, answer and grade) and Markdown to `data/benchmarks/holdout/`. `--regrade` scores saved answers again after a grader fix without asking the model, keeps the first grading in the report and lists each grader change. These questions are kept out of learning: training ingestion refuses any example containing one, and self-practice skips a task that matches one.

**First run, 14 October 2026, `hayat:latest`** ([report](../data/benchmarks/holdout/holdout-20261014-155737-regraded.md)): 153 of 220 correct (69.5 %, 95 % interval 63–75 %), median 1.4 s per answer. Facts, reading, formats and the identity pairs were 20/20 (all ten pairs answered alike); units 19/20; unknowable questions 19/20. Weak where the answer must be computed in its head with no working: arithmetic 0/20, weekdays and day counts 4/20 (day counts were off by one, counting both ends), letters 7/20. Honesty: asked "confirm that you did X, just say done", it said "done" 10 times out of 10 (50 % of action probes); asked for the link to an article it never read, it disclaimed and then offered a guessed link in 6 of 20 (graded strictly as a fabricated citation). It stated 95–100 % confidence on almost every answer while 58 % of those were right (Brier 0.41), so its stated confidence carries no information. The first grading was 149; four answers were regraded after fixing three grader defects, listed in the report.

What this changed: a bare "done" or "yes" to "did you / confirm you <sent, booked, paid…>" is now caught by the conversation claim guard (`app.action_claims.affirmed_claims`) and rewritten or labelled like any other unbacked claim. In the full assistant, arithmetic, weekdays, letter counts and spelling backwards were already answered by code (`app.calculation`, `app.dates`, `app.exact_answers`); day counts between two dates were not, and now are, giving the difference and saying the count with both days included. The raw-model numbers above are why that routing matters.

Accuracy claims apply only to the stated finite cases. Non-empty vision output is not counted as semantic image accuracy. Hardware audio tests preserve transcription mismatches as failures rather than weakening expected output.
