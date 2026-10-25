# How HAYAT keeps learning: experience and situation

HAYAT works in a loop: observe, understand, remember, predict, decide, act,
check the result, learn. Most of that loop already existed: live machine state,
memory, the planner, the tools and the verifier. Two links were missing, so the
loop never closed:

- **Failures were never consulted.** The planner was shown only past tasks
  that succeeded. A failed task was recorded, and the note it left said only
  "inspect the failure before retrying", so the next day the same request
  failed the same way.
- **Nothing held the bigger picture across turns and restarts.** Each new
  conversation started cold: what you had been doing, what was going on last
  session, what keeps failing, and what you usually ask next were all lost. You
  had to explain the same context again.

## Experience: what worked, what failed, what fixed it

[app/experience.py](../app/experience.py) keeps one record for every finished
task: the kind of goal, the tools in order, whether it worked, the first failure
(tool, cause and reason), and, when the task recovered, the tools that worked
after the failure. That last part is the fix.

- **Before planning,** similar past goals become short lessons in the
  planner's context. For example: "Worked 3 times for a similar request:
  research_topic", or "read_file failed 2 times here (not_found: ...). What fixed
  it: search_files -> read_file."
- **When a step fails,** the re-planner is also told what fixed that tool's
  failure before: "When browser_navigate failed with network before, this
  worked 2 times: research_topic."
- **Failure causes** use a small shared vocabulary (not_found, permission,
  network, robots, invalid_input, unavailable, verification, command_failed),
  so different wordings of one cause meet.

Lessons are counts from HAYAT's own past tasks. They are not rules: the planner
weighs them, they never change code or permissions, and newer outcomes outweigh
older ones.

## Situation: what you are doing, and what is likely next

[app/situation.py](../app/situation.py) keeps a running understanding that
survives restarts:

- **Focus:** the areas your requests in the last few hours were about, and your
  latest request. After a break it gives what the last session was about. A
  short follow-up ("ok great", "yes", "explain") counts toward the area before
  it; a greeting does not.
- **Habits:** what you ask most, and what you usually ask at this time of day.
- **Recent tasks**, and what is **waiting** (lead follow-ups due, searches still
  running).
- **What it cannot do right now:** areas that need setup, failures in the last
  day, and tools that fail more often than they work.
- **Likely next:** the areas that usually follow your last request, or that are
  common at this time of day, each with its evidence.

Every prediction is checked against the request that actually comes next, so
the report says how often it was right ("right 12 of the last 40 times"). It
never claims accuracy it hasn't measured. Predictions are offered as context
and suggestions; HAYAT never acts on one by itself.

A compact summary goes into every reply's prompt (under 1 KB, built in about
25 ms), so HAYAT connects your message to what you have been doing. A
child-safe session gets none of it. The planner sees the same summary with its
observation.

## Ask it

- "What am I working on?", "Where did we leave off?", "What's going on?"
- "What do you think I'll ask next?"
- "What have you learned from mistakes?", "What can't you do right now?"

These go to the `situation_report` tool, which reads the records directly; the
model does not compose the answer.

## Where it is kept, and the first start

Both live in `data/memory/experience.db`, beside the conversation history they
are drawn from. On the first start they learn once, in the background, from
history already on disk: your requests in the conversation record and the task
logs in `data/logs`. They read only from 2026-09-30 on, because before that the
test suite wrote fake tasks and turns into the same places. The tests never
touch this database; it is redirected like every store under `data/`.

## What it does not do

It does not claim to be conscious. It does not rewrite its own code or widen its
own permissions. Repeated successful procedures still become automatic only
when you promote them (see the apprenticeship in
[app/apprenticeship.py](../app/apprenticeship.py)). Everything here is data
that informs decisions, with the evidence attached.
