"""Repeat real local storage/streaming/standby operations in an isolated directory.

This measures backend endurance, not live model quality or hardware availability.
A sleeping computer or stale heartbeat fails the continuous-run requirement.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path


def run(directory, seconds=86400, interval=10):
    import psutil
    from app.gui_workspace import DraftStore
    from app.learning.continuous import ContinuousLearner, CycleResult, LearningBudget
    from app.memory.database import MemoryDatabase
    from app.models.streaming import PublicTextFilter
    from app.task_graph import GraphStore
    from scripts.supervise import acquire_lock

    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    lock = acquire_lock(directory / "run.lock")
    if lock is None:
        raise RuntimeError("An endurance run already owns this directory")
    if (directory / "result.json").exists():
        lock.close()
        raise ValueError("Use a fresh run directory; existing evidence is never overwritten")
    memory = MemoryDatabase(directory / "memory.db")
    memory.initialize()
    drafts = DraftStore(directory / "workspace.db")
    graphs = GraphStore(directory / "workspace.db")
    learner = ContinuousLearner(root=directory / "learning", standby_after_budget=True,
        budget=LearningBudget(max_cycles_per_hour=2), cycle=lambda topic: CycleResult(action="index", ok=True))
    process = psutil.Process()
    started = time.monotonic()
    previous = started
    report = {"status": "running", "pid": os.getpid(), "started_at": time.time(), "target_seconds": seconds,
              "scope": "Isolated SQLite memory, draft and plan persistence, public streaming filter, continuous learner budget standby and rollover",
              "cycles": 0, "elapsed_seconds": 0, "max_gap_seconds": 0, "failures": [],
              "baseline_rss_bytes": process.memory_info().rss, "peak_rss_bytes": process.memory_info().rss}
    def persist():
        temporary = directory / "result.tmp"
        report["heartbeat_at"] = time.time()
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(directory / "result.json")
    learner.start(topics=lambda: "endurance fixture", interval=.25)
    try:
        persist()
        while time.monotonic() - started < seconds:
            now = time.monotonic()
            gap = now - previous
            previous = now
            report["max_gap_seconds"] = max(report["max_gap_seconds"], round(gap, 3))
            if gap > max(60, interval * 3):
                raise RuntimeError("Heartbeat gap exceeded continuity limit (host sleep or stalled worker)")
            if (directory / "stop").exists():
                report["status"] = "stopped"
                break
            marker = f"endurance-{report['cycles']}"
            drafts.save("endurance", marker)
            if drafts.load("endurance") != marker:
                raise AssertionError("Draft readback mismatch")
            nodes = [{"id": "1", "goal": marker, "depends_on": []},
                     {"id": "2", "goal": "verify", "depends_on": ["1"]}]
            graphs.save("endurance", nodes)
            if graphs.load("endurance") != nodes:
                raise AssertionError("Plan readback mismatch")
            with memory.connect() as db:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise AssertionError("SQLite integrity check failed")
            memory.record_message("endurance", "user", marker)
            with memory.connect() as db:
                if db.execute("SELECT content FROM conversations ORDER BY id DESC LIMIT 1").fetchone()[0] != marker:
                    raise AssertionError("Conversation readback mismatch")
                db.execute("DELETE FROM conversations WHERE id < (SELECT MAX(id)-100 FROM conversations)")
            filter_ = PublicTextFilter()
            output = "".join(filter_.feed(part) for part in ("<thi", "nk>private", "</think>", marker)) + filter_.feed("", final=True)
            if output != marker:
                raise AssertionError("Public stream filtering mismatch")
            if not learner.running:
                raise AssertionError("Continuous learner exited")
            if learner.ledger.cycles_this_hour > 2:
                raise AssertionError("Continuous learner exceeded hourly cycle budget")
            report.update(cycles=report["cycles"] + 1, elapsed_seconds=round(now - started, 2),
                          learner_cycles=learner.ledger.cycles, learner_state=learner.worker_state,
                          threads=threading.active_count())
            report["peak_rss_bytes"] = max(report["peak_rss_bytes"], process.memory_info().rss)
            if report["peak_rss_bytes"] - report["baseline_rss_bytes"] > 256 * 1024 * 1024:
                raise AssertionError("Memory growth exceeded 256 MiB")
            if report["threads"] > 30:
                raise AssertionError("Thread count exceeded 30")
            persist()
            time.sleep(min(interval, max(0, seconds - (time.monotonic() - started))))
        else:
            report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["failures"].append(f"{type(exc).__name__}: {exc}")
    finally:
        if not learner.stop(timeout=5):
            report["status"] = "failed"
            report["failures"].append("Learner did not stop cleanly")
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        persist()
        lock.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=86400)
    parser.add_argument("--interval", type=float, default=10)
    args = parser.parse_args()
    if not 0 < args.seconds <= 7 * 86400 or not .01 <= args.interval <= 20:
        parser.error("Use 0–604800 seconds and a 0.01–20 second interval")
    result = run(args.directory, args.seconds, args.interval)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "passed" else 1)
