from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app.config import PROJECT_ROOT


class HayatModelManager:
    """Build and inspect HAYAT's governed local Ollama model package.

    Building is explicit and reversible.  This class never downloads a base
    model, starts training, changes permissions, or promotes an adapter.
    """

    def __init__(self, root: Path | None = None, *, name: str = "hayat:latest",
                 base_model: str | None = None,
                 base_url: str = "http://localhost:11434") -> None:
        self.project_root = root or PROJECT_ROOT
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.package_root = self.project_root / "models" / "hayat"
        self.modelfile = self.package_root / "Modelfile"
        self.package_manifest = self.package_root / "manifest.json"
        manifest = self._read_json(self.package_manifest)
        # The executable Modelfile is more authoritative than optional
        # descriptive metadata. An explicit runtime configuration wins over
        # both. This prevents status/build checks drifting back to an old base
        # after the identity package is rebuilt on a new foundation.
        self.base_model = (
            str(base_model).strip() if base_model else
            self._modelfile_base() or str(manifest.get("base_model") or "qwen3.6-35b-a3b-uncensored:q4_k_m")
        )
        self.state_root = self.project_root / "data" / "models" / "hayat"
        self.build_record = self.state_root / "build.json"

    def _backend_record(self, base_url: str) -> Path:
        key = hashlib.sha256(base_url.rstrip("/").encode("utf-8")).hexdigest()[:16]
        return self.state_root / "builds" / f"{key}.json"

    def status(self) -> dict[str, Any]:
        installed = self._installed_models()
        manifest = self._read_json(self.package_manifest)
        build = self._read_json(self._backend_record(self.base_url)) or self._read_json(self.build_record)
        details: dict[str, Any] = {}
        if self.name in installed:
            try:
                response = httpx.post(
                    self.base_url + "/api/show", json={"model": self.name}, timeout=5,
                )
                response.raise_for_status()
                payload = response.json()
                details = {
                    "capabilities": list(payload.get("capabilities") or []),
                    "parameters": str(payload.get("parameters") or ""),
                    "system_current": self._same_system(str(payload.get("system") or "")),
                }
            except Exception as exc:
                details = {"inspection_error": f"{type(exc).__name__}: {exc}"}
        return {
            "name": self.name,
            "installed": self.name in installed,
            "base_model": self.base_model,
            "base_installed": self.base_model in installed,
            "kind": manifest.get("kind", "ollama_custom_identity_model"),
            "weights": manifest.get("weights", "inherited_foundation_weights"),
            "modelfile": str(self.modelfile),
            "modelfile_sha256": self._sha256(self.modelfile),
            "local_only": True,
            "automatic_training": False,
            "automatic_promotion": False,
            "foundation_independent": False,
            "build": build or None,
            "backend": self.base_url,
            "package_current": bool(details.get("system_current") and build.get("success")
                                    and build.get("backend") == self.base_url
                                    and build.get("modelfile_sha256") == self._sha256(self.modelfile)),
            **details,
        }

    def build(self, *, timeout: float = 900.0) -> dict[str, Any]:
        executable = shutil.which("ollama")
        if not executable:
            raise RuntimeError("Ollama is not installed or is not on PATH.")
        if not self.modelfile.is_file():
            raise FileNotFoundError(self.modelfile)
        installed = self._installed_models()
        if self.base_model != self._modelfile_base():
            raise RuntimeError("Configured foundation does not match the Modelfile FROM declaration.")
        if self.base_model not in installed:
            raise RuntimeError(
                f"The declared foundation model {self.base_model!r} is not installed. "
                "HAYAT never downloads model weights implicitly."
            )
        environment = {**os.environ, "OLLAMA_HOST": self.base_url}
        def run(*arguments: str):
            return subprocess.run(
                [executable, *arguments], cwd=self.package_root, capture_output=True,
                text=True, encoding="utf-8", errors="replace", env=environment,
                timeout=max(30.0, float(timeout)), check=False,
            )
        backup = None
        if self.name in installed:
            backup = self.name.split(":", 1)[0] + ":rollback-" + uuid4().hex[:12]
            copied = run("cp", self.name, backup)
            if copied.returncode:
                raise RuntimeError("Could not preserve the installed HAYAT package before rebuilding.")
        try:
            completed = run("create", self.name, "-f", str(self.modelfile))
            if completed.returncode:
                raise RuntimeError((completed.stderr or completed.stdout or "Ollama build failed")[-2000:])
            if self.name not in self._installed_models():
                raise RuntimeError("Ollama reported success but HAYAT is absent from this server's inventory.")
            shown = httpx.post(self.base_url + "/api/show", json={"model": self.name}, timeout=10)
            shown.raise_for_status()
            if not self._same_system(str(shown.json().get("system") or "")):
                raise RuntimeError("The installed HAYAT system instructions do not match the updated package.")
        except Exception as exc:
            if backup:
                try:
                    restored = run("cp", backup, self.name)
                    if restored.returncode:
                        raise RuntimeError("rollback copy failed")
                except Exception as restore_error:
                    raise RuntimeError(f"Build failed and automatic rollback failed; retained backup: {backup}") from restore_error
                raise RuntimeError(f"Build failed; the previous package was restored: {exc}") from exc
            raise
        record = {
            "model": self.name,
            "base_model": self.base_model,
            "built_at": datetime.now(UTC).isoformat(),
            "modelfile_sha256": self._sha256(self.modelfile),
            "exit_code": int(completed.returncode),
            "success": completed.returncode == 0,
            "backend": self.base_url,
            "rollback_model": backup,
            "output": (completed.stdout or completed.stderr or "").strip()[-2000:],
        }
        self.state_root.mkdir(parents=True, exist_ok=True)
        previous = self._read_json(self.build_record)
        if previous.get("backend"):
            previous_path = self._backend_record(str(previous["backend"]))
            previous_path.parent.mkdir(parents=True, exist_ok=True)
            previous_path.write_text(json.dumps(previous, indent=2), encoding="utf-8")
        backend_record = self._backend_record(self.base_url)
        backend_record.parent.mkdir(parents=True, exist_ok=True)
        backend_record.write_text(json.dumps(record, indent=2), encoding="utf-8")
        self.build_record.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return self.status()

    def _same_system(self, installed: str) -> bool:
        text = self.modelfile.read_text(encoding="utf-8")
        match = re.search(r'^SYSTEM\s+"""(.*?)"""', text, re.M | re.S)
        if not match:
            match = re.search(r'^SYSTEM\s+([^\n]+)', text, re.M)
        expected = match.group(1).strip() if match else ""
        return bool(expected) and " ".join(expected.split()) == " ".join(installed.split())

    def _installed_models(self) -> set[str]:
        try:
            response = httpx.get(self.base_url + "/api/tags", timeout=5)
            response.raise_for_status()
            return {str(item.get("name")) for item in response.json().get("models", [])}
        except Exception:
            return set()

    @staticmethod
    def _sha256(path: Path) -> str | None:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _modelfile_base(self) -> str:
        try:
            for line in self.modelfile.read_text(encoding="utf-8").splitlines():
                match = line.strip().split(maxsplit=1)
                if len(match) == 2 and match[0].upper() == "FROM":
                    return match[1].strip()
        except OSError:
            pass
        return ""
