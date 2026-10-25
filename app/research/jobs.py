"""Research that runs in the background and comes back when it is done.

A search used to run inside the turn that asked for it. Slow sources made
the turn look stuck, and a new message replaced the turn, so the answer was
thrown away when it finally arrived -- Mohsin saw HAYAT "start talking about
something else". Each search is now a job:

* it starts, and the turn waits a short while (``inline_seconds``); a quick
  answer is returned as usual;
* otherwise HAYAT says it is still searching and will come back, and the
  conversation carries on;
* when the job finishes -- or runs past ``timeout_seconds`` -- the result is
  delivered as its own message, in the window, the terminal and voice.

"Did you find anything?" is answered from these jobs, never from a guess.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


#: Search sources by their internal names, as the owner knows them.
_SOURCE_NAMES = {"duckduckgo_html": "DuckDuckGo", "duckduckgo": "DuckDuckGo", "bing_rss": "Bing", "bing": "Bing",
                 "google": "Google", "wikipedia": "Wikipedia", "searxng": "SearXNG", "brave": "Brave",
                 "mojeek": "Mojeek", "ahmia": "Ahmia (onion index)"}


def plain_errors(error: str) -> str:
    """Provider failures as one sentence: which sources failed and why, said once.

    "duckduckgo_html: ConnectError: [WinError 10061] No connection could be made
    because the target machine actively refused it; bing_rss: ConnectError: ..."
    reached the owner verbatim on 2026-10-25.
    """
    import re

    text = " ".join(str(error or "").split())
    if not text:
        return ""
    parts = re.split(r";\s+(?=[A-Za-z][\w .()-]{1,40}:\s)", text)
    reasons: dict[str, list[str]] = {}
    for part in parts:
        source, sep, detail = part.partition(": ")
        if not sep or " " in source.strip() and source.strip().lower() not in _SOURCE_NAMES:
            source, detail = "", part
        detail = re.sub(r"^(?:httpx\.)?[A-Z]\w*(?:Error|Exception|Timeout)\s*:\s*", "", detail)  # exception class
        detail = re.sub(r"^\[[a-z_]+;\s*route=\w+\]\s*", "", detail)  # diagnostic tag
        if re.search(r"WinError 10061|actively refused|connection_refused", detail):
            detail = "the connection was refused (the network, a firewall, or a Tor/proxy route that isn't running)"
        elif re.search(r"(?i)timed? ?out|ReadTimeout|ConnectTimeout", detail):
            detail = "it took too long to answer"
        elif re.search(r"(?i)getaddrinfo|Name or service not known|nodename nor servname|11001", detail):
            detail = "the address could not be looked up (no internet or DNS)"
        detail = detail.strip().rstrip(".")
        name = _SOURCE_NAMES.get(source.strip().lower(), source.strip().replace("_", " ")) if source else ""
        reasons.setdefault(detail, []).append(name)
    sentences = []
    for detail, names in reasons.items():
        named = [name for name in names if name]
        who = (" and ".join(named) if len(named) <= 2 else ", ".join(named[:-1]) + " and " + named[-1]) if named else ""
        lowered = detail[:1].lower() + detail[1:] if detail[1:2].islower() else detail
        sentences.append(f"{who} failed: {lowered}." if who else f"{detail[:1].upper() + detail[1:]}.")
    if len(reasons) == 1 and len(parts) > 1 and sentences[0].count(" and ") >= 1:
        sentences[0] = "Every search source tried (" + sentences[0].split(" failed:")[0] + ") failed:" + sentences[0].split(" failed:")[1]
    return " ".join(sentences)


def compose_answer(query: str, asked: str, result: Any) -> str:
    """The sourced answer, how far to trust it, and -- for a book -- where copies are catalogued."""
    from app.research.catalogs import books_section, find_books, looks_like_a_book
    from app.research.verdict import verdict

    parts = [str(getattr(result, "answer", "") or "").strip()]
    if getattr(result, 'trace', {}).get('person_scope') or getattr(result, 'trace', {}).get('public_data_scope'):
        return parts[0]  # its source-by-source identity limitations are already explicit
    try:
        parts.append(verdict(result))
    except Exception:
        log_swallowed(_log, "judging research claims")
    if getattr(result, 'trace', {}).get('onion_scope'):
        rounds = result.trace.get('search_rounds', [])
        coverage = [f"{item['provider']}: {item['status']} ({item['usable_results']} leads)"
                    for run in rounds for item in run.get('indexes', [])]
        if coverage:
            parts.append('Discovery coverage:\n' + '\n'.join(coverage))
        if getattr(result, 'errors', []):
            parts.append('Unavailable or blocked sources:\n' + '\n'.join(str(item)[:400] for item in result.errors[:8]))
        parts.append('Onion research requires Tor. Temporary evidence caches are removed, but local search history and transfer logs can retain request URLs.')
        return '\n\n'.join(part for part in parts if part)
    if getattr(result, 'trace', {}).get('tor_web_scope'):
        if getattr(result, 'errors', []):
            parts.append('Unavailable or blocked sources:\n' + '\n'.join(str(item)[:400] for item in result.errors[:8]))
        parts.append('Public internet sources through Tor; onion-only discovery is a separate mode. No direct fallback was used.')
        return '\n\n'.join(part for part in parts if part)
    if looks_like_a_book(query, asked):
        parts.append(books_section(find_books(query)))
    return "\n\n".join(part for part in parts if part)


@dataclass
class ResearchJob:
    job_id: str
    query: str
    asked: str
    level: str
    started_at: str
    status: str = "running"            # running | done | failed | cancelled
    answer: str = ""
    confidence: float = 0.0
    sources: int = 0
    error: str = ""
    finished_at: str = ""
    delivered: bool = False
    overdue_noted: bool = False
    result: Any = field(default=None, repr=False)
    person: dict[str, str] | None = None
    public_data: dict[str, str] | None = None
    onion: bool = False
    tor_web: bool = False
    _started: float = field(default_factory=time.monotonic, repr=False)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def to_dict(self) -> dict[str, Any]:
        data = {item.name: getattr(self, item.name) for item in fields(self)
                if item.name not in ('result', '_started', '_done')}
        if self.person is not None:
            data['person'] = dict(self.person)
        if self.public_data is not None:
            data['public_data'] = dict(self.public_data)
        data["elapsed_seconds"] = round(self.elapsed, 1)
        return data


class ResearchJobs:
    def __init__(self, research: Callable[[], Any], *, deliver: Callable[[ResearchJob, str], None] | None = None,
                 inline_seconds: float = 20.0, timeout_seconds: float = 180.0, keep: int = 30) -> None:
        self.research = research
        self.deliver = deliver or (lambda job, text: None)
        self.inline_seconds = inline_seconds
        self.timeout_seconds = timeout_seconds
        self.keep = keep
        self._jobs: list[ResearchJob] = []
        self._lock = threading.Lock()
        self._deliver_lock = threading.Lock()
        self._listeners: list[Callable[[ResearchJob], None]] = []

    # ------------------------------------------------------------ observers
    def add_listener(self, callback: Callable[[ResearchJob], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback) if callback in self._listeners else None

    def _changed(self, job: ResearchJob) -> None:
        for callback in list(self._listeners):
            try:
                callback(job)
            except Exception:
                log_swallowed(_log, "notifying a research-job observer")

    # ------------------------------------------------------------ jobs
    def start(self, query: str, *, asked: str = "", level: str = "normal", person: dict[str, str] | None = None,
              public_data: dict[str, str] | None = None, onion: bool = False, tor_web: bool = False) -> ResearchJob:
        if (type(onion) is not bool or type(tor_web) is not bool or (onion and tor_web)
                or ((onion or tor_web) and (person is not None or public_data is not None))):
            raise ValueError('Choose onion research separately from person or registry searches.')
        if public_data is not None:
            if person is not None or set(public_data) != {'kind'} or public_data['kind'] not in ('all', 'datasets', 'publications', 'literature'):
                raise ValueError('Choose one public metadata kind: all, datasets or publications.')
            public_data = dict(public_data)
        if person is not None:
            from app.research.people import PersonRequest
            request = PersonRequest(**person)
            person = {'name': request.name, 'context': request.context, 'profile_url': request.profile_url}
            query = request.query
        job = ResearchJob(f"job-{uuid.uuid4().hex[:8]}", query, asked or query, level, datetime.now(UTC).isoformat())
        job.person = person
        job.public_data = public_data
        job.onion = onion
        job.tor_web = tor_web
        with self._lock:
            self._jobs.append(job)
            del self._jobs[:-self.keep]
        threading.Thread(target=self._run, args=(job,), name=f"hayat-research-{job.job_id}", daemon=True).start()
        threading.Thread(target=self._watch, args=(job,), name=f"hayat-research-watch-{job.job_id}", daemon=True).start()
        self._changed(job)
        return job

    def _run(self, job: ResearchJob) -> None:
        try:
            manager = self.research()
            if job.tor_web:
                result = manager.research_tor_web(job.query, level=job.level)
            elif job.onion:
                result = manager.research_onion(job.query, level=job.level)
            elif job.person is not None:
                result = manager.research_person(**job.person, level=job.level)
            elif job.public_data is not None:
                result = manager.research_data(job.query, **job.public_data, level=job.level)
            else:
                result = manager.research(job.query, level=job.level, remember=True)
            if job.status == "cancelled":
                return
            job.result = result
            job.answer = compose_answer(job.query, job.asked, result)
            job.confidence = float(getattr(result, "confidence", 0.0) or 0.0)
            job.sources = len(getattr(result, "sources", []) or [])
            complete = getattr(result, 'complete', None)
            supported = complete is True if complete is not None else bool(job.answer)
            job.status = 'done' if job.answer and supported else 'failed'
            if job.status == 'failed':
                job.error = '; '.join(str(item) for item in getattr(result, 'errors', [])[:2]) or 'The retrieved evidence did not establish a supported answer.'
        except Exception as exc:
            log_swallowed(_log, "running a background research job", level=logging.WARNING)
            if job.status != "cancelled":
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = datetime.now(UTC).isoformat()
            job._done.set()
            self._changed(job)
            if job.status != "cancelled" and not job.delivered and self._handed_off(job):
                self._deliver(job)

    def _handed_off(self, job: ResearchJob) -> bool:
        """The turn stopped waiting (or was replaced), so the result needs its own message."""
        return job.elapsed >= self.inline_seconds or getattr(job, "_released", False)

    def _watch(self, job: ResearchJob) -> None:
        if job._done.wait(self.timeout_seconds):
            return
        if job.status == "running" and not job.overdue_noted:
            job.overdue_noted = True
            self.deliver(job, f"I'm still searching for \"{job.query}\" -- it's been over "
                              f"{int(self.timeout_seconds // 60)} minutes because the sources are slow. I'll keep at "
                              "it and tell you as soon as I have something. Say \"cancel the search\" to stop it.")

    def wait_inline(self, job: ResearchJob, *, still_wanted: Callable[[], bool] = lambda: True) -> bool:
        """Wait briefly for a quick answer. True when it is ready to be returned in the turn."""
        deadline = time.monotonic() + self.inline_seconds
        while time.monotonic() < deadline:
            if job._done.wait(0.1):
                if job.status == "done" and still_wanted():
                    job.delivered = True
                    return True
                break
            if not still_wanted():
                break
        self.release(job)
        return False

    def release(self, job: ResearchJob) -> None:
        """Hand a job to background delivery, including one that already finished."""
        job._released = True
        if job._done.is_set() and job.status != "cancelled":
            self._deliver(job)

    def redeliver(self, job_id: str) -> None:
        """A turn returned the answer but was replaced before it was shown."""
        job = self.get(job_id)
        if job is not None and job._done.is_set():
            job.delivered = False
            self._deliver(job)

    def _deliver(self, job: ResearchJob) -> None:
        # The job finishing and the turn giving up can race; one message only.
        with self._deliver_lock:
            if job.delivered:
                return
            job.delivered = True
        self.deliver(job, self.result_text(job))
        self._changed(job)

    def result_text(self, job: ResearchJob) -> str:
        if job.status == "done":
            return f"I finished looking up \"{job.query}\":\n\n{job.answer}"
        if not job.sources:
            from app.research.network_diagnostics import failed_search_guidance
            guidance = failed_search_guidance(job.error, tor_required=job.onion or job.tor_web)
            if guidance:
                return (f'I could not retrieve search evidence for "{job.query}".\n\n'
                        + guidance + '\n\nObserved issues: ' + job.error)
        if job.onion:
            return (f'No supported onion answer was retrieved for "{job.query}".\n\n'
                    + (job.answer or 'The configured providers returned no usable evidence.')
                    + '\n\nTry a specific service/organization or a known public v3 URL in Onion pages. '
                    'For a general topic such as AI, choose Tor web (public internet) in Searches; '
                    'it researches ordinary websites through Tor. Browse search opens the user-operated index. '
                    'Provider failure is not proof that no matching service exists.\nObserved issues: ' + job.error)
        if job.tor_web:
            return (f'No supported public-web answer through Tor was retrieved for "{job.query}".\n'
                    + (job.answer or job.error) + '\nNo direct-network fallback was used. Try a more specific question or Browse search.')
        if job.sources and job.result is not None:
            pages = ['- ' + str(source.final_url) + ' (retrieved ' + str(getattr(source, 'retrieved_at', 'unknown')) + ')'
                     for source in getattr(job.result, 'sources', [])[:8] if getattr(source, 'final_url', None)]
            return (f'I read {job.sources} source page(s) for "{job.query}", but could not establish a supported answer.\n\n'
                    + job.answer + ('\n\nPages actually read:\n' + '\n'.join(pages) if pages else '')
                    + '\n\nRetrieval is not a verified finding. Narrow the question or use search again.\nObserved issues: ' + job.error)
        return (f"I couldn't find a reliable answer for \"{job.query}\". {plain_errors(job.error)} "
                "Tell me more about what you're after, or say \"search again\" and I'll try different sources.")

    def cancel(self, job_id: str | None = None) -> ResearchJob | None:
        job = self.get(job_id) if job_id else next((item for item in reversed(self._jobs) if item.status == "running"), None)
        if job is None or job.status != "running":
            return None
        job.status = "cancelled"
        job.finished_at = datetime.now(UTC).isoformat()
        job._done.set()
        self._changed(job)
        return job

    def get(self, job_id: str) -> ResearchJob | None:
        return next((job for job in self._jobs if job.job_id == job_id), None)

    def running(self) -> list[ResearchJob]:
        return [job for job in self._jobs if job.status == "running"]

    def recent(self, limit: int = 10) -> list[ResearchJob]:
        return list(reversed(self._jobs[-limit:]))

    def status_text(self) -> str:
        running = self.running()
        if running:
            lines = [f"Still searching: \"{job.query}\" ({int(job.elapsed)} s so far)." for job in running]
            return " ".join(lines) + " I'll tell you as soon as it's done."
        finished = [job for job in reversed(self._jobs) if job.status in {"done", "failed"}]
        if not finished:
            return ("I haven't searched for anything yet in this session. Tell me what to look up -- for example "
                    "\"search for Shams al-Ma'arif\" -- and I'll start right away.")
        latest = finished[0]
        return ("Here's what my last search found:\n\n" if latest.status == "done" else "") + self.result_text(latest)
