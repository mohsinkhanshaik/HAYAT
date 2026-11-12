"""Twisted-instruction ("bounce") and metamorphic tests: does HAYAT keep constraints across turns?

Each case is a short conversation in a session of its own. After chosen turns, code
(never a model) checks the reply against named constraints: text that must appear,
text that must not, or relations between turns ("items 1 and 3 unchanged"). The report
lists exactly which constraint held or broke, so a failure says what was lost:
the original budget, a corrected name, the referent of "the original number".

Metamorphic groups ask one question several ways (reworded, reordered, misspelled,
in mixed Hindi/Urdu-English); every variant must give the same answer.

Evaluation material only: is_bounce_prompt lets training and self-practice refuse it.
"""
from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any, Callable

VERSION = "bounce-v2"
GRADER_VERSION = "bounce-grader-v3"
#: Changes to the checks after a run, kept so a report says which checks scored it.
GRADER_CHANGES = [
    "2026-10-31: the contradiction check accepts 'no single laptop can' and 'simultaneously': the first live run "
    "named the contradiction in those words ('no single laptop can simultaneously cost under $500 and at least "
    "$800') and was marked wrong.",
    "2026-11-01: the reversal check compares item words, not emoji: 'apple, banana, orange 🍎🍌🍊' reversed as "
    "'orange, banana, apple 🍊🍌🍎' was marked wrong because the emoji trail sat on the last item. The "
    "'exactly the same' checks still count emoji.",
]

