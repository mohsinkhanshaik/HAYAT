"""A goal measured against its own size: what it requires, which kinds of business can reach it, what to check.

Asked for "better ideas to make money and become billionaire by 2030", HAYAT answered three times with the list of
services one person can start this week (freelance writing, logo design ...). That list answers a different
question. No amount of hours sold reaches a billion dollars, and that can be shown with arithmetic instead of
being argued about.

This module does the part that can be *computed* and the part that can be *structured*, and it names the part
that has to be *looked up*:

* **Computed** -- from the amount and the year in the owner's own words: the years left, the money per year a
  wage or a service would have to bring, the return an investment would need, the company value an equity stake
  implies, the yearly revenue behind that value, and the growth per year needed to get there. Every number is
  arithmetic on stated assumptions, shown with the assumption.
* **Structured** -- the five things a business must have to reach that size (revenue that grows without the
  founder's hours, a large enough market, a way to reach customers that costs less than they are worth, capital,
  and an advantage a richer competitor cannot copy), and how each *kind* of business does on them. These are
  tests, not recommendations: they say which kinds can reach the size at all and what would have to be true.
* **Looked up** -- base rates and today's markets. Nothing here states a statistic from memory. The evaluation
  lists the questions, and the assistant starts real searches for them (app/assistant.py), whose sourced results
  arrive as their own messages.

Circumstances (money in hand, city, computer) are shown as the starting point with the date he stated them. They
decide the first step. They never decide what is recommended.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.dialogue_state import parse_amount, parse_horizon

#: Asking about money, income or wealth at all.
_MONEY = re.compile(
    r"(?i)\b(?:money|income|rich|wealth(?:y)?|billion\w*|million\w*|earn(?:ing|ings)?|net\s*worth|revenue|profit|"
    r"financial(?:ly)?\s+(?:free|freedom|independen\w+)|fortune|unicorn)\b")
#: Asking how, or for ways, ideas, a plan.
_ASK = re.compile(
    r"(?i)\b(?:how|ways?|ideas?|plans?|paths?|roadmap|strateg\w+|options?|methods?|steps?|what\s+(?:should|can|could|do)|"
    r"where\s+(?:to|do|should)|look|find|search|research|show|give|tell|help|want|need|become|reach|make|build|start)\b")
#: The size of the question is the subject: "better and higher ways", "as AI rises and jobs go".
_SCALE_WORDS = re.compile(
    r"(?i)\b(?:higher|bigger|biggest|large[- ]scale|scale|scalable|massive|huge|serious|real\s+wealth|generational|empire|"
    r"elite|top\s+1\s?%|rise\s+of\s+ai|ai\s+(?:era|age|boom|revolution|wave)|(?:jobs?|people|workers?)\s+(?:are\s+|being\s+|getting\s+)?"
    r"laid\s+off|lay\s?offs?|automation|future[- ]proof|grow\s+up|level\s+up|next\s+level)\b")
#: He asked for the small list on purpose.
_SMALL_START = re.compile(
    r"(?i)\b(?:side\s+hustles?|this\s+week|today|quick(?:ly)?|small|simple|easy|beginner|one[- ]person|solo|freelanc\w+|"
    r"part[- ]time|pocket\s+money|extra\s+(?:cash|money|income)|starter\s+(?:business\s+)?list|first\s+\$?\d+)\b")
_OTHER_PERSON = re.compile(r"(?i)\bhow\s+(?:did|does|do|has|have)\s+(?!i\b)[\w\s.'’-]{2,40}?\s+(?:become|make|get|earn|build)\b")


@dataclass(frozen=True)
class Ambition:
    """What a message asks for when its size is the point."""

    amount: float | None
    horizon: int | None
    reason: str
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"amount": self.amount, "horizon": self.horizon, "reason": self.reason}


def ambition_request(text: str, *, today: date | None = None) -> Ambition | None:
    """Whether a message asks how to reach money at a scale that a list of small services cannot answer."""
    value = " ".join(str(text or "").split())
    if not value or len(value) > 600 or not _MONEY.search(value) or not _ASK.search(value):
        return None
    if _OTHER_PERSON.search(value) and not re.search(r"(?i)\b(?:i|me|my)\b", value):
        return None
    amount = parse_amount(value)
    if amount is not None and amount >= 1e6:
        return Ambition(amount, parse_horizon(value, today), "names an amount", value)
    if _SCALE_WORDS.search(value) and not _asks_small(value):
        return Ambition(None, parse_horizon(value, today), "asks for larger ways than a small start", value)
    return None


#: "not small ones", "nothing small", "instead of side hustles", "beyond freelancing": a small start he turned down.
_DECLINED_SMALL = re.compile(
    r"(?i)\b(?:not|no|nothing|never|don['’]?t\s+want|dont\s+want|instead\s+of|rather\s+than|beyond|bigger\s+than|"
    r"more\s+than|other\s+than|apart\s+from|except)\s+(?:the\s+|any\s+|those\s+|these\s+|just\s+|a\s+|some\s+)?"
    r"(?:\w+\s+){0,2}?" + _SMALL_START.pattern.removeprefix("(?i)"))


def _asks_small(value: str) -> bool:
    """He asked for the small list, not merely mentioned it to turn it down. Live chat, 2026-11-11: "i want higher
    ways not small ones" counted as asking for small ones, and the starter list came back."""
    return bool(_SMALL_START.search(_DECLINED_SMALL.sub(" ", value)))


