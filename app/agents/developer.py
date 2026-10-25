"""Developer super-agent view over HAYAT's shared execution core."""
import re

from app.agents.manager import SpecialistAgent


class DeveloperAgent(SpecialistAgent):
    """Owns multi-step code/build/test work without bypassing verification.

    This is a role boundary, not a privileged second runtime: filesystem,
    package, shell, repair and test actions still use the same registry,
    sandbox, authority kernel, task log and verifier as every other request.
    """

    def __init__(self, core):
        super().__init__("developer", core)
        self.monitor = None

    def run(self, goal, intent=None, session=None):
        # Give the planner fresh, bounded evidence when the owner asks it to
        # debug or change code. The background watcher only observes changes;
        # execution still goes through the ordinary tool and verifier path.
        if (self.monitor is not None and intent is not None
                and re.search(r"(?i)\b(?:fix|repair|debug|diagnose|error|bug|syntax|test)\b", goal)):
            try:
                reports = self.monitor.scan_all()
                intent.entities["observed_code_issues"] = [
                    {"project": report["project"], "root": report["root"],
                     "issues": report.get("issues", [])[:5], "issue_count": report.get("issue_count", 0)}
                    for report in reports.values() if report.get("issue_count")
                ][:5]
            except (OSError, ValueError):
                pass
        return super().run(goal, intent=intent, session=session)
