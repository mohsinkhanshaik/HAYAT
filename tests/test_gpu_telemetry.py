"""NVIDIA-SMI output fixtures include decimal temperatures from the actual driver."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import app.observability.manager as observability


def _driver_output(monkeypatch, output):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(observability.subprocess, "run", run)
    return calls


def test_decimal_driver_temperature_and_other_fractional_metrics_are_preserved(monkeypatch):
    calls = _driver_output(monkeypatch,
                           "NVIDIA GeForce RTX 5060 Laptop GPU, 8151, 102.25, 7798.5, 14.5, 50.69\n")
    row = observability.ObservabilityManager._gpu()[0]
    assert row == {"name": "NVIDIA GeForce RTX 5060 Laptop GPU", "vram_total_mb": 8151,
                   "vram_used_mb": 102.25, "vram_free_mb": 7798.5, "utilization_percent": 14.5,
                   "temperature_c": 50.69}
    assert isinstance(row["vram_total_mb"], int)
    assert calls[0][1]["check"] and calls[0][1]["timeout"] == 8
    json.dumps(row, allow_nan=False)


def test_unknown_sensors_do_not_hide_the_rest_of_the_gpu_or_other_devices(monkeypatch):
    _driver_output(monkeypatch, '"NVIDIA, virtual GPU", 24576, 0, 24576, N/A, [Not Supported]\n'
                   "NVIDIA second GPU, 8151, 100, 8000, 0, 47.22\n")
    rows = observability.ObservabilityManager._gpu()
    assert len(rows) == 2
    assert rows[0]["name"] == "NVIDIA, virtual GPU"
    assert rows[0]["utilization_percent"] is None and rows[0]["temperature_c"] is None
    assert rows[0]["vram_used_mb"] == 0 and rows[1]["temperature_c"] == 47.22
    assert not any(row.get("available") is False for row in rows)


@pytest.mark.parametrize("bad_row", [
    "NVIDIA broken GPU, not-a-number, 0, 1000, 0, 40",
    "NVIDIA broken GPU, 1000, 0, 1000, nan, 40",
    "NVIDIA broken GPU, 1000, 0, 1000, 0, inf",
    "NVIDIA broken GPU, 1000, 0",
])
def test_one_invalid_device_does_not_erase_a_healthy_device(monkeypatch, bad_row):
    _driver_output(monkeypatch, bad_row + "\nNVIDIA healthy GPU, 8151, 102, 7798, 0, 47.25\n")
    rows = observability.ObservabilityManager._gpu()
    assert rows[0]["available"] is False and "ValueError" in rows[0]["error"]
    assert rows[1]["name"] == "NVIDIA healthy GPU" and rows[1]["temperature_c"] == 47.25
    json.dumps(rows, allow_nan=False)


def test_missing_driver_reports_unavailable_without_raising(monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi is not installed")

    monkeypatch.setattr(observability.subprocess, "run", unavailable)
    rows = observability.ObservabilityManager._gpu()
    assert rows[0]["available"] is False and "FileNotFoundError" in rows[0]["error"]


def test_driver_failure_is_not_a_valid_gpu_measurement(monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(9, "nvidia-smi")

    monkeypatch.setattr(observability.subprocess, "run", failed)
    assert observability.ObservabilityManager._gpu()[0]["available"] is False