def wants_small_start(text: str) -> bool:
    return _asks_small(" ".join(str(text or "").split()))


# ------------------------------------------------------------------------------------------------ the arithmetic


def years_left(horizon: int, today: date) -> float:
    """Years from today to the end of the horizon year (the latest reading of "by 2030")."""
    return max(0.0, (date(horizon, 12, 31) - today).days / 365.25)


def required_return(start: float, target: float, years: float) -> float | None:
    """The yearly return that turns ``start`` into ``target`` in ``years`` (0.25 = 25% a year)."""
    if start <= 0 or years <= 0 or target <= start:
        return None
    return (target / start) ** (1 / years) - 1


def yearly_growth(first: float, last: float, years: float) -> float | None:
    """The multiple revenue must grow by each year to go from ``first`` to ``last`` in ``years``."""
    if first <= 0 or years <= 0 or last <= first:
        return None
    return (last / first) ** (1 / years)


def money(value: float) -> str:
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(value) >= size:
            scaled = value / size
            return f"${scaled:.1f}{suffix}".replace(".0", "")
    return f"${value:,.0f}"


#: Assumptions the arithmetic uses. They are shown beside every number that depends on them.
STAKES = (1.0, 0.5, 0.2, 0.1)
REVENUE_MULTIPLES = (5, 10, 20)
SERVICE_RATE, SERVICE_HOURS = 200.0, 2000.0
FIRST_REVENUE, FIRST_REVENUE_YEARS = 1e6, 1.0

#: The five tests of scale, in the order a plan meets them.
TESTS = (
    ("leverage", "Does revenue grow without the founder's hours growing with it?"),
    ("market", "Is the market big enough that the revenue needed is a small share of it?"),
    ("distribution", "Is there a way to reach customers that costs less than a customer is worth?"),
    ("capital", "How much money is needed before revenue, and who would fund it?"),
    ("advantage", "What stops a better-funded competitor from copying it?"),
)

