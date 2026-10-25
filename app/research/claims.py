from __future__ import annotations

import re
from datetime import UTC, datetime

from app.research.freshness import classify_freshness, validity_horizon
from app.research.models import Claim, ClaimStatus, ClaimType, Evidence, SourceDocument, SupportType
from app.research.text import extract_entities, overlap, stable_id, tokens


NAVIGATION = re.compile(r"(?i)^(?:home|menu|search|skip to|cookie|sign in|log in|previous|next|contents|copyright)\b|^categories\s*:")
_PAGE_FURNITURE = re.compile(
    r"(?i)^(?:you are viewing this page in an unauthorized frame|"
    r"this is a potential security issue,? you are being redirected|"
    r"send inquiries about terminology|comments? about (?:the|this) (?:glossary|page)|"
    r"(?:end )?google tag manager|your browser (?:does not support|has javascript disabled))\b"
)


def is_page_furniture(sentence: str) -> bool:
    """Publisher navigation/security notices do not answer the research question."""
    return bool(_PAGE_FURNITURE.search(str(sentence).strip()))
_REFERS_BACK = re.compile(r"(?i)^(?:(?:one|some|each|all) of (?:these|those)\b|(?:it|they|this|that|these|those)\b)")

#: A question is not a claim. It cannot be true or false, so it cannot be
#: verified, and presenting one as a validated claim is a plain falsehood
#: about what was established. Asked about taxation, HAYAT returned "Is the
#: willful failure to file a return, by itself, enough to constitute the
#: felony of tax evasion?" as one of nine validated claims.
_QUESTION = re.compile(r"\?\s*$|^(?:is|are|was|were|do|does|did|can|could|should|would|will|"
                       r"what|why|how|when|where|which|who|whom|whose)\b[^.]*\?", re.IGNORECASE)

#: Words title case leaves in lowercase. Without them a heading like "What Is
#: Tax Evasion and Its Impact on U.S." reads as ordinary prose, because the
#: single lowercase "and" breaks any run of capitalised words.
_TITLE_LOWERCASE = frozenset({
    "a", "an", "the", "and", "or", "but", "nor", "for", "of", "on", "in", "to",
    "at", "by", "vs", "via", "with", "from", "into", "over", "as", "is",
})

#: Text that says outright it is not describing the real world. Stating it as
#: fact is how "(hypothetical example)" becomes evidence.
_HYPOTHETICAL = re.compile(r"(?i)\b(?:hypothetical|for illustration|purely as an example|"
                           r"suppose that|imagine that|fictional)\b")

#: Calls to action, which are instructions to the reader rather than
#: statements about anything.
_CALL_TO_ACTION = re.compile(r"(?i)^(?:learn|read|click|subscribe|download|contact|call|visit|"
                             r"sign up|get started|try|book|schedule|find out)\b")
_BIBLIOGRAPHY_ENTRY = re.compile(
    r"(?:^\[[A-Z][A-Z0-9._-]{1,30}\]\s+|\bRFC\s+\d+,\s+DOI\b.*<https?://)",
    re.IGNORECASE,
)

# Line-wrapped standards, PDFs, and terminal output often end a physical line
# halfway through a proposition. A fragment such as "MUST NOT be" was treated
# as a complete negative claim and falsely contradicted the following line.
_INCOMPLETE_END = re.compile(
    r"(?i)\b(?:a|an|the|and|or|but|to|of|in|on|for|with|by|from|as|"
    r"is|are|was|were|be|been|being|not|must|should|can|could|would|may)\s*[,;:]?$"
)

# The same wrapping cuts the *front* off a proposition, and that end is the
# dangerous one: a fragment can lose its subject and its negation together.
# One RFC boilerplate sentence -- "Documents approved for publication by the
# RFC Editor are not candidates for any level of Internet Standard" -- entered
# the store as four overlapping fragments, and the one that had shed the "not"
# was then recorded as contradicting the three that kept it. Four unresolved
# contradictions, all of a sentence with itself.
#
# A sentence opens with a capital, a digit, or a quote. Lowercase openings are
# allowed only for words that are genuinely spelled that way -- iPhone, gRPC,
# eBay -- or for code-like tokens.
_SENTENCE_START = re.compile(r"[A-Z0-9\"'(\[]")
_LOWERCASE_NAME = re.compile(r"^[a-z]+[A-Z]|^[a-z][\w.]*[_.(]")


