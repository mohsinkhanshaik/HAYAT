"""The owner's own cameras: exact addresses, logins only in the credential store, played only when registered."""
import json
from types import SimpleNamespace

import pytest

from app.intelligence.my_cameras import MyCameras, check_url, kind_of, my_camera_report, my_camera_request, registered


class Secrets:
    """The SecretBroker calls MyCameras uses, kept in memory."""

    def __init__(self):
        self.values, self.leases = {}, {}

    def store(self, name, value, *, scopes):
        self.values[name] = (value, tuple(scopes))

    def issue_lease(self, name, scope, *, ttl_seconds=30):
        assert scope in self.values[name][1] and ttl_seconds <= 30
        token = f"lease-{len(self.leases)}"
        self.leases[token] = name
        return token

    def redeem(self, token, scope):
        return self.values[self.leases.pop(token)][0]

    def delete(self, name):
        return self.values.pop(name, None) is not None


@pytest.fixture
def cameras(tmp_path):
    return MyCameras(tmp_path / "my-cameras.json", Secrets())


def test_addresses_are_exact_and_carry_no_login():
    assert check_url("rtsp://192.168.1.20:554/stream1") == "rtsp://192.168.1.20:554/stream1"
    for bad, reason in (("rtsp://admin:hunter2@192.168.1.20/stream", "login"), ("ftp://cam/x", "rtsp://"),
                        ("192.168.1.20", "rtsp://"), ("rtsp://cam:99999/x", "valid"), ("", "full stream address")):
        with pytest.raises(ValueError, match=reason):
            check_url(bad)
    assert kind_of("rtsp://h/s") == "RTSP stream" and kind_of("http://h/live.m3u8") == "HLS stream"
    assert kind_of("http://h/video.mjpg") == "MJPEG stream" and kind_of("http://h/snapshot.jpg") == "Snapshot"


def test_a_login_goes_to_the_credential_store_never_to_the_file_or_results(cameras):
    added = cameras.add("Front door", "rtsp://192.168.1.20:554/stream1", 51.5, -0.12, "admin", "hunter2")
    assert added["added"]["id"] == "mine:front-door" and added["added"]["login"] is True
    stored = cameras.path.read_text(encoding="utf-8")
    assert "hunter2" not in stored and "admin" not in stored
    listing = cameras.list()
    assert listing["count"] == 1 and "hunter2" not in json.dumps(listing)
    played = cameras.play("front door")
    assert played["video_url"] == "rtsp://192.168.1.20:554/stream1" and played["feed"] == "my_cameras" and played["login"]
    assert "hunter2" not in json.dumps(played) and played["tool"] == "my_camera_play"
    assert cameras.login("mine:front-door") == ("admin", "hunter2")
    assert not cameras.secrets.leases  # each lease is used once
    cameras.remove("mine:front-door")
    assert cameras.secrets.values == {} and cameras.list()["count"] == 0


def test_registration_rules(cameras, tmp_path):
    cameras.add("Driveway", "http://192.168.1.30/video.mjpg")
    with pytest.raises(ValueError, match="already registered"):
        cameras.add("Driveway", "http://192.168.1.31/video.mjpg")
    with pytest.raises(ValueError, match="address is already registered"):
        cameras.add("Garage", "http://192.168.1.30/video.mjpg")
    with pytest.raises(ValueError, match="both latitude and longitude"):
        cameras.add("Shop", "rtsp://10.0.0.5/s", latitude=51.5)
    with pytest.raises(ValueError, match="credential store"):
        MyCameras(tmp_path / "other.json", None).add("Back", "rtsp://10.0.0.6/s", username="u", password="p")
    with pytest.raises(ValueError, match="not one of your registered cameras"):
        cameras.play("kitchen")
    with pytest.raises(ValueError, match="No registered camera"):
        cameras.remove("mine:kitchen")
    assert cameras.login("mine:driveway") == ("", "")


def test_partial_camera_name_cannot_silently_choose_between_two_owned_cameras(cameras):
    cameras.add('Front door', 'rtsp://192.168.1.20/s')
    cameras.add('Front shop', 'rtsp://192.168.1.21/s')
    with pytest.raises(ValueError, match='More than one'):
        cameras.play('front')
    assert cameras.play('Front door')['event_id'] == 'mine:front-door'


def test_the_player_opens_only_registered_addresses(cameras):
    cameras.add("Front door", "rtsp://192.168.1.20:554/stream1")
    assert registered("rtsp://192.168.1.20:554/stream1", cameras.path) == "rtsp://192.168.1.20:554/stream1"
    assert registered("rtsp://192.168.1.21:554/stream1", cameras.path) == ""
    assert registered("rtsp://192.168.1.20:554/stream1?x=1", cameras.path) == ""


