"""Downloading a local model for real, in the background, after Mohsin's yes.

Before this there was no model download at all, so a conversation could only
pretend: asked for Dolphin-Mixtral, HAYAT replied "Starting the pull now 🚀 ...
1-3 minutes" to each "YES", forever, and nothing ran. A 26 GB download is not
1-3 minutes either.

Now the request becomes a plan with the real size from the registry, the yes
starts an ``/api/pull`` against HAYAT's own Ollama in a background thread, and
the finished (or failed) download reports back on its own, like a search does.
Ollama keeps partial downloads, so a dropped connection is resumed
automatically a few times, and "download it again" resumes it later.

A downloaded model is an advisor. HAYAT still writes every answer
(app/models/council.py); nothing here changes which model is the main one.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.logging import log_swallowed
from app.identity import IDENTITY, pronouns as _pronouns

_P = _pronouns(IDENTITY.owner_pronoun)

_log = logging.getLogger(__name__)

REGISTRY = "https://registry.ollama.ai/v2"
HF_REGISTRY = "https://hf.co/v2"
MANIFEST_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"
AUTOMATIC_RESUMES = 3
MAX_REGISTRY_PROBES = 8
# Correct the incomplete name used in the old chat. The target is still
# checked against its live manifest before a plan is shown or approved.
MODEL_ALIASES = {
    "dolphin-mixtral-8x7b-v2-q4_k_m": "dolphin-mixtral:8x7b-v2.7-q4_K_M",
}
#: Families that make a bare name ("dolphin mixtral") a model rather than a program.
FAMILIES = (
    "llama", "mistral", "mixtral", "dolphin", "qwen", "qwq", "gemma", "phi", "deepseek", "codellama", "llava",
    "hermes", "wizard", "vicuna", "command-r", "starcoder", "granite", "olmo", "smollm", "tinyllama", "nomic",
    "minilm", "bge", "falcon", "orca", "solar", "yi", "openchat", "zephyr", "nous", "aya", "internlm", "glm",
    "moondream", "bakllava", "magistral", "devstral", "ministral", "nemotron", "exaone", "cogito", "hayat",
    "qwythos",
)
_FAMILY = re.compile(r"(?i)(?<![a-z])(?:" + "|".join(re.escape(name) for name in FAMILIES) + r")")
_NAME = re.compile(r"(?i)(?:hf\.co/[\w.-]+/[\w.-]+(?::[\w.-]+)?|[a-z][\w.-]*(?::[\w.-]+)?)")


def looks_like_model(name: str) -> bool:
    value = str(name or "").strip()
    return bool(value) and ("/" in value or ":" in value or bool(_FAMILY.search(value)))


def model_name_in(text: str) -> str | None:
    """The first model-like name in a sentence ("Pulling Dolphin-Mixtral-8x7B-v2-Q4_K_M now")."""
    for match in _NAME.finditer(str(text or "").replace("\\_", "_")):
        candidate = match.group(0).rstrip(".-")
        if looks_like_model(candidate) and not re.fullmatch(r"(?i)(?:model|models|ollama)", candidate):
            return candidate
    return None


def _quant_case(tag: str) -> str:
    """Ollama spells quantisations q4_K_M; people type Q4_K_M or q4_k_m."""
    return re.sub(r"(?i)\bq(\d)_k_([sml])\b", lambda m: f"q{m.group(1)}_K_{m.group(2).upper()}", tag.lower())


@dataclass
class Plan:
    found: bool
    name: str = ""
    size_bytes: int = 0
    asked: str = ""
    installed: bool = False
    text: str = ""


@dataclass
class PullJob:
    name: str
    size_bytes: int
    status: str = "starting"          # starting | downloading | verifying | done | failed | cancelled
    completed: int = 0
    total: int = 0
    error: str = ""
    attempts: int = 0
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    #: Its entry in the transfer log (the Downloads window).
    ticket: int = 0

    @property
    def fraction(self) -> float:
        total = self.total or self.size_bytes
        return min(1.0, self.completed / total) if total else 0.0

    def rate(self) -> float:
        elapsed = (self.finished or time.monotonic()) - self.started
        return self.completed / elapsed if elapsed > 1 else 0.0


def gigabytes(size: int) -> str:
    return f"{size / 1e9:.1f} GB"


class ModelPulls:
    """Plans, approves and runs model downloads into the configured Ollama."""

    def __init__(self, base_url: str, *, store: Path | None = None, http: Any = None,
                 notify: Callable[[PullJob, str], None] | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.store = store
        self._http = http
        self.notify = notify
        self._approved: set[str] = set()
        self.jobs: list[PullJob] = []
        self._lock = threading.RLock()
        self._lookup_unavailable = False

    # ---------------------------------------------------------------- http
    @property
    def http(self):
        if self._http is None:
            import httpx

            self._http = httpx
        return self._http

    def _manifest_size(self, url: str) -> int | None:
        try:
            response = self.http.get(url, headers={"Accept": MANIFEST_ACCEPT}, timeout=5, follow_redirects=True)
        except Exception:
            self._lookup_unavailable = True
            log_swallowed(_log, "reading a model manifest")
            return None
        if getattr(response, "status_code", 0) >= 500:
            self._lookup_unavailable = True
        if getattr(response, "status_code", 0) != 200:
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        layers = [data.get("config") or {}, *(data.get("layers") or [])]
        return sum(int(layer.get("size") or 0) for layer in layers)

    def _size(self, name: str) -> int | None:
        if name.lower().startswith("hf.co/"):
            repo, _, tag = name[len("hf.co/"):].partition(":")
            return self._manifest_size(f"{HF_REGISTRY}/{repo}/manifests/{tag or 'latest'}")
        model, _, tag = name.partition(":")
        path = model if "/" in model else f"library/{model}"
        return self._manifest_size(f"{REGISTRY}/{path}/manifests/{tag or 'latest'}")

    def installed(self) -> set[str]:
        try:
            response = self.http.get(f"{self.base_url}/api/tags", timeout=10)
            return {str(item.get("name")) for item in response.json().get("models", [])}
        except Exception:
            log_swallowed(_log, "listing installed models")
            return set()

    # ------------------------------------------------------------- resolve
    def resolve(self, asked: str) -> tuple[str, int] | None:
        """The registry name and size for what was asked, or None when nothing matches."""
        value = str(asked or "").replace("\\_", "_").strip().strip(".!?")
        if not value:
            return None
        value = MODEL_ALIASES.get(value.lower(), value)
        if "/" in value or ":" in value:
            name = value if value.lower().startswith("hf.co/") else _quant_case(value)
            size = self._size(name)
            return (name, size) if size else None
        # "Dolphin-Mixtral-8x7B-v2-Q4_K_M": the longest leading words that name
        # a library model, then the rest as a tag, then just its size, then latest.
        # Split on spaces and hyphens only: "q4_k_m" is one quantisation, not three words.
        words = [word for word in re.split(r"[\s-]+", value.lower()) if word]
        probes = 0
        # Full library name first, then the common two-word family with the
        # remaining words as a tag (for example dolphin-mixtral:8x7b-v2-q4_K_M).
        cuts = list(dict.fromkeys([len(words), 2, 1, *range(len(words) - 1, 2, -1)]))
        for cut in cuts:
            if cut > len(words):
                continue
            model, rest = "-".join(words[:cut]), words[cut:]
            tags = [_quant_case("-".join(rest))] if rest else []
            precise = any(re.fullmatch(r"(?:q\d[\w]*|f(?:p)?(?:16|32)|v\d[\w.]*)", word) for word in rest)
            if not precise:
                tags += [rest[0]] if rest else []
                tags += ["latest"]
            for tag in dict.fromkeys(tag for tag in tags if tag):
                if probes >= MAX_REGISTRY_PROBES:
                    return None
                probes += 1
                size = self._size(f"{model}:{tag}")
                if size:
                    return (model if tag == "latest" else f"{model}:{tag}", size)
        return None

    def plan(self, asked: str) -> Plan:
        self._lookup_unavailable = False
        requested = str(asked or "").strip().strip(".!?")
        installed = self.installed()
        exact = next((name for name in installed if name.casefold() == requested.casefold()), None)
        if exact is None and ":" not in requested:
            exact = next((name for name in installed if name.casefold() == f"{requested}:latest".casefold()), None)
        if exact is not None:
            return Plan(True, exact, 0, asked, True, f"{exact} is already installed.")
        resolved = self.resolve(asked)
        if resolved is None:
            if self._lookup_unavailable:
                return Plan(False, asked=asked, text="I couldn't reach the model registry to verify that name and size. Try again later.")
            return Plan(False, asked=asked, text=(
                f"I couldn't find a model called \"{asked}\" in the Ollama library or on Hugging Face. "
                "Check the exact name (for example dolphin-mixtral:8x7b or hf.co/<user>/<repo>:<quant>) "
                "and ask again."))
        name, size = resolved
        if name in installed or f"{name}:latest" in installed:
            return Plan(True, name, size, asked, True, f"{name} is already installed ({gigabytes(size)}).")
        lines = [f"{name} is {gigabytes(size)}" + (f" (the closest match to \"{asked}\")" if name.lower() != asked.lower() else "") + "."]
        if self.store is not None:
            target = self.store.resolve()
            while not target.exists():
                target = target.parent
            free = shutil.disk_usage(target).free
            lines.append(f"It goes into HAYAT's own model store; {gigabytes(free)} is free there.")
            if free < size * 1.1:
                return Plan(False, name, size, asked, text=" ".join(lines) + " That is not enough room, so I won't start it.")
        lines.append("The time depends on your connection; I'll report progress and tell you when it's done.")
        lines.append("It will be an advisor I can consult -- I still write every answer myself.")
        return Plan(True, name, size, asked, False, " ".join(lines))

    # ---------------------------------------------------------------- run
    def approve(self, name: str) -> None:
        with self._lock:
            self._approved.add(name)

    def active(self) -> PullJob | None:
        with self._lock:
            return next((job for job in reversed(self.jobs) if job.status in {"starting", "downloading", "verifying"}), None)

    def start(self, name: str, size_bytes: int = 0) -> PullJob:
        with self._lock:
            if name not in self._approved:
                raise PermissionError(f"{name} was not approved; show the plan and wait for a yes first")
            running = self.active()
            if running is not None and running.name == name:
                return running
            if running is not None:
                raise RuntimeError(f"{running.name} is still downloading; one download at a time")
            self._approved.discard(name)
            job = PullJob(name, size_bytes)
            self.jobs.append(job)
        threading.Thread(target=self._run, args=(job,), name=f"model-pull-{name}", daemon=True).start()
        return job

    def cancel(self) -> PullJob | None:
        job = self.active()
        if job is not None:
            job.cancel.set()
        return job

    def revoke(self, name: str) -> None:
        """Drop an approval when policy refused the tool before it could start."""
        with self._lock:
            self._approved.discard(name)

    def _run(self, job: PullJob) -> None:
        from app.transfers import transfers
        job.ticket = transfers().start("model", job.name, "registry.ollama.ai", "Ollama's model store on this computer",
                                       job.size_bytes or None)
        while not job.cancel.is_set():
            job.attempts += 1
            job.error = ""
            if self._stream(job):
                break
            if job.cancel.is_set() or job.attempts > AUTOMATIC_RESUMES:
                break
            if job.error.startswith("Ollama answered 4") and not job.error.startswith("Ollama answered 429"):
                break
            if job.cancel.wait(min(60, 10 * job.attempts)):
                break
        job.finished = time.monotonic()
        if job.cancel.is_set():
            job.status = "cancelled"
        elif job.status != "done":
            job.status = "failed"
        transfers().finish(job.ticket, {"done": "done", "cancelled": "cancelled"}.get(job.status, "failed"), done=job.completed,
                           error=job.error)
        self._report(job)

    def _stream(self, job: PullJob) -> bool:
        layers: dict[str, tuple[int, int]] = {}
        try:
            with self.http.stream("POST", f"{self.base_url}/api/pull",
                                  json={"model": job.name, "stream": True}, timeout=None) as response:
                if getattr(response, "status_code", 200) >= 400:
                    job.error = f"Ollama answered {response.status_code}"
                    return False
                for line in response.iter_lines():
                    if job.cancel.is_set():
                        return False
                    if not line:
                        continue
                    event = json.loads(line)
                    if event.get("error"):
                        job.error = str(event["error"])
                        return False
                    status = str(event.get("status") or "")
                    if event.get("digest") and event.get("total"):
                        layers[event["digest"]] = (int(event.get("completed") or 0), int(event["total"]))
                        job.completed = sum(done for done, _ in layers.values())
                        job.total = max(job.size_bytes, sum(total for _, total in layers.values()))
                        job.status = "downloading"
                        if job.ticket:
                            from app.transfers import transfers
                            transfers().progress(job.ticket, job.completed, job.total)
                    elif status.startswith(("verifying", "writing")):
                        job.status = "verifying"
                    elif status == "success":
                        job.status = "done"
                        job.completed = job.total = job.total or job.size_bytes
                        return True
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
            log_swallowed(_log, "streaming a model download")
            return False
        job.error = job.error or "the download stream ended before Ollama reported success"
        return False

    # ------------------------------------------------------------- report
    def status_text(self) -> str:
        job = self.active() or (self.jobs[-1] if self.jobs else None)
        if job is None:
            return "I'm not downloading any model."
        if job.status in {"starting", "downloading", "verifying"}:
            rate = job.rate()
            left = (job.total or job.size_bytes) - job.completed
            eta = f", about {max(1, round(left / rate / 60))} min left" if rate > 0 and left > 0 else ""
            return (f"Downloading {job.name}: {job.fraction:.0%} ({gigabytes(job.completed)} of "
                    f"{gigabytes(job.total or job.size_bytes)}" + (f", {rate / 1e6:.0f} MB/s" if rate else "")
                    + eta + ")." + (" Checking it now." if job.status == "verifying" else ""))
        return self._summary(job)

    def _summary(self, job: PullJob) -> str:
        minutes = max(1, round(((job.finished or time.monotonic()) - job.started) / 60))
        if job.status == "done":
            return (f"{job.name} is downloaded ({gigabytes(job.total or job.size_bytes)}, {minutes} min). "
                    "It's available as an advisor; I still write the answers.")
        if job.status == "cancelled":
            return f"Stopped downloading {job.name} at {job.fraction:.0%}. Say \"download it again\" to resume."
        return (f"Downloading {job.name} failed at {job.fraction:.0%} after {job.attempts} attempt(s): "
                f"{job.error or 'unknown error'}. What arrived is kept; say \"download it again\" to resume.")

    def _report(self, job: PullJob) -> None:
        if self.notify is None:
            return
        try:
            self.notify(job, self._summary(job))
        except Exception:
            log_swallowed(_log, "reporting a finished model download", level=logging.WARNING)


def register_model_pull_tools(registry, pulls: ModelPulls) -> ModelPulls:
    def find_model(name: str) -> dict[str, Any]:
        plan = pulls.plan(name)
        return {"found": plan.found, "name": plan.name, "size_bytes": plan.size_bytes,
                "installed": plan.installed, "text": plan.text}

    def pull_model(name: str, size_bytes: int = 0) -> dict[str, Any]:
        job = pulls.start(name, size_bytes)
        return {"started": True, "name": job.name, "status": job.status}

    registry.register(
        "find_model",
        "Look up a local AI model in the Ollama library or on Hugging Face: its exact name, real size, and "
        "whether it is installed. Show this plan to the owner and wait for their yes before pulling it.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        "low", "dependency_inspect", find_model,
    )
    registry.register(
        "pull_model",
        "Download a local AI model into HAYAT's own Ollama in the background. Refused unless the owner approved "
        "that exact model after seeing the plan from find_model.",
        {"type": "object", "properties": {"name": {"type": "string"},
                                         "size_bytes": {"type": "integer", "minimum": 0}}, "required": ["name"]},
        "medium", "install_system_software", pull_model,
    )
    return pulls
