from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.intent.router import IntentRouter
from app.session import SessionState


GROUPS = {
    "CONVERSATION": ["Hi", "Hello", "Hey AI", "Good morning", "Thanks", "Thank you", "Okay", "Who are you?", "What can you do?", "Tell me about yourself."],
    "QUESTION": ["What is a mutex?", "How does SQLite work?", "Why is the sky blue?", "Who invented Python?", "When was Git created?", "Where is the config documented?", "Is JSON text?", "Are threads processes?", "Does Python have a GIL?", "Can you explain recursion?"],
    "DIAGNOSTIC": ["That didn't work", "You misunderstood me", "Why did you do that?", "It only opened Notepad", "That is not what I asked", "It's not working", "That's wrong", "It did nothing", "It just opened the app", "No, that's not what I meant"],
    "RESEARCH": ["Research GPU scheduling", "Look this up: current HTTPX API", "Search the internet for AI news", "What is the latest Python release?", "Use official sources for Kubernetes GPUs", "Find evidence about coffee hydration", "Is this still current?", "Research this deeply", "Find the current version of Playwright", "Check the latest Ollama release"],
    "COMPUTER_CONTROL": ["Open Notepad", "Close Notepad", "Write hello in Notepad", "Type hello in Notepad", "Click the settings button", "Scroll down", "Press enter", "Open calculator", "Close this window", "Write a note in Notepad saying hello"],
    "BROWSER": ["Open Chrome", "Open Edge", "Navigate to https://example.com", "Open YouTube", "Search YouTube for GPU scheduling", "Go back in the browser", "Open GitHub in Chrome", "Download the public PDF", "Click the browser login link", "Open the website"],
    "CODING": ["Fix this Python bug", "Run the tests", "Create a Python script", "Edit the code", "Debug this project", "Install the project dependency", "Compile the application", "Fix the failing test", "Run pytest", "Open the repository and inspect code"],
    "MEMORY": ["Remember that Atlas is my project", "Remember this preference", "Recall Project Atlas", "What do you remember about Atlas?", "Do you remember my project?", "Remember that I prefer concise replies", "Recall the last procedure", "Remember the server name", "What do you remember?", "Remember that tests run with pytest"],
    "CONTROL": ["Stop", "Cancel", "Never mind", "Mute", "Unmute", "Voice on", "Voice off", "Voice mode activate", "Push-to-talk mode", "Wake mode"],
    "SAFE_NON_ACTION": ["Notepad is frustrating", "I used Chrome yesterday", "The tests were interesting", "Maybe later", "I am thinking about Python", "This app seems useful", "Not sure", "Interesting", "The browser was slow", "I like concise answers"],
}


def main() -> int:
    router = IntentRouter()
    session = SessionState(active_application="notepad", last_opened_application="notepad")
    expected_map = {name: name for name in GROUPS if name != "SAFE_NON_ACTION"}
    total = correct = false_actions = unnecessary_planner = wrong_tool = 0
    rows = []
    confusion = Counter()
    for group, inputs in GROUPS.items():
        for text in inputs:
            intent = router.route(text, session)
            expected = expected_map.get(group)
            if group == "SAFE_NON_ACTION":
                matched = not intent.requires_tools
            else:
                matched = intent.intent.value == expected
            should_act = group in {"RESEARCH", "COMPUTER_CONTROL", "BROWSER", "CODING"}
            false_action = not should_act and intent.requires_tools
            unnecessary = not should_act and intent.requires_planning
            wrong = should_act and not intent.requires_tools
            total += 1; correct += int(matched); false_actions += int(false_action); unnecessary_planner += int(unnecessary); wrong_tool += int(wrong)
            confusion[(group, intent.intent.value)] += 1
            rows.append({"input": text, "group": group, "actual": intent.intent.value, "correct": matched, "requires_tools": intent.requires_tools, "false_action": false_action})
    report = {
        "cases": total, "intent_accuracy": round(correct / total, 4),
        "false_action_rate": round(false_actions / sum(len(values) for name, values in GROUPS.items() if name not in {"RESEARCH", "COMPUTER_CONTROL", "BROWSER", "CODING"}), 4),
        "unnecessary_planner_rate": round(unnecessary_planner / total, 4),
        "wrong_or_missing_tool_rate": round(wrong_tool / total, 4),
        "confusion": {f"{left}->{right}": count for (left, right), count in sorted(confusion.items())}, "rows": rows,
    }
    output = PROJECT_ROOT / "data/benchmarks/intent_routing/after.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"rows", "confusion"}}, indent=2))
    print(output)
    return 0 if report["intent_accuracy"] >= .9 and report["false_action_rate"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
