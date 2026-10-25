from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import time
from ctypes import wintypes
from datetime import UTC, datetime
from typing import Any

from app.config import PROJECT_ROOT


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class FILETIME(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


def _filetime(value: FILETIME) -> int:
    return (value.high << 32) + value.low


class ObservabilityManager:
    def __init__(self, memory, storage, event_bus=None) -> None:
        self.memory = memory; self.storage = storage; self.event_bus = event_bus

    #: Cached because it asks Windows a question whose answer cannot change
    #: while the process is running: DIMMs are not hot-swapped.
    _installed_cache: int | None = None

    @classmethod
    def _installed_ram(cls) -> int | None:
        """RAM physically in the machine, which is not what Windows offers.

        GlobalMemoryStatusEx reports 39.7 GiB on this laptop and Mohsin
        correctly said it has 48. Both are true: the missing 8.3 GiB is
        carved out for hardware before Windows sees any of it. Reporting only
        the smaller number made HAYAT look like it could not read its own
        machine; reporting only the larger one would make every percentage
        wrong. So it reports both, and says which is which.
        """
        if cls._installed_cache is not None:
            return cls._installed_cache or None
        if os.name != "nt":
            try:
                import psutil

                cls._installed_cache = int(psutil.virtual_memory().total)
                return cls._installed_cache
            except Exception:
                cls._installed_cache = 0
                return None
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "(Get-CimInstance Win32_PhysicalMemory | "
                 "Measure-Object -Property Capacity -Sum).Sum"],
                capture_output=True, text=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            cls._installed_cache = int((completed.stdout or "0").strip() or 0)
        except (OSError, ValueError, subprocess.SubprocessError):
            cls._installed_cache = 0
        return cls._installed_cache or None

    def system(self) -> dict[str, Any]:
        if os.name == "nt":
            memory = MEMORYSTATUSEX(); memory.dwLength = ctypes.sizeof(memory)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
            total, available, used_percent = (
                int(memory.ullTotalPhys), int(memory.ullAvailPhys), float(memory.dwMemoryLoad))
        else:
            import psutil

            measured = psutil.virtual_memory()
            total, available, used_percent = int(measured.total), int(measured.available), float(measured.percent)
        disk = shutil.disk_usage(PROJECT_ROOT)
        result = {
            "observed_at": datetime.now(UTC).isoformat(), "cpu_percent": self._cpu_percent(),
            "ram": {"total_bytes": total, "available_bytes": available,
                    "used_percent": used_percent, "installed_bytes": self._installed_ram()},
            "disk": {"total_bytes": disk.total, "free_bytes": disk.free, "project_budget": self.storage.maximum, "reserve_bytes": self.storage.reserve},
            "gpu": self._gpu(), "paid_telemetry": False,
        }
        if (result["ram"]["used_percent"] >= 90 or result["disk"]["free_bytes"] <= self.storage.reserve) and self.event_bus:
            self.event_bus.publish("RESOURCE_ALERT", "observability", {"ram_used_percent": result["ram"]["used_percent"], "disk_free_bytes": result["disk"]["free_bytes"]})
        return result

    def performance(self) -> dict[str, Any]:
        with self.memory.connect() as db:
            row = db.execute("SELECT COUNT(*),AVG(duration_ms),SUM(CASE WHEN json_extract(result_json,'$.success')=1 THEN 1 ELSE 0 END) FROM actions").fetchone()
            recent = db.execute("SELECT tool,COUNT(*) count,AVG(duration_ms) mean_ms,SUM(CASE WHEN json_extract(result_json,'$.success')=1 THEN 1 ELSE 0 END)*1.0/COUNT(*) success_rate FROM actions GROUP BY tool ORDER BY count DESC LIMIT 20").fetchall()
        benchmarks = {}
        paths = {
            "retrieval": PROJECT_ROOT / "data/benchmarks/retrieval/after.json",
            "model_routing": PROJECT_ROOT / "data/benchmarks/model_routing/adaptive.json",
            "model_warmth": PROJECT_ROOT / "data/benchmarks/model_routing/warmth.json",
            "voice": PROJECT_ROOT / "data/benchmarks/voice/hardware.json",
            "tools": PROJECT_ROOT / "data/benchmarks/tools/latest.json",
        }
        for name, path in paths.items():
            try: benchmarks[name] = json.loads(path.read_text(encoding="utf-8"))
            except Exception: benchmarks[name] = None
        return {"actions": {"count": int(row[0]), "mean_duration_ms": round(float(row[1] or 0), 3), "success_rate": round(float(row[2] or 0) / max(1, int(row[0])), 4)}, "tools": [dict(item) for item in recent], "benchmarks": benchmarks, "live_system": self.system()}

    def health(self, runtime=None) -> dict[str, Any]:
        system = self.system(); components = {}
        if runtime is not None:
            components = {
                "model": bool(runtime.model), "memory": runtime.memory.path.is_file(), "research": runtime.research.knowledge.path.is_file(),
                "vision": bool(runtime.vision), "voice": bool(runtime.voice), "camera": runtime.camera.status()["state"],
                "browser_registered": "browser_read_page" in runtime.registry.names(), "zero_cost": runtime.cost_policy.mode == "ZERO-COST",
            }
        return {"healthy": all(value not in {False, "ERROR"} for value in components.values()), "components": components, "resources": system}

    @staticmethod
    def _cpu_percent() -> float:
        if os.name != "nt":
            try:
                import psutil

                return round(float(psutil.cpu_percent(interval=0.08)), 2)
            except Exception:
                return 0.0
        kernel32 = ctypes.windll.kernel32
        idle1 = FILETIME(); kernel1 = FILETIME(); user1 = FILETIME(); kernel32.GetSystemTimes(ctypes.byref(idle1), ctypes.byref(kernel1), ctypes.byref(user1)); time.sleep(.08)
        idle2 = FILETIME(); kernel2 = FILETIME(); user2 = FILETIME(); kernel32.GetSystemTimes(ctypes.byref(idle2), ctypes.byref(kernel2), ctypes.byref(user2))
        idle = _filetime(idle2) - _filetime(idle1); total = (_filetime(kernel2) - _filetime(kernel1)) + (_filetime(user2) - _filetime(user1))
        return round(max(0.0, min(100.0, (1 - idle / max(1, total)) * 100)), 2)

    @staticmethod
    def _gpu() -> list[dict[str, Any]]:
        try:
            command = ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"]
            output = subprocess.run(command, capture_output=True, text=True, timeout=8, check=True).stdout.strip().splitlines()
            rows = []
            for line in output:
                name, total, used, free, utilization, temperature = [item.strip() for item in line.split(",")]
                rows.append({"name": name, "vram_total_mb": int(total), "vram_used_mb": int(used), "vram_free_mb": int(free), "utilization_percent": int(utilization), "temperature_c": int(temperature)})
            return rows
        except Exception as exc:
            return [{"available": False, "error": f"{type(exc).__name__}: {exc}"}]
