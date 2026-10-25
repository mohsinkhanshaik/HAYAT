from __future__ import annotations

import logging
import re
import threading
import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


class Activity(StrEnum):
    IDLE = "idle"
    CONVERSATION = "conversation"
    PLANNING = "planning"
    RESEARCHING = "researching"
    CODING = "coding"
    BROWSING = "browsing"
    COMPUTER_CONTROL = "computer_control"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    SPEAKING = "speaking"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentStateService:
    """Authoritative operational state. This object observes components; it never asks an LLM."""

    def __init__(self, *, model, reasoning_model, agent, registry, memory, session, storage, research, vision, modes, projects) -> None:
        self.model = model
        self.reasoning_model = reasoning_model
        self.agent = agent
        self.registry = registry
        self.memory = memory
        self.session = session
        self.storage = storage
        self.research = research
        self.vision = vision
        self.modes = modes
        self.projects = projects
        self.voice = None
        self.camera = None
        self.displays = None
        self.clipboard = None
        self.browser = None
        self.observability = None
        self.cost_policy = None
        self.model_resources = None
        self.self_change = None
        self.activity = Activity.IDLE
        self.current_plan: list[dict[str, Any]] = []
        self.current_step: dict[str, Any] | None = None
        self._internet_cache: tuple[float, dict[str, Any]] | None = None
        self._lock = threading.RLock()

    def set_activity(self, activity: Activity | str) -> None:
        with self._lock:
            self.activity = Activity(activity)

    #: How many tools to summarise in a state snapshot. The snapshot goes into
    #: a prompt, so this is a budget, not a preference: ninety full records
    #: would crowd out the sentence they are supposed to help answer.
    _TOOL_EXPERIENCE_LIMIT = 8

    def _tool_experience(self) -> list[dict[str, Any]]:
        """The tools HAYAT has actually used, and how they went.

        Asked what tools it had and which it trusted, HAYAT answered "I
        currently have 91 registered tools" -- six words, because a count was
        the only thing about tools the snapshot carried. The registry knew
        their names and purposes and the record knew how they behaved, and
        neither reached the prompt. This puts the busiest few in, so the
        answer can be about experience rather than arithmetic.
        """
        understanding = getattr(self.registry, "understanding", None)
        if understanding is None:
            return []
        try:
            records = understanding.totals()[: self._TOOL_EXPERIENCE_LIMIT]
        except Exception:
            return []
        return [{
            "tool": record["tool"], "calls": record["calls"],
            "worked": record["succeeded"], "failed": record["failed"],
            "typical_ms": record["avg_ms"],
            "verdict": ("too early to say" if not record["enough_evidence"]
                        else "reliable" if record["failure_rate"] < 0.1
                        else "patchy" if record["failure_rate"] < 0.34 else "unreliable"),
        } for record in records]

    def _browser_state(self) -> dict[str, Any]:
        """What page is open, or an honest admission that it cannot be read.

        Playwright's sync API belongs to the thread that created it, and every
        call here -- is_closed(), .url, .title() -- raises `greenlet.error:
        Cannot switch to a different thread` when the snapshot is taken from
        anywhere else. That is exactly what happened, twice: an ordinary
        conversational turn asked for state, touched the page from the wrong
        thread, and died with a traceback instead of an answer.

        The read now goes to the thread that owns the browser, so it returns
        the real page rather than surviving the attempt. The guard stays
        underneath it, because describing the system must never be the thing
        that breaks it -- a field that cannot be read is reported unreadable,
        which is both true and survivable.
        """
        blank = {"active": False, "url": None, "title": None}
        page = getattr(self.browser, "page", None) if self.browser else None
        if page is None:
            return blank
        host = getattr(self.browser, "host", None)
        try:
            def read() -> dict[str, Any]:
                if page.is_closed():
                    return blank
                return {"active": True, "url": page.url, "title": page.title()}

            return host.run(read, timeout=10.0) if host is not None else read()
        except Exception as exc:
            return {"active": True, "url": None, "title": None,
                    "readable": False, "reason": f"{type(exc).__name__}"}

    def snapshot(self, live_internet: bool = False, detailed: bool = False) -> dict[str, Any]:
        storage = self.storage.status() if detailed else (
            self.storage.quick_status() if hasattr(self.storage, "quick_status") else self.storage.status()
        )
        last = getattr(self.session, "last_task", None)
        history = getattr(last, "history", []) if last else []
        last_event = history[-1] if history else {}
        voice = self.voice.status() if self.voice else {"state": "unavailable", "enabled": False}
        project = self.projects.status() if detailed else (
            self.projects.quick_status() if hasattr(self.projects, "quick_status") else self.projects.status()
        )
        display_state = self.displays.status() if self.displays else {"count": 0, "active_window": {"available": False}}
        browser_state = self._browser_state()
        active_activity = self.activity.value
        if self.agent.is_running:
            active_activity = getattr(self.agent, "active_activity", None) or active_activity
        from app.operational_awareness import execution_state
        return {
            "claim_type": "SYSTEM_STATE",
            "observed_at": datetime.now(UTC).isoformat(),
            "provider": getattr(self.model, "name", None),
            "model": getattr(self.model, "model", None),
            "reasoning_provider": getattr(self.reasoning_model, "name", None),
            "reasoning_model": getattr(self.reasoning_model, "model", None),
            "mode": self.modes.snapshot(),
            "activity": active_activity,
            "active_task": getattr(self.agent, "active_goal", None),
            "active_project": project.get("active"),
            "active_application": self.session.active_application,
            "active_window": self.session.active_window,
            "voice": voice,
            "camera": self.camera.status() if self.camera else {"state": "UNAVAILABLE"},
            "computer": {"active_window": display_state.get("active_window"), "monitors": display_state.get("displays", []), "monitor_count": display_state.get("count", 0)},
            "browser": browser_state,
            "clipboard": self.clipboard.status() if self.clipboard else {"type": "unavailable"},
            "research": {"ready": self.research.knowledge.path.is_file(), "active": active_activity == Activity.RESEARCHING.value},
            "internet": self._internet(live=live_internet),
            "memory": {"ready": self.memory.path.is_file(), "database": str(self.memory.path)},
            "vision": {"ready": self.vision is not None, "provider": getattr(getattr(self.vision, "provider", None), "name", None), "model": getattr(getattr(self.vision, "provider", None), "model", None)},
            "tool_count": len(self.registry.names()),
            "tools_i_know_how_they_behave": self._tool_experience(),
            "selected_tools": list(getattr(self.agent.planner, "last_candidate_tools", [])),
            "current_plan": list(getattr(self.agent, "active_plan", []) or []),
            "current_step": getattr(self.agent, "active_step", None),
            "execution": execution_state(self.agent, self.session),
            "last_action": last_event.get("tool"),
            "last_result": last_event.get("verification"),
            "storage_used": storage["used_bytes"],
            "storage_limit": storage["maximum_total_bytes"],
            "storage_state": storage["state"],
            "self_change": self.self_change.status() if self.self_change else {},
            "system": self.observability.system() if self.observability else {},
            "cost": self.cost_policy.status() if self.cost_policy else {},
            "models": (
                self.model_resources.status() if detailed
                else self.model_resources.quick_status() if self.model_resources and hasattr(self.model_resources, "quick_status")
                else self.model_resources.status() if self.model_resources else {}
            ),
            "detail_level": "detailed" if detailed else "quick",
        }

    def answer(self, question: str) -> str:
        lower = question.lower()
        observation = getattr(self.session, "last_camera_observation", None)
        if observation and re.search(r"(?i)how do i look|what do i look like|what did you see|describe how i look|describe me", lower):
            captured = str(observation.get("observed_at", ""))
            detail = str(observation.get("interpretation", "")).strip()
            return f"From the frame I captured at {captured}:\n\n{detail}\n\n(Local analysis by {observation.get('model') or 'the local vision model'}; no network was used and the raw frame was not kept. Say \"look again\" for a fresh capture.)"
        provider = str(getattr(self.model, "name", None) or "no active provider")
        model = str(getattr(self.model, "model", None) or "no active model")
        if "openai" in lower or "chatgpt" in lower:
            if provider.lower() == "openai":
                return f"Yes. This response is using {model} through OpenAI."
            return f"No, not for this response. The active provider is {provider.title()}, using {model} locally." if provider == "ollama" else f"No, not for this response. The active provider is {provider}, using {model}."
        if "model" in lower or "provider" in lower:
            local = " locally" if provider == "ollama" else ""
            return f"{model} through {provider.title()}{local}."
        if "what are we working on" in lower or "project are we working on" in lower:
            project = self.projects.active
            return "No project is currently active." if not project else f"The active project is {project.name} at {project.root}. I am not running an independent project task right now."
        if "working on" in lower or "doing right now" in lower or "currently doing" in lower or "what are u working" in lower:
            if self.agent.active_goal:
                return f"I'm currently {self.agent.active_activity} on: {self.agent.active_goal}"
            return "I'm not running an independent task right now. I'm currently handling this conversation."
        if "project" in lower:
            project = self.projects.active
            return "No project is currently active." if not project else f"The active project is {project.name} at {project.root}."
        if "how many tools" in lower or "what tools" in lower or "tools do" in lower:
            # A bare count was the entire answer here, which read as evasive
            # to a question that asked two things: "what tools do you actually
            # have, and which ones do you trust?" got "I currently have 91
            # registered tools." The second half was not refused, it was
            # dropped, because a count was all this branch could produce.
            understanding = getattr(self.registry, "understanding", None)
            if understanding is not None:
                try:
                    return understanding.overview(self.registry)
                except Exception:
                    log_swallowed(_log, "tool understanding overview", level=logging.WARNING)
            return f"I currently have {len(self.registry.names())} registered tools."
        if "vision" in lower:
            vision_provider = getattr(self.vision, "provider", None)
            return f"Vision is ready through {getattr(vision_provider, 'model', None)} on {str(getattr(vision_provider, 'name', None)).title()}." if self.vision else "Vision is not currently available."
        if "research" in lower:
            if "website" in lower or "what are you researching" in lower:
                return "No website or research task is active right now." if self.activity != Activity.RESEARCHING else f"Research is active for {self.agent.active_goal or 'the current request'}."
            return "Research is currently active." if self.activity == Activity.RESEARCHING else "Research is available and idle." if self.research.knowledge.path.is_file() else "Research is unavailable."
        if "internet" in lower or "connected" in lower:
            internet = self._internet(live=True)
            return "Internet access is available; I verified a public HTTPS connection just now." if internet["available"] else f"Internet access could not be verified: {internet.get('error', 'unknown error')}"
        if "voice" in lower:
            voice = self.voice.status() if self.voice else {"state": "unavailable", "enabled": False}
            return f"Voice is {voice.get('state', 'unavailable')}; listening is {'active' if voice.get('enabled') else 'not active'}."
        if "camera" in lower:
            camera = self.camera.status() if self.camera else {"state": "UNAVAILABLE", "devices_detected": 0}
            return f"Camera is {camera['state']}; {camera.get('devices_detected', 0)} device(s) are detected. Raw retention is {'on' if camera.get('raw_retention') else 'off'}."
        if "cost" in lower or "paid" in lower:
            policy = self.cost_policy.status() if self.cost_policy else {}
            return f"Cost mode is {policy.get('mode', 'unknown')}. Paid APIs and cloud inference are {'enabled' if policy.get('paid_api_allowed') else 'disabled'} by default."
        if "storage" in lower:
            storage = self.storage.status()
            return f"Storage is {self.storage.human(storage['used_bytes'])} used of the {self.storage.human(storage['maximum_total_bytes'])} project budget."
        if "application" in lower or "window" in lower:
            return "I do not have evidence of an active application in this session." if not self.session.active_application else f"The active session application is {self.session.active_application}."
        if "file" in lower and ("just" in lower or "last" in lower):
            return "I do not have evidence of a file modified in this session." if not self.session.last_file else f"The last file observed in this session was {self.session.last_file}."
        if self.self_change and any(phrase in lower for phrase in ("changed your code", "changed your voice", "changed the code", "modified your code", "edited your code", "i just changed", "did you notice", "code change", "restart required", "do you need a restart", "changed anything")):
            change = self.self_change.status()
            if not change["restart_required"]:
                return (
                    f"I don't see any change under app, config, scripts, or tests since I started "
                    f"({change['runtime_started_at']}). Still on commit {str(change['current_commit'])[:8]} "
                    f"with a {'dirty' if change['working_tree_dirty'] else 'clean'} working tree."
                )
            details = []
            if change["changed_file_count"]:
                details.append(f"{change['changed_file_count']} file(s) modified on disk since I started ({', '.join(change['changed_files_since_start'][:5])}{'...' if change['changed_file_count'] > 5 else ''})")
            if change["commit_changed_since_start"]:
                details.append(f"the commit moved from {str(change['startup_commit'])[:8]} to {str(change['current_commit'])[:8]}")
            return (
                f"Yes -- I detected {'; '.join(details) if details else 'a change to the working tree'}. "
                "This process is still running the Python modules it imported at startup, so those edits are not active yet. Restart required."
            )
        if "while i was away" in lower or "have you been doing" in lower:
            return "I don't have evidence of any independent work while you were away. Scheduled tasks would appear in the task history if they had run."
        return "I only report operational state I can observe. No matching state field was identified for that question."

    def _internet(self, live: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not live and self._internet_cache and now - self._internet_cache[0] < 60:
            return self._internet_cache[1]
        started = time.perf_counter()
        try:
            response = httpx.get("https://example.com/", timeout=2.5, follow_redirects=True)
            response.raise_for_status()
            result = {"available": True, "checked_live": True, "latency_ms": int((time.perf_counter() - started) * 1000)}
        except Exception as exc:
            result = {"available": False, "checked_live": True, "error": f"{type(exc).__name__}: {exc}", "latency_ms": int((time.perf_counter() - started) * 1000)}
        self._internet_cache = (now, result)
        return result
