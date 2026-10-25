"""Conservative chat routes for the owner's saved, local networking records.

A contact is chosen by its ID, a unique short ID, or a name only one active
contact has (NetworkingBook.resolve); namesakes are listed, never guessed
between. Drafting needs a stated purpose. No route discovers or messages a person.
"""
from __future__ import annotations

import re
from typing import Any


_SAVED = r"(?:saved|private(?: networking)?|saved networking) contacts?"
_LEAD = r"(?:(?:please|can you|could you|would you)\s+)?"


def request(text: str) -> tuple[str, dict[str, Any]] | None:
    value = " ".join(str(text or "").split()).strip().rstrip(".!?")
    if not value or len(value) > 1800:
        return None
    flags = re.IGNORECASE
    if re.fullmatch(_LEAD + r"(?:show|list|display)(?: me)? (?:my |the )?" + _SAVED, value, flags):
        return "networking_list", {}
    if re.fullmatch(_LEAD + r"(?:show|list|display)(?: me)? (?:my |the )?private network", value, flags):
        return "networking_list", {}
    match = re.fullmatch(_LEAD + r"(?:find|show|list|match)(?: me)? (?:my |the )?" + _SAVED
                         + r" (?:who |that )?(?:can |could )?(?:help(?: me)? with|work on|with expertise in|for) (.+)", value, flags)
    if not match:
        match = re.fullmatch(_LEAD + r"(?:who in my private network|which (?:of my )?saved contacts) "
                             r"(?:can |could )?help(?: me)? with (.+)", value, flags)
    if match and 1 <= len(match[1]) <= 1000:
        return "networking_match", {"goal": match[1], "limit": 20}
    match = re.fullmatch(_LEAD + r"(?:search|filter)(?: me)? (?:my |the )?" + _SAVED + r" for (.+)", value, flags)
    if match and 1 <= len(match[1]) <= 300:
        return "networking_list", {"query": match[1]}
    match = re.fullmatch(_LEAD + r"(?:restore|import|load)(?: back)? (?:my |the )?(?:private )?networking(?: contacts| book| export)?"
                         r" from (.{3,1000})", value, flags)
    if match:
        return "networking_restore", {"path": match[1].strip('"\' ')}
    # A contact is named by its full ID, a unique short ID or a name only one saved contact has; the book
    # resolves it and lists namesakes instead of guessing. Typing a 32-character ID was the only way before.
    contact = _LEAD + r"(?:my |the |a )?(?:(?:saved|private(?: networking)?|networking) contact|contact)"
    match = re.fullmatch(_LEAD + r"(?:show|read|view|open)(?: me)? (?:(?:my |the )?(?:saved|private(?: networking)?|networking) "
                         r"contact|my contact) (.{1,160})", value, flags)
    if match:
        return "networking_detail", {"contact_id": match[1].strip('"\' ')}
    verb = _LEAD + r"(?:draft|prepare|write) (?:an? )?(?:unsent )?(?:introduction|outreach|message) (?:to|for) "
    match = re.fullmatch(verb + contact.removeprefix(_LEAD) + r" (.{1,160}?) (?:about|for|to discuss|regarding) (.+)", value, flags)
    if match and 1 <= len(match[2]) <= 1500:
        return "networking_draft", {"contact_id": match[1].strip('"\' '), "purpose": match[2]}
    # A contact without a purpose: show the record and how to ask, never an invented purpose.
    match = re.fullmatch(verb + contact.removeprefix(_LEAD) + r" (.{1,160})", value, flags)
    if match:
        return "networking_detail", {"contact_id": match[1].strip('"\' ')}
    match = re.fullmatch(verb + contact.removeprefix(_LEAD), value, flags)
    if match:
        return "networking_list", {}
    return None


def plan(text: str, names: Any) -> dict[str, Any] | None:
    found = request(text)
    if found is None or found[0] not in names:
        return None
    description = {"networking_draft": "Prepare a local unsent networking draft for the selected saved record.",
                   "networking_restore": "Add contacts from the owner's networking export file without changing saved ones."
                   }.get(found[0], "Read saved private networking records and their entered evidence.")
    return {"description": description, "tool": found[0], "arguments": found[1],
            "expected": {"type": "tool_success"}, "max_retries": 0}