def _starts_mid_sentence(value: str) -> bool:
    """Whether this text begins partway through a proposition."""
    head = value.lstrip()
    if not head:
        return True
    if _SENTENCE_START.match(head):
        return False
    return not _LOWERCASE_NAME.match(head)


def is_assertion(sentence: str) -> bool:
    """Whether this sentence asserts something that could be checked.

    Applied before anything is called a claim, because every later stage --
    verification, citation checking, confidence -- assumes it is working with
    an assertion. Feed it a question and the whole pipeline dutifully computes
    a confidence score for something that was never stated.
    """
    value = " ".join(str(sentence or "").split())
    if not value or is_page_furniture(value):
        return False
    if _QUESTION.search(value) or _CALL_TO_ACTION.match(value) or _BIBLIOGRAPHY_ENTRY.match(value):
        return False
    if _HYPOTHETICAL.search(value):
        return False
    if _INCOMPLETE_END.search(value) or _starts_mid_sentence(value):
        return False
    if _looks_like_a_heading(value):
        return False
    # A sentence with no lowercase letters at all is a banner or a heading.
    return any(character.islower() for character in value)


def is_complete_assertion(sentence: str) -> bool:
    """A checkable assertion that also ends as a complete sentence."""
    value = " ".join(str(sentence or "").split())
    return is_assertion(value) and bool(re.search(r"[.!?][\"')\]]*$", value))


def contextual_passages(text: str):
    """Keep adjacent referential sentences together before passage ranking."""
    previous = ""
    for raw in re.split(r"(?<=[.!?])\s+|\n+", text):
        sentence = raw.strip()
        if not sentence:
            continue
        passage = sentence
        if _REFERS_BACK.match(sentence) and previous and is_complete_assertion(previous) and len(previous + sentence) < 1600:
            passage = previous + " " + sentence
        yield passage
        previous = sentence


def _looks_like_a_heading(value: str) -> bool:
    """Whether this is a page title rather than a sentence.

    Title case is the tell: nearly every word capitalised except the small
    connecting ones. Checked by counting rather than by pattern, because one
    lowercase "and" in the middle defeats any regex that wants an unbroken
    run of capitals -- which is how "What Is Tax Evasion and Its Impact on
    U.S." survived as a validated claim.
    """
    words = re.findall(r"[A-Za-z][\w'-]*", value)
    if len(words) < 4:
        return False
    significant = [word for word in words if word.lower() not in _TITLE_LOWERCASE]
    if len(significant) < 3:
        return False
    capitalised = sum(word[0].isupper() for word in significant)
    if capitalised / len(significant) < 0.8:
        return False
    # A real sentence ends in punctuation and is not cut off mid-phrase.
    return not value.rstrip().endswith((".", "!", "?")) or bool(
        re.search(r"\b(?:U\.S|Inc|Ltd|etc)\.$", value.rstrip()))


