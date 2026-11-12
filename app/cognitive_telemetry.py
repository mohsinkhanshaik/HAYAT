"""Live readings of HAYAT's own machinery for the Mind window: model, speed, hardware, work in progress, errors.

Every value is read at the moment it is asked for from the part that owns it (the model provider's last
generation, the hardware profile, the turn log, the research jobs, the learner, the failure log). A part that is
missing or fails to answer is left out of the result; nothing is filled in with a guess.

``explain_recall`` answers "why was this memory retrieved?": it runs the assistant's own retrieval for a question
and returns, for each memory found, the score parts the ranking really used (meaning, shared words, named things,
recency, importance, past usefulness) with the strongest one named.
"""
from __future__ import annotations

import logging
from statistics import median
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

_FEATURE_WORDS = {
    "neural": "close in meaning (embedding)", "hashed_semantic": "similar wording overall",
    "lexical": "shares the question's words", "entity": "names the same things", "project": "same project",
    "recency": "recent", "confidence": "trusted", "successful_use": "helped before",
}


def _safe(label: str, read: Any) -> Any:
    try:
        return read()
    except Exception:
        log_swallowed(_log, f"telemetry: {label}")
        return None


def snapshot(runtime: Any) -> dict[str, Any]:
    """One reading of every part; sections that cannot be read are absent."""
    result: dict[str, Any] = {}
    model = getattr(runtime, "model", None)
    if model is not None:
        result["model"] = {"name": getattr(model, "model", None) or getattr(model, "name", None),
                           "provider": getattr(model, "name", None)}
        from app.models import local

        last = _safe("last generation", lambda: dict(getattr(local, "LAST_GENERATION", {}) or {}))
        if last:
            result["inference"] = last

    def hardware() -> dict[str, Any]:
        from app.hardware import profile
        from app.learning.continuous import _windows_cpu_percent

        machine = profile()
        found: dict[str, Any] = {"cpu_percent": _windows_cpu_percent()}
        if machine.total_ram_bytes and machine.available_ram_bytes is not None:
            found["ram_used_gb"] = round((machine.total_ram_bytes - machine.available_ram_bytes) / 1024**3, 1)
            found["ram_total_gb"] = round(machine.total_ram_bytes / 1024**3, 1)
        gpu = machine.gpu
        if gpu.available:
            found["gpu"] = gpu.name
            found["vram_total_gb"] = round(gpu.total_bytes / 1024**3, 1)
            if gpu.used_bytes is not None:
                found["vram_used_gb"] = round(gpu.used_bytes / 1024**3, 1)
            if gpu.holders:
                found["vram_held_by"] = list(gpu.holders)
        return found

    found = _safe("hardware", hardware)
    if found:
        result["hardware"] = found

    turns = getattr(runtime, "turns", None)
    if turns is not None:
        rows = _safe("turns", lambda: turns.recent(30)) or []
        times = [float((row.get("latency") or {}).get("total_ms")) for row in rows
                 if (row.get("latency") or {}).get("total_ms") is not None]
        if times:
            result["latency"] = {"turns": len(times), "median_ms": round(median(times)), "last_ms": round(times[0]),
                                 "slowest_ms": round(max(times))}

    jobs = getattr(runtime, "research_jobs", None)
    if jobs is not None:
        running = _safe("research jobs", jobs.running) or []
        recent = _safe("recent research", lambda: jobs.recent(5)) or []
        result["research"] = {
            "running": [{"query": job.query, "seconds": round(job.elapsed())} for job in running],
            "recent": [{"query": job.query, "status": job.status, "sources": job.sources,
                        "confidence": round(float(job.confidence or 0), 2)} for job in recent]}

    learner = getattr(runtime, "learner", None)
    if learner is not None:
        status = _safe("learner", learner.status)
        if status:
            result["learning"] = {"state": status.get("worker_state"), "may_run": status.get("may_run"),
                                  "why": status.get("reason"), "queue": (status.get("queue") or {}).get("counts"),
                                  "cycles_this_hour": (status.get("spent") or {}).get("cycles_this_hour"),
                                  "maintenance": status.get("maintenance")}

    network = getattr(runtime, "memory_network", None)
    if network is not None:
        status = _safe("memory network", network.status)
        if status:
            result["memory"] = {key: status.get(key) for key in
                                ("memories", "links", "links_per_memory", "standing_alone", "kinds", "helped",
                                 "did_not_help")}

    topics = getattr(runtime, "topics", None)
    if topics is not None:
        stats = _safe("topics", topics.stats)
        if stats:
            current = next((row for row in (_safe("topic list", lambda: topics.topics(None, 30)) or [])
                            if row["current"]), None)
            result["topics"] = {"current": current["title"] if current else None, "topics": stats["topics"],
                                "messages_placed": stats["decisions"], "corrected_by_you": stats["flagged_wrong"]}

    failures = getattr(runtime, "failures", None)
    if failures is not None:
        rows = _safe("failures", lambda: failures.recent(5)) or []
        result["errors"] = [{"at": str(row.get("created_at") or row.get("timestamp") or "")[:19],
                             "stage": row.get("stage"),
                             "what": (f"{row.get('exception_type') or row.get('failure_class') or 'failure'}: "
                                      f"{row.get('exception_message') or ''}")[:160]}
                            for row in rows]
    return result