def test_the_worker_plays_an_own_camera_with_its_login_as_player_options(cameras, monkeypatch):
    from app import video_player_worker as worker
    from app.intelligence import my_cameras as module
    from tests.test_camera_chat import FakeVlc, run_worker
    cameras.add("Front door", "rtsp://192.168.1.20:554/stream1")
    monkeypatch.setattr(module, "DEFAULT_REGISTRY", cameras.path)
    monkeypatch.setattr(module.registered, "__defaults__", (cameras.path,))
    vlc = FakeVlc()
    options = []
    instance = vlc.Instance

    def make_instance(*flags):
        made = instance(*flags)
        made.media_new_location = lambda url: SimpleNamespace(url=url, options=options, add_option=options.append)
        return made
    vlc.Instance = make_instance

    class Stdin:
        def __init__(self):
            self.sent = False

        def readline(self, _limit=-1):
            return json.dumps({"camera_url": "rtsp://192.168.1.20:554/stream1", "feed": "my_cameras", "headless": True,
                               "username": "admin", "password": "hunter2"}) + "\n"

        def __iter__(self):
            return iter([json.dumps({"action": "close"}) + "\n"])
    messages = []
    monkeypatch.setattr(worker, "load_vlc", lambda: vlc)
    monkeypatch.setattr(worker, "emit", messages.append)
    monkeypatch.setattr(worker.sys, "stdin", Stdin())
    worker.main()
    assert ":rtsp-user=admin" in options and ":rtsp-pwd=hunter2" in options and ":rtsp-tcp" in options
    assert not any("hunter2" in json.dumps(message) for message in messages)
    with pytest.raises(ValueError, match="not one of your registered cameras"):
        run_worker(monkeypatch, "rtsp://192.168.1.99/stream", "my_cameras", FakeVlc())


@pytest.mark.parametrize(("text", "expected"), [
    ("show my cameras", {"tool": "my_camera_list", "arguments": {}}),
    ("list my own cams", {"tool": "my_camera_list", "arguments": {}}),
    ("play my front door camera", {"tool": "my_camera_play", "arguments": {"event_id": "front door"}}),
    ("hayat, show me my driveway cam", {"tool": "my_camera_play", "arguments": {"event_id": "driveway"}}),
    ("play the second camera", None), ("show cameras in London", None)])
def test_chat_sentences(text, expected):
    assert my_camera_request(text) == expected


def test_direct_plan_sends_own_cameras_to_the_own_camera_tools_first():
    from app.intelligence.tools import ROUTED_NAMES, direct_plan
    assert direct_plan("play my front door camera", ROUTED_NAMES)["tool"] == "my_camera_play"
    assert direct_plan("find cameras in London", ROUTED_NAMES)["tool"] == "intelligence_cameras"


def test_replies(cameras):
    assert "no registered cameras" in my_camera_report("my_camera_list", cameras.list())
    cameras.add("Front door", "rtsp://192.168.1.20/s", username="a", password="b")
    text = my_camera_report("my_camera_list", cameras.list())
    assert "Front door · RTSP stream · 192.168.1.20 · login stored" in text and "b" not in text.split("login stored")[1][:3]
    assert 'play my front door camera' in my_camera_report("my_camera_add", {"added": cameras.list()["cameras"][0]})


def test_the_my_cameras_window_adds_plays_and_removes(shared_tk, cameras, monkeypatch):
    from app import gui_video_player
    from app.gui_my_cameras import MyCamerasWindow
    from app.gui_theme import Palette

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root = shared_tk
    gui.runtime = SimpleNamespace(intelligence=SimpleNamespace(my_cameras=cameras))
    played = []
    monkeypatch.setattr(gui_video_player, "open_camera_window", lambda parent, gui, camera: played.append(camera) or ("window", "card"))
    window = MyCamerasWindow(gui)
    try:
        window.fields["name"].set("Front door")
        window.fields["url"].set("rtsp://admin:pw@192.168.1.20/s")
        assert window.add() is False and "login out of the address" in window.status.get()
        window.fields["url"].set("rtsp://192.168.1.20/s")
        window.fields["username"].set(" admin ")
        window.fields["password"].set("  hunter2  ")
        assert window.add() and window.fields["password"].get() == "" and window.tree.get_children() == ("mine:front-door",)
        assert cameras.login('mine:front-door') == (' admin ', '  hunter2  ')
        assert window.tree.item("mine:front-door", "values")[2] == "stored safely"
        window.tree.selection_set("mine:front-door")
        window.play()
        assert played[0]["event_id"] == "mine:front-door" and "hunter2" not in json.dumps(played[0])
        assert window.remove() and window.tree.get_children() == ()
    finally:
        window.destroy()


def test_own_camera_filter_refresh_preserves_the_selected_camera_and_scroll(shared_tk, cameras):
    from app.gui_my_cameras import MyCamerasWindow
    from app.gui_theme import Palette
    for index in range(40):
        cameras.add(f'Shop {index}', f'rtsp://192.168.1.{index + 20}/stream')
    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(intelligence=SimpleNamespace(my_cameras=cameras))
    window = MyCamerasWindow(gui)
    try:
        window.update()
        assert all(button.winfo_ismapped() for button in window.action_buttons.values())
        window.tree.selection_set('mine:shop-20')
        window.tree.yview_moveto(.4)
        position = window.tree.yview()[0]
        window.refresh()
        window.update()
        assert window.tree.selection() == ('mine:shop-20',)
        assert window.tree.yview()[0] == pytest.approx(position)
        window.query.set('192.168.1.40')
        assert window.tree.get_children() == ('mine:shop-20',)
        window.query.set('not a camera')
        assert window.tree.get_children() == ()
    finally:
        window.destroy()
