# HAYAT executive brain and invention workbench

Added October 9, 2026. Restart HAYAT, then open **Invention** from the sidebar.
**Load example** now starts an editable sensor-calibration, delivery-cost or
distance-response draft. Create it to freeze the cases, then **Work it out** to
run a local numeric fit. These are synthetic teaching examples, not experimental
evidence. Saved projects stay intact, and replacing unsaved editor text asks
first. The fitter also handles a bounded setup-plus-product family for small
input sets. Workflow checks include unseen points.
Updated October 23: responsive panes and scrolling, evidence audit and next
experiments, round comparison, interactive simulation probes, stronger
cancellation and journal integrity, grouped cross-validation and honest
simulation-failure counts. See the implementation and verification record.
The Operator brain now has **20 nodes and 29 contracts**, including the
Prefrontal Cortex, Dual Brain and Invention nodes. Double-click one of these
nodes to open the workbench.

## Artificial Prefrontal Cortex

The software executive controller keeps the current goal, constraints,
acceptance criteria, stage and recent observations. It supplies the action
planner and conversation model with a goal contract. It checks bounded cycles,
model calls, elapsed time and cancellation before starting more work. Its
working memory retains the latest 16 observations; the workbench journal keeps
completed rounds durably. Tool execution continues through the existing
authority kernel and verifier.

## Dual brain intelligence

Substantive Deep and Adversarial conversation turns use a proposer, a skeptical
critic and at most one revision. The existing router admits the models. A
measured specialist can critique when available; otherwise both roles use the
admitted HAYAT model sequentially. Reports identify the models and whether they
are the same. Critiques are model opinions. Model agreement does not become
independent evidence or a verified success. Internal review text is withheld
from public answer streaming. Bad, empty or truncated reviews are reported as
unavailable; a bad revision retains the original draft.

`executive_brain.dual_review_enabled` in settings controls automatic review.
The normal answer completeness and unsupported-action checks still apply.
Review adds up to two model calls, so Deep turns can take longer.

## The workbench window (2026-10-11)

- **Overview:**
  - the state, in colour and in plain words;
  - a chart of every round: bars for cases passed, gold dots for closeness
    (higher is closer). Click a round to open it;
  - the best candidate written out: hypothesis, design, formula, assumptions
    and the tests still to do.
- **Rounds:** each round's candidate, cases passed, closeness and the critic's
  verdict, marked ★ for the best. Select a round to read it and its critique.
  The critique is a model opinion, never verification.
- **Cases & try a formula:**
  - every frozen case with its expected value, actual value, how far off it
    was, and ✓ or ✗, for the selected round;
  - **Grade it** runs your own formula on the same cases. It is graded but never
    saved as a round, so it cannot change the journal.
- **Research:** the sources a run read, or the gap if research failed.
- **Raw:** the full saved project.
- **Run** takes 1 to 8 cycles from the spin box.
- **Add a case:** type the inputs as `x=2, y=3` and the expected value, then
  press **Add**. The case is appended to the JSON, so you never have to write
  it by hand.
- **Check cases** validates the JSON before you create a project.
- **Export report** writes a Markdown report to
  `data/invention/exports/<project>.md`.

**Tolerance.** By default every case must match exactly. A tolerance, such as
0.01 for within 1 %, lets a case pass when
|actual − expected| ≤ tolerance × max(1, |expected|). It can be at most 0.5.
The tolerance is frozen into the contract hash with the cases, so changing it
later blocks the run. Exact projects keep their original hash, so older
journals stay valid.

**Closeness.** Every case records its relative error. When no round passes
every case, the best round is chosen this way:
1. the most cases passed;
2. then the formula that ran on the most cases;
3. then the smallest mean relative error.

Before this, a near miss and a wild guess counted the same.

## Working it out and simulating it (2026-10-17)

Code: [app/invention_sim.py](../app/invention_sim.py). Tests: [tests/test_invention_sim.py](../tests/test_invention_sim.py).