#: Kinds of business, judged on the tests. ``reaches`` is whether the kind can reach nine or ten figures at all.
MODELS: tuple[dict[str, Any], ...] = (
    {"key": "hours", "name": "Selling your own hours (freelance, consulting, a service you perform)",
     "reaches": False, "leverage": "No. Income is hours times rate, and hours are capped.",
     "capital": "Almost none.", "kills": "Nothing kills it; it simply stops at what one person can bill.",
     "role": "A way to earn the first money and to learn a customer's problem from the inside. Not the destination."},
    {"key": "agency", "name": "An agency or staffed service", "reaches": False,
     "leverage": "Partly. Revenue grows with headcount, so margin stays thin and each new dollar needs a new hire.",
     "capital": "Low: payroll before clients pay.", "kills": "Losing two large clients; hiring ahead of work.",
     "role": "Can fund a product and reveal which task every client pays for again and again."},
    {"key": "software", "name": "A software product sold to many customers (subscription, usage or licence)",
     "reaches": True, "leverage": "Yes. One more customer costs almost nothing to serve.",
     "capital": "Low to start, large to grow fast: selling and engineering ahead of revenue.",
     "kills": "Building what nobody will pay for; customers who leave as fast as they join.",
     "role": "Needs the least capital to begin of the kinds that can reach the size."},
    {"key": "platform", "name": "A marketplace or network that gets more useful as more people join",
     "reaches": True, "leverage": "Yes, strongly, once both sides are present.",
     "capital": "High early: one side has to be paid for or subsidised until the other arrives.",
     "kills": "Never reaching the point where each side attracts the other.",
     "role": "The hardest software kind to start: it is worth nothing to anyone until both sides are there."},
    {"key": "infrastructure", "name": "Infrastructure other companies build on (tools, compute, data, payments, models)",
     "reaches": True, "leverage": "Yes. Revenue follows the customers' own growth.",
     "capital": "Medium to very high, depending on whether it needs hardware.",
     "kills": "A large platform giving the same thing away; customers building it themselves.",
     "role": "Fits someone with deep technical skill in one layer that others find painful."},
    {"key": "deeptech", "name": "A physical or scientific product (hardware, energy, biotech, robotics)",
     "reaches": True, "leverage": "Yes, after it works; before that, years of cost with no revenue.",
     "capital": "Very high, for a long time.", "kills": "Running out of money before the product works; regulation.",
     "role": "Needs outside capital and a team from the first day."},
    {"key": "audience", "name": "Media, education or a brand built on an audience", "reaches": True,
     "leverage": "Yes for content, less for anything delivered in person.",
     "capital": "Low.", "kills": "Attention that does not turn into repeat purchases; dependence on one platform.",
     "role": "On its own it sells attention; reaching the size needs a product sold to that audience."},
    {"key": "investing", "name": "Investing or trading your own money", "reaches": True,
     "leverage": "Yes, but only in proportion to the money already there.",
     "capital": "It is the capital.", "kills": "Losses compound too; borrowed money multiplies them.",
     "role": "Grows wealth that exists; see the return it would need from a small base, above."},
    {"key": "acquire", "name": "Buying existing businesses and growing them", "reaches": True,
     "leverage": "Yes, through other people's cash flow and debt.",
     "capital": "High, mostly borrowed against the business bought.",
     "kills": "Debt payments when a bought business shrinks.", "role": "Needs a track record that lenders trust."},
)


@dataclass
class Evaluation:
    amount: float
    amount_assumed: bool
    horizon: int | None
    years: float | None
    lines: list[str] = field(default_factory=list)
    numbers: dict[str, Any] = field(default_factory=dict)
    questions: list[str] = field(default_factory=list)
    milestones: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"amount": self.amount, "amount_assumed": self.amount_assumed, "horizon": self.horizon,
                "years": self.years, "numbers": self.numbers, "questions": self.questions,
                "milestones": self.milestones, "text": "\n".join(self.lines)}


QUESTIONS_PER_ROUND = 3


def research_questions(amount: float, horizon: int | None, *, focus: str = "",
                       circumstances: list[dict[str, Any]] | None = None) -> list[str]:
    """Everything that has to be looked up, in the order to look it up; each round takes the next unasked ones."""
    year = horizon or (date.today().year + 5)
    size = "billion-dollar" if amount >= 1e9 else "hundred-million-dollar" if amount >= 1e8 else "multi-million-dollar"
    questions = [
        f"what share of venture-funded startups reach a {size} valuation, and how many years it takes",
        f"which industries are creating the most {size} companies now and are forecast to grow fastest to {year}",
        "fastest companies to reach $100 million annual revenue: what they sold and how they reached customers",
    ]
    if focus:
        questions.insert(1, f"largest unsolved problems companies pay for in {focus}, with market size estimates")
    # Questions his own circumstances raise: they decide what the first step can be.
    for row in circumstances or []:
        attribute, value = str(row.get("attribute") or ""), str(row.get("value") or "")
        if attribute == "visa":
            questions.append(f"rules for founding and working for your own company while on {value}, and the usual paths")
        elif attribute == "capital":
            questions.append("how first-time founders with no savings fund a company: grants, accelerators, pre-sales, "
                             "and what each requires")
        elif attribute in {"location", "planned_move"}:
            questions.append(f"startup accelerators, founder programs and investors for first-time founders in {value}")
        elif attribute == "hardware":
            questions.append(f"software products companies pay for that can be built and demonstrated on one {value} computer")
        elif attribute == "work":
            questions.append(f"problems people working as {value} say they would pay to have solved, with market sizes")
    questions += [
        "how companies that grew from zero to $100 million revenue found their first 10 paying customers",
        f"which kinds of AI companies reached $100 million revenue fastest up to {date.today().year}, and what they sold",
        "most common reasons venture-backed startups fail, with the share for each reason",
        "revenue multiples at which private software companies are valued now, by growth rate",
    ]
    return list(dict.fromkeys(questions))