def lines(reading: dict[str, Any]) -> list[tuple[str, str]]:
    """The reading as (label, value) rows for display."""
    rows: list[tuple[str, str]] = []
    model = reading.get("model")
    if model:
        rows.append(("Active model", f"{model.get('name')} via {model.get('provider')}"))
    inference = reading.get("inference")
    if inference:
        rows.append(("Last generation", f"{inference.get('tokens')} tokens at {inference.get('tokens_per_second')} "
                                        f"tokens/s ({inference.get('model')}, {inference.get('at', '')[11:19]} UTC)"))
    hardware = reading.get("hardware") or {}
    if hardware.get("cpu_percent") is not None:
        rows.append(("CPU", f"{hardware['cpu_percent']:.0f}%"))
    if "ram_used_gb" in hardware:
        rows.append(("RAM", f"{hardware['ram_used_gb']} of {hardware['ram_total_gb']} GB in use"))
    if "vram_total_gb" in hardware:
        held = f", held by {', '.join(hardware['vram_held_by'])}" if hardware.get("vram_held_by") else ""
        rows.append(("GPU memory", f"{hardware.get('vram_used_gb', '?')} of {hardware['vram_total_gb']} GB "
                                   f"({hardware.get('gpu')}{held})"))
    latency = reading.get("latency")
    if latency:
        rows.append(("Reply time", f"median {latency['median_ms']} ms over the last {latency['turns']} turns; "
                                   f"last {latency['last_ms']} ms, slowest {latency['slowest_ms']} ms"))
    research = reading.get("research")
    if research is not None:
        running = "; ".join(f"“{job['query'][:50]}” ({job['seconds']} s)" for job in research["running"])
        rows.append(("Searches running", running or "none"))
        for job in research["recent"][:3]:
            rows.append(("Recent search", f"“{job['query'][:60]}”: {job['status']}, {job['sources']} sources, "
                                          f"confidence {job['confidence']}"))
    learning = reading.get("learning")
    if learning:
        rows.append(("Background learning", f"{learning.get('state')}: {'may run' if learning.get('may_run') else 'waiting'}"
                                            f" ({learning.get('why')}); queue {learning.get('queue')}"))
    memory = reading.get("memory")
    if memory:
        rows.append(("Memory network", f"{memory.get('memories')} memories, {memory.get('links')} links "
                                       f"({memory.get('kinds')}); recalls that helped {memory.get('helped')}, "
                                       f"did not {memory.get('did_not_help')}"))
    topics = reading.get("topics")
    if topics:
        rows.append(("Conversation", f"current topic: {topics.get('current') or 'none yet'}; {topics['topics']} topics, "
                                     f"{topics['messages_placed']} messages placed, {topics['corrected_by_you']} "
                                     "corrected by you"))
    for error in reading.get("errors") or []:
        rows.append(("Recent error", f"{error['at']} {error.get('stage') or ''}: {error['what']}"))
    return rows


def _linked(row: dict[str, Any]) -> list[str]:
    """A memory that came in through a link, not through the question's own words."""
    how = row.get("retrieval") or {}
    if how.get("strategy") != "dna_associative":
        return []
    return [f"linked to memory #{how.get('seed_id')} by “{how.get('relation')}” "
            f"({how.get('depth')} step{'s' if how.get('depth') != 1 else ''}, strength {how.get('path_weight')})"
            + (" and contradicts it" if how.get("contradiction") else "")]


def explain_recall(memory: Any, question: str, limit: int = 5) -> list[dict[str, Any]]:
    """Why each memory comes back for ``question``: the ranking's own score parts, strongest first."""
    found = memory.retrieve(question, limit=limit)
    explained = []
    for row in found:
        parts = {key: float(value) for key, value in (row.get("retrieval") or {}).items()
                 if key in _FEATURE_WORDS and isinstance(value, (int, float)) and value > 0}
        ordered = sorted(parts.items(), key=lambda item: -item[1])
        explained.append({
            "memory_id": row.get("id"), "type": row.get("memory_type"), "text": str(row.get("content", ""))[:200],
            "score": round(float(row.get("relevance") or 0), 3),
            "because": [f"{_FEATURE_WORDS[key]} ({value:.2f})" for key, value in ordered[:3]] or _linked(row),
            "linked_from": (row.get("retrieval") or {}).get("seed_id")})
    return explained