class ClaimExtractor:
    """Turns retrieved evidence into bounded factual units; it does not invent claims."""

    def extract(self, query: str, source: SourceDocument, excerpt: str,
                max_claims: int = 3, *, strict_complete: bool = False) -> list[Claim]:
        candidates = [value.strip(" \t\r\n-•") for value in re.split(r"(?<=[.!?])\s+|\n+", excerpt)]
        ranked: list[tuple[float, str]] = []
        for sentence in candidates:
            if _REFERS_BACK.match(sentence):
                # Preserve the source's adjacent antecedent. Isolating "One
                # of these, Canberra, is also the national capital" loses the
                # country stated in the previous sentence. Never join distant
                # ranked excerpts as though they were adjacent in the source.
                position = source.text.find(sentence)
                if position > 0:
                    preceding = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", source.text[max(0, position - 1800):position]) if part.strip()]
                    if preceding and is_complete_assertion(preceding[-1]) and len(preceding[-1] + sentence) < 1200:
                        sentence = preceding[-1] + " " + sentence
            word_count = len(tokens(sentence, 2))
            if word_count < 6 or len(sentence) < 35 or NAVIGATION.search(sentence):
                continue
            if not is_assertion(sentence):
                continue
            if strict_complete and not is_complete_assertion(sentence):
                continue
            relevance = overlap(query, sentence)
            specificity = min(1.0, (len(re.findall(r"\b\d+(?:\.\d+)?%?\b", sentence)) + len(extract_entities(sentence))) / 4)
            normative = 1.0 if re.search(
                r"\b(?:MUST(?:\s+NOT)?|SHALL(?:\s+NOT)?|REQUIRED|RECOMMENDED|SHOULD(?:\s+NOT)?)\b",
                sentence,
            ) else 0.0
            from app.research.subject import direct_answer_score, relevant_to_question
            topical = 1.0 if relevant_to_question(query, sentence) else 0.0
            ranked.append((topical + direct_answer_score(query, sentence) * .35 + relevance * 0.72 + specificity * 0.14 + normative * 0.14, sentence[:1200]))
        claims: list[Claim] = []
        freshness, _ = classify_freshness(query)
        now = datetime.now(UTC).isoformat()
        for _, sentence in sorted(ranked, key=lambda item: item[0], reverse=True)[:max_claims]:
            claim_id = stable_id("claim", sentence.lower())
            evidence_id = stable_id("evidence", claim_id, source.content_hash, sentence)
            location = self._location(source, sentence)
            directness = overlap(sentence, excerpt)
            support = SupportType.DIRECT if directness >= 0.75 else SupportType.INFERRED if directness >= 0.45 else SupportType.WEAK
            evidence = Evidence(
                evidence_id, claim_id, source.final_url, source.title, sentence, source.quality, source.retrieved_at,
                source.published_at, location, support, round(min(1.0, directness * 0.65 + source.quality * 0.35), 3),
                "supporting", source.content_hash, source.independence_group,
            )
            claim_type = self._claim_type(sentence, query)
            years = re.findall(r"\b(?:19|20)\d{2}\b", sentence)
            valid_from = f"{years[-1]}-01-01T00:00:00+00:00" if claim_type == ClaimType.TEMPORAL and years else None
            claim = Claim(
                claim_id, sentence, claim_type, ClaimStatus.SUPPORTED if support == SupportType.DIRECT else ClaimStatus.PARTIALLY_SUPPORTED,
                [evidence], [], 0.0, freshness, validity_horizon(source.retrieved_at, freshness), now, now,
                valid_from, None, extract_entities(sentence), tokens(sentence)[:20], self._scope(sentence),
            )
            claims.append(claim)
        return claims

    @staticmethod
    def _location(source: SourceDocument, sentence: str) -> str:
        pages = source.metadata.get("pdf_pages", [])
        for page in pages:
            if sentence[:80] in str(page.get("text", "")):
                heading = page.get("heading")
                return f"page {page['page']}" + (f", {heading}" if heading else "")
        lower = source.text.lower()
        position = lower.find(sentence[:80].lower())
        if position >= 0:
            line = source.text[:position].count("\n") + 1
            prior = [heading for heading in source.headings if source.text.lower().find(heading.lower()) <= position]
            return f"line {line}" + (f", section {prior[-1]}" if prior else "")
        return "retrieved excerpt"

    @staticmethod
    def _claim_type(text: str, query: str) -> ClaimType:
        lower = f"{query} {text}".lower()
        if re.search(r"\b(?:19|20)\d{2}\b|\b(?:today|currently|latest|now)\b", lower):
            return ClaimType.TEMPORAL
        if any(word in lower for word in ("compared", "versus", "whereas", "than")):
            return ClaimType.COMPARISON
        if any(word in lower for word in ("causes", "results in", "leads to", "because")):
            return ClaimType.CAUSAL
        return ClaimType.FACT

    @staticmethod
    def _scope(text: str) -> str:
        years = re.findall(r"\b(?:19|20)\d{2}\b", text)
        versions = re.findall(r"(?i)\bv?\d+(?:\.\d+){1,3}\b", text)
        return " ".join([*years, *versions])
