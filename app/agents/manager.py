from __future__ import annotations

import re

from app.agent.core import Agent, TaskOutcome
from app.research.manager import ResearchManager


class ManagerAgent:
    """Single-agent-first dispatcher; specialists share the same safe core and tool permissions."""

    def __init__(self, core: Agent, specialists: dict[str, "SpecialistAgent"] | None = None) -> None:
        self.core = core
        self.specialists = specialists or {}

    #: Work aimed at this system itself rather than at the user's world. Kept
    #: ahead of the coder heuristic, which would otherwise claim anything
    #: containing "bug" or "error" and lose the verification loop that makes
    #: self-modification safe.
    #: Must require an explicit self-reference. An earlier version accepted
    #: "fix the ..." and so captured "fix the bug in my python script" -- which
    #: sent ordinary user work into the self-repair loop, where it ran the
    #: project's own test suite. The agent's target has to be named, not
    #: inferred from a verb.
    SELF_REPAIR = re.compile(
        r"(?i)(?:"
        r"\b(?:fix|repair|debug|diagnose|heal|check|test)\s+(?:up\s+)?(?:your\s*self|yourself|itself)\b"
        r"|\bself[-\s](?:repair|heal|diagnose|fix|check|test)\b"
        r"|\b(?:fix|repair|debug|diagnose|heal)\s+your\s+own\b"
        r"|\b(?:fix|repair|debug|diagnose)\s+(?:the\s+)?(?:ai|agent|runtime|system)\s+itself\b"
        r"|\bwhat(?:'|’)?s\s+(?:broken|wrong)\s+(?:in|with)\s+(?:you|yourself)\b"
        r"|\b(?:run|are)\s+your\s+(?:own\s+)?tests\b"
        r")"
    )

    def route(self, goal: str, intent=None, session=None) -> TaskOutcome:
        lowered = goal.lower()
        kind = getattr(getattr(intent, "intent", None), "value", "")
        if "repair" in self.specialists and self.SELF_REPAIR.search(goal):
            return self.specialists["repair"].run(goal, intent=intent, session=session)
        coding_role = "developer" if "developer" in self.specialists else "coder"
        role = coding_role if kind == "CODING" else "researcher" if kind == "RESEARCH" else "computer" if kind == "COMPUTER_CONTROL" else "reviewer" if "review" in lowered else ""
        if not intent:
            role = coding_role if any(word in lowered for word in ("code", "python", "bug", "error", "failure", "dependency", "deploy", "project")) and not any(phrase in lowered for phrase in ("research", "look up", "search the internet")) else "researcher" if ResearchManager.needs_research(goal) else "computer" if any(word in lowered for word in ("screen", "mouse", "windows")) else "reviewer" if "review" in lowered else ""
        specialist = self.specialists.get(role)
        if intent is None and session is None:
            return specialist.run(goal) if specialist else self.core.run(goal)
        return specialist.run(goal, intent=intent, session=session) if specialist else self.core.run(goal, intent=intent, session=session)

    def delegate(self, role: str, goal: str, *, intent=None, session=None) -> TaskOutcome:
        if role not in self.specialists:
            raise KeyError(f"Unknown specialist role: {role}")
        if intent is None and session is None:
            return self.specialists[role].run(goal)
        return self.specialists[role].run(goal, intent=intent, session=session)

    def roles(self) -> list[str]:
        return sorted(self.specialists)


class SpecialistAgent:
    def __init__(self, role: str, core: Agent) -> None:
        self.role = role
        self.core = core

    def run(self, goal: str, intent=None, session=None) -> TaskOutcome:
        return self.core.run(goal, intent=intent, session=session)