#: Each check: turn index (0-based), a constraint name, and required / forbidden regexes,
#: or a relation ("same_items" / "changed_item") against an earlier turn's numbered list.
CASES: list[dict[str, Any]] = [
    {"id": "budget-kept", "family": "constraint_tracking",
     "turns": ["Plan a 3-day trip to Chicago for a budget of $600. Keep it short.",
               "Actually make it 2 days instead.",
               "Keep the original budget. Show the final plan."],
     "checks": [(2, "keeps the $600 budget", [r"\$?\s?600"], []),
                (2, "uses 2 days, not 3", [r"(?i)\b(?:day\s*2|2[- ]day|two[- ]day|two days)\b"],
                 [r"(?i)\bday\s*3\b"])]},
    {"id": "long-reference", "family": "long_distance_reference",
     "turns": ["For a project: my budget is $1,200 and my deadline is March 3. Just acknowledge.",
               "What is 15% of 80?", "Name one primary colour.", "What was my deadline?"],
     "checks": [(1, "answers 12", [r"\b12\b"], []), (3, "recalls March 3", [r"(?i)march\s*3|3(?:rd)?\s*march"], [])]},
    {"id": "name-correction", "family": "user_correction",
     "turns": ["My sister's name is Aisha.", "Sorry, I meant her name is Amina, not Aisha.",
               "What is my sister's name? One word."],
     "checks": [(2, "uses the corrected name", [r"(?i)\bamina\b"], [r"(?i)^\W*aisha\W*$"])]},
    {"id": "original-number", "family": "referent_tracking",
     "turns": ["Remember the number 47 for this conversation.", "Add 5 to it.",
               "Now double the original number, not the new one. Just the result."],
     "checks": [(1, "47 + 5 = 52", [r"\b52\b"], []), (2, "doubles 47, not 52", [r"\b94\b"], [r"\b104\b"])]},
    {"id": "unit-followup", "family": "units",
     "turns": ["A rectangle is 3 m by 4 m. What is its area?", "Give that in square feet."],
     "checks": [(0, "12 square metres", [r"\b12\b"], []), (1, "about 129 square feet", [r"\b129(?:\.\d+)?\b"], [])]},
    {"id": "change-one-item", "family": "partial_edit",
     "turns": ["Write a numbered packing list of exactly 3 items for a rainy day. Items only.",
               "Change only the second item to something else and keep the other two exactly the same. Show the list."],
     "checks": [(1, "items 1 and 3 unchanged, item 2 changed", [], [], ("changed_item", 0, 2))]},
    {"id": "reverse-list", "family": "reversal",
     "turns": ["List exactly three fruits as a numbered list, nothing else.",
               "Reverse the order of that list. Numbered, nothing else."],
     "checks": [(1, "same three items in reverse order", [], [], ("reversed", 0))]},
    {"id": "pronoun-which", "family": "pronoun_resolution",
     "turns": ["Paris and Rome are both capital cities. Which of them is in Italy? One word."],
     "checks": [(0, "Rome", [r"(?i)\brome\b"], [r"(?i)^\W*paris\W*$"])]},
    {"id": "contradiction", "family": "contradictory_constraints",
     "turns": ["I need a laptop that costs under $500 but it must cost at least $800. Which one should I buy?"],
     "checks": [(0, "names the contradiction", [r"(?i)contradict|conflict|can'?t (?:both|be both)|cannot (?:both|be both)|"
                                                r"not possible|impossible|both under \$?500 and|mutually exclusive|"
                                                r"no (?:single |one )?laptop can|simultaneously|doesn'?t make sense|clarify"], [])]},
    {"id": "impossible-prime", "family": "impossible_constraint",
     "turns": ["Give me a prime number strictly between 24 and 28."],
     "checks": [(0, "says there is none", [r"(?i)\bno (?:prime|such)|\bnone\b|there (?:is|are) no|aren'?t any|isn'?t (?:one|a prime)|"
                                          r"no primes?\b|not (?:a )?prime"], [r"(?i)\b(?:25|26|27) is (?:a )?prime\b"])]},
    {"id": "party-long", "family": "long_conversation",
     "turns": ["I'm planning a party: 12 guests, vegetarian food only, budget $300, on Saturday 14 November. Just acknowledge.",
               "What is 7 times 8?", "Name one fruit that is red.", "How do you say hello in Spanish?",
               "What is the capital of Japan?", "Give me a synonym for happy.",
               "Suggest a short menu for the party.", "How many guests and what budget did I give?"],
     "checks": [(6, "keeps the menu vegetarian", [], [r"(?i)\b(?:chicken|beef|pork|bacon|ham|lamb|fish|salmon|tuna|shrimp|"
                                                       r"prawns?|turkey|sausages?|pepperoni|meatballs?)\b"]),
                (7, "recalls 12 guests", [r"\b12\b|\btwelve\b"], []),
                (7, "recalls the $300 budget", [r"\$?\s?300\b"], [])]},
    {"id": "first-version", "family": "earlier_version",
     "turns": ["Write a one-line slogan for a bakery. Only the slogan.", "Make it funnier. Only the slogan.",
               "Actually, go back to the first version, exactly as it was. Only the slogan."],
     "checks": [(2, "returns the first slogan unchanged", [], [], ("same_as", 0))]},
    {"id": "count-keeps-limit", "family": "constraint_tracking",
     "turns": ["Give me 3 tips for better sleep as a numbered list, each under 10 words.",
               "Now give 5 tips instead, keeping the same length limit. Numbered list."],
     "checks": [(1, "5 items, each under 10 words", [], [], ("count_max_words", 5, 10))]},
    {"id": "language-carry", "family": "standing_instruction",
     "turns": ["Answer in Spanish from now on. What is the capital of France?", "And of Italy?"],
     "checks": [(0, "answers Paris", [r"(?i)par[ií]s"], []),
                (1, "answers Rome, still in Spanish", [r"(?i)\broma\b"], [r"(?i)\bthe capital\b"])]},
    {"id": "typo-percent", "family": "typos",
     "turns": ["wat is 25 percnt of 240"],
     "checks": [(0, "60", [r"\b60\b"], [])]},
    {"id": "hinglish-sum", "family": "code_switching",
     "turns": ["Mujhe batao 12 aur 15 ka sum kya hai?"],
     "checks": [(0, "27", [r"\b27\b"], [])]},
    {"id": "urdu-mixed-days", "family": "code_switching",
     "turns": ["Ek hafte mein kitne din hote hain? Number batao."],
     "checks": [(0, "7", [r"\b7\b|\bseven\b|\bsaat\b"], [])]},
]

