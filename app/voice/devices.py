from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
import subprocess
from typing import Any


@dataclass(slots=True)
class AudioDevice:
    id: int
    name: str
    kind: str
    channels: int
    default_sample_rate: int
    is_default: bool
    host_api: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DeviceManager:
    def _sounddevice(self):
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("Audio support is not installed. Run .\\setup.ps1.") from exc
        return sd

    def list_devices(self) -> dict[str, Any]:
        sd = self._sounddevice()
        default_input, default_output = sd.default.device
        host_apis = sd.query_hostapis()
        microphones: list[AudioDevice] = []
        speakers: list[AudioDevice] = []
        for index, raw in enumerate(sd.query_devices()):
            host_name = str(host_apis[raw["hostapi"]]["name"])
            if int(raw["max_input_channels"]) > 0:
                microphones.append(AudioDevice(index, str(raw["name"]), "input", int(raw["max_input_channels"]), int(raw["default_samplerate"]), index == default_input, host_name))
            if int(raw["max_output_channels"]) > 0:
                speakers.append(AudioDevice(index, str(raw["name"]), "output", int(raw["max_output_channels"]), int(raw["default_samplerate"]), index == default_output, host_name))
        return {
            "microphones": [device.to_dict() for device in microphones],
            "speakers": [device.to_dict() for device in speakers],
            "default_input": default_input,
            "default_output": default_output,
        }

    def diagnose_input(self, *, include_windows: bool = True) -> dict[str, Any]:
        """Explain recording readiness instead of treating an empty list as ready."""
        inventory = self.list_devices()
        microphones = inventory["microphones"]
        result: dict[str, Any] = {
            "available": bool(microphones),
            "microphone_count": len(microphones),
            "default_input": inventory["default_input"],
            "reason": None if microphones else "The host exposes no recording endpoint to PortAudio.",
            "remedy": None if microphones else "Enable or repair a microphone/audio input device, then run /voice devices and /voice probe.",
            "windows_device_errors": [],
        }
        if microphones or not include_windows or os.name != "nt":
            return result
        script = (
            "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Status -eq 'Error' -and ($_.Class -in @('AudioEndpoint','Media','System')) "
            "-and $_.FriendlyName -match 'Audio|Microphone|Sound' } | "
            "Select-Object Status,Class,FriendlyName,InstanceId,Problem,ConfigManagerErrorCode | ConvertTo-Json -Compress"
        )
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=8,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                decoded = json.loads(completed.stdout)
                result["windows_device_errors"] = decoded if isinstance(decoded, list) else [decoded]
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            pass
        return result

    def resolve(self, requested: str | int | None, kind: str) -> int | None:
        if requested in (None, "", "auto"):
            return None
        devices = self.list_devices()["microphones" if kind == "input" else "speakers"]
        if isinstance(requested, int) or str(requested).isdigit():
            identifier = int(requested)
            if any(item["id"] == identifier for item in devices):
                return identifier
            raise ValueError(f"Audio {kind} device {identifier} is unavailable.")
        matches = [item for item in devices if str(requested).lower() in item["name"].lower()]
        if len(matches) != 1:
            raise ValueError(f"Audio {kind} device name must match exactly one device; found {len(matches)}.")
        return int(matches[0]["id"])
