"""Noticing that HAYAT was cut off, and being able to say so.

A task that stops halfway leaves a perfectly good record of itself: the plan,
the step it reached, the tools it ran, the results it verified. All of that
was already being written. Nothing ever read it back. So a crash mid-task --
a real one happened today, an ordinary conversational turn taken down by a
Playwright call from the wrong thread -- left a row marked "running" forever
and a user with no idea anything had been in flight.

The mechanism is deliberately small. A marker file records that this process
is alive; a clean shutdown removes it. A marker still present at startup means
the previous run did not get to remove it, which means it died. Tasks left
"running" from that run are then reconciled: marked interrupted, with the step
they reached, so they stop pretending to be in progress and start being
history that can be reported.

What this does NOT do is resume anything by itself. The checkpoints have said
`resume_policy: manual_review_required` since they were first written, and
they are right. A half-executed plan replayed blind repeats whatever the
finished steps already did -- a file written twice, a message sent twice, a
delete re-attempted against something that is no longer there. HAYAT can say
"I got to step 3 of 6 on this and stopped"; deciding whether step 4 should
happen now is Mohsin's, because only he knows whether the world still looks
the way the plan assumed.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


#: States a task can be left in that mean "nobody finished this".
UNFINISHED = ("running",)

#: What an interrupted task is marked as. Distinct from "failed": nothing went
#: wrong with the work, the process stopped underneath it.
INTERRUPTED = "interrupted"


class CrashRecovery:
    """Detects an unclean shutdown and reconciles what it left behind."""

    def __init__(self, memory: Any, marker: Path | None = None) -> None:
        self.memory = memory
        self.marker = Path(marker) if marker else PROJECT_ROOT / "data" / "runtime-alive.json"
        self.previous: dict[str, Any] = {}
        self.unclean = False
        self._owner_token = uuid.uuid4().hex

    # ------------------------------------------------------------- lifecycle

    def mark_started(self, session_id: str = "") -> dict[str, Any]:
        """Claim liveness, after reading whatever the last run left behind.

        Read before write: the marker is the only evidence of the previous
        run, and overwriting it first would destroy the thing being looked
        for.
        """
        self.previous = self._read_marker()
        # A marker naming this very process is not evidence of a crash -- it
        # is this run's own marker, seen again because something called
        # mark_started twice.
        previous_pid = self.previous.get("pid")
        other_process = bool(self.previous) and previous_pid != os.getpid()
        if other_process and self._process_alive(previous_pid):
            # A second GUI/CLI instance must not declare the first crashed or
            # erase its marker on exit. Unknown liveness is treated as live.
            self.unclean = False
            return {"unclean_shutdown": False, "previous": self.previous}
        self.unclean = other_process
        temporary = None
        try:
            self.marker.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.marker.parent,
                                             prefix="runtime-marker-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({"pid": os.getpid(), "session_id": session_id,
                           "owner_token": self._owner_token,
                           "started_at": datetime.now(UTC).isoformat()}, handle)
            temporary.replace(self.marker)
        except OSError:
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return {"unclean_shutdown": self.unclean, "previous": self.previous}

    @staticmethod
    def _process_alive(pid: Any) -> bool:
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        try:
            import psutil
            return psutil.pid_exists(pid)
        except Exception:
            return True

    def _read_marker(self) -> dict[str, Any]:
        """Whatever the previous run left, or nothing.

        A marker that is missing, unreadable, or corrupt all mean the same
        useful thing here: no trustworthy evidence of an interrupted run. A
        crash detector that itself crashes on a truncated file would be a
        strange way to make startup more reliable.
        """
        try:
            data = json.loads(self.marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def mark_clean_exit(self) -> None:
        """Remove only this runtime's marker, after its cleanup succeeds."""
        if self._read_marker().get("owner_token") != self._owner_token:
            return
        try:
            self.marker.unlink(missing_ok=True)
        except OSError:
            pass

    # ----------------------------------------------------------- reconciling

    def unfinished_tasks(self, limit: int = 10) -> list[dict[str, Any]]:
        """Tasks left mid-flight, newest first, with the step each reached."""
        try:
            with self.memory.connect() as db:
                rows = db.execute(
                    "SELECT id, goal, created_at, status, plan_json FROM tasks "
                    f"WHERE status IN ({','.join('?' * len(UNFINISHED))}) "
                    "ORDER BY created_at DESC LIMIT ?",
                    (*UNFINISHED, max(1, min(limit, 50))),
                ).fetchall()
        except Exception:
            return []

        found = []
        for row in rows:
            item = dict(row)
            try:
                plan = json.loads(item.get("plan_json") or "[]")
            except ValueError:
                plan = []
            checkpoints = []
            try:
                checkpoints = self.memory.checkpoints(item["id"], limit=1)
            except Exception:
                log_swallowed(_log, "reading task checkpoints")
            reached = int(checkpoints[0]["step_index"]) + 1 if checkpoints else 0
            found.append({
                "task_id": item["id"], "goal": item["goal"],
                "started_at": item["created_at"],
                "steps_done": reached, "steps_total": len(plan),
                "next_step": _describe_step(plan, reached),
                # Stated on every record rather than assumed: replaying a plan
                # whose finished steps already touched the world is not a
                # neutral act.
                "resume_policy": "manual_review_required",
            })
        return found

    def reconcile(self, limit: int = 10) -> list[dict[str, Any]]:
        """Stop unfinished tasks claiming to be in progress.

        Only ever called for a run that already ended -- a task genuinely
        running right now belongs to this process, and this runs before any
        task of this process has started.
        """
        stranded = self.unfinished_tasks(limit)
        for item in stranded:
            try:
                self.memory.update_task(
                    item["task_id"], INTERRUPTED,
                    f"Interrupted after {item['steps_done']} of {item['steps_total']} steps "
                    "when the previous session stopped. Not resumed automatically.",
                )
            except Exception:
                log_swallowed(_log, "marking an interrupted task", level=logging.WARNING)
        return stranded

    # -------------------------------------------------------------- speaking

    def greeting(self) -> str:
        """What to tell Mohsin at startup, or "" when there is nothing to say.

        Silence is the correct output for the ordinary case. An assistant that
        announces a clean startup every time trains its owner to skip the one
        message that mattered.
        """
        if not self.unclean:
            return ""
        stranded = self.reconcile()
        when = str(self.previous.get("started_at", ""))[:19].replace("T", " ")
        opening = ("I didn't shut down cleanly last time"
                   + (f" -- that session started {when}." if when else "."))
        if not stranded:
            return opening + " Nothing was in progress, so nothing was lost."

        verb = "was" if len(stranded) == 1 else "were"
        lines = [f"{opening} {_count(len(stranded)).capitalize()} {verb} in progress:"]
        for item in stranded:
            lines.append(
                f"  - {str(item['goal'])[:70]}\n"
                f"    got through {item['steps_done']} of {item['steps_total']} steps"
                + (f"; next was {item['next_step']}" if item["next_step"] else "")
            )
        lines.append("I haven't picked any of them back up. Some of those steps already "
                     "changed something, and running them again could do it twice -- tell me "
                     "which one you want and I'll start from where it stopped.")
        return "\n".join(lines)

    def status(self) -> dict[str, Any]:
        return {
            "marker": str(self.marker),
            "last_shutdown_clean": not self.unclean,
            "previous_session": self.previous,
            "unfinished": self.unfinished_tasks(),
        }


def _describe_step(plan: list[Any], reached: int) -> str:
    if 0 <= reached < len(plan):
        step = plan[reached]
        if isinstance(step, dict):
            return str(step.get("description") or step.get("tool") or "")[:70]
    return ""


def _count(number: int) -> str:
    return "one task" if number == 1 else f"{number} tasks"