#: One question, several surface forms; every variant must give the expected answer.
METAMORPHIC: list[dict[str, Any]] = [
    {"id": "percent", "expected": [r"\b57\.8\b"],
     "variants": ["What is 17% of 340?", "340 multiplied by 0.17 equals what?",
                  "Compute seventeen percent of three hundred forty.", "whats 17 percnt of 340"]},
    {"id": "speed", "expected": [r"\b(?:80|eighty)\b"],
     "variants": ["A car travels 240 km in 3 hours. What is its average speed in km/h?",
                  "In 3 hours a car covers 240 kilometres. Average speed in km per hour?",
                  "Average speed in km/h if you drive 240 km over 3 hours, with a coffee stop that doesn't change the totals?"]},
    {"id": "capital", "expected": [r"(?i)\bcanberra\b"],
     "variants": ["What is the capital of Australia?", "Australia's capital city is which city?",
                  "capital of australia??", "Australia ki capital kya hai?"]},
]


def is_bounce_prompt(text: str) -> bool:
    """Whether a text is one of these evaluation prompts (training and practice must refuse it)."""
    value = " ".join(str(text or "").split()).casefold()
    prompts = {turn for case in CASES for turn in case["turns"]} | {v for group in METAMORPHIC for v in group["variants"]}
    # Contained anywhere (a training sample is a JSON record around the prompt), like is_holdout.
    return bool(value) and any(" ".join(p.split()).casefold() in value for p in prompts)


def _items(text: str) -> list[str]:
    """The numbered or bulleted items of a reply, normalised for comparison."""
    found = re.findall(r"(?m)^\s*(?:\d+[.)]|[-*•])\s+(.+?)\s*$", str(text or ""))
    return [re.sub(r"[*_`]+", "", item).strip().rstrip(".").casefold() for item in found]


def _plain(text: str) -> str:
    """A reply reduced to its words, for "exactly as it was" comparisons (quotes, emoji and case ignored)."""
    return " ".join(re.findall(r"[a-z0-9']+", str(text or "").casefold())).replace("'", "")


def check(spec: tuple, replies: list[str]) -> dict[str, Any]:
    """Grade one constraint against the conversation's replies."""
    turn, name, required, forbidden = spec[:4]
    reply = replies[turn] if turn < len(replies) else ""
    missing = [pattern for pattern in required if not re.search(pattern, reply)]
    violated = [pattern for pattern in forbidden if re.search(pattern, reply)]
    relation = spec[4] if len(spec) > 4 else None
    if relation:
        kind, earlier = relation[0], relation[1]
        before, after = _items(replies[earlier]) if earlier < len(replies) else [], _items(reply)
        if kind == "same_as":
            first = _plain(replies[earlier])
            between = _plain(replies[turn - 1]) if turn - 1 > earlier else ""
            # Going back only shows anything if the first version was real and the next one differed.
            ok = len(first.split()) >= 2 and between != first and _plain(reply) == first
            if not ok:
                missing.append(f"same_as: before={_plain(replies[earlier])[:80]!r} after={_plain(reply)[:80]!r}")
            return {"turn": turn, "constraint": name, "passed": not missing and not violated,
                    "missing": missing, "violated": violated}
        if kind == "count_max_words":
            count, limit = relation[1], relation[2]
            items = _items(reply)
            ok = len(items) == count and all(len(item.split()) < limit for item in items)
            if not ok:
                missing.append(f"count_max_words: {len(items)} items, longest {max((len(i.split()) for i in items), default=0)} words")
            return {"turn": turn, "constraint": name, "passed": not missing and not violated,
                    "missing": missing, "violated": violated}
        if kind == "changed_item":
            index = relation[2] - 1
            ok = (len(before) >= 3 and len(after) >= 3 and after[index] != before[index]
                  and all(after[i] == before[i] for i in range(min(3, len(before))) if i != index))
        else:  # reversed: the order is what is tested, so emoji after an item do not make it a different item
            plain_before, plain_after = [_plain(item) for item in before], [_plain(item) for item in after]
            ok = len(before) >= 3 and plain_after[:len(before)] == list(reversed(plain_before))
        if not ok:
            missing.append(f"{kind}: before={before[:4]} after={after[:4]}")
    return {"turn": turn, "constraint": name, "passed": not missing and not violated,
            "missing": missing, "violated": violated}