**Richer formulas.** A formula may now use `pi`, `e`, `tau` and the functions `sqrt`, `exp`, `log`
(`ln`), `log10`, `log2`, `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `sinh`, `cosh`, `tanh`, `abs`,
`min`, `max`, `floor`, `ceil` and `hypot`. Exact arithmetic is tried first. Only a formula that exact
arithmetic cannot express is evaluated in floating point, and each case records which it was
(`exact`). A floating-point result rarely matches an exact case, so physical formulas usually need a
tolerance. Each formula is checked node by node: only numbers, names, arithmetic and the listed
functions are allowed. Attributes, imports, lambdas, indexing and keyword arguments are refused. All
numbers become floats, so `10**10**10` overflows at once instead of hanging.

**Unknown constants are fitted.** A name that is not a case input, such as `c1` in `c1 * x ** c2`,
is a constant to fit, up to six per formula. HAYAT fits the constants to the frozen cases by least
squares from several starting points. It then writes them as the shortest numbers that grade no
worse (`2 * x`, not `1.9999999998 * x`). With a tolerance, a tenth of it is allowed as slack, so
`sqrt(L)` rather than `L ** 0.50000012`. Identities drop out (`1 * h * w` becomes `h * w`), and
negatives read naturally (`a - 0.15 * x`). The proposer is told this, so the model chooses the shape
and HAYAT works out the numbers. A model round with fitted constants records the shape it gave and
the constants found.

**Work it out (no model).** **Work it out** in the window, `invention_work_out`, or
"Work out invention invent-…" in chat fits up to 48 standard shapes to the cases:
- a constant;
- proportional, straight line, quadratic, exponential, power law, logarithmic and inverse in each input;
- a line in every input;
- products, ratios and power laws across inputs.

They are ranked:
1. Shapes with fewer constants than distinct input points first. Repeated measurements at one point do not increase this count.
2. The most cases passed.
3. For the closest eight, **leave-one-input-group-out**: each case is predicted by a fit that excludes every repetition of its input point. This internal check follows shape selection and is not an independent external test. It is unavailable when there are too few distinct input points.
4. Fewer constants.

The best becomes a round marked "fitted, no model", listing the alternatives that came close.

**Runs.** A run on a project with cases first adds one worked-out round, unless **Work it out first** is
unticked or `work_out: false` is passed. This gives the proposer a baseline to beat. The proposer also
sees every earlier formula and its score (`tried_so_far`), so it doesn't repeat one. When no model is
available and the project has cases, the run finishes with the worked-out formula instead of stopping
as BLOCKED, and says that no new design was proposed. A project without cases is still blocked without
a model.

**Simulate.** At the end of a run or work-out, and from **Simulate** (`invention_simulate`,
"simulate invention invent-… with 20% spread"), the formula is run over its inputs:
- **Sweeps:** each input from a quarter below to a quarter above the cases' range, the others held at
  their median. Each sweep says whether the result rises, falls, rises then falls, and so on. It
  marks where the formula has no value, and where it shoots off to infinity (a sign flip with an
  outsized jump). Either one inside the cases' range is a warning.
- **Sensitivity:** the effect of a 10 % rise in each input, largest first.
- **Monte Carlo:** every case run again with each input off by up to ± the spread (10 % by default,
  1 to 50 %). It reports the 5th, 50th and 95th percentiles and how often the result stays within
  the tolerance (or 1 % for exact projects). It is seeded, so the same project gives the same
  numbers. Failed evaluations stay in the denominator and their count and evaluation coverage
  are reported. A zero input is perturbed using the largest observed absolute value for that
  input, or one unit if all its supplied values are zero; the structured result records this scale.

The Simulate tab draws the sweeps (the cases' range shaded, red marks where there is no value) and
the sensitivity bars. **Simulate my formula** runs the formula typed under **Cases & try a formula**,
fitting its constants first. A simulation shows what the formula does, not that a device follows it.
Reports and replies say so.

**Example.** Five cases of a pendulum's period, T = 2π√(L/9.81) to six decimals, with tolerance 0.001.
Work it out finds "a power law in L: `2.006 * sqrt(L)`", which passes 5/5. Leave-one-out predicts
every case from the other four within 2.4 × 10⁻⁷. The simulation says the period rises with length, a
10 % longer pendulum ticks 4.9 % slower, and the formula has no value for negative lengths.

## Tested hard (2026-10-17, later the same day)

**Without a model:** [scripts/benchmark_invention.py](../scripts/benchmark_invention.py) gives the
work-out 16 real laws from physics, engineering and finance. Each comes with a lab-style tolerance
and held-out cases that are graded after the fit, so memorising the supplied cases does not count.
Several use 3–4 inputs, a tiny constant, or 1–2 % measurement noise.

The first run solved 8 of 16. What fixed the misses:

| Missed | Why | Fix |
|---|---|---|
| Gravitation | The constant 6.674e-11 can't be found by searching from 1 | Power laws, exponentials and the logistic curve start from a closed-form fit in log space |
| Radioactive decay, compound interest, a battery's internal resistance, logistic growth, cooling towards room temperature | Their shapes weren't in the list | Added: x × exponential in y, x × (1 + y)^z, x ÷ (y + c), the logistic curve, settling towards a level |
| Projectile height | Needs a quadratic in two inputs | Added it; constants that are really zero are dropped |
| Damped oscillation | The fit can't find a frequency it doesn't start near | Starts from a spread of frequencies |
| Gravitation, again | The masses multiply to 10^53 before the division, beyond the exact evaluator's 10^30 limit | Such a step now falls back to floating point; only the final value is limited |

It now solves **16 of 16**, each also passing its held-out cases, and the formulas read like the
textbook:
- `6.674e-11 * m1 * m2 / r ** 2`
- `P * (1 + r) ** n`
- `V / (R + 1 / 2)`
- `1000 / (1 + 9 * exp(-(1 / 2) * t))`
- `exp(-(3 / 10) * t) * cos(2 * t)`
- `2.006 * sqrt(L)`

Each constant is written as a whole number or simple fraction when it is one (1/3, not 0.333), and
otherwise with the fewest digits that grade no worse. `x ** -1` reads as a division, `x ** (1/2)` as
`sqrt(x)`, and `-1 * x` as `-x`.

When two fits explain the cases equally well, the one with the smaller constants wins. Fitting
`c1*sin(c2*x)/x` to cases at multiples of 0.5 returned `-sin(11.57x)/x`, which matches sin(x)/x at
every one of those points (aliasing); it now returns `sin(x) / x`. A formula that fails inside Python
(a complex result, an overflow) is an ordinary failed case, never a crash.

**With the model:** [scripts/live_invention_trial.py](../scripts/live_invention_trial.py) starts HAYAT's
own Ollama, runs tasks no standard shape solves, and stops it again. It keeps its workbench under
`.test-runs`. On `hayat:latest`, both trials solved every task, each passing its held-out cases:

| Task | Without a model | With the model |
|---|---|---|
| Distance from the origin | 3/7 | `sqrt(x ** 2 + y ** 2)` |
| Shipping with a minimum charge | 1/8 | `max(5, 0.5 * w)` |
| A diffraction pattern | 0/8 | `sin(x) / x` |
| Income tax with an allowance | 3/7 | `max(0, I - 10000) * 0.2` |
| Thin-lens image distance | 1/7 | `(f * d) / (d - f)` |

The first trial covered the first three tasks. In it the model proposed `(sin(x)/x)**2` twice, and the
critic accepted it although it failed all eight cases, because the critic reviewed before anything was
measured. Two changes followed:
- A proposal that repeats an earlier formula is sent back once with its score.
- The critic now gets the measurement first: the cases passed, the worst misses, and "the measurement
  outranks any argument".

In the second trial every task was solved in its first model round, 20–55 s each. One run doesn't
prove the change caused that. Evidence: `.test-runs/live-invention-20261017-144619.json` and
`-145040.json`.

## Autonomous invention cycles

1. Enter a goal and constraints, then press **Create project**.
2. Optionally supply numeric acceptance cases as JSON. Decimal strings preserve
   exact input values. For example:

   ```json
   [{"inputs":{"x":2},"expected":4}, {"inputs":{"x":-3},"expected":-6}]
   ```

3. Select **Research primary sources** if public research is useful, then press
   **Run 3 cycles**. The workbench generates a hypothesis, design, formula,
   assumptions and next tests; obtains critique; runs the formula against the
   supplied cases; and uses measured failures as feedback for the next cycle.
4. Inspect the full saved project, critique and measurements. **Stop** requests
   cancellation. **Refresh** and selecting a saved project reload its journal.
   Running it again resumes with its preceding round as context.

Cases are frozen when the project is created and protected by a contract hash.
The model cannot rewrite their expected values. Numeric experiments use the
bounded exact evaluator first (supplied variables, +, -, *, /, integer powers
and parentheses). Only when exact arithmetic cannot express a formula do they
use the checked floating-point evaluator, with the constants and functions
listed above. They do not execute arbitrary generated Python or modify the
machine. A physical or software design can be produced as a proposal
even when a numeric experiment does not apply; its next tests remain explicit.

- **TESTED:** all supplied numeric cases passed. This covers those cases only.
- **TEST_FAILED:** the bounded run completed with failing numeric cases.
- **NEEDS_VALIDATION:** a proposal exists without supplied numeric tests.
- **BLOCKED:** a provider, artifact or other required input failed.
- **STOPPED / BUDGET_REACHED:** completed rounds remain saved.
- **INTERRUPTED:** the previous process ended during a run; a new run can resume.

Novelty, patentability and physical validity remain unverified. Research failure
is retained as an evidence gap. Public research uses the governed research tool,
including its privacy and cost rules. One run is active per workbench; a SQLite
lease also prevents simultaneous runs of the same project across processes.
At most 8 cycles run per request, 3 model calls per cycle, 600 seconds between
checkpoints, and 32 rounds per project. Individual in-flight requests retain
their provider/fetch timeout; stop is cooperative at cancellation checkpoints.

The journal is local SQLite at `data/invention/workbench.db`. No training
examples are approved and no model weights are promoted by the workbench.

## Chat and CLI

These explicit commands use the same governed tools without a planning-model call:

```text
Create invention project: design a low-cost water monitor
List invention projects
Inspect invention invent-<32 hex characters>
Run invention invent-<32 hex characters> for 3 cycles
Resume invention invent-<32 hex characters> for 2 cycles
Stop invention invent-<32 hex characters>
Try formula x * 2 on invention invent-<32 hex characters>
Try formula c1 * x ** c2 on invention invent-<32 hex characters>
Work out invention invent-<32 hex characters>
Simulate invention invent-<32 hex characters>
Simulate invention invent-<32 hex characters> with 20% spread
Simulate formula c1 * sqrt(L) on invention invent-<32 hex characters>
Prefrontal cortex status
Dual brain status
```

A project created from chat has a goal but no test cases yet. Since
2026-10-24 its reply says so in plain words: what was created, its id, that a
test case is input numbers with the result you expect (for example flow 2 L/min
and 30 minutes → 60 L), and the next step — add cases in **Invention** (Load
example shows the format), then "Work out invention <id>".

Replace the identifier with the actual saved project ID. The GUI supports
constraints and frozen numeric cases when creating a project.

## Research behind the update

The October 9 review used primary sources, including 2026 updates through September:

- [Google Developers, The anatomy of harness engineering, September 9, 2026](https://developers.googleblog.com/the-anatomy-of-harness-engineering-how-to-evaluate-iterate-and-guard-ai-coding-agents/):
  combine behavioral checks with end-to-end trials. HAYAT's regressions cover
  cancellation, recovery, frozen grading inputs and failed review contracts;
  the real local-model trial separately exercises proposal and evaluation.
- [Google DeepMind, Conjecture machines, July 2026](https://deepmind.google/public-policy/conjecture-machines-ai-agents-and-the-new-validation-bottleneck-in-science/):
  generating plausible ideas and validating them remain different tasks.
  HAYAT retains proposed designs and next tests, and restricts its measured
  success claim to supplied numeric cases. Physical validity and novelty need
  further evidence.
- [Anthropic, Harness design for long-running application development, March 24, 2026](https://www.anthropic.com/engineering/harness-design-long-running-apps):
  separate planning, generation and evaluation; define observable sprint criteria
  and preserve structured artifacts. HAYAT uses bounded goal contracts, critique
  and saved rounds. Its numeric evaluator supplies objective checks for the
  supported experiment scope.
- [Google DeepMind, AlphaEvolve impact, May 7, 2026](https://deepmind.google/blog/alphaevolve-impact/):
  algorithm evolution gains value from measurable evaluation. HAYAT applies that
  principle by keeping acceptance cases fixed and feeding failures back into
  refinement. This is a narrower local implementation.
- [Google Research, AI co-scientist, February 19, 2025](https://research.google/blog/accelerating-scientific-breakthroughs-with-an-ai-co-scientist/):
  hypothesis generation, reflection and evolution motivate the workbench's
  proposed-hypothesis, critique and experiment stages. Its biomedical results
  are not evidence for HAYAT's own performance.
- [Anthropic, Effective harnesses for long-running agents, November 26, 2025](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents):
  persistent progress and explicit state motivate interruption recovery and
  durable working artifacts.

The implementation choices above are adaptations inferred from these sources,
not a replication of their systems or results.

## Real local trial

The current `ollama/hayat:latest` model generated a profit formula and completed
a skeptical review using the same model for both roles. The formula passed
three frozen cases: profits 16, 25 and -2, including a loss case. One round took
43.34 seconds. The exact evaluator, rather than the critic's acceptance,
established those three results. This small test does not measure general
reasoning quality or invention novelty.

Evidence: `.test-runs/live-invention-20261009.json`. An initial live probe found
that structured responses were being sent through a streaming provider method
that did not accept a schema. Structured generation is now buffered through
the provider's schema-capable method, with cancellation checks retained.
