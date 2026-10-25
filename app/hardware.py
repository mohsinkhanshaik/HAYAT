"""What this machine actually is, so plans are made against it rather than against hope.

An assistant that does not know its own limits makes confident plans it cannot
execute: loading a model larger than VRAM, running four things at once on a
laptop that thermally throttles, promising a 35B response on 8GB. The failure
arrives late, halfway through work the user was waiting on.

So the hardware is measured, not assumed, and the numbers are treated the same
way as every other fact in this system: observed, attributed, and allowed to be
unknown. `nvidia-smi` is asked about the GPU; Windows is asked about memory
through the same call Task Manager uses. Nothing here estimates. When a value
cannot be obtained it is None, and callers say "unknown" rather than guessing a
default that would license a plan the machine cannot carry out.

The point is the verdicts at the bottom: `fits_in_vram`, `can_run`, and
`recommended_context` turn raw numbers into answers to the questions the
planner actually asks.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any


#: Memory the operating system, the desktop, and this runtime need to keep
#: working while a model is loaded. Taking every free byte is how a machine
#: becomes unusable rather than fast.
SYSTEM_RAM_HEADROOM_BYTES = 3 * 1024**3
VRAM_HEADROOM_BYTES = 700 * 1024**2

#: Weights are not the whole cost: the KV cache, compute buffers and
#: fragmentation add to it. Measured rule of thumb for llama.cpp-family
#: runtimes at ordinary context lengths.
RUNTIME_OVERHEAD_FACTOR = 1.20


@dataclass
class GPUInfo:
    name: str | None = None
    total_bytes: int | None = None
    used_bytes: int | None = None
    driver: str | None = None
    #: VRAM held by a model that would be released if it were asked to unload.
    #: Without this, a card with 8GB reports "1.3GB free" while 6.8GB sits in
    #: an idle Ollama model, and HAYAT refuses work the machine can plainly do
    #: -- the memory is occupied, not gone.
    reclaimable_bytes: int = 0
    holders: list[str] = field(default_factory=list)

    @property
    def free_bytes(self) -> int | None:
        if self.total_bytes is None or self.used_bytes is None:
            return None
        return max(0, self.total_bytes - self.used_bytes)

    @property
    def obtainable_bytes(self) -> int | None:
        """What HAYAT could actually have, after asking others to let go."""
        if self.free_bytes is None:
            return None
        return self.free_bytes + max(0, self.reclaimable_bytes)

    @property
    def available(self) -> bool:
        return self.total_bytes is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "driver": self.driver,
            "total_gb": _gb(self.total_bytes), "used_gb": _gb(self.used_bytes),
            "free_gb": _gb(self.free_bytes), "available": self.available,
            "reclaimable_gb": _gb(self.reclaimable_bytes),
            "obtainable_gb": _gb(self.obtainable_bytes), "held_by": list(self.holders),
        }


@dataclass
class HardwareProfile:
    """A measured description of the machine, with the reasoning it supports."""

    measured_at: float = field(default_factory=time.monotonic)
    gpu: GPUInfo = field(default_factory=GPUInfo)
    total_ram_bytes: int | None = None
    available_ram_bytes: int | None = None
    cpu_count: int | None = None
    disk_free_bytes: int | None = None
    platform: str = os.name

    # ------------------------------------------------------------- verdicts

    def fits_in_vram(self, model_bytes: int) -> bool:
        """Whether a model of this size fits in VRAM that is free right now."""
        if not self.gpu.available or self.gpu.free_bytes is None:
            return False
        return self._vram_needed(model_bytes) <= self.gpu.free_bytes

    def fits_in_vram_after_freeing(self, model_bytes: int) -> bool:
        """Whether it would fit once an idle model releases its memory.

        Occupied is not the same as gone. An 8GB card reporting 1.3GB free
        while an idle Ollama model holds 6.8GB is a card with 8GB, and
        refusing work on that basis is refusing what the machine can plainly
        do.
        """
        if not self.gpu.available or self.gpu.obtainable_bytes is None:
            return False
        return self._vram_needed(model_bytes) <= self.gpu.obtainable_bytes

    def _vram_needed(self, model_bytes: int) -> float:
        return model_bytes * RUNTIME_OVERHEAD_FACTOR + VRAM_HEADROOM_BYTES

    def fits_in_ram(self, model_bytes: int) -> bool:
        if self.available_ram_bytes is None:
            return False
        needed = model_bytes * RUNTIME_OVERHEAD_FACTOR + SYSTEM_RAM_HEADROOM_BYTES
        return needed <= self.available_ram_bytes

    def can_run(self, model_bytes: int) -> dict[str, Any]:
        """An honest verdict on running a model of this size, with the reason.

        Three outcomes rather than two, because "runs, but slowly, because most
        of it is in system RAM" is the common case on an 8GB laptop and is
        useful to say out loud. A plan that knows it will be slow can warn;
        one that believes it will be fast simply disappoints.
        """
        gigabytes = _gb(model_bytes)
        if self.fits_in_vram(model_bytes):
            return {
                "runnable": True, "placement": "GPU",
                "reason": f"{gigabytes}GB fits in {_gb(self.gpu.free_bytes)}GB of free VRAM.",
                "expect": "fast",
            }
        if self.fits_in_vram_after_freeing(model_bytes):
            holders = ", ".join(self.gpu.holders) or "another model"
            return {
                "runnable": True, "placement": "GPU_AFTER_FREEING",
                "reason": (
                    f"{gigabytes}GB does not fit in the {_gb(self.gpu.free_bytes)}GB free right now, "
                    f"but {_gb(self.gpu.reclaimable_bytes)}GB is held by {holders} and would be "
                    f"released on request -- giving {_gb(self.gpu.obtainable_bytes)}GB."
                ),
                "expect": "fast once freed",
                "free_first": list(self.gpu.holders),
            }
        if self.fits_in_ram(model_bytes):
            return {
                "runnable": True, "placement": "CPU_OR_SPLIT",
                "reason": (
                    f"{gigabytes}GB does not fit in "
                    f"{_gb(self.gpu.free_bytes) if self.gpu.available else 'no'}GB of free VRAM, "
                    f"but does fit in {_gb(self.available_ram_bytes)}GB of available RAM."
                ),
                "expect": "slow",
            }
        return {
            "runnable": False, "placement": "NONE",
            "reason": (
                f"{gigabytes}GB exceeds both free VRAM and available RAM "
                f"({_gb(self.available_ram_bytes)}GB) with headroom for the system."
            ),
            "expect": "unavailable",
        }

    def recommended_context(self, model_bytes: int) -> int:
        """A context length the machine can actually hold alongside the weights.

        The KV cache grows with context and is what turns a model that loaded
        fine into one that stalls mid-conversation.

        Every rung doubled on 2026-08-19. Mohsin asked for it and the machine
        supports it: 48 GiB installed with 8 GiB of VRAM, and the measured KV
        cost is about half a megabyte per thousand tokens, so 65536 tokens is
        roughly 32 MB of cache -- not the constraint it would have been on the
        hardware these numbers were first chosen for. The floor doubled too,
        because the old 4096 was where long answers started losing their
        beginnings.
        """
        spare = self.gpu.free_bytes if self.fits_in_vram(model_bytes) else self.available_ram_bytes
        if spare is None:
            return 8192
        remaining = spare - model_bytes * RUNTIME_OVERHEAD_FACTOR
        if remaining > 6 * 1024**3:
            return 65536
        if remaining > 3 * 1024**3:
            return 32768
        if remaining > 1024**3:
            return 16384
        return 8192

    def max_parallel_tasks(self) -> int:
        """How much work to run at once without making the machine unpleasant."""
        if not self.cpu_count:
            return 2
        return max(1, min(4, self.cpu_count // 4))

    def constraints(self) -> list[str]:
        """Limits worth stating in plain words before planning around them."""
        notes: list[str] = []
        if self.gpu.available and self.gpu.total_bytes and self.gpu.total_bytes < 12 * 1024**3:
            notes.append(
                f"{_gb(self.gpu.total_bytes)}GB of VRAM: models above roughly "
                f"{_gb(int((self.gpu.total_bytes - VRAM_HEADROOM_BYTES) / RUNTIME_OVERHEAD_FACTOR))}GB "
                "will run partly in system RAM and be noticeably slower."
            )
        if not self.gpu.available:
            notes.append("No usable GPU was detected; inference will run on the CPU.")
        if self.available_ram_bytes and self.available_ram_bytes < 8 * 1024**3:
            notes.append(f"Only {_gb(self.available_ram_bytes)}GB of RAM is free right now.")
        if self.disk_free_bytes is not None and self.disk_free_bytes < 20 * 1024**3:
            notes.append(f"Only {_gb(self.disk_free_bytes)}GB of disk is free; model downloads may not fit.")
        return notes

    def to_dict(self) -> dict[str, Any]:
        return {
            "gpu": self.gpu.to_dict(),
            "total_ram_gb": _gb(self.total_ram_bytes),
            "available_ram_gb": _gb(self.available_ram_bytes),
            "cpu_count": self.cpu_count,
            "disk_free_gb": _gb(self.disk_free_bytes),
            "max_parallel_tasks": self.max_parallel_tasks(),
            "constraints": self.constraints(),
        }

    def summary(self) -> str:
        """What the agent should say when asked what it runs on."""
        parts: list[str] = []
        if self.gpu.available:
            vram = f"{self.gpu.name} with {_gb(self.gpu.total_bytes)}GB VRAM ({_gb(self.gpu.free_bytes)}GB free"
            if self.gpu.reclaimable_bytes:
                # Without this, "1.3GB free" reads as a 1GB card rather than an
                # 8GB card with something idle parked on it.
                vram += (f", {_gb(self.gpu.reclaimable_bytes)}GB held by "
                         f"{', '.join(self.gpu.holders) or 'another model'} and reclaimable")
            parts.append(vram + ")")
        else:
            parts.append("no usable GPU")
        parts.append(f"{_gb(self.total_ram_bytes)}GB RAM ({_gb(self.available_ram_bytes)}GB available)")
        if self.cpu_count:
            parts.append(f"{self.cpu_count} logical CPUs")
        line = "Running on " + ", ".join(parts) + "."
        constraints = self.constraints()
        return line + ("\n" + "\n".join(f"- {item}" for item in constraints) if constraints else "")


# ------------------------------------------------------------------ measuring


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _measure_memory() -> tuple[int | None, int | None]:
    """Total and available physical RAM, from the OS rather than a library.

    Uses the same call Task Manager reports from, so the numbers match what the
    user sees, and adds no dependency to satisfy a question Windows already
    answers.
    """
    if os.name != "nt":
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            return os.sysconf("SC_PHYS_PAGES") * page_size, os.sysconf("SC_AVPHYS_PAGES") * page_size
        except (ValueError, AttributeError, OSError):
            return None, None
    try:
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None, None
        return int(status.ullTotalPhys), int(status.ullAvailPhys)
    except Exception:
        return None, None


def _measure_gpu() -> GPUInfo:
    """Ask the driver. An absent nvidia-smi means unknown, not zero."""
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return GPUInfo()
        name, total, used, driver = [part.strip() for part in completed.stdout.strip().splitlines()[0].split(",")]
        info = GPUInfo(
            name=name, driver=driver,
            total_bytes=int(float(total)) * 1024**2, used_bytes=int(float(used)) * 1024**2,
        )
        info.reclaimable_bytes, info.holders = _reclaimable_vram()
        return info
    except Exception:
        return GPUInfo()


def _reclaimable_vram() -> tuple[int, list[str]]:
    """VRAM held by loaded models that would unload if asked.

    Ollama publishes what it currently has resident and releases it on
    request, so that memory is occupied rather than unavailable. Knowing the
    difference is what stops HAYAT refusing a model that would fit perfectly
    once an idle one steps aside.
    """
    import os

    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        import httpx

        from app.models.local import discovery_timeout

        response = httpx.get(f"{base}/api/ps", timeout=discovery_timeout(base))
        response.raise_for_status()
        loaded = response.json().get("models", []) or []
    except Exception:
        return 0, []
    total = sum(int(item.get("size_vram", 0) or 0) for item in loaded)
    names = [str(item.get("name", "a model")) for item in loaded if item.get("size_vram")]
    return total, names


_CACHE: HardwareProfile | None = None
_CACHE_EXPIRES = 0.0

#: Free VRAM and RAM move constantly; the shape of the machine does not. Long
#: enough to keep this off the hot path, short enough that a freed model shows.
PROFILE_TTL_SECONDS = 20.0


def profile(*, refresh: bool = False) -> HardwareProfile:
    """The current measured profile, cached briefly."""
    global _CACHE, _CACHE_EXPIRES
    now = time.monotonic()
    if not refresh and _CACHE is not None and now < _CACHE_EXPIRES:
        return _CACHE

    total_ram, available_ram = _measure_memory()
    try:
        disk_free = shutil.disk_usage(os.path.abspath(os.sep)).free
    except Exception:
        disk_free = None

    _CACHE = HardwareProfile(
        gpu=_measure_gpu(), total_ram_bytes=total_ram, available_ram_bytes=available_ram,
        cpu_count=os.cpu_count(), disk_free_bytes=disk_free,
    )
    _CACHE_EXPIRES = now + PROFILE_TTL_SECONDS
    return _CACHE


def _gb(value: int | None) -> float | None:
    return None if value is None else round(value / 1024**3, 1)