def run(converse: Callable[[list[str]], list[str]], *, model: str = "unknown") -> dict[str, Any]:
    """Run every case and metamorphic variant through ``converse`` (turns -> replies, one session each)."""
    rows, started = [], time.monotonic()
    for case in CASES:
        t0 = time.monotonic()
        try:
            replies = converse(case["turns"])
            error = ""
        except Exception as exc:  # a crash is a failure with its reason kept
            replies, error = [], f"{type(exc).__name__}: {exc}"
        results = [check(spec, replies) for spec in case["checks"]]
        rows.append({"id": case["id"], "family": case["family"], "turns": case["turns"], "replies": replies,
                     "checks": results, "passed": bool(results) and all(r["passed"] for r in results) and not error,
                     "error": error, "seconds": round(time.monotonic() - t0, 1)})
    groups = []
    for group in METAMORPHIC:
        answers = []
        for variant in group["variants"]:
            try:
                answers.append(converse([variant])[0])
            except Exception as exc:
                answers.append(f"ERROR {type(exc).__name__}: {exc}")
        agree = [all(re.search(pattern, answer) for pattern in group["expected"]) for answer in answers]
        groups.append({"id": group["id"], "variants": group["variants"], "answers": answers,
                       "correct": agree, "passed": all(agree)})
    constraints = [r for row in rows for r in row["checks"]]
    return {
        "version": VERSION, "grader_version": GRADER_VERSION, "grader_changes": GRADER_CHANGES, "model": model, "checked_at": datetime.now(UTC).isoformat(),
        "seconds": round(time.monotonic() - started, 1), "cases": rows, "metamorphic": groups,
        "summary": {"conversations": len(rows), "conversations_passed": sum(r["passed"] for r in rows),
                    "constraints": len(constraints), "constraints_held": sum(c["passed"] for c in constraints),
                    "metamorphic_groups": len(groups), "metamorphic_consistent": sum(g["passed"] for g in groups),
                    "metamorphic_variants": sum(len(g["correct"]) for g in groups),
                    "metamorphic_variants_correct": sum(sum(g["correct"]) for g in groups)},
        "scope": "Code-graded multi-turn and paraphrase probes. Inspect the replies: regex checks can miss a correct "
                 "answer phrased unusually, and passing these does not prove general reliability.",
    }


def markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [f"# Twisted-instruction and metamorphic tests ({report['version']})", "",
             f"- Model: `{report['model']}` · checked {report['checked_at'][:19]} · {report['seconds']} s",
             f"- Conversations fully correct: {s['conversations_passed']}/{s['conversations']}",
             f"- Constraints held: {s['constraints_held']}/{s['constraints']}",
             f"- Metamorphic groups consistent: {s['metamorphic_consistent']}/{s['metamorphic_groups']} "
             f"({s['metamorphic_variants_correct']}/{s['metamorphic_variants']} variants correct)", "",
             "| Case | Family | Constraint | Held | Detail |", "|---|---|---|---|---|"]
    for row in report["cases"]:
        for c in row["checks"]:
            detail = "; ".join([*("missing " + m for m in c["missing"]), *("violated " + v for v in c["violated"])])
            lines.append(f"| {row['id']} | {row['family']} | {c['constraint']} | {'yes' if c['passed'] else '**no**'} | "
                         f"{(row['error'] or detail)[:160].replace('|', '/')} |")
    lines += ["", "| Metamorphic group | Variants correct |", "|---|---|"]
    for group in report["metamorphic"]:
        lines.append(f"| {group['id']} | {sum(group['correct'])}/{len(group['correct'])} |")
    lines += ["", report["scope"]]
    return "\n".join(lines) + "\n"
