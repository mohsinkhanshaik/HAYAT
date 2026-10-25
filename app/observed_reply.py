"""Turn what verified tools returned into the reply itself.

A task that lists a folder, reads a file or runs a script has an answer: the
listing, the text, the output. The reply used to be "Done -- 2 steps completed
and verified: list folder, read file", which named the tools and left the
answer in the task history. This renders the observations instead.

Only the tools' own verified outputs are used. Nothing here guesses or
paraphrases; a tool it does not recognise contributes nothing, and the caller
keeps its previous wording when nothing is recognised at all.
"""
from __future__ import annotations

import time
from pathlib import PurePath
from typing import Any, Callable

#: Long outputs are cut; the chat is a reply, not a file viewer.
LIST_LIMIT = 15
TEXT_LINES = 40
TEXT_CHARACTERS = 3000


def _name(path: Any) -> str:
    value = str(path or "")
    return PurePath(value).name or value


def _size(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ""
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def _excerpt(text: str) -> str:
    lines = str(text).splitlines()
    clipped = "\n".join(lines[:TEXT_LINES])
    if len(clipped) > TEXT_CHARACTERS:
        clipped = clipped[:TEXT_CHARACTERS].rstrip()
    hidden = len(lines) - min(len(lines), TEXT_LINES)
    if hidden > 0 or len(clipped) < len("\n".join(lines[:TEXT_LINES])):
        clipped += f"\n… ({len(lines)} lines in total; showing the start)"
    return clipped


def _redacted(text: str) -> str:
    try:
        from app.safety.sensitive import redact_text

        return redact_text(text)[0]
    except Exception:
        return text


def _listing(output: dict[str, Any]) -> str | None:
    entries = output.get("entries")
    if not isinstance(entries, list):
        return None
    where = output.get("path") or "that folder"
    if not entries:
        return f"{where} is empty."
    folders = [entry for entry in entries if isinstance(entry, dict) and entry.get("type") in {"dir", "directory", "folder"}]
    lines = []
    for entry in entries[:LIST_LIMIT]:
        if not isinstance(entry, dict):
            continue
        name = _name(entry.get("path"))
        is_folder = entry in folders
        size = "" if is_folder else _size(entry.get("size"))
        lines.append(f"  - {name}{'/' if is_folder else ''}{f'  ({size})' if size else ''}")
    more = f"\n  … and {len(entries) - LIST_LIMIT} more" if len(entries) > LIST_LIMIT else ""
    counts = f"{len(entries)} item{'s' if len(entries) != 1 else ''}"
    if folders:
        counts += f" ({len(folders)} folder{'s' if len(folders) != 1 else ''})"
    return f"{where} has {counts}:\n" + "\n".join(lines) + more


def _matches(output: dict[str, Any]) -> str | None:
    matches = output.get("matches")
    if not isinstance(matches, list):
        return None
    pattern = output.get("pattern") or "that pattern"
    where = output.get("path") or "the folder"
    if not matches:
        return f"Nothing matching {pattern} in {where}."
    shown = "\n".join(f"  - {item}" for item in matches[:LIST_LIMIT])
    more = f"\n  … and {len(matches) - LIST_LIMIT} more" if len(matches) > LIST_LIMIT else ""
    return f"Found {len(matches)} match{'es' if len(matches) != 1 else ''} for {pattern} in {where}:\n{shown}{more}"


def _contents(output: dict[str, Any]) -> str | None:
    content = output.get("content")
    if not isinstance(content, str):
        return None
    name = _name(output.get("path")) or "The file"
    if not content.strip():
        return f"{name} is empty."
    return f"{name}:\n{_excerpt(_redacted(content))}"


def _exists(output: dict[str, Any]) -> str | None:
    if "exists" not in output or "size" in output:
        return None
    name = _name(output.get("path"))
    if not output.get("exists"):
        return f"{name} does not exist."
    kind = "folder" if output.get("is_directory") else "file"
    return f"Yes, {name} exists (a {kind})."


def _info(output: dict[str, Any]) -> str | None:
    if "size" not in output or "modified" not in output:
        return None
    name = _name(output.get("path"))
    try:
        modified = time.strftime("%d %b %Y, %H:%M", time.localtime(float(output["modified"])))
    except (TypeError, ValueError, OverflowError):
        modified = "at an unknown time"
    kind = "folder" if output.get("is_directory") else "file"
    return f"{name} is a {kind} of {_size(output['size'])}, last modified {modified}."


def _system(output: dict[str, Any]) -> str | None:
    if "platform" not in output:
        return None
    parts = [f"{output['platform']}"]
    if output.get("python"):
        parts.append(f"Python {output['python']}")
    if output.get("disk_total") and output.get("disk_free") is not None:
        parts.append(f"{_size(output['disk_free'])} free of {_size(output['disk_total'])} on disk")
    return "System: " + ", ".join(parts) + "."


def _download(output: dict[str, Any]) -> str | None:
    if (output.get("complete") is not True or "sha256" not in output
            or "bytes" not in output or "path" not in output):
        return None
    path = str(output["path"])
    from pathlib import Path
    if not Path(path).is_file() or Path(path).stat().st_size != output["bytes"]:
        return None
    return f"Downloaded to {path} ({_size(output['bytes'])}). SHA-256: {output['sha256']}."


def _process(output: dict[str, Any]) -> str | None:
    if "exit_code" not in output:
        return None
    code = output.get("exit_code")
    stdout = str(output.get("stdout") or "").strip()
    stderr = str(output.get("stderr") or "").strip()
    what = f"{_name(output['script'])} " if output.get("script") else ""
    shell = output.get("shell")
    subject = f"The {shell} command" if shell in {"PowerShell", "CMD"} else f"The script {what}".rstrip()
    if output.get("timed_out"):
        return f"{subject} timed out and was stopped."
    if code == 0:
        head = f"{subject} ran successfully"
        return f"{head} and printed:\n{_excerpt(_redacted(stdout))}" if stdout else f"{head}. It printed nothing."
    detail = stderr or stdout
    head = f"{subject} exited with code {code}"
    return f"{head}:\n{_excerpt(_redacted(detail))}" if detail else f"{head}."


def _ideas(output: dict[str, Any]) -> str | None:
    """Business ideas, written for someone deciding what to start."""
    ideas = output.get("ideas")
    if not isinstance(ideas, list) or not ideas or not isinstance(ideas[0], dict) or "first_offer" not in ideas[0]:
        return None
    matched = any(str(idea.get("why", "")).startswith("Uses what you said") for idea in ideas)
    lines = ["Here are businesses you could start with me, matched to what you told me:" if matched else
             "Here are businesses one person can start with my help. Tell me your skills and I'll narrow it down:", ""]
    for number, idea in enumerate(ideas[:6], 1):
        lines.append(f"{number}. {idea['title']} ({idea['kind']}): {idea['what']}")
        lines.append(f"   {idea['why']} To start: {idea['startup']}")
        lines.append(f"   First clients: {idea['first_clients']} First offer: {idea['first_offer']}")
        lines.append(f"   I help with: {idea['hayat_helps_with']}.")
    lines.append("")
    lines.extend(output.get("notes", []))
    lines.append("Pick one and say: start a business called <name> offering <what> for <who>. Tell me your skills "
                 "for a closer match, or open Business, Launch.")
    return "\n".join(lines)


def _launch_status(output: dict[str, Any]) -> str | None:
    steps = output.get("steps")
    if not isinstance(steps, list) or "progress" not in output or "next_step" not in output:
        return None
    lines = [f"{output.get('business', 'Your business')}: {output['progress']}."]
    for step in steps:
        lines.append(f"  {'✓' if step.get('done') else '○'} {step.get('title')}")
    lines.append(f"Next: {output['next_step']}")
    if output.get("note"):
        lines.append(output["note"])
    return "\n".join(lines)


def _rate(output: dict[str, Any]) -> str | None:
    if "hourly_rate" not in output or "explained" not in output:
        return None
    return f"{output['explained']}\nDay rate (8 hours): {output.get('day_rate_8h')}.\n{output.get('check', '')}".strip()


def _proposal(output: dict[str, Any]) -> str | None:
    """A prepared outward action: what will happen, every value, and how to confirm."""
    if "action_id" not in output or "how_to_confirm" not in output:
        return None
    lines = [f"Action {output['action_id']} is ready for you to check: {output.get('title')}.",
             f"Where: {output.get('target')}"]
    lines.extend(f"  {item['field']}: {item['value']}" for item in output.get("details", []))
    lines.extend(output.get("notes", []))
    lines.append(output["how_to_confirm"])
    return "\n".join(lines)


def _money_report(output: dict[str, Any]) -> str | None:
    if "by_currency" not in output or "next_actions" not in output:
        return None
    lines = [f"Your business for {output.get('period', 'this period')}:"]
    for row in output["by_currency"]:
        lines.append(f"  Earned {row['client_income']} from clients, {row['product_sales_net']} from products"
                     + (f", {row['other_income']} other" if row.get("other_income") else "")
                     + f". Spent {row['expenses']}. Profit before tax {row['profit_before_tax']}.")
    if not output["by_currency"]:
        lines.append("  Nothing recorded yet.")
    goal = output.get("goal")
    if goal:
        lines.append(f"  Goal {goal['target']}: {goal['percent']}% reached, {goal['remaining']} to go.")
    if output.get("wallet"):
        lines.append("  In your wallet: " + ", ".join(output["wallet"].values()) + ".")
    lines.extend(f"Next: {action}" for action in output["next_actions"])
    return "\n".join(lines)


def _wallet(output: dict[str, Any]) -> str | None:
    balances = output.get("balances")
    if not isinstance(balances, list) or "payout" not in output:
        return None
    if not balances:
        lines = ["Your wallet is empty so far. Earnings from invoices, product sales and other income land here."]
    else:
        lines = ["Available to withdraw: " + ", ".join(b["available"] for b in balances) + "."]
        lines.extend(f"  {b['currency']}: earned {b['earned']}, spent {b['expenses']}, withdrawn {b['withdrawn']}"
                     + (f", pending {b['pending']}" if not str(b['pending']).startswith("0.00") else "") for b in balances)
    payout = output["payout"]
    lines.append(f"Withdrawals go to {payout['destination']}." if payout.get("configured") else payout.get("note", ""))
    return "\n".join(line for line in lines if line)


def _streams(output: dict[str, Any]) -> str | None:
    streams = output.get("streams")
    if not isinstance(streams, list) or "totals" not in output or "basis" not in output:
        return None
    if not streams:
        return "\n".join([f"Income streams for {output.get('period')}: none recorded yet."] + output.get("notes", []))
    lines = [f"Where your money came from in {output.get('period')}: "
             + ", ".join(output["totals"].values()) + f" from {len(streams)} source{'s' if len(streams) != 1 else ''}."]
    for stream in streams:
        change = f", {stream['change']} vs last period" if stream.get("change") else ""
        lines.append(f"  {stream['source']} ({stream['kind'].lower()}): {stream['amount']}, {stream['share']:g}%{change}")
    lines.extend(output.get("notes", []))
    lines.append(output["basis"])
    return "\n".join(lines)


def _brief(output: dict[str, Any]) -> str | None:
    if "needs_attention" not in output or not isinstance(output.get("text"), str):
        return None
    return output["text"]


def _business_result(output: dict[str, Any]) -> str | None:
    """Any other business or launch result that says what happened and what to do next."""
    keys = ("number", "total", "service", "price", "path", "explained", "next_step", "note", "verdict", "amount",
            "status", "lead_id", "client", "business")
    if not any(key in output for key in ("next_step", "note", "verdict")) or not any(key in output for key in keys):
        return None
    lines = []
    if output.get("number") and output.get("total"):
        lines.append(f"{output['number']} for {output['total']}" + (f", due {output['due']}" if output.get("due") else "") + ".")
    if output.get("service") and output.get("price"):
        lines.append(f"{output['service']}: {output['price']}.")
    if output.get("amount") and output.get("status"):
        lines.append(f"{output['amount']}: {output['status']}.")
    if output.get("verdict"):
        lines.append(str(output["verdict"]).capitalize() + ".")
    if output.get("path"):
        lines.append(f"Saved: {output['path']}")
    for key in ("note", "next_step"):
        if output.get(key):
            lines.append(str(output[key]))
    return "\n".join(lines) or None


def _location(output: dict[str, Any]) -> str | None:
    """Where this computer is, with how that was found and how far to trust it."""
    if "method" not in output or "windows_location" not in output:
        return None
    place = output.get("place") or {}
    label = place.get("label") or ""
    zone = f" Time zone: {output['time_zone']}." if output.get("time_zone") else ""
    if output["method"] == "windows_location_service":
        accuracy = f", accurate to about {output['accuracy_m']} m" if output.get("accuracy_m") else ""
        where = f"in {label}" if label else "at the position below"
        lines = [f"📍 This computer is {where} (Windows location service{accuracy}).",
                 f"Coordinates: {output.get('latitude')}, {output.get('longitude')}.{zone}"]
        if place.get("postcode"):
            lines.append(f"Postcode: {place['postcode']}.")
        return "\n".join(lines)
    if output["method"] == "public_ip_lookup":
        network = f" on {output['network']}" if output.get("network") else ""
        return (f"📍 Approximately {label or 'unknown'}, from your public IP address{network}. "
                f"That is {output.get('accuracy', 'approximate')}.{zone}\n"
                f"Why not exact: {output.get('why_not_precise', '')}\n{output.get('how_to_get_precise', '')}").strip()
    return "\n".join(part for part in (output.get("note"), output.get("why_not_precise"),
                                       output.get("how_to_get_precise")) if part)


def _devices(output: dict[str, Any]) -> str | None:
    groups = output.get("groups")
    if not isinstance(groups, dict) or "hidden_system_entries" not in output:
        return None
    from app.tools.devices import GROUP_TITLES

    if not output.get("available", True):
        return output.get("note") or "The device list is not available on this computer."
    lines = [f"Connected to this computer right now ({output.get('count', 0)} devices, from Windows Plug and Play):"]
    for key, items in groups.items():
        if not items:
            continue
        names = ", ".join(item["name"] + ("" if str(item.get("status", "OK")).upper() == "OK"
                                          else f" [{item['status']}]") for item in items[:12])
        more = f" and {len(items) - 12} more" if len(items) > 12 else ""
        lines.append(f"  {GROUP_TITLES.get(key, key)}: {names}{more}")
    battery = output.get("battery")
    if battery:
        lines.append(f"  Battery: {battery['percent']}%, {battery['state']}.")
    problems = output.get("problems") or []
    lines.append(f"Devices reporting a problem: {', '.join(item['name'] for item in problems)}." if problems
                 else "No device reports a problem.")
    if output.get("note"):
        lines.append(output["note"])
    return "\n".join(lines)


def _prospects(output: dict[str, Any]) -> str | None:
    prospects = output.get("prospects")
    if not isinstance(prospects, list) or "niche" not in output or "directories_skipped" not in output:
        return None
    where = f" in {output['place']}" if output.get("place") else ""
    if not prospects:
        return f"I searched for {output['niche']}{where}. {output.get('note', '')}".strip()
    lines = [f"I found {len(prospects)} real {output['niche']}{where} on the web "
             f"({output.get('with_published_email', 0)} publish an email address on their homepage; "
             f"{output.get('directories_skipped', 0)} directory and review results skipped):", ""]
    for number, item in enumerate(prospects, 1):
        contact = ", ".join(part for part in (item.get("published_email"), item.get("published_phone")) if part)
        lines.append(f"{number}. {item['name']} -- {item['website']}" + (f" -- {contact}" if contact else ""))
    lines.append("")
    if output.get("csv"):
        lines.append(f"Saved: {output['csv']}")
    if output.get("added_to_leads") or output.get("already_in_leads"):
        lines.append(f"Added {output.get('added_to_leads', 0)} to your leads"
                     + (f" ({output['already_in_leads']} were already there)" if output.get("already_in_leads") else "")
                     + ". Say \"show my leads\" to see them.")
    lines.append(output.get("note", ""))
    lines.append(output.get("next_step", ""))
    return "\n".join(line for line in lines if line is not None).strip()


def _lead_list(output: dict[str, Any]) -> str | None:
    leads = output.get("leads")
    if not isinstance(leads, list) or "follow_ups_due" not in output:
        return None
    if not leads:
        return ("Your lead list is empty -- no leads have been saved yet. To find real ones, say for example: "
                "\"find solar installers in Austin, Texas and make a lead list\". To add one yourself: "
                "\"add a lead <title> from <where you found it>\".")
    lines = [f"You have {len(leads)} open lead{'s' if len(leads) != 1 else ''}"
             + (f", {output['follow_ups_due']} with a follow-up due" if output.get("follow_ups_due") else "") + ":"]
    for lead in leads[:30]:
        due = f", follow up {lead['follow_up']}" if lead.get("follow_up") else ""
        link = f" -- {lead['url']}" if lead.get("url") else ""
        lines.append(f"  #{lead['id']} {lead['title']} ({lead['status']}{due}){link}")
    if len(leads) > 30:
        lines.append(f"  ...and {len(leads) - 30} more in Business, Leads.")
    return "\n".join(lines)


def _quran(output: dict[str, Any]) -> str | None:
    """Ayahs with Roman-English reading first, then the Arabic and the meaning."""
    if output.get("quran") is not True or not isinstance(output.get("ayahs"), list):
        return None
    surah = output.get("surah") or {}
    lines = [f"Surah {surah.get('name')} ({surah.get('meaning')}) · {output.get('range')} of {surah.get('ayahs')} ayahs", ""]
    opening = output.get("bismillah")
    if isinstance(opening, dict):
        lines.extend([opening["transliteration"], f"   {opening['arabic']}", f"   {opening['translation']}", ""])
    for ayah in output["ayahs"]:
        lines.append(f"{ayah['ayah']}. {ayah['transliteration']}")
        if ayah.get("arabic"):
            lines.append(f"   {ayah['arabic']}")
        lines.append(f"   {ayah['translation']}")
        lines.append("")
    lines.append(f"Source: {output.get('source')}. {output.get('note')}")
    lines.append(str(output.get("next_step") or ""))
    return "\n".join(lines).strip()


def _camera_status(output: dict[str, Any]) -> str | None:
    """camera_status used to end in "it returned nothing to show you"."""
    if "devices_detected" not in output or "state" not in output or "background_recording" not in output:
        return None
    count = output.get("devices_detected") or 0
    lines = [f"Camera is {str(output['state']).lower()}; {count} camera{'s' if count != 1 else ''} detected."]
    if output.get("last_error"):
        lines.append(f"Last problem: {output['last_error']}")
    ocr = output.get("ocr") or {}
    if ocr.get("available"):
        lines.append("Text reading (OCR) is ready, on this computer.")
    lines.append("Nothing is recorded in the background." if not output.get("background_recording") else
                 "Background recording is on.")
    if count:
        lines.append('To test it, say "turn on the camera and tell me what you see".')
    return "\n".join(lines)


def _faces(output: dict[str, Any]) -> str | None:
    """The face tools (app/tools/faces.py) used to end in "it returned nothing to show you"."""
    if "files" in output and "installed" in output:  # face_models_setup
        if not output["installed"]:
            return f"The face models were not installed. {output.get('reason') or ''}".strip()
        parts = [f"{row['name']} {row['status']} ({_size(row.get('bytes'))})" for row in output["files"]]
        return ("Face recognition is set up: " + "; ".join(parts) + ". Enrolled people can now be told apart; "
                'enrol someone with "enroll <name> from <photo>".')
    if "detection" in output and "recognition" in output and "enrolled" in output:  # face_recognition_status
        people = output.get("enrolled") or []
        lines = ["Face recognition is ready." if output["recognition"] else
                 ("Face detection works; " + (output.get("reason") or "recognition needs the face models."))]
        lines.append(f"Enrolled: {', '.join(p['name'] for p in people)}." if people else "Nobody is enrolled yet.")
        if output.get('gallery_error'):
            lines.append(str(output['gallery_error']))
        if output.get('gallery_storage') == 'legacy_plaintext':
            lines.append('Legacy gallery storage is plaintext; the next explicit gallery edit encrypts it with this Windows account.')
        return "\n".join(lines)
    if "enrolled" in output and isinstance(output["enrolled"], bool):  # face_enroll
        if output["enrolled"]:
            count = output.get("signatures") or 1
            if output.get('duplicate_sample') is True:
                return f"This exact normalized sample is already enrolled for {output.get('name')}; {count} local signature(s) retained."
            return f"Enrolled {output.get('name')} ({count} signature{'s' if count != 1 else ''} kept on this computer)."
        return f"Not enrolled: {output.get('reason') or 'no reason given.'}"
    if "faces" in output and "enrolled_known" in output:  # face_recognize
        if 'ambiguous' in output or any(row.get('metric') == 'cosine_similarity' for row in output['faces']):
            from app.perception.faces import format_recognition_report
            return format_recognition_report(output)
        names = [row["name"] for row in output["faces"] if row.get("name")]
        count = output.get("count") or 0
        if not count:
            return "No faces were found in that picture."
        text = f"{count} face{'s' if count != 1 else ''} in the picture"
        text += (": " + ", ".join(names) + " (enrolled)") if names else ", none of them enrolled"
        if output.get("unknown"):
            text += f"; {output['unknown']} unknown (not identified, and nobody is looked up)"
        return text + "."
    if output.get("available") is False and "faces_detected" in output:  # face_recognize without models
        count = output["faces_detected"]
        return f"{count} face{'s' if count != 1 else ''} detected, but nobody can be recognised yet: {output.get('reason')}"
    return None


def _people(output: dict[str, Any]) -> str | None:
    """Local person detection (app/perception/people.py): presence, confidence and where in the frame.

    Showed raw JSON on 2026-10-24. It finds THAT a person is present, never who; identity stays with
    the opt-in face tools (enrol a known person), and images are checked only against the exact-file source search.
    """
    if output.get("person_detection") is not True:
        return None
    if "count" not in output:  # people_status
        if not output.get("installed"):
            return ("The person detector is not installed yet. Run python scripts/setup_person_detection.py "
                    "to download its pinned local model (no camera is opened).")
        missing = [name for name, ok in (output.get("dependencies") or {}).items() if not ok]
        if missing:
            return f"The person detector is installed but needs: {', '.join(missing)}. Install them, then try again."
        return ("Person detector: installed and ready, running locally on the CPU. It finds that people are "
                "present in an image; it does not identify anyone.")
    count, width, height = output["count"], output.get("width") or 0, output.get("height") or 0
    where = (" in " + PurePath(str(output["path"])).name) if output.get("path") else (
        " from the camera" if output.get("source") == "owner camera" else "")
    if not count:
        return (f"No person was detected{where} at {int(round(output.get('threshold', .5) * 100))}% confidence or above. "
                "A zero count does not prove nobody is present — small, dark, side-on or partly hidden people can be missed.")
    lines = [f"Found {count} {'person' if count == 1 else 'people'}{where}:"]
    frame_area = (width * height) or 1
    for index, person in enumerate(sorted(output["people"], key=lambda item: item.get("score", 0), reverse=True)[:100], 1):
        box = person.get("box") or [0, 0, 0, 0]
        pct = f"{float(person.get('score', 0)) * 100:.2f}"
        size = max(0, min(100, round(100 * (box[2] - box[0]) * (box[3] - box[1]) / frame_area)))
        cols = ("left", "centre", "right")
        rows = ("top", "middle", "bottom")
        col = cols[min(2, int(3 * ((box[0] + box[2]) / 2) / width))] if width else "centre"
        row = rows[min(2, int(3 * ((box[1] + box[3]) / 2) / height))] if height else "middle"
        spot = "centre" if (col, row) == ("centre", "middle") else (row + "-" + col if col != "centre" else row).replace("middle-", "")
        lines.append(f"  Person {index}: {pct}% confidence · about {size}% of the frame · {spot}")
    lines.append("This finds that a person is present, not who it is. To tell apart people you have enrolled, say "
                 '"enroll <name> from <photo>"; HAYAT never identifies an unknown person from a face.')
    lines.append('For exact image provenance, open Studios → Searches → Image research. '
                 'For professional sources, enter a known name and organization; detection scores are not identity-match probabilities.')
    return "\n".join(lines)


def _privacy_masks(output: dict[str, Any]) -> str | None:
    """Masks (app/privacy_masks.py): the layers, leaks, what sites see, and starting or stopping Tor."""
    if output.get("kind") == "privacy_masks":
        lines = [f"Masks on: {output['masks_on']} of {len(output['layers'])}."]
        lines += [f"{'●' if layer['on'] else '○'} {layer['layer']}: {layer['detail']}" for layer in output["layers"]]
        lines += [f"• {row['what']} {row['fix']}".strip() for row in output["leaks"]]
        computer, hayat = output.get("computer_seen_as") or {}, output.get("hayat_seen_as") or {}
        if computer.get("checked"):
            lines.append(f"Websites see your computer near {computer['place']}"
                         + (f" ({computer['network']})" if computer.get("network") else "") + ".")
        if hayat.get("checked"):
            lines.append(f"Websites see HAYAT near {hayat['place']}"
                         + (f" ({hayat['network']})" if hayat.get("network") else "") + ", through Tor.")
        if not output["tor"].get("masked"):
            lines.append('Say "mask HAYAT" to send HAYAT through Tor, then "open masked browser" for your own browsing.')
        lines.append(output["limits"])
        return "\n".join(lines)
    if "masked" in output and ("proxy" in output or "note" in output or "reason" in output):
        return output.get("note") or output.get("reason") or None
    if output.get("installed") is not None and "tor" in str(output.get("path", "")).lower():
        if not output["installed"]:
            return f"Tor was not set up: {output.get('reason')}"
        return (f"Tor {output.get('version')} is set up in {output['path']}. Say \"mask hayat\" to send HAYAT's web "
                "reading through it.")
    if "opened" in output and "browser" in output or (output.get("opened") is False and "Tor is not on" in str(output.get("reason"))):
        return output.get("note") or output.get("reason")
    return None


def _location_privacy(output: dict[str, Any]) -> str | None:
    """location_privacy_check and its helpers (app/location_privacy.py)."""
    if output.get("kind") == "location_privacy":
        lines = ["Who can learn where you are:"]
        lines += [f"• {row['what']} {row['fix']}" for row in output["findings"]] or ["• Nothing found that gives it away."]
        lines.append("What HAYAT does to keep it private:")
        lines += [f"• {line}" for line in output["protections"]]
        return "\n".join(lines)
    if "installed" in output and str(output.get("path", "")).endswith("places.csv"):
        if not output["installed"]:
            return f"Place names were not installed: {output.get('reason')}"
        count = f"{output['places']:,} places " if output.get("places") else ""
        return (f"Private place names are set up ({count}in {output['path']}). Where you are is now named on this "
                "computer; your coordinates are not sent anywhere for it.")
    if output.get("allowed") is True and "minutes" in output and "note" in output:
        return output["note"]
    if "stripped" in output and "path" in output:
        return (f"Saved {output['path']} without its location. The original is unchanged." if output["stripped"]
                else output.get("note") or "")
    return None


def _email_check(output: dict[str, Any]) -> str | None:
    """email_address_check (app/email_check.py): mail setup, where the address is published, and no fake location."""
    if output.get("kind") != "email_address_check":
        return None
    mail, published, location = output["mail"], output["published"], output["location"]
    lines = [f"{output['address']}" + (" (your address)" if output.get("mine") else "")]
    if mail.get("exists") is False:
        lines.append(f"{mail['domain']} does not exist in DNS.")
    elif mail.get("error"):
        lines.append(mail["error"])
    else:
        lines.append(f"Mail for {mail['domain']} is received by {mail.get('provider') or 'no mail server'}"
                     + (f" ({', '.join(mail['mx'][:3])})" if mail.get("mx") else "") + ".")
        lines.append("SPF: " + ("published" if mail.get("spf") else mail.get("spf_problem") or "none")
                     + " · DMARC: " + (f"p={mail['dmarc_policy']}" if mail.get("dmarc_policy") else
                                       "published" if mail.get("dmarc") else "none")
                     + " · DKIM: " + (", ".join(mail["dkim_selectors_found"]) if mail.get("dkim_selectors_found")
                                      else "not found under common selectors") + ".")
    if published.get("checked"):
        lines.append("Printed on " + ", ".join(published["found_on"]) + "." if published.get("found_on")
                     else f"Not printed on the {len(published['checked'])} page(s) read.")
    lines.append(location["answer"])
    if location.get("you_now"):
        here = _location(location["you_now"])
        if here:
            lines.append("Where you are now, from this computer: " + here.replace("📍 ", ""))
    elif location.get("you_now_error"):
        lines.append("Your own location could not be read: " + location["you_now_error"])
    elif location.get("note"):
        lines.append(location["note"])
    if output.get("advice"):
        lines.append("To fix: " + " ".join(output["advice"]))
    return "\n".join(lines)


def _invention(output: dict[str, Any]) -> str | None:
    """Invention workbench outputs (app/invention.py): a project, a simulation, or a graded formula."""
    if "sweeps" in output and "monte_carlo" in output:  # invention_simulate
        return "\n".join([f"Simulation of {output.get('formula')}:", *(output.get("summary") or []),
                          str(output.get("basis") or "")]).strip()
    if "measurement" in output and output.get("saved") is False and "formula" in output:  # invention_try
        measured = output["measurement"]
        text = f"{output['formula']} passed {measured['passed']} of {measured['total']} frozen cases (graded, not saved)."
        if output.get("fitted_constants"):
            text = (f"Fitted {output.get('shape')}: " + ", ".join(f"{k} = {v:.6g}" for k, v in output["fitted_constants"].items())
                    + ". " + text)
        return text
    if "acceptance_hash" not in output or "rounds" not in output:
        return None
    best = output.get("best") or {}
    if not output["rounds"] and not output.get("tests"):
        # "Create invention project: design a low-cost water monitor" got back only "Invention invent-…: DRAFT
        # (0 rounds)" -- nothing about what was made or how to go on (live, 2026-10-24).
        goal = " ".join(str(output.get("goal") or "").split())
        return "\n".join([
            f"🧪 New invention project: “{goal[:160]}”",
            f"Project id: {output['id']}",
            "It has no test cases yet, so there is nothing to work out or simulate. A test case is a set of input "
            "numbers and the result you expect, e.g. flow 2 L/min and 30 minutes → 60 L.",
            "Next: open Invention in the sidebar and add a few cases (Load example shows the format), then say "
            f"“work out invention {output['id']}”.",
            "Numeric cases only: novelty and physical validity are not verified.",
        ])
    lines = [f"Invention {output['id']}: {output.get('state')} ({len(output['rounds'])} rounds). {output.get('stop_reason') or ''}".strip()]
    if best:
        measured, candidate = best["measurement"], best["candidate"]
        how = "worked out by fitting, no model" if best.get("method") == "fit" else "proposed by the model"
        lines.append(f"Best: round {best['round']} ({how}), {candidate['title']}: "
                     f"{measured['passed']} of {measured['total']} cases passed with {candidate.get('formula') or '(no formula)'}.")
        loo = best.get("leave_one_out")
        if loo:
            lines.append(f"Predicting each case from the others: mean error {loo['mean_relative_error']:.3g} "
                         f"({loo['predicted_within_tolerance']} of {loo['cases']} within tolerance).")
    lines += (output.get("simulation") or {}).get("summary") or []
    lines.append("Numeric cases only: novelty and physical validity are not verified.")
    return "\n".join(lines)


def _situation(output: dict[str, Any]) -> str | None:
    """HAYAT's understanding of what is going on, with the evidence for each part."""
    if output.get("situation") is not True:
        return None
    focus, habits = output.get("focus") or {}, output.get("habits") or {}
    lines: list[str] = []
    if focus.get("areas"):
        lines.append("You have been working on: " + ", ".join(
            f"{item['title']} ({item['requests']} request{'s' if item['requests'] != 1 else ''})" for item in focus["areas"]) + ".")
    elif focus.get("last_session"):
        last = focus["last_session"]
        lines.append(f"Nothing new in the last few hours. Last session ({last['at'][:16].replace('T', ' ')}) was about: "
                     + (", ".join(last["areas"]) or "general conversation") + ".")
    else:
        lines.append("I have no record of your requests yet, so I don't know what you're working on.")
    if focus.get("latest"):
        lines.append(f"Your last request: “{focus['latest']['text'][:120]}”")
    brief = output.get("brief") or {}
    if brief.get("recent_tasks"):
        lines.append("Recent tasks: " + "; ".join(brief["recent_tasks"]) + ".")
    if output.get("waiting"):
        lines.append("Waiting: " + "; ".join(output["waiting"]) + ".")
    if habits.get("most_asked"):
        lines.append(f"Over {habits['requests']} requests you ask most about {', '.join(habits['most_asked'])}"
                     + (f"; in the {habits['part_of_day']} usually {', '.join(habits['usual_now'])}" if habits.get("usual_now") else "") + ".")
    predictions = output.get("predictions") or []
    accuracy = output.get("prediction_accuracy") or {}
    if predictions:
        lines.append("Likely next: " + "; ".join(f"{item['title']} ({item['basis']})" for item in predictions) + ".")
        lines.append(f"My next-request guesses were right {accuracy['right']} of the last {accuracy['checked']} times."
                     if accuracy.get("checked") else "I haven't checked any guesses yet, so their accuracy is unknown.")
    else:
        lines.append("I don't see a pattern strong enough to predict your next request yet.")
    if brief.get("learned_from"):
        lines.append("Learned from " + brief["learned_from"] + ".")
    if output.get("gaps"):
        lines.append("What I can't do right now:")
        lines.extend(f"  - {gap}" for gap in output["gaps"][:6])
    return "\n".join(lines)


def _discovery(output: dict[str, Any]) -> str | None:
    """What public-source discovery found, what is live, what waits, what needs access."""
    if output.get("discovery") is not True:
        return None
    if output.get("approved"):
        return (f"Source {output['approved']} is now live as {output['feed']}: {output['cameras']} cameras indexed. "
                "Its media is fetched only from the hosts its own catalog uses.")
    counts = output.get("counts") or {}
    lines = []
    for run in output.get("ran") or []:
        lines.append(f"Ran {run.get('job')}: " + ", ".join(f"{key} {value}" for key, value in run.items()
                                                          if key not in {"job", "ok"} and value not in ("", None))[:200])
    lines.append("Discovery " + ("is running in the background" if output.get("running") else "is not running")
                 + ("" if output.get("can_run_now") else f" (waiting: {output.get('waiting_because')})")
                 + f"; budget {output.get('budget_per_hour')} requests an hour.")
    if counts:
        lines.append("Sources: " + ", ".join(f"{count} {status.replace('_', ' ')}" for status, count in sorted(counts.items())) + ".")
    for source in output.get("active") or []:
        lines.append(f"  Live: #{source['id']} {source['domain']} -- {source['reason']}")
    for source in output.get("proposed") or []:
        lines.append(f"  Waiting for you: #{source['id']} {source['domain']} -- {source['reason']} "
                     f"Say \"approve source {source['id']}\" to use it.")
    for source in output.get("needs_access") or []:
        lines.append(f"  Needs access: #{source['id']} {source['domain']} -- {source['access']}")
    for row in output.get("health_problems") or []:
        lines.append(f"  Health: {row['feed']} served {row['working']} of {row['sampled']} sampled frames.")
    if output.get("mcp_suggestions"):
        lines.append("MCP servers worth a look (not installed): " + "; ".join(
            f"{row['name']} ({row['access']})" for row in output["mcp_suggestions"][:5]) + ".")
    for source in output.get("trial_failed") or []:
        lines.append(f"  Failed its trial: #{source['id']} {source['domain']} -- {source['reason']}")
    best = [row for row in output.get("strategies") or [] if row.get("runs")][:4]
    if best:
        lines.append("Search strategies so far: " + "; ".join(
            f"{row['kind']} '{row['query'][:40]}' -> {row['candidates']} candidates, {row['catalogs']} catalogs, {row['cameras']} cameras"
            for row in best) + ".")
    if output.get("category_coverage"):
        thin = sorted(output["category_coverage"].items(), key=lambda pair: pair[1])[:3]
        lines.append("Fewest cameras by kind: " + ", ".join(f"{name} {count:,}" for name, count in thin) + " (searched for first).")
    if output.get("coverage_gaps"):
        lines.append(f"Cameras in {output.get('countries_covered')} countries; searching first where there are none: "
                     + ", ".join(output["coverage_gaps"][:8]) + ".")
    lines.append(str(output.get("basis") or ""))
    return "\n".join(line for line in lines if line)


def _local_image_tools(output):
    if output.get('image_source_result') is True:
        from app.tools.image_sources import format_source_report
        return format_source_report(output)
    if any(output.get(key) is True for key in ('local_image_inspection', 'local_image_comparison', 'local_image_toolkit')):
        from app.perception.image_tools import format_image_tool_report
        return format_image_tool_report(output)
    return None


#: Checked in order; the first renderer that recognises an output wins.
RENDERERS: tuple[Callable[[dict[str, Any]], str | None], ...] = (
    _local_image_tools, _listing, _matches, _contents, _process, _info, _exists, _system, _download,
    _location, _devices, _prospects, _lead_list, _quran, _camera_status, _faces, _people, _invention, _email_check, _privacy_masks, _location_privacy, _situation, _discovery,
    _ideas, _launch_status, _rate, _proposal, _money_report, _wallet, _streams, _brief, _business_result,
)


def render(output: Any) -> str | None:
    if not isinstance(output, dict):
        return None
    for renderer in RENDERERS:
        try:
            text = renderer(output)
        except Exception:
            text = None
        if text:
            return text
    return None


def report(history: list[dict[str, Any]]) -> str | None:
    """The verified observations of a task, written as its reply."""
    sections: list[str] = []
    for event in history or []:
        if not event.get("verification", {}).get("passed"):
            continue
        text = render(event.get("result", {}).get("output"))
        if text and text not in sections:
            sections.append(text)
    return "\n\n".join(sections) or None


def _plain_error(tool: str, arguments: dict[str, Any], error: str) -> str:
    """One failure, said the way a person would say it."""
    kind, _, message = error.partition(":")
    target = arguments.get("path") or arguments.get("url") or arguments.get("application") or ""
    name = str(target)
    if kind in {"FileNotFoundError"} or "No such file" in error:
        return f"there is no {name or 'such file'}" + (" here" if not name else "")
    if kind in {"NotADirectoryError"}:
        # list_folder raises this for a path that is not there at all, too.
        import os

        resolved = message.strip() or name
        return f"{name} is a file, not a folder" if os.path.isfile(resolved) else f"there is no folder {name}"
    if kind in {"IsADirectoryError"}:
        return f"{name} is a folder, not a file; ask me to list it instead"
    if "is not connected" in message:
        # An account that was never connected is not a refusal: say how to connect it.
        account = message.split(" is not connected", 1)[0].strip() or "That account"
        return (f"{account} isn't connected yet. Connect it in Studios → Accounts & sign-in; "
                "you sign in on its own page or paste the token it gives you")
    if kind in {"PermissionError"} or "not allowed" in error.lower() or "outside" in error.lower():
        return f"I'm not allowed to access {name or 'that'} ({message.strip() or kind})"
    if kind in {"TimeoutError"} or "timed out" in error.lower():
        return f"{tool.replace('_', ' ')} took too long and was stopped"
    readable = message.strip() or kind
    return f"{tool.replace('_', ' ')} failed: {readable[:220]}"


def explain_failure(goal: str, history: list[dict[str, Any]]) -> str | None:
    """Why a task failed, from its last failed step, or None if nothing says."""
    for event in reversed(history or []):
        result = event.get("result") or {}
        output = result.get("output")
        if ((event.get("verification") or {}).get("passed") is False
                and isinstance(output, dict) and output.get("exit_code") != 0
                and "exit_code" in output):
            return render(output)
        error = str(result.get("error") or "").strip()
        if error and not result.get("success"):
            reason = _plain_error(str(event.get("tool") or "the step"), event.get("arguments") or {}, error)
            request = " ".join(str(goal or "that").split()).rstrip(".!?")
            request = request[:1].lower() + request[1:] if request else "that"
            return f"I couldn't {request}: {reason}."
    return None


def steps_trace(intent: Any, outcome: Any) -> list[str]:
    """How a reply was reached, one line per step, for the chat's expander.

    Taken from the task's own history: what the request was understood as,
    each tool that ran, whether its check passed and why. Nothing inferred.
    """
    history = list(getattr(outcome, "history", None) or [])
    if not history:
        return []
    kind = getattr(getattr(intent, "intent", None), "value", None) or str(getattr(intent, "intent", "") or "")
    lines = [f"Understood as {kind.replace('_', ' ').lower()}" if kind else "Understood the request"]
    total = 0
    for index, event in enumerate(history, 1):
        verification = event.get("verification") or {}
        result = event.get("result") or {}
        passed = bool(verification.get("passed"))
        duration = int(result.get("duration_ms") or event.get("duration_ms") or 0)
        total += duration
        tool = str(event.get("tool") or "step").replace("_", " ")
        description = str(event.get("description") or "").strip().rstrip(".")
        retries = int(event.get("retry") or 0)
        line = f"{'✓' if passed else '✗'} {index}. {description or tool} — {tool}"
        if duration:
            line += f", {duration} ms"
        if retries:
            line += f", {retries} retr{'y' if retries == 1 else 'ies'}"
        reason = str(verification.get("reason") or result.get("error") or "").strip()
        if reason and not passed:
            line += f"\n      {_redacted(reason)[:200]}"
        lines.append(line)
    verified = sum(bool((event.get("verification") or {}).get("passed")) for event in history)
    lines.append(f"{verified} of {len(history)} step{'s' if len(history) != 1 else ''} verified"
                 + (f" · {total} ms of tool time" if total else ""))
    return lines
