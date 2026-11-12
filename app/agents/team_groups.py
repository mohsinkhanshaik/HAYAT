"""Grouped worker teams: how a group lead condenses its members' findings without losing a source.

A flat team is as big as the brain's window lets the Verification Agent and the
final answer read every worker at a useful size (app/agents/capacity.py). A
bigger team is split into groups. Each group's lead reads its members' full
findings and writes a short condensed report; the verifier and the final answer
read the group reports instead of every raw finding.

Condensing is where a big team could quietly become dishonest, so the model
only proposes the condensed claims and code decides what they are:

* a claim counts as **cited** only when its link is one a member's own tool call
  actually returned; any other claim -- no link, or a link nobody observed -- is
  kept and marked **unverified**, never dropped;
* every member that returned findings reaches the group report with at least one
  line (its digest); when the lead left a member out, code adds an excerpt;
* the items each member covered (already checked against observed links) are
  merged by name, so an item two members or two groups covered counts once;
* when something does not fit the report's share of the window it is counted in
  a "left out to fit" line, and the full claim list is saved with the team report;
* if the lead fails or the team stops, code condenses instead and says so.

Nothing here calls a tool; leads have no tools at all.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.agents.capacity import MIN_DIGEST_CHARS

#: What a group lead returns. Its claims are checked here, not trusted.
LEAD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "claims": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"claim": {"type": "string"}, "source": {"type": "string"},
                           "member": {"type": "string"}},
            "required": ["claim", "source", "member"]}},
    }, "required": ["summary", "claims"],
}
CITED, UNVERIFIED, EXCERPT = "cited", "unverified", "excerpt"
_URL = re.compile(r"https?://[^\s)\]\"'<>]+")


@dataclass
class TeamGroup:
    """One group of a grouped team, and (once its lead has run) its condensed report."""

    name: str
    focus: str
    members: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    designed_by: str = ""
    report: str = ""
    summary: str = ""
    #: Every claim, with its source, the member it came from, and cited/unverified/excerpt.
    claims: list[dict[str, str]] = field(default_factory=list)
    coverage: list[dict[str, str]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    condensed_by: str = ""
    #: What the report left out to fit its share of the window (all of it is in `claims`/`coverage`).
    omitted: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0

    def cited_sources(self) -> list[str]:
        return list(dict.fromkeys(claim["source"] for claim in self.claims if claim["status"] == CITED))


def norm_url(url: str) -> str:
    return str(url or "").strip().rstrip(".,;").rstrip("/")


def distinct_coverage(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """Items merged by name (case and spacing ignored): the first observed source is kept."""
    seen: dict[str, dict[str, str]] = {}
    for row in rows:
        name = " ".join(str(row.get("company") or "").split())
        if name and name.casefold() not in seen:
            seen[name.casefold()] = {"company": name, "source": str(row.get("source") or "")}
    return list(seen.values())


def member_brief(result: Any, share: int) -> str:
    """One member's findings for its lead, sources and covered items included, within `share` characters."""
    status = "evidence gathered with tools" if result.ok else "UNVERIFIED: no successful tool evidence"
    head = f"[{result.role}] ({status}; task complete: {result.complete})"
    gaps = f"Gaps: {result.note[:300]}" if result.note else "Gaps: none recorded"
    items = "; ".join(f"{row['company']} ({row['source']})" for row in result.coverage)
    sources = list(result.sources)
    room = max(200, share - len(head) - len(gaps) - 40)
    findings = (result.findings or "(no findings)")[: max(120, room * 2 // 3)]
    tail_room = max(80, room - len(findings))
    items_text = f"Items covered: {items}"[: tail_room // 2] if items else ""
    shown, used = [], 0
    for url in sources:
        if used + len(url) + 2 > tail_room - len(items_text):
            break
        shown.append(url)
        used += len(url) + 2
    more = f" (+{len(sources) - len(shown)} more)" if len(shown) < len(sources) else ""
    lines = [head, findings, f"Observed source links: {', '.join(shown) or 'none'}{more}"]
    if items_text:
        lines.append(items_text)
    lines.append(gaps)
    return "\n".join(lines)


def lead_prompt(goal: str, group: TeamGroup, members: list[Any], share: int, report_chars: int) -> str:
    briefs = "\n\n".join(member_brief(result, share) for result in members if result.findings)
    return (
        f"You are the lead of the '{group.name}' group in HAYAT's temporary team. Team goal: {goal}\n"
        f"Group focus: {group.focus}\n"
        f"Condense your {len(members)} members' findings below into one short group report for the Verification "
        "Agent. Rules: every claim must keep the exact source link the member gave for it; copy links exactly. "
        "Give at least one claim for every member that found something, naming that member. If a claim has no "
        "link, set source to an empty string -- it will be marked unverified, which is fine; never invent or "
        "repair a link. Merge duplicate claims. Do not add facts the members did not report. "
        f"Keep the summary under 300 characters and the whole reply under {report_chars} characters.\n\n"
        f"{briefs}\n\n"
        'Reply with JSON only: {"summary": "...", "claims": [{"claim": "...", "source": "https://...", '
        '"member": "member role"}]}'
    )


def parse_lead(content: str) -> dict[str, Any] | None:
    value = str(content or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        return None
    return data


def _excerpt(result: Any) -> str:
    text = " ".join(str(result.findings or "").split())
    text = _URL.sub("", text).strip(" -:;,")
    return text[:200] or "(findings without readable text)"


def condense(group: TeamGroup, members: list[Any], lead: dict[str, Any] | None, *, budget: int,
             why: str = "") -> None:
    """Fill the group's claims, coverage, gaps and report text from its members and its lead's reply.

    `lead` is the lead's parsed reply, or None when code must condense alone
    (`why` says why). Claims are checked against links the members' tools
    actually returned; nothing a member reported is dropped silently.
    """
    by_role = {result.role.casefold(): result for result in members}
    observed: dict[str, str] = {}          # normalised link -> exact link, from members with tool evidence
    observer: dict[str, str] = {}          # normalised link -> first member that observed it
    for result in members:
        if result.ok:
            for url in result.sources:
                key = norm_url(url)
                observed.setdefault(key, url)
                observer.setdefault(key, result.role)
    claims: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(text: str, source: str, member: str, status: str, reason: str = "") -> None:
        text = " ".join(str(text or "").split())[:300]
        key = (text.casefold(), norm_url(source))
        if not text or key in seen:
            return
        seen.add(key)
        claims.append({"claim": text, "source": source, "member": member, "status": status, "reason": reason})

    for row in (lead or {}).get("claims") or []:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source") or "").strip()
        named = by_role.get(" ".join(str(row.get("member") or "").split()).casefold())
        member = named.role if named is not None else ""
        key = norm_url(source)
        if source and key in observed:
            add(row.get("claim"), observed[key], member or observer[key], CITED)
        elif source:
            add(row.get("claim"), source, member, UNVERIFIED, "its link was not returned by any worker's tools")
        else:
            add(row.get("claim"), "", member, UNVERIFIED, "no source link")
    # Every member that found something reaches the report, even if the lead left it out.
    for result in members:
        if not result.findings or any(claim["member"] == result.role for claim in claims):
            continue
        own = [norm_url(url) for url in _URL.findall(result.findings or "")]
        link = next((observed[key] for key in own if key in observed), "") if result.ok else ""
        link = link or (result.sources[0] if result.ok and result.sources else "")
        if result.ok:
            add(_excerpt(result), link, result.role, EXCERPT,
                "not condensed by the lead; an excerpt with one link this worker observed")
        else:
            add(_excerpt(result), "", result.role, UNVERIFIED, "this worker gathered no tool evidence")
    group.claims = claims
    group.coverage = distinct_coverage(row for result in members for row in result.coverage)
    group.gaps = [f"{result.role}: {result.note}" if result.findings else
                  f"{result.role}: no findings ({result.note or 'nothing returned'})"
                  for result in members if result.note or not result.findings]
    if lead is not None:
        group.summary = " ".join(str(lead.get("summary") or "").split())[:400]
        group.condensed_by = "lead"
    else:
        group.summary = ""
        group.condensed_by = f"code ({why or 'no lead reply'})"
    group.report = render(group, members, budget)


def render(group: TeamGroup, members: list[Any], budget: int) -> str:
    """The group report the verifier and the final answer read, within `budget` characters.

    Priority: header, one line per member, the summary, unverified claims,
    covered items, remaining cited claims, gaps. Whatever does not fit is
    counted in the last line, never silently cut.
    """
    evidence = sum(1 for result in members if result.ok)
    head = (f"Group '{group.name}': {len(members)} workers, {evidence} with tool evidence; condensed by "
            f"{group.condensed_by}. Focus: {group.focus[:120]}")
    titles = {"summary": "Summary: ", CITED: "Cited claims (each link was returned by a worker's tool):\n",
              UNVERIFIED: "Unverified claims (kept; no link a worker observed):\n", "items": ": ", "gaps": "Gaps: "}
    count_line = f"Items covered: {len(group.coverage)} distinct"
    reserve = len(count_line) + 170              # the item count line and the "left out to fit" line
    room = max(0, budget - len(head) - reserve)
    picked: dict[str, list[str]] = {"summary": [], CITED: [], UNVERIFIED: [], "items": [], "gaps": []}
    used_claims: set[int] = set()

    def line(claim: dict[str, str], cap: int) -> str:
        if claim["status"] == UNVERIFIED:
            return f"- UNVERIFIED: {claim['claim'][:cap]} [{claim['reason']}]"
        prefix = "- " if claim["status"] == CITED else "- (excerpt) "
        return f"{prefix}{claim['claim'][:cap]} -- {claim['source']}"

    def take(section: str, text: str) -> bool:
        nonlocal room
        cost = len(text) + (1 if section in (CITED, UNVERIFIED) else 3) + (
            len(titles[section]) + 1 if not picked[section] else 0)
        if cost > room:
            return False
        picked[section].append(text)
        room -= cost
        return True

    # 1. One digest line per member: its first claim, shortened to the digest size.
    for result in members:
        index = next((i for i, claim in enumerate(group.claims) if claim["member"] == result.role), None)
        if index is None:
            continue
        claim = group.claims[index]
        cap = max(60, MIN_DIGEST_CHARS - len(claim["source"]) - 8)
        if take(UNVERIFIED if claim["status"] == UNVERIFIED else CITED, line(claim, cap)):
            used_claims.add(index)
    # 2. The lead's summary.
    if group.summary and room > 80:
        take("summary", group.summary[: room - 40])
    # 3. Unverified claims, so they are never hidden behind cited ones.
    for index, claim in enumerate(group.claims):
        if index not in used_claims and claim["status"] == UNVERIFIED and take(UNVERIFIED, line(claim, 200)):
            used_claims.add(index)
    # 4. Items covered (merged by name).
    shown_items = 0
    for row in group.coverage:
        if not take("items", f"{row['company']} ({row['source']})"):
            break
        shown_items += 1
    # 5. Remaining cited claims and excerpts.
    for index, claim in enumerate(group.claims):
        if index not in used_claims and take(CITED, line(claim, 300)):
            used_claims.add(index)
    # 6. Gaps.
    shown_gaps = sum(1 for gap in group.gaps if take("gaps", gap[:160]))
    group.omitted = {key: value for key, value in (
        ("claims", len(group.claims) - len(used_claims)), ("items", len(group.coverage) - shown_items),
        ("gaps", len(group.gaps) - shown_gaps)) if value > 0}
    lines = [head]
    if picked["summary"]:
        lines.append(titles["summary"] + picked["summary"][0])
    if picked[CITED]:
        lines += [titles[CITED].rstrip("\n"), *picked[CITED]]
    if picked[UNVERIFIED]:
        lines += [titles[UNVERIFIED].rstrip("\n"), *picked[UNVERIFIED]]
    lines.append(count_line + (titles["items"] + "; ".join(picked["items"]) if picked["items"] else ""))
    if picked["gaps"]:
        lines.append(titles["gaps"] + " | ".join(picked["gaps"]))
    if group.omitted:
        lines.append("Left out here to fit the brain's window (all of it is in the saved team report): "
                     + ", ".join(f"{value} {key}" for key, value in group.omitted.items()) + ".")
    text = "\n".join(lines)
    if len(text) > budget:                       # only with pathological link lengths
        text = text[: max(0, budget - 90)] + "\n[cut to fit the window; the full claims are in the saved report]"
    return text