#: Assumptions round 1 rests on, each challenged in a later round with its own numbers.
CHALLENGES = ("stake", "timing", "date", "amount")


def _challenge(name: str, target: float, horizon: int | None, today: date) -> str:
    first_year = today.year + int(FIRST_REVENUE_YEARS)
    last_year = horizon or today.year + 5
    if name == "stake":
        return ("I assumed you keep 20% of the company. Every funding round sells part of it: if you keep 10%, it "
                f"must be worth {money(target / 0.1)} (about {money(target / 0.1 / 10)} of yearly revenue at 10× "
                f"revenue); at 5%, {money(target / 0.05)}.")
    if name == "timing":
        late = yearly_growth(FIRST_REVENUE, target / 0.2 / 10, last_year - first_year - 1)
        return (f"I assumed the first {money(FIRST_REVENUE)} of yearly revenue by the end of {first_year}. If it comes "
                f"a year later, revenue has to multiply by {late:.1f} every year after that"
                if late else f"I assumed the first {money(FIRST_REVENUE)} of yearly revenue by the end of {first_year}. "
                "A year later leaves no time at all") + " — the first year is the one that decides the rest."
    if name == "date":
        later = last_year + 5
        relaxed = yearly_growth(FIRST_REVENUE, target / 0.2 / 10, later - first_year)
        return (f"I took {last_year} as fixed. With {later} instead, revenue would have to multiply by {relaxed:.1f} "
                "a year instead: still steep, but a pace companies have recorded. Moving the date changes the "
                "plan more than any choice of business does." if relaxed else "")
    if name == "amount":
        smaller = target / 10
        need = yearly_growth(FIRST_REVENUE, smaller / 0.2 / 10, last_year - first_year)
        return (f"I took {money(target)} as the only finish line. {money(smaller)} by {last_year} needs revenue to "
                f"multiply by {need:.1f} a year; a stage worth reaching on the way, and the same company either way."
                if need else "")
    return ""


