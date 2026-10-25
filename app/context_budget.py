"""Deciding how much context a turn is worth.

Two numbers were fixed constants everywhere: a 4096-token window and a 2048
token answer ceiling. Both were wrong in opposite directions at different
moments. "thanks" got a four-thousand-token window it had no use for, and a
question about how three Rust concepts fit together got the same window as
"thanks" -- with eleven earlier exchanges and eight retrieved memories
competing to fit inside it.

Nothing was choosing. This chooses, from three things it can actually measure:

  what the turn is        -- a greeting, a question, a task, a continuation
  what the machine has    -- free VRAM and free RAM, right now
  what the model is       -- how much window it will accept at all

and it explains every decision, because a budget that silently shrinks is
indistinguishable from a model that has started forgetting.

The bias is deliberate. When the evidence is thin it spends more rather than
less: a window slightly too large costs some milliseconds, and a window
slightly too small costs the end of the answer -- which Mohsin has already
had happen to him once, and noticed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


#: Roughly four characters per token for English prose. Wrong for code and for
#: other scripts, and right enough to size a window: every use here is a
#: comparison against a budget, never a promise about exact token counts.
CHARS_PER_TOKEN = 4

#: The smallest window worth asking for. Below this even a short exchange
#: loses its own system prompt.
MINIMUM_CONTEXT = 4096

#: The largest worth asking for on this machine. Doubled with everything else
#: on 2026-08-19: 48 GiB of RAM and 8 GiB of VRAM will hold a 64k cache at the
#: measured half-megabyte per thousand tokens, and the models here accept it.
MAXIMUM_CONTEXT = 65536

#: KV cache cost, in bytes per token of context. Measured as roughly half a
#: megabyte per thousand tokens for the 7B-class quantised models used here,
#: so 512 bytes each -- deliberately generous, because overestimating costs
#: some speed and underestimating costs a failed load.
BYTES_PER_TOKEN = 512

#: Tokens held back beyond the answer itself, for the system prompt and the
#: current message. Without it, "reserved" output space is reserved right up
#: until the prompt overruns it.
RESERVE_HEADROOM = 1024

#: What each kind of turn is worth, before the machine gets a say. These are
#: windows, not answer lengths -- see `answer_tokens`.
SHAPES: dict[str, int] = {
    "greeting": 4096,        # "hey", "thanks", "ok" -- history is the whole need
    "chat": 8192,            # ordinary back-and-forth
    "question": 16384,       # wants an explanation, and room to give one
    "task": 24576,           # tools, plans, results to hold on to
    "deep": 32768,           # explicitly asked to think, or a long thread
}

#: Answer ceilings per shape. Separate from the window because they fail
#: differently: too small a window drops context, too small a ceiling cuts the
#: reply mid-sentence.
ANSWERS: dict[str, int] = {
    "greeting": 512,
    "chat": 3072,
    "question": 6144,
    "task": 5120,
    "deep": 8192,
}

_GREETING = re.compile(
    r"(?i)^\s*(?:hi|hey|hello|yo|sup|thanks|thank you|ok|okay|k|cool|nice|"
    r"good (?:morning|afternoon|evening|night)|bye|see ya|np|got it|sure)\b[\s!.,]*$"
)
_DEEP = re.compile(
    r"(?i)\b(?:explain|why does|how does|compare|walk me through|in detail|"
    r"deep dive|thoroughly|step by step|design|architect|trade-?offs?|"
    r"analyse|analyze|reason about)\b"
)
_TASK = re.compile(
    r"(?i)\b(?:run|open|install|create|write|build|fix|search|find|download|"
    r"delete|move|rename|screenshot|capture|launch|start|stop|deploy|commit)\b"
)
_QUESTION = re.compile(r"(?i)\b(?:what|how|why|when|where|which|who|can you|could you|is it|are there)\b|\?")

#: Questions that are conversation rather than enquiry. "How are you doing
#: today" begins with "how" and wants none of what a technical question wants
#: -- it was getting an eight-thousand-token window and six recalled memories
#: to answer "good, you?", which is how a pleasantry ends up answered with
#: last week's screen contents.
_SOCIAL = re.compile(
    r"(?i)^\s*(?:so\s+|and\s+|ok\s+|okay\s+)?"
    r"(?:how(?:'s| is| are| have)?\s+(?:you|u|it|things|everything|life|your day)"
    r"|what(?:'s| is)\s+up"
    r"|you\s+(?:good|ok|okay|alright|there)"
    r"|good\s+(?:morning|afternoon|evening|night))\b"
)


@dataclass
class Budget:
    """One decision, with the reasoning attached."""

    context_tokens: int
    answer_tokens: int
    #: What the prompt may use once the reply's space is set aside.
    prompt_tokens: int
    history_turns: int
    memory_items: int
    shape: str
    reasons: list[str] = field(default_factory=list)
    constrained_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_tokens": self.context_tokens, "answer_tokens": self.answer_tokens,
            "prompt_tokens": self.prompt_tokens,
            "history_turns": self.history_turns, "memory_items": self.memory_items,
            "shape": self.shape, "constrained_by": self.constrained_by,
            "reasons": list(self.reasons),
        }

    def describe(self) -> str:
        limit = f", limited by {self.constrained_by}" if self.constrained_by else ""
        return (f"{self.shape}: {self.context_tokens} token window, "
                f"{self.answer_tokens} to answer in{limit}")


class ContextBudget:
    """Decides the window, the answer ceiling, and how much to recall.

    Takes the runtime rather than a pile of numbers so it can look at the
    machine as it is now: a decision made while a 21GB model holds the GPU is
    not the same decision made while it is idle.
    """

    def __init__(self, runtime: Any = None) -> None:
        self.runtime = runtime
        self._last: Budget | None = None

    # ----------------------------------------------------------- what it is

    @staticmethod
    def classify(text: str, history: list[dict[str, Any]] | None = None,
                 modes: set[str] | None = None) -> str:
        """What kind of turn this is, from the words and the thread so far."""
        modes = modes or set()
        value = " ".join(str(text or "").split())
        if not value:
            return "chat"
        if _GREETING.match(value) or _SOCIAL.match(value):
            return "greeting"
        if modes & {"DEEP", "ADVERSARIAL", "TEACH", "MENTOR"}:
            return "deep"
        if _DEEP.search(value) or len(value) > 400:
            return "deep"
        if _TASK.search(value):
            return "task"
        if _QUESTION.search(value):
            return "question"
        # A long thread is itself evidence that context matters: eleven
        # exchanges in, a short remark is rarely a fresh start.
        if history and len(history) >= 12:
            return "question"
        return "chat"

    # ------------------------------------------------------ what it can have

    def _machine_ceiling(self) -> tuple[int | None, str]:
        """The largest window this machine can afford right now.

        Context is KV cache, and KV cache is memory that has to come from
        somewhere. Roughly 0.5 MB per thousand tokens for a 7B-class model at
        the quantisations used here -- deliberately generous, because being
        wrong in this direction costs speed and being wrong the other way
        costs a failed load.
        """
        hardware = getattr(self.runtime, "hardware", None)
        if hardware is None:
            try:
                from app.hardware import profile

                hardware = profile()
            except Exception:
                return None, ""
        try:
            free_vram = float(getattr(getattr(hardware, "gpu", None), "free_bytes", 0) or 0)
            free_ram = float(getattr(hardware, "available_ram_bytes", 0) or 0)
        except Exception:
            return None, ""

        # Never spend more than a quarter of free VRAM, or a twentieth of free
        # RAM, on cache alone.
        spendable = max(free_vram, 0) * 0.25 + max(free_ram, 0) * 0.05
        if spendable <= 0:
            return None, ""
        tokens = int(spendable / BYTES_PER_TOKEN)
        if free_vram and free_vram < 1.5 * 1024**3:
            # Little free VRAM used to mean an 8192 hard cap, which is what cut
            # Mohsin's answer off mid-word: with the model resident there is
            # ~1.3 GiB of VRAM left, and a long follow-up carrying the previous
            # reply in its history filled an 8192 window before the new answer
            # could finish. This machine has 48 GiB of RAM, so the honest ceiling
            # is not "what fits in spare VRAM" but "what fits once the overflow
            # spills to RAM" -- slower per token, but the whole answer arrives.
            # 16384 doubles the room for prompt-plus-reply and is the value
            # local.py allocates, so the two never disagree.
            return max(MINIMUM_CONTEXT, min(tokens, 16384)), "little free VRAM, cache spills to RAM"
        # Capped, because a ceiling of six million tokens is not a ceiling --
        # it is arithmetic nobody checked, and it would read as one in any
        # status display that showed it. No local model here accepts more
        # than this anyway, so the honest answer is the largest real one.
        return max(MINIMUM_CONTEXT, min(tokens, MAXIMUM_CONTEXT)), "available memory"

    @staticmethod
    def _model_ceiling(model: Any) -> int | None:
        from app.models.base import model_context_limit
        return model_context_limit(model)

    # -------------------------------------------------------------- deciding

    def decide(self, text: str, *, history: list[dict[str, Any]] | None = None,
               modes: set[str] | None = None, model: Any = None) -> Budget:
        """Choose a budget for this turn and say why."""
        shape = self.classify(text, history, modes)
        wanted = SHAPES[shape]
        reasons = [f"the turn looks like {shape}, which is worth {wanted} tokens"]
        constrained_by = ""

        machine, machine_reason = self._machine_ceiling()
        if machine is not None and machine < wanted:
            reasons.append(f"trimmed to {machine} by {machine_reason}")
            wanted, constrained_by = machine, machine_reason

        ceiling = self._model_ceiling(model if model is not None
                                      else getattr(self.runtime, "model", None))
        if ceiling is not None and ceiling < wanted:
            reasons.append(f"the model accepts at most {ceiling}")
            wanted, constrained_by = ceiling, "the model's own window"

        # A configured model window is a hard ceiling, even below our preferred floor.
        wanted = max(1, wanted) if ceiling is not None else max(MINIMUM_CONTEXT, wanted)
        answer = ANSWERS[shape]
        # The reply has to fit inside the window with room for the prompt. A
        # ceiling larger than the window is how a request asks for an answer
        # that cannot be delivered.
        headroom = min(RESERVE_HEADROOM, wanted // 4)
        minimum_prompt = min(MINIMUM_CONTEXT // 2, max(1, wanted // 2))
        answer = max(0, min(answer, int(wanted * 0.6), wanted - minimum_prompt - headroom))
        # Output space is reserved, not borrowed. Prompt and reply share one
        # window, so history and recalled memory are allowed only what is left
        # after the answer has been set aside -- otherwise a long conversation
        # fills the window and the reply is squeezed into whatever remains,
        # which is truncation caused by the prompt rather than by the model.
        reserved = answer + headroom
        prompt_room = wanted - reserved

        budget = Budget(
            context_tokens=wanted, answer_tokens=answer, prompt_tokens=prompt_room,
            history_turns=self._history_for(shape), memory_items=self._memories_for(shape),
            shape=shape, reasons=reasons, constrained_by=constrained_by,
        )
        reasons.append(f"{answer} reserved for the reply, {prompt_room} left for the prompt")
        self._last = budget
        return budget

    @staticmethod
    def _history_for(shape: str) -> int:
        return {"greeting": 4, "chat": 8, "question": 14, "task": 12, "deep": 20}[shape]

    @staticmethod
    def _memories_for(shape: str) -> int:
        # A greeting retrieving eight memories is how an ordinary hello ends up
        # answered with last week's screen contents.
        return {"greeting": 0, "chat": 3, "question": 6, "task": 5, "deep": 8}[shape]

    # ------------------------------------------------------------- reporting

    @property
    def last(self) -> Budget | None:
        return self._last

    def status(self) -> dict[str, Any]:
        machine, reason = self._machine_ceiling()
        return {
            "machine_ceiling": machine, "machine_reason": reason,
            "shapes": dict(SHAPES), "answers": dict(ANSWERS),
            "last": self._last.to_dict() if self._last else None,
        }


def estimate_tokens(text: str) -> int:
    """A rough token count, good enough to compare against a budget."""
    return max(0, len(str(text or "")) // CHARS_PER_TOKEN)


def message_tokens(item) -> int:
    content = item.get('content', '') if isinstance(item, dict) else item.content
    value = str(content)
    return 8 + max(estimate_tokens(value), sum(not char.isascii() for char in value))


def reference_excerpt(content: str, max_chars: int = 2400) -> str:
    """Extractively retain both ends of large reports, with explicit omissions.

    This is prior evidence, not a model-created summary or a new verification.
    Drop repeated lines first; never mutate the durable conversation record.
    """
    value = str(content or '')
    if len(value) <= max_chars:
        return value
    if value.startswith('World update checked'):
        from app.research.world_brief import SECTORS
        titles = [sector[0] for sector in SECTORS if sector[0] in value]
        if titles:
            allowance = max(100, (max_chars - 180) // len(titles))
            packets = [value.splitlines()[0], '[Earlier report excerpted by sector; full evidence remains in conversation history.]']
            for title in titles:
                body = value.split(title, 1)[1]
                stops = [body.find(other) for other in titles if other != title and body.find(other) >= 0]
                if stops:
                    body = body[:min(stops)]
                observations = [line.strip() for line in body.splitlines() if line.strip().startswith('- ')]
                urls = list(dict.fromkeys(re.findall(r'https?://[^\s)\]]+', body)))
                source = '\nSource: ' + urls[0] if urls else ''
                content_room = max(0, allowance - len(title) - len(source) - 5)
                selected = '\n'.join(observations[:3]) or body.strip()
                packets.append(title + '\n' + selected[:content_room] + source)
            return '\n\n'.join(packets)[:max_chars]
    seen, lines = set(), []
    for line in value.splitlines():
        clean = line.strip()
        if clean and clean not in seen:
            lines.append(clean)
            seen.add(clean)
    reduced = '\n'.join(lines)
    if len(reduced) <= max_chars:
        return reduced
    marker = '\n[Earlier report excerpted to fit; omitted material remains in conversation history.]\n'
    room = max(0, max_chars - len(marker))
    head = room // 2
    return reduced[:head] + marker + reduced[-(room - head):]


def required_message_indices(messages) -> set[int]:
    def role(item):
        return item.get('role') if isinstance(item, dict) else item.role

    # Optionality is internal typed metadata, never a flag supplied by a web
    # page or arbitrary message dictionary.
    def optional(item):
        from app.models.base import Message
        return isinstance(item, Message) and item.optional_context

    required = {i for i, item in enumerate(messages) if role(item) == 'system' and not optional(item)}
    if messages and not optional(messages[-1]):
        required.add(len(messages) - 1)
    latest_user = next((i for i in range(len(messages) - 1, -1, -1)
                        if role(messages[i]) == 'user' and not optional(messages[i])), None)
    if latest_user is not None:
        required.add(latest_user)
    return required


def fit_model_messages(messages, prompt_tokens: int):
    """Conservative prompt estimate; retain instructions and the newest request.

    Do not silently cut a user's request or safety/identity instructions. Drop
    older optional context, retaining complete recent exchanges, or fail
    explicitly when essential content cannot fit.
    This is a budgeting estimate, not a provider-specific tokenizer.
    """
    from app.models.base import ModelError
    from app.failures import FailureClass
    if not messages:
        return []
    required = required_message_indices(messages)
    spent = sum(message_tokens(messages[index]) for index in required)
    if spent > prompt_tokens:
        raise ModelError('Essential instructions and the current request exceed the configured model context window. Reduce the request or use a larger configured window.',
                         failure_class=FailureClass.RESOURCE_EXHAUSTED, dependency='model context')
    from app.models.base import Message

    # Keep exchanges together, including tool results. A reply whose question
    # was removed can change the meaning of a follow-up. Historical exchanges
    # form a contiguous suffix; do not skip a large recent turn for older ones.
    exchanges, context = [], []
    for index, item in enumerate(messages):
        role = item.get('role') if isinstance(item, dict) else item.role
        if role == 'system' or (isinstance(item, Message) and item.optional_context):
            context.append(index)
        elif role == 'user':
            exchanges.append([index])
        elif exchanges:
            exchanges[-1].append(index)
        # Leading assistant/tool messages have no retained question. Only keep
        # them if they are essential, as determined above.
    kept = set(required)
    for exchange in reversed(exchanges):
        remaining = [index for index in exchange if index not in kept]
        cost = sum(message_tokens(messages[index]) for index in remaining)
        if spent + cost > prompt_tokens:
            break
        kept.update(remaining)
        spent += cost
    # Explicitly optional state/recaps are independent of conversation turns.
    for index in reversed(context):
        cost = message_tokens(messages[index])
        if index not in kept and spent + cost <= prompt_tokens:
            kept.add(index)
            spent += cost
    return [item for index, item in enumerate(messages) if index in kept]


def compress_history(history: list[dict[str, Any]], budget_tokens: int,
                     keep_recent: int = 6) -> list[dict[str, Any]]:
    """Fit a conversation into its allowance without losing the older half.

    Dropping old turns is the cheap option and it is why an assistant forgets
    what you told it twenty minutes ago. The recent exchanges are kept whole,
    because that is the thread being spoken in; everything before them is
    folded into one short recap, which costs a few dozen tokens instead of a
    few thousand and keeps the shape of what was discussed.
    """
    if not history:
        return []
    total = sum(estimate_tokens(str(item.get("content", ""))) for item in history)
    if total <= budget_tokens:
        return list(history)

    recent = history[-keep_recent:] if keep_recent else []
    older = history[:-keep_recent] if keep_recent else list(history)
    if not older:
        return recent

    topics: list[str] = []
    for item in older:
        content = " ".join(str(item.get("content", "")).split())
        if not content:
            continue
        who = "you" if item.get("role") == "user" else "I"
        topics.append(f"{who}: {content[:110]}")

    # Both ends, not just the tail. How a conversation opened is often where
    # Mohsin said what he actually wanted, and keeping only the most recent of
    # the old turns quietly discards exactly that. An earlier version kept the
    # last fourteen while the label claimed "oldest first", which was worse
    # than either choice: it dropped the beginning and said it had not.
    def recap_for(head: int, tail: int) -> dict[str, Any]:
        chosen = topics if len(topics) <= head + tail else topics[:head] + ["…"] + topics[-tail:]
        return {
            "role": "system",
            "content": ("Earlier in this conversation, condensed to fit -- the opening, "
                        "then the most recent of what came before: " + " | ".join(chosen)),
        }

    # If it overruns, fewer exchanges are kept -- never fewer characters.
    # Slicing the string chops the tail off, which throws away the most recent
    # of the old turns while claiming to have kept them.
    head, tail = 5, 9
    summary = recap_for(head, tail)
    while (head + tail) > 2 and sum(
            estimate_tokens(str(item.get("content", "")))
            for item in [summary, *recent]) > budget_tokens:
        head, tail = max(1, head - 1), max(1, tail - 1)
        summary = recap_for(head, tail)
    return [summary, *recent]


def fit_to_budget(items: list[str], budget_tokens: int) -> list[str]:
    """Keep as many items as fit, newest first.

    Newest first because when context has to be dropped, the exchange from ten
    minutes ago is the one that can go -- dropping the most recent turn to
    keep an older one is how an assistant loses the thread it is in.
    """
    kept: list[str] = []
    spent = 0
    for item in reversed(items):
        cost = estimate_tokens(item)
        if spent + cost > budget_tokens and kept:
            break
        kept.append(item)
        spent += cost
    return list(reversed(kept))
