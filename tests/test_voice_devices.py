from types import SimpleNamespace

from app.voice.devices import DeviceManager


class FakeSoundDevice:
    default = SimpleNamespace(device=(0, 1))

    @staticmethod
    def query_hostapis():
        return [{"name": "Test API"}]

    @staticmethod
    def query_devices():
        return [
            {"name": "Test microphone", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0, "default_samplerate": 16000},
            {"name": "Test speaker", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 48000},
        ]


def test_device_discovery_and_selection(monkeypatch):
    devices = DeviceManager()
    monkeypatch.setattr(devices, "_sounddevice", lambda: FakeSoundDevice)
    result = devices.list_devices()
    assert result["microphones"][0]["is_default"]
    assert result["speakers"][0]["is_default"]
    assert devices.resolve("microphone", "input") == 0
    assert devices.resolve(1, "output") == 1


def test_empty_input_inventory_is_reported_as_unavailable(monkeypatch):
    class OutputOnly(FakeSoundDevice):
        default = SimpleNamespace(device=(-1, 1))

        @staticmethod
        def query_devices():
            return [
                {"name": "Test speaker", "hostapi": 0, "max_input_channels": 0,
                 "max_output_channels": 2, "default_samplerate": 48000},
            ]

    devices = DeviceManager()
    monkeypatch.setattr(devices, "_sounddevice", lambda: OutputOnly)
    result = devices.diagnose_input(include_windows=False)
    assert result["available"] is False
    assert result["default_input"] == -1
    assert "no recording endpoint" in result["reason"].lower()
