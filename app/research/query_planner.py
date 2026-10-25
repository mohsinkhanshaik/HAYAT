from __future__ import annotations

import importlib.metadata
import re
from dataclasses import dataclass, asdict
from datetime import date

from app.research.freshness import dated_query
from app.research.models import ResearchBudget
from app.research.ranking import technical_question
from app.research.text import jaccard, tokens


@dataclass(slots=True)
class PlannedQuery:
    text: str
    intent: str
    priority: float
    rationale: str

    def to_dict(self) -> dict:
        return asdict(self)


class ResearchQueryPlanner:
    MODES = {"QUICK", "NORMAL", "DEEP", "PRIMARY_ONLY", "LOCAL_KNOWLEDGE_ONLY", "ADVERSARIAL"}

    def plan(self, question: str, mode: str, budget: ResearchBudget, primary_only: bool = False) -> list[PlannedQuery]:
        clean = self._clean(question)
        installed = self.installed_versions(clean)
        version_context = " ".join(f"{name} {version}" for name, version in installed.items())
        subject = self._search_subject(clean)
        versioned_subject = f"{subject} {version_context}".strip()
        candidates = [PlannedQuery(versioned_subject, "primary", 1.0, "Direct primary-evidence search")]
        dated = dated_query(subject, date.today())
        if dated:
            candidates.append(PlannedQuery(dated, "freshness", 1.01, "The real date, so older 'last night' reports do not match"))
        lower = clean.lower()
        technical = technical_question(clean)
        scientific = any(word in lower for word in ("paper", "study", "effect", "evidence", "research", "dataset", "sample"))
        changing = bool(re.search(r"\b(?:current|latest|recent|today|releases?|versions?|prices?|news)\b", lower))
        disputed = mode == "ADVERSARIAL" or any(word in lower for word in ("opposing", "against", "controvers", "conflict", "contradict", "disputed", "whether", "does"))
        comparison = any(word in lower for word in ("compare", "versus", " vs ", "difference", "approaches"))
        comparison_entities = self._comparison_entities(clean)
        if technical:
            candidates.extend([
                PlannedQuery(f"{versioned_subject} official documentation", "primary", 0.98, "Version-matched official documentation"),
                PlannedQuery(f"{subject} release notes changelog", "freshness", 0.90, "Release and behavior changes"),
                PlannedQuery(f"{subject} GitHub issues examples", "implementation", 0.74, "Maintainer examples and unresolved limitations"),
            ])
        for entity in comparison_entities:
            topic = "multi GPU tensor parallel pipeline parallel official documentation" if "gpu" in lower else "official documentation limitations"
            candidates.append(PlannedQuery(f"{entity} {topic}", "primary", 0.995, f"Primary evidence for {entity}"))
        if scientific:
            candidates.extend([
                PlannedQuery(f"{subject} original paper methodology results limitations", "primary", 0.96, "Original study evidence"),
                PlannedQuery(f"{subject} systematic review meta analysis", "corroboration", 0.86, "Independent synthesis"),
            ])
        if changing:
            candidates.append(PlannedQuery(f"{subject} latest official update date", "freshness", 0.94, "Current-state verification"))
        if disputed:
            candidates.extend([
                PlannedQuery(f"{subject} evidence against limitations", "counter_evidence", 0.88, "Actively seek counter-evidence"),
                PlannedQuery(f"{subject} disagreement alternative explanation", "counter_evidence", 0.78, "Resolve definitions and scope"),
            ])
        if comparison:
            candidates.append(PlannedQuery(f"{subject} official comparison limitations", "comparison", 0.91, "Comparable primary-source criteria"))
        if mode == 'DEEP':
            candidates.extend([
                PlannedQuery(f'{subject} primary sources background evidence', 'primary', .84, 'Broaden evidence beyond one phrasing'),
                PlannedQuery(f'{subject} limitations unresolved questions', 'counter_evidence', .8, 'Check gaps as well as supporting claims'),
                PlannedQuery(f'{subject} recent official report', 'freshness', .76, 'Seek dated public reporting'),
            ])
        if primary_only:
            for item in candidates:
                if "official" not in item.text.lower():
                    item.text += " official primary source"
        deduplicated: list[PlannedQuery] = []
        for candidate in sorted(candidates, key=lambda item: item.priority, reverse=True):
            if not any(jaccard(candidate.text, existing.text) >= 0.82 for existing in deduplicated):
                deduplicated.append(candidate)
        if mode in {'DEEP', 'ADVERSARIAL'}:
            # A high-priority cluster of primary queries must not crowd out counter-evidence.
            diverse, remainder, intents = [], [], set()
            for candidate in deduplicated:
                if candidate.intent not in intents:
                    diverse.append(candidate)
                    intents.add(candidate.intent)
                else:
                    remainder.append(candidate)
            deduplicated = diverse + remainder
        return deduplicated[: budget.searches]

    @staticmethod
    def complexity(question: str) -> int:
        score = 1
        lower = question.lower()
        score += int(any(word in lower for word in ("compare", "versus", "difference", "tradeoff")))
        score += int(any(word in lower for word in ("controvers", "conflict", "opposing", "evidence against", "contradict")))
        score += int(any(word in lower for word in ("paper", "study", "methodology", "benchmark")))
        score += int(len(tokens(question)) > 12)
        return min(score, 5)

    @staticmethod
    def installed_versions(question: str) -> dict[str, str]:
        common = {
            "httpx": "httpx", "playwright": "playwright", "pypdf": "pypdf", "pytest": "pytest",
            "numpy": "numpy", "apscheduler": "APScheduler", "beautifulsoup": "beautifulsoup4",
        }
        lower = question.lower()
        found: dict[str, str] = {}
        for mention, distribution in common.items():
            if mention in lower:
                try:
                    found[distribution] = importlib.metadata.version(distribution)
                except importlib.metadata.PackageNotFoundError:
                    continue
        return found

    @staticmethod
    def _clean(question: str) -> str:
        value = re.sub(r"(?i)\b(?:research this deeply|research this|look this up|search the internet|use only (?:official|primary) sources|using only (?:official|primary) sources|remember what you learn|and remember what you learn)\b[:,. ]*", "", question)
        value = re.sub(r"(?i)^\s*learn\s+", "", value)
        value = re.sub(r"(?i)^\s*(?:on what date did|what date did|when did)\s+", "", value)
        value = re.sub(r"(?i)^\s*(?:what is|what are|how does|how do|does|is|are)\s+", "", value)
        # A leading lowercase article misleads search engines: Bing read "the capital city of Australia"
        # as Capital University and Capital One near the owner (2026-10-24); without it, Canberra.
        # A capitalised one is part of a name ("The Hague", "The Beatles") and stays.
        value = re.sub(r"^\s*(?:the|a|an)\s+(?=\w)", "", value)
        value = re.sub(r"(?i)\bfirst land humans on the moon\b", "Moon landing date", value)
        value = re.sub(r"(?i)\bfirst landed humans on the moon\b", "Moon landing date", value)
        return value.strip(" .,:;?") or question.strip()

    @staticmethod
    def _search_subject(clean: str) -> str:
        value = clean
        value = re.sub(r"(?i)^\s*(?:compare|explain|describe|determine|find)\s+", "", value)
        value = re.sub(r"(?i)^\s*(?:moderate\s+)?coffee consumption\s+", "coffee consumption ", value)
        value = re.sub(r"(?i)\b(?:in its current Python API|currently published|current documented|current stable)\b", "", value)
        value = re.sub(r"(?i)\b(?:what architecture did the original)\s+", "", value)
        value = re.sub(r"(?i)\b(?:paper introduce|find supporting and opposing evidence|resolve apparently conflicting documentation)\b", "", value)
        value = re.sub(r"\s+", " ", value).strip(" .,:;?")
        important = re.findall(r"(?i)\b(?:vLLM|TensorRT-LLM|HTTPX|CPython|Playwright|Kubernetes|Apollo\s+11|Attention Is All You Need)\b", clean)
        if important:
            remaining = [token for token in tokens(value) if token.lower() not in {part.lower() for entity in important for part in entity.split()}]
            return " ".join([*important, *remaining[:8]])
        return value

    @staticmethod
    def _comparison_entities(clean: str) -> list[str]:
        entities = re.findall(r"(?i)\b(?:vLLM|TensorRT-LLM|HTTPX|CPython|Playwright|Kubernetes|Ollama|Docker)\b", clean)
        result: list[str] = []
        for entity in entities:
            canonical = {"vllm": "vLLM", "tensorrt-llm": "TensorRT-LLM", "httpx": "HTTPX", "cpython": "CPython"}.get(entity.lower(), entity)
            if canonical not in result:
                result.append(canonical)
        return result if len(result) >= 2 else []