def _contact_lines(row: dict[str, Any], *, detail: bool = False) -> list[str]:
    lines = [str(row.get("name") or "Unnamed contact") + (" | " + str(row["organization"]) if row.get("organization") else ""),
             "ID: " + str(row.get("id") or "unknown")]
    if row.get("role"):
        lines.append("Role: " + str(row["role"]))
    if row.get("expertise"):
        lines.append("Entered expertise: " + ", ".join(row["expertise"]))
    if row.get("goals"):
        lines.append("Entered shared goals: " + ", ".join(row["goals"]))
    relevance = row.get("relevance")
    if isinstance(relevance, dict):
        lines.append("Topic overlap: " + str(relevance.get("score", 0)) + "%")
        matches = [*relevance.get("matched_expertise", []), *relevance.get("matched_goals", [])]
        if matches:
            lines.append("Matched entered topics: " + ", ".join(matches))
        if relevance.get("unmatched_terms"):
            lines.append("Not evidenced: " + ", ".join(relevance["unmatched_terms"]))
    if row.get("follow_up"):
        lines.append("Follow-up date: " + str(row["follow_up"]))
    if row.get("source_url"):
        lines.append("Owner-entered source: " + str(row["source_url"]))
    if detail:
        if row.get("notes"):
            lines.append("Your notes: " + str(row["notes"]))
        for channel in row.get("channels", []):
            lines.append(f"{channel['kind']}: {channel['value']} ({channel['status']}; owner-entered)")
        if row.get("source_recorded_at"):
            lines.append("Source recorded locally: " + str(row["source_recorded_at"]))
    return lines


def report(history: list[dict[str, Any]]) -> str | None:
    local_tools = {"networking_list", "networking_match", "networking_detail", "networking_draft", "networking_restore"}
    # A later non-networking action may have sent something. Let the general
    # mixed-task reporter account for its evidence instead of claiming unsent.
    if any(event.get("tool") not in local_tools for event in history):
        return None
    for event in reversed(history):
        tool = event.get("tool", "")
        if tool not in local_tools:
            continue
        result = event.get("result") or {}
        if not result.get("success"):
            error = re.sub(r"^(?:LookupError|ValueError|PermissionError|FileNotFoundError):\s*", "",
                           str(result.get("error") or "No successful result was returned."))
            # "More than one saved contact is named …" is a question back, not a failure.
            if re.search(r"(?i)saved contact|networking export|export file", error):
                return error
            return "The private networking operation failed: " + error
        output = result.get("output")
        if not isinstance(output, dict):
            return "The private networking operation returned no readable record."
        if output.get("choice_needed") is True:
            return str(output.get("message") or "Say which saved contact you mean.") + " Nothing was drafted or sent."
        if tool == "networking_draft":
            if output.get("sent") is not False or not isinstance(output.get("body"), str):
                return "No valid unsent outreach draft was returned; nothing was sent by this local workflow."
            return ("Unsent draft for your review\nSubject: " + str(output.get("subject") or "") + "\n\n"
                    + output["body"] + "\n\nVerify the saved identity, facts and channel before sending. Nothing was sent.")
        if tool == "networking_restore":
            lines = [f"Restored {output.get('restored', 0)} contact{'s' if output.get('restored') != 1 else ''} into your private book."]
            if output.get("added"):
                lines.append("Added: " + ", ".join(output["added"][:20]))
            if output.get("skipped_existing"):
                lines.append("Already saved, left unchanged: " + ", ".join(output["skipped_existing"][:20]))
            if output.get("rejected"):
                lines.append("Not restored: " + "; ".join(output["rejected"][:10]))
            return "\n".join(lines + [str(output.get("basis") or ""), "Nothing was sent."]).strip()
        if tool == "networking_detail":
            return ("\n".join(_contact_lines(output, detail=True))
                    + "\n\nFacts and links are owner-entered, not independently verified.\nTo prepare an unsent draft, say: "
                    + "Draft an introduction to saved contact " + str(output.get("name") or output.get("id") or "<name>")
                    + " about <your purpose>.")
        rows = output.get("contacts")
        if not isinstance(rows, list):
            return "No saved-contact list was returned."
        if not rows:
            return ("No saved contacts match this request. This checked only your active local networking records. "
                    "Open Studios > Private networking to add facts or professional source links; no people were invented or contacted.")
        heading = ("Matching saved contacts" if tool == "networking_match" else "Your saved private networking contacts")
        sections = [heading + f" ({len(rows)}):", *["\n".join(_contact_lines(row)) for row in rows[:20] if isinstance(row, dict)]]
        if len(rows) > 20:
            sections.append("Showing 20; narrow the search or use the private workspace to inspect the rest.")
        sections.append("Topic overlap uses entered expertise and shared goals, not a prediction of help or friendship. "
                        "Namesakes stay separate: if two share a name, use the first 8 characters of the ID.\n"
                        "Draft format: Draft an introduction to saved contact <name or ID> about <your purpose>.")
        return "\n\n".join(sections)
    return None
