from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from typing import Any

import httpx


class IncidentManager:
    """Read-only first-pass diagnostics for this Windows PC and local AI infrastructure."""

    def __init__(self, observability) -> None:
        self.observability = observability

    def snapshot(self, scope: str = "system") -> dict[str, Any]:
        scope = scope.lower().strip()
        result = {"observed_at": datetime.now(UTC).isoformat(), "scope": scope, "resources": self.observability.system(), "changes_made": False, "next_step": "Form a hypothesis from observed evidence before applying any fix."}
        if scope in {"system", "ai", "gpu", "ollama", "docker", "kubernetes", "network"}:
            result["components"] = self._components()
        if scope in {"system", "network"}:
            result["listening_ports"] = self._listening_ports()
        return result

    @staticmethod
    def _components() -> dict[str, Any]:
        values: dict[str, Any] = {}
        for name, command in {"docker": ["docker", "version", "--format", "{{.Server.Version}}"], "kubectl": ["kubectl", "version", "--client=true", "--output=json"], "git": ["git", "--version"]}.items():
            if not shutil.which(command[0]): values[name] = {"installed": False}; continue
            completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
            values[name] = {"installed": True, "reachable": completed.returncode == 0, "output": (completed.stdout or completed.stderr).strip()[:1000]}
        try:
            base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
            response = httpx.get(f"{base}/api/tags", timeout=3); response.raise_for_status()
            values["ollama"] = {"reachable": True, "models": len(response.json().get("models", []))}
        except Exception as exc: values["ollama"] = {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}
        return values

    @staticmethod
    def _powershell(script: str) -> str:
        completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace")
        return completed.stdout.strip() if completed.returncode == 0 else completed.stderr.strip()

    @classmethod
    def _listening_ports(cls) -> Any:
        if os.name == "nt":
            return cls._powershell("Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Select-Object -First 50 LocalAddress,LocalPort,OwningProcess | ConvertTo-Json -Compress")
        try:
            import psutil

            return [
                {"local_address": item.laddr.ip, "local_port": item.laddr.port,
                 "pid": item.pid}
                for item in psutil.net_connections(kind="inet")
                if item.status == psutil.CONN_LISTEN and item.laddr
            ][:50]
        except Exception as exc:
            return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def register_incident_tools(registry, manager: IncidentManager) -> None:
    schema = {"type": "object", "properties": {"scope": {"enum": ["system", "ai", "gpu", "ollama", "docker", "kubernetes", "network"]}}, "additionalProperties": False}
    registry.register("incident_snapshot", "Collect read-only local incident evidence for system, GPU, Ollama, Docker, Kubernetes, or networking diagnosis.", schema, "low", "system_read", manager.snapshot)
