"""Free developer keys: catalog, safe .env storage, masking, and real tests."""
import os
from types import SimpleNamespace

import pytest

from app import keys


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("OTHER_SETTING=keep me\nHAYAT_KEY_511_ONTARIO=old-value-1234\n", encoding="utf-8")
    for item in keys.catalog():
        monkeypatch.delenv(item["name"], raising=False)
    yield path
    for item in keys.catalog():
        os.environ.pop(item["name"], None)


def test_the_catalog_names_every_keyed_network_and_where_to_sign_up():
    from app.intelligence.feeds import KEYED_CAMERA_FEEDS
    names = {item["name"] for item in keys.catalog()}
    assert {feed.key_env for feed in KEYED_CAMERA_FEEDS} <= names and "HAYAT_KEY_FINNHUB" in names
    assert all(item["signup"].startswith("https://") and item["unlocks"] for item in keys.catalog())


def test_a_key_is_saved_beside_other_settings_applied_now_and_masked(env):
    state = keys.save("HAYAT_KEY_WSDOT", "abcd-1234-efgh-5678", env)
    text = env.read_text(encoding="utf-8")
    assert "OTHER_SETTING=keep me" in text and "HAYAT_KEY_WSDOT=abcd-1234-efgh-5678" in text
    assert os.environ["HAYAT_KEY_WSDOT"] == "abcd-1234-efgh-5678" and state == "set · ends …5678"
    keys.save("HAYAT_KEY_511_ONTARIO", "new-value-9999", env)
    assert env.read_text(encoding="utf-8").count("HAYAT_KEY_511_ONTARIO") == 1
    status = {item["name"]: item["state"] for item in keys.status(env)}
    assert status["HAYAT_KEY_511_ONTARIO"] == "set · ends …9999" and status["HAYAT_KEY_FINNHUB"] == "not set"
    assert "new-value-9999" not in str(status)
    keys.remove("HAYAT_KEY_WSDOT", env)
    assert "HAYAT_KEY_WSDOT" not in env.read_text(encoding="utf-8") and "HAYAT_KEY_WSDOT" not in os.environ


@pytest.mark.parametrize(("name", "value"), [("HAYAT_KEY_WSDOT", "has space"), ("HAYAT_KEY_WSDOT", ""),
                                             ("HAYAT_KEY_WSDOT", "x\nINJECTED=1"), ("PATH", "abc"), ("HAYAT_KEY_UNKNOWN", "abcdef")])
def test_bad_keys_and_names_are_refused(env, name, value):
    with pytest.raises(ValueError):
        keys.save(name, value, env)
    assert "INJECTED" not in env.read_text(encoding="utf-8")


def test_keys_are_tested_for_real():
    calls = []
    runtime = SimpleNamespace(
        markets=SimpleNamespace(exchange_quote=lambda symbol: {"price": 336.5, "at": "2026-10-11T20:00:00+00:00"}),
        intelligence=SimpleNamespace(refresh=lambda feeds: calls.append(feeds) or {
            "sources": [{"feed": feeds[0], "status": "ok", "count": 2711}]}))
    assert keys.test("HAYAT_KEY_FINNHUB", runtime) == "Works: Apple 336.50 USD on the exchange (20:00 UTC)."
    assert keys.test("HAYAT_KEY_511_ONTARIO", runtime) == "Works: 2,711 cameras connected." and calls == [["cameras_511_ontario"]]
    runtime.intelligence.refresh = lambda feeds: {"sources": [{"feed": feeds[0], "status": "error", "error": "HTTP 401 Unauthorized"}]}
    assert keys.test("HAYAT_KEY_WSDOT", runtime).startswith("Did not work yet: HTTP 401")


def test_the_keys_window_saves_tests_and_clears_the_field(shared_tk, env, monkeypatch):
    from app.gui_keys import KeysWindow
    from app.gui_theme import Palette
    monkeypatch.setattr(keys, "ENV_PATH", env)
    monkeypatch.setattr(keys.save, "__defaults__", (env,))
    monkeypatch.setattr(keys.remove, "__defaults__", (env,))
    monkeypatch.setattr(keys.status, "__defaults__", (env,))
    opened = []

    class GUI(Palette):
        def _open_source(self, url):
            opened.append(url)
    gui = GUI()
    gui.root = shared_tk
    gui.runtime = SimpleNamespace(markets=SimpleNamespace(exchange_quote=lambda symbol: {"price": 1.0, "at": "2026-10-11T20:00:00"}))
    window = KeysWindow(gui)
    try:
        row = window.rows["HAYAT_KEY_FINNHUB"]
        row["value"].set("bad key")
        assert window.save("HAYAT_KEY_FINNHUB") is False and "no spaces" in row["state"].get() and row["value"].get() == ""
        row["value"].set("goodkey12345678")
        assert window.save("HAYAT_KEY_FINNHUB") and row["value"].get() == ""
        for _ in range(100):
            shared_tk.update()
            if "Works" in row["state"].get():
                break
            import time
            time.sleep(.02)
        assert row["state"].get() == "set · ends …5678 · Works: Apple 1.00 USD on the exchange (20:00 UTC)."
        window.remove("HAYAT_KEY_FINNHUB")
        assert row["state"].get() == "not set"
    finally:
        window.destroy()