#: Kinds of business his circumstances make worth testing first (hypotheses, each with the test that decides it).
def _possibilities(circumstances: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for row in circumstances:
        attribute, value = str(row.get("attribute") or ""), str(row.get("value") or "")
        if attribute == "hardware":
            found.append(f"Infrastructure or software built on what your computer ({value}) can already run: a "
                         "local-AI tool that a business pays for because its data cannot leave its own machines. "
                         "Test: can you find 10 companies that say yes to a paid pilot before you build more?")
        elif attribute == "work":
            found.append(f"A software product for the problem you see every day as {value}. Test: name the task, who "
                         "pays for it today, and what they pay; if nobody pays for it now, it is not a business yet.")
        elif attribute in {"location", "planned_move"}:
            found.append(f"Using where you are ({value}) for distribution: founder programs and customers you can meet "
                         "in person. Test: how many target customers can you talk to in the next 30 days?")
    found.append("A problem companies already pay to have solved, over and over, where the current answer is slow or "
                 "done by hand. Test: three customers describe the same pain in their own words without being led.")
    return found


def evaluate(amount: float | None = None, horizon: int | None = None, *, today: date | None = None,
             circumstances: list[dict[str, Any]] | None = None, refused: list[dict[str, Any]] | None = None,
             goal_statement: str = "", focus: str = "", round_number: int = 1, asked: Any = (),
             evidence: list[dict[str, Any]] | None = None) -> Evaluation:
    """The goal against its own size. Pure arithmetic and structure; no statistic is stated from memory.

    Round 1 measures the goal. Each later round goes further instead of repeating it: it challenges the
    assumptions round 1 rested on with new numbers, tests possibilities against his stated circumstances, reports
    the evidence that has come back, and researches questions not asked before.
    """
    today = today or date.today()
    if round_number > 1:
        return _further(amount, horizon, today=today, circumstances=circumstances or [], refused=refused or [],
                        focus=focus, round_number=round_number, asked=set(asked or ()), evidence=evidence or [])
    assumed = amount is None
    target = float(amount or 1e9)
    years = years_left(horizon, today) if horizon else None
    result = Evaluation(target, assumed, horizon, round(years, 1) if years is not None else None)
    out = result.lines
    numbers = result.numbers

    goal_line = f"Goal: {money(target)}" + (f" by the end of {horizon}" if horizon else "")
    if goal_statement:
        goal_line += f" (your words: “{goal_statement[:140]}”)"
    elif assumed:
        goal_line += " (you named no amount, so I used your scale words and worked it for $1B; tell me the real figure)"
    out.append(goal_line + ".")
    if years is not None:
        out.append(f"Time left: {years:.1f} years from today ({today.isoformat()}).")
    if refused:
        out.append("Not repeating: " + "; ".join(f"“{str(row.get('reply', ''))[:70].strip()}…”" for row in refused[:2])
                   + " — you refused that, and the arithmetic below shows why it could not answer this goal.")

    span = years if years and years > 0 else 5.0
    span_note = "" if years else " (no year named, so 5 years is assumed)"

    out += ["", "1. What the goal requires (arithmetic, with its assumption beside each number)"]
    per_year = target / span
    service = SERVICE_RATE * SERVICE_HOURS
    numbers["needed_per_year"] = per_year
    numbers["service_income_per_year"] = service
    numbers["service_share"] = service / per_year
    out.append(f"- Earned as income: {money(target)} over {span:.1f} years is {money(per_year)} a year{span_note}. "
               f"A service billed at ${SERVICE_RATE:,.0f} an hour for {SERVICE_HOURS:,.0f} hours is {money(service)} a year: "
               f"{service / per_year:.2%} of what is needed. No wage or solo service reaches it; this is why a list of "
               "freelance services cannot be the answer.")
    invest_lines = []
    for start in (1e4, 1e6, 1e8):
        rate = required_return(start, target, span)
        if rate is not None:
            numbers[f"return_from_{int(start)}"] = rate
            invest_lines.append(f"{money(start)} needs {rate:,.0%} a year")
    if invest_lines:
        out.append(f"- Reached by investing: {'; '.join(invest_lines)}, every year for {span:.1f} years. Investing "
                   "multiplies money that already exists; from a small base the return required is far outside "
                   "anything that can be planned on.")
    out.append("- Reached through ownership: the two lines above leave a share of a company as the route. "
               "Company value needed for your share to be worth " + money(target) + ": "
               + ", ".join(f"{money(target / stake)} if you own {stake:.0%}" for stake in STAKES)
               + ". Each funding round sells part of the company, so the share shrinks as outside money comes in.")
    stake = 0.2
    company = target / stake
    revenue = {multiple: company / multiple for multiple in REVENUE_MULTIPLES}
    numbers["company_value_at_20pct"] = company
    numbers["revenue_needed"] = revenue
    out.append(f"- Revenue behind that value, if you keep {stake:.0%}: "
               + ", ".join(f"{money(amount_)} a year if the company is valued at {multiple}× revenue"
                           for multiple, amount_ in revenue.items())
               + ". The multiple is an assumption; fast-growing software is valued higher than services or retail.")
    middle = revenue[10]
    # Whole calendar years, so the stages below can be checked on a date: the first revenue by the end of next
    # year, then one multiplication per year until the end of the horizon year.
    first_year = today.year + int(FIRST_REVENUE_YEARS)
    last_year = horizon or math.ceil(today.year + span)
    growth_years = last_year - first_year
    growth = yearly_growth(FIRST_REVENUE, middle, growth_years) if growth_years >= 1 else None
    if growth is not None:
        numbers["yearly_growth_multiple"] = growth
        out.append(f"- Growth needed: if the first {money(FIRST_REVENUE)} of yearly revenue arrives by the end of "
                   f"{first_year}, getting to {money(middle)} by the end of {last_year} leaves {growth_years} "
                   f"year{'s' if growth_years != 1 else ''}: revenue has to multiply by {growth:.1f} every one of them. "
                   "That multiple is the size of the ask.")
        for index in range(growth_years + 1):
            result.milestones.append({"year": first_year + index,
                                      "yearly_revenue": round(FIRST_REVENUE * (growth ** index))})
    else:
        out.append(f"- Growth needed: there is no full year between a first {money(FIRST_REVENUE)} of revenue and the "
                   f"end of {last_year}, so the date cannot be met by growing a new company; either the date or the "
                   "amount has to move.")

    out += ["", "2. Which kinds of business can reach that size at all (five tests, not picks)"]
    out += [f"- {question}" for _, question in TESTS]
    out.append("How each kind does on the first test, which decides whether it can get there:")
    for model in MODELS:
        verdict = "can reach it" if model["reaches"] else "cannot reach it"
        out.append(f"- {model['name']}: {verdict}. {model['leverage']} Capital: {model['capital']} "
                   f"What usually ends it: {model['kills']} {model['role']}")

    out += ["", "3. Your starting point (where the first step begins, not a limit)"]
    if circumstances:
        for row in circumstances:
            label = str(row.get("label") or row.get("attribute") or "")
            out.append(f"- {label}: {row.get('value')} (you said this on {str(row.get('at', ''))[:10]}; tell me when it changes)")
        out.append("These decide the first step only. A plan that needs capital, a move or a team says so, and the "
                   "question becomes how to get them, not whether to leave the plan out.")
    else:
        out.append("- I hold no stated circumstances for you. Tell me your skills, money in hand and where you are, "
                   "and I will use them for the first step only.")

    out += ["", "4. Honest odds"]
    out.append("I am not stating a probability from memory. The arithmetic above needs an outcome at the very top of "
               "what companies achieve, in less time than most take, so treat the full goal as a long shot and each "
               "stage on the way as worth reaching on its own. I am looking up the recorded base rates now and will "
               "give them with their sources.")
    if result.milestones:
        out.append("Stages to check progress against (yearly revenue on the curve above): "
                   + "; ".join(f"{item['year']}: {money(item['yearly_revenue'])}" for item in result.milestones) + ".")

    pool = research_questions(target, horizon, focus=focus, circumstances=circumstances)
    result.questions = [question for question in pool if question not in set(asked or ())][:QUESTIONS_PER_ROUND]
    if result.questions:
        out += ["", "5. What I am looking up now (sourced answers arrive as separate messages)"]
        out += [f"- {question}" for question in result.questions]
    out += ["", "Tell me which kind of business you want examined against the five tests, or name a problem you "
            "know from the inside, and I will work that one through with evidence. Ask again and I will go further "
            "rather than repeat this. If you want the small list of services to start this week instead, say "
            "“show the starter business list”."]
    return result


def _further(amount: float | None, horizon: int | None, *, today: date, circumstances: list[dict[str, Any]],
             refused: list[dict[str, Any]], focus: str, round_number: int, asked: set[str],
             evidence: list[dict[str, Any]]) -> Evaluation:
    """A later round: what is new since the last one, never the same measurement again."""
    target = float(amount or 1e9)
    years = years_left(horizon, today) if horizon else None
    result = Evaluation(target, amount is None, horizon, round(years, 1) if years is not None else None)
    out = result.lines
    out.append(f"Look number {round_number} at your goal ({money(target)}" + (f" by the end of {horizon}" if horizon else "")
               + "). The arithmetic from last time stands and I won't repeat it; this is what is new.")
    if refused:
        out.append("You refused: " + "; ".join(f"“{str(row.get('reply', ''))[:60].strip()}…”" for row in refused[:2]) + ".")
    found = [row for row in evidence if str(row.get("answer") or "").strip()]
    challenges = [text for text in (_challenge(name, target, horizon, today) for name in
                                    CHALLENGES[(round_number - 2) * 2:(round_number - 2) * 2 + 2]) if text]
    pool = research_questions(target, horizon, focus=focus, circumstances=circumstances)
    result.questions = [question for question in pool if question not in asked][:QUESTIONS_PER_ROUND]
    if not found and not challenges and not result.questions:
        # Nothing new to say: saying so is the honest answer, not a reworded copy of the last one.
        out += ["", "I have nothing new to add from here: every assumption is challenged, every question I can frame "
                "has been searched, and no new evidence has come back since the last look. Repeating it would not "
                "help you. What would move this forward is something only you can give me:",
                "- a specific problem, market or kind of business to test against the five tests;",
                "- a change in what you have (skills, money, a co-founder, a place) or in the amount or the date;",
                "- or what you have tried since, and what happened."]
        return result
    out += ["", "1. Evidence that has come back from the searches since the last look"]
    if found:
        for row in found[:4]:
            answer = " ".join(str(row["answer"]).split())
            out.append(f"- {row['question']}: {answer[:420]}{'…' if len(answer) > 420 else ''}")
        out.append("(Each full answer, with its sources, was sent as its own message. A search that ran is not proof "
                   "its answer is right: check the sources it names.)")
    else:
        out.append("- Nothing new has come back yet, so nothing below leans on it.")
    out += ["", "2. Assumptions from last time, challenged"]
    out += [f"- {text}" for text in challenges] or ["- I have challenged every assumption the measurement rested on. "
                                                     "What is left to change is your input: the amount, the date, or "
                                                     "a specific business to test."]
    if round_number == 2:
        out += ["", "3. Possibilities worth testing first, given what you have told me (hypotheses, each with its test)"]
        out += [f"- {text}" for text in _possibilities(circumstances)]
    else:
        out += ["", "3. The possibilities and their tests from look 2 still stand. Tell me which one you have tried "
                "and what happened, and I will judge the next step on that."]
    out += ["", "4. New questions I am looking up now"]
    out += ([f"- {question}" for question in result.questions] if result.questions else
            ["- I have searched every question I can frame from what I know. Name a problem, a market or a kind of "
             "business and I will research that instead of guessing."])
    return result


# ------------------------------------------------------------------------------------------------------ the tool


def register_ambition_tools(registry: Any, dialogue: Any = None) -> None:
    def run(amount: float = 0, year: int = 0, focus: str = "") -> dict[str, Any]:
        goal = dialogue.main_goal() if dialogue is not None else None
        target = float(amount) if amount else float(goal["amount"]) if goal and goal.get("amount") else None
        horizon = int(year) if year else int(goal["horizon"]) if goal and goal.get("horizon") else None
        circumstances = []
        if dialogue is not None:
            from app.dialogue_state import CIRCUMSTANCE, LABELS

            circumstances = [{**row, "label": LABELS.get(row["attribute"], row["attribute"])}
                             for row in dialogue.facts()[CIRCUMSTANCE]]
        return evaluate(target, horizon, circumstances=circumstances, focus=str(focus or ""),
                        goal_statement=str(goal["statement"]) if goal and not amount else "").to_dict()

    registry.register(
        "ambition_evaluate",
        "Measure a large money goal against its own size: years left, what income, investing or company ownership "
        "would each have to deliver (arithmetic on stated assumptions), which kinds of business can reach that "
        "size on five tests (leverage, market, distribution, capital, advantage), stages to check progress "
        "against, and the questions that need sourced research. Uses the owner's stated goal when no amount is "
        "given. States no statistic from memory. Read-only.",
        {"type": "object", "properties": {
            "amount": {"type": "number", "description": "Target in dollars, e.g. 1000000000."},
            "year": {"type": "integer", "description": "The year the goal is due."},
            "focus": {"type": "string", "description": "A field to centre the research questions on."}},
         "additionalProperties": False},
        "low", "system_read", run,
    )


def direct_ambition_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    """The plan for a goal-sized money question, when the tool is installed."""
    if "ambition_evaluate" not in names:
        return None
    found = ambition_request(text)
    if found is None:
        return None
    arguments: dict[str, Any] = {}
    if found.amount:
        arguments["amount"] = found.amount
    if found.horizon:
        arguments["year"] = found.horizon
    return [{"description": "Measure the goal against its own size and list what must be researched.",
             "tool": "ambition_evaluate", "arguments": arguments}]
