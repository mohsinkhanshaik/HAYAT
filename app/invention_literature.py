"""The invention workbench reads the literature, as a scientist does: before proposing, and again when stuck.

Before this, a project looked something up once, at the start: one official-sources web search on the goal. Two
things a researcher does were missing:

* **Prior art.** What published work already says about the problem. :func:`papers` asks OpenAlex, the open
  catalogue of scholarly works (CC0, no key, robots allow all), for the most relevant works on the goal, and keeps
  each one's title, year, citation count, link and abstract (rebuilt from OpenAlex's word index, at most 900
  characters). The proposer reads them as untrusted data, like any web page.
* **Going back when stuck.** When two model rounds in a row fail to beat the best formula, :func:`stuck_question`
  finds the acceptance case the best formula gets most wrong and asks what changes in that regime: the input that
  is at its largest or smallest there ("pendulum period with large amplitude", "drag at small radius"). That is
  where established models usually break, and where the literature says what replaces them. The workbench
  searches the web and the papers once for it and gives the proposer what came back.

Nothing here changes the acceptance cases, and a paper never counts as evidence that a formula works: only the
frozen cases measure that.
"""
from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import quote

OPENALEX_WORKS = "https://api.openalex.org/works"
ABSTRACT_CHARS = 900


def abstract_text(inverted: dict[str, list[int]] | None) -> str:
    """OpenAlex stores an abstract as {word: [positions]}; put the words back in order."""
    if not isinstance(inverted, dict):
        return ""
    positions: dict[int, str] = {}
    for word, places in inverted.items():
        for place in places or ():
            if isinstance(place, int) and 0 <= place < 5000:
                positions[place] = str(word)
    text = " ".join(positions[index] for index in sorted(positions))
    return text if len(text) <= ABSTRACT_CHARS else text[:ABSTRACT_CHARS - 1].rstrip() + "…"


def papers(question: str, get_json: Callable[[str], Any], *, limit: int = 5) -> list[dict[str, Any]]:
    """The most relevant published works with an abstract, most relevant first."""
    query = " ".join(re.findall(r"[\w-]+", question))[:200]
    if not query:
        return []
    url = (f"{OPENALEX_WORKS}?search={quote(query)}&per-page={max(1, min(int(limit) * 2, 20))}"
           "&select=id,doi,display_name,publication_year,cited_by_count,abstract_inverted_index")
    data = get_json(url)
    found = []
    for work in (data or {}).get("results", []) if isinstance(data, dict) else []:
        abstract = abstract_text(work.get("abstract_inverted_index"))
        title = " ".join(str(work.get("display_name") or "").split())
        if not abstract or not title:
            continue
        found.append({"title": title[:300], "year": work.get("publication_year"),
                      "citations": int(work.get("cited_by_count") or 0),
                      "url": work.get("doi") or work.get("id"), "abstract": abstract})
        if len(found) >= limit:
            break
    return found


def stuck(data: dict[str, Any], rounds: int = 2) -> bool:
    """Whether the last ``rounds`` model proposals all failed to beat the best before them."""
    model_rounds = [record for record in data.get("rounds") or [] if record.get("method") == "model"]
    if len(model_rounds) < rounds:
        return False
    best = data.get("best") or {}
    measurement = best.get("measurement") or {}
    if measurement.get("total") and measurement.get("passed") == measurement.get("total"):
        return False  # everything already passes: not stuck, finished
    return all(record is not best for record in model_rounds[-rounds:])


def _worst_case(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    best = data.get("best") or {}
    cases = (best.get("measurement") or {}).get("cases") or []
    tests = data.get("tests") or []
    scored = []
    for index, case in enumerate(cases):
        if case.get("passed") or index >= len(tests):
            continue
        error = case.get("relative_error")
        scored.append((float(error) if isinstance(error, (int, float)) else float("inf"), index))
    if not scored:
        return None
    _, index = max(scored)
    return tests[index], cases[index]


def _number(value: Any) -> float | None:
    """Acceptance inputs are stored as written ("0.5" or 0.5)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def stuck_question(data: dict[str, Any]) -> str | None:
    """What to look up when stuck: the goal, in the regime where the best formula is most wrong."""
    worst = _worst_case(data)
    goal = " ".join(str(data.get("goal") or "").split())[:160]
    if worst is None or not goal:
        return None
    test, _case = worst
    inputs = test.get("inputs") or {}
    extremes = []
    for name, raw in inputs.items():
        value = _number(raw)
        numbers = [number for other in data.get("tests") or []
                   if (number := _number(other.get("inputs", {}).get(name))) is not None]
        if len(numbers) >= 2 and value is not None and max(numbers) != min(numbers):
            if value == max(numbers):
                extremes.append(f"large {name}")
            elif value == min(numbers):
                extremes.append(f"small {name}")
    regime = " and ".join(extremes[:2])
    return f"{goal} {regime} correction beyond the simple model" if regime else f"{goal} where the simple model fails"
