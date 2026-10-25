"""Exact chat routes for screen reading, person detection and Replica."""
import json
import re


def request(text):
    value = text.strip().rstrip(".!?")
    lower = value.lower()
    commands = {
        "start live screen reading": "screen_read_start", "start screen reading": "screen_read_start",
        "watch my screen": "screen_read_start", "read my screen live": "screen_read_start",
        "stop screen reading": "screen_read_stop", "stop live screen reading": "screen_read_stop",
        "stop watching my screen": "screen_read_stop", "screen reading status": "screen_read_status",
        "live screen status": "screen_read_status", "latest screen text": "screen_read_status",
        "read my screen": "screen_read", "read the current screen": "screen_read", "read current screen": "screen_read",
        "read screen text": "screen_read", "detect people in camera": "camera_people",
        "detect humans in camera": "camera_people", "count people in camera": "camera_people",
        "person detection status": "people_status", "human detection status": "people_status",
        "replica": "replica_catalog", "replica workflows": "replica_catalog", "replica skills": "replica_catalog",
        "list replica workflows": "replica_catalog"}
    if lower in commands:
        return commands[lower], {}
    if match := re.match(r"(?i)^(?:detect|count) (?:people|humans) in (.+)$", value):
        return "detect_people", {"path": match[1].strip('"')}
    if match := re.match(r"(?i)^prepare replica (\w+) in (.+)$", value):
        return "replica_prepare", {"stage": match[1].lower(), "workspace": match[2].strip('"')}
    if match := re.match(r"(?is)^run replica (\w+) in (.+?):\s+(.+)$", value):
        return "replica_run", {"stage": match[1].lower(), "workspace": match[2].strip('"'), "goal": match[3]}
    return None


def plan(text, names):
    found = request(text)
    if not found or found[0] not in names:
        return None
    return {"description": "Run the requested local workspace action.", "tool": found[0], "arguments": found[1],
            "expected": {"type": "tool_success"}, "max_retries": 0}


def report(history):
    for row in reversed(history):
        result = row.get("result", {})
        value = result.get("output", {})
        if not result.get("success") or not isinstance(value, dict):
            continue
        if value.get("screen_reading"):
            if "stopped" in value:
                return "Screen reading stopped; latest text cleared." if value["stopped"] else "Screen reading is stopping; the active read has not returned yet."
            latest = value.get("latest") or value
            return (f"Screen reading: {'running' if value.get('running') else 'read complete' if 'text' in value else 'stopped'}\n"
                    + str(latest.get("window", "")) + "\n" + (latest.get("text") or value.get("error") or "Waiting for the first read."))
        if value.get("person_detection"):
            # Confidence as a percentage, size and position, and the identity boundary, in one readable renderer.
            from app.observed_reply import render as render_observation
            return render_observation(value)
        if value.get("replica"):
            if "stages" in value:
                return "Replica workflows for HAYAT: " + ", ".join(row["stage"] for row in value["stages"]) + ". No Claude dependency. Open Replica app studio from Studios."
            if "checker" in value:
                return f"Replica {value['checker']}: {'passed' if value['passed'] else 'findings need attention'}.\n" + json.dumps(value["result"], ensure_ascii=False, indent=2)[:12000]
            return (f"Replica {value.get('stage', '')}: {value.get('status', '')}.\n"
                    + ("Missing: " + ", ".join(value["missing"]) if value.get("missing") else "") + "\n"
                    + str(value.get("manifest") or value.get("path") or "") + "\nGenerated code is not executed or published by this step.")
    return None
