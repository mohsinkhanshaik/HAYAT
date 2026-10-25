"""The libraries HAYAT actually calls at runtime, checked by using them.

Importing proves a wheel landed. It does not prove the package works: a
native extension can import cleanly and fail on its first real call, and on
Windows that is the common shape. So each check does a small real thing.

This covers what HAYAT depends on to function, not everything installed --
a suite that imports a hundred packages spends its time proving pip works.
The full survey lives in data/benchmarks/dependencies/.

The case that made this worth writing: webrtcvad installs, pip lists it as
present, and importing it raises -- setuptools 84 dropped pkg_resources,
which it needs. "pip says it is installed" and "it works" are different
claims, and only the second one matters.
"""
from __future__ import annotations

import importlib

import pytest


def _module(name: str):
    try:
        return importlib.import_module(name)
    except ImportError as exc:  # pragma: no cover - reported, not skipped silently
        pytest.fail(f"{name} is required at runtime but does not import: {exc}")


# ------------------------------------------------------- the runtime spine


@pytest.mark.parametrize(
    "name",
    ["httpx", "yaml", "jsonschema", "numpy", "sqlite3", "prompt_toolkit"],
)
def test_the_core_runtime_libraries_import(name):
    _module(name)


def test_numpy_actually_computes():
    assert float(_module("numpy").array([1, 2, 3]).mean()) == 2.0


def test_yaml_round_trips():
    yaml = _module("yaml")
    assert yaml.safe_load(yaml.safe_dump({"a": 1}))["a"] == 1


def test_jsonschema_validates():
    _module("jsonschema").validate({"a": 1}, {"type": "object"})


# ------------------------------------------------------------- perception


def test_opencv_loads_its_native_extension():
    """cv2 is the case where import and use diverge most often."""
    cv2 = _module("cv2")
    assert cv2.__version__
    import numpy

    assert cv2.cvtColor(numpy.zeros((4, 4, 3), "uint8"), cv2.COLOR_BGR2GRAY).shape == (4, 4)


def test_pillow_can_make_an_image():
    assert _module("PIL.Image").new("RGB", (4, 4)).size == (4, 4)


def test_onnxruntime_reports_providers():
    """Piper speaks through this; a broken ORT means no voice."""
    assert _module("onnxruntime").get_available_providers()


def test_piper_and_whisper_are_importable():
    _module("piper")
    _module("faster_whisper")


# --------------------------------------------------------------- the web


def test_html_parsing_works():
    soup = _module("bs4").BeautifulSoup("<p>hello</p>", "html.parser")
    assert soup.text == "hello"


def test_lxml_native_extension_loads():
    assert _module("lxml.etree").fromstring("<a><b/></a>").tag == "a"


# ------------------------------------------------------------- documents


def test_pdf_and_office_readers_import():
    for name in ("pypdf", "docx", "openpyxl", "pptx"):
        _module(name)


def test_pandas_builds_a_frame():
    assert len(_module("pandas").DataFrame({"a": [1, 2]})) == 2


def test_integration_gateway_dependencies_are_operational():
    assert _module("keyring").get_keyring() is not None
    assert _module("cryptography").__version__
    assert _module("websockets.sync.server").serve
    assert _module("mcp").ClientSession
    assert _module("watchdog.observers").Observer
    assert _module("rapidfuzz.fuzz").ratio("hayat", "hayat") == 100.0


# ------------------------------------------------------------ windows


def test_windows_control_stack_imports():
    import sys

    if sys.platform != "win32":  # pragma: no cover
        pytest.skip("windows only")
    for name in ("win32api", "pywinauto", "pyautogui", "mss"):
        _module(name)


# ------------------------------------------- installed is not the same as working


def test_pkg_resources_absence_is_known_and_not_silently_depended_on():
    """setuptools 84 dropped pkg_resources. Anything importing it is broken
    on this interpreter, and webrtcvad is -- which is why the project's own
    energy-threshold VAD is what actually runs.
    """
    import importlib.util

    if importlib.util.find_spec("pkg_resources") is not None:
        return  # a future setuptools may restore it; nothing to assert
    with pytest.raises(ImportError):
        importlib.import_module("webrtcvad")


def test_voice_does_not_depend_on_the_broken_vad():
    """The config must not point at something that cannot load."""
    from app.config import load_settings

    vad = load_settings()["voice"]["vad"]
    assert vad.get("enabled") is True
    assert "webrtc" not in str(vad).lower(), "the configured VAD must be one that imports"
