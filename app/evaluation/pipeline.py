"""Ask evaluation questions through HAYAT's whole assistant, not only its language model.

The held-out set (app/evaluation/holdout.py) graded the bare brain: arithmetic 0/20,
fabricated actions in half the probes. The owner never talks to the bare brain. His
messages go through routing, exact calculators, research, claim guards and the
conversation model. This transport sends each case through ``assistant.handle`` in a
session of its own (no case inherits another's referents), waits for any research it
handed to the background (the answer the owner would end up reading), and returns the
text the grader scores. The graders are unchanged, so the two reports compare directly.

Run it only against a sandboxed runtime: every turn is recorded like a real one.
"""
from __future__ import annotations

import time
from typing import Any, Callable

TRANSPORT = "assistant-pipeline-v1"


def confine_files(runtime: Any, folder: Any) -> Any:
    """Point the file tools at ``folder`` for an evaluation; returns the folder.

    The data sandbox redirects databases only, and the first live run wrote six answer
    files into the project. With the project root and the only allowed root set to a
    scratch folder, a misrouted write lands there (and is reported) and cannot reach
    the owner's files.
    """
    from pathlib import Path

    permissions = runtime.registry.permissions
    root = Path(folder).resolve()
    root.mkdir(parents=True, exist_ok=True)
    permissions.project_root = root
    permissions.config = {**permissions.config, "allowed_roots": ["."]}
    return root


def pipeline_generate(runtime: Any, *, wait_seconds: float = 240.0,
                      poll: float = 1.0, sleep: Callable[[float], None] = time.sleep) -> Callable[[str], str]:
    """A prompt -> answer function for ``holdout.run`` that goes through the full assistant."""
    converse = pipeline_converse(runtime, wait_seconds=wait_seconds, poll=poll, sleep=sleep)
    return lambda prompt: converse([prompt])[0]


def pipeline_converse(runtime: Any, *, wait_seconds: float = 240.0, poll: float = 1.0,
                      sleep: Callable[[float], None] = time.sleep) -> Callable[[list[str]], list[str]]:
    """turns -> replies: one conversation in a session of its own, each turn answered as the owner would see it."""
    from app.session import SessionState

    counter = [0]

    def ask(prompt: str) -> str:
        jobs = getattr(runtime, "research_jobs", None)
        before = {job.job_id for job in jobs.recent(50)} if jobs is not None else set()
        reply = runtime.assistant.handle(prompt).response.display_text or ""
        if jobs is not None:
            for job in [job for job in jobs.recent(50) if job.job_id not in before]:
                deadline = time.monotonic() + wait_seconds
                while jobs.get(job.job_id) in jobs.running() and time.monotonic() < deadline:
                    sleep(poll)
                finished = jobs.result_text(jobs.get(job.job_id))
                if finished:
                    reply = finished
        return reply

    def converse(turns: list[str]) -> list[str]:
        counter[0] += 1
        session = f"eval-{counter[0]}-{time.time_ns()}"
        original, original_chat = runtime.session, runtime.conversation.session_id
        runtime.session = SessionState(session)
        runtime.conversation.session_id = session
        try:
            return [ask(turn) for turn in turns]
        finally:
            runtime.session = original
            runtime.conversation.session_id = original_chat

    return converse
