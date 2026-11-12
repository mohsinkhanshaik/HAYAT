"""Which public cameras really work: cards, night scenes, DRM, slates, and the choices that follow (camera_health.py)."""
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import cv2
import pytest
from PIL import Image

from app.intelligence import camera_health
from app.intelligence.camera_health import check_cameras, drm, failing, hls_state, label, network_states, report_lines
from app.intelligence.explore import explore_report, explore_request, flat_share, placeholder
from tests.test_intelligence_resources import expanded  # noqa: F401 -- fixture

STILLS = Path(__file__).parent / "fixtures" / "camera-stills"


# ------------------------------------------------------------------ cards versus real pictures, measured on real stills
@pytest.mark.parametrize("name", ["card-caltrans", "card-loading-indiana", "card-difficulties-kingcounty", "card-off-massdot",
                                  "card-lyon", "card-nopower-txdot"])
def test_publisher_cards_are_recognised(name):
    assert placeholder(cv2.imread(str(STILLS / f"{name}.jpg")))


@pytest.mark.parametrize("name", ["night-spain", "night-norway", "night-fog-iceland"])
def test_real_night_pictures_are_not_cards(name):
    # 2026-11-06: these real scenes sat 55-83 % in one band of grey and were refused as "placeholders" before.
    image = cv2.imread(str(STILLS / f"{name}.jpg"))
    assert not placeholder(image) and flat_share(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)) < .7


def test_a_small_picture_is_never_enlarged_into_flat_blocks():
    import random
    noise = random.Random(2)
    small = Image.frombytes("RGB", (40, 30), bytes(noise.randrange(20, 80) for _ in range(40 * 30 * 3)))
    buffer = BytesIO()
    small.save(buffer, "PNG")
    image = cv2.imdecode(__import__("numpy").frombuffer(buffer.getvalue(), "uint8"), cv2.IMREAD_COLOR)
    assert not placeholder(image)


# ------------------------------------------------------------------ streams
MEDIA = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\nseg1.ts\n"
FAIRPLAY = ('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://fps.ezdrm.com/;b634",KEYFORMAT="com.apple.streamingkeydelivery",'
            'KEYFORMATVERSIONS="1"\n#EXTINF:12.0,\nmedia_1.ts\n')


def serve(pages):
    def fetch(url):
        value = pages[url]
        if isinstance(value, Exception):
            raise value
        return value.encode()
    return fetch


def test_hls_state_reads_master_playlists_and_refuses_drm():
    base = "https://cdn.example.gov/cam/"
    assert hls_state(serve({base + "live.m3u8": MEDIA}), base + "live.m3u8") == ("live", "")
    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=284973\nchunklist.m3u8\n"
    assert hls_state(serve({base + "playlist.m3u8": master, base + "chunklist.m3u8": MEDIA}), base + "playlist.m3u8")[0] == "live"
    # Alabama, 2026-11-06: the master is plain, its media playlist carries a FairPlay key.
    assert hls_state(serve({base + "playlist.m3u8": master, base + "chunklist.m3u8": FAIRPLAY}), base + "playlist.m3u8")[0] == "drm"
    assert hls_state(serve({base + "a.m3u8": "#EXTM3U\n#EXT-X-VERSION:3\n"}), base + "a.m3u8")[0] == "empty"
    assert hls_state(serve({base + "a.m3u8": "<html>offline</html>"}), base + "a.m3u8")[0] == "stream_dead"
    assert hls_state(serve({base + "a.m3u8": ConnectionError("502")}), base + "a.m3u8")[0] == "stream_dead"
    assert hls_state(serve({base + "playlist.m3u8": master, base + "chunklist.m3u8": ConnectionError("404")}),
                     base + "playlist.m3u8")[0] == "stream_dead"


def test_ordinary_aes_encryption_is_not_drm():
    assert not drm('METHOD=AES-128,URI="https://cdn.example.gov/key.bin"')
    assert drm('METHOD=SAMPLE-AES,URI="skd://fps.ezdrm.com/;x",KEYFORMAT="com.apple.streamingkeydelivery"')
    assert drm('METHOD=SAMPLE-AES,URI="data:...",KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"')  # Widevine


# ------------------------------------------------------------------ what a check records, and what it changes
def card_bytes(name="card-caltrans"):
    return (STILLS / f"{name}.jpg").read_bytes()


def test_frames_record_working_cards_web_pages_and_gifs(expanded):  # noqa: F811
    london = "cameras_london:JamCams_test"
    first = expanded.camera_frame(london)
    assert first["publisher_card"] is False
    assert expanded.store.camera_health([london])[london]["verdict"] == "working"
    expanded.fetcher.image = card_bytes()
    card = expanded.camera_frame(london, fresh=True)
    assert card["publisher_card"] and "unavailable" in card["basis"]
    record = expanded.store.camera_health([london])[london]
    assert record["verdict"] == "card" and not record["ok"] and failing(record)
    expanded.fetcher.image = b"<html>The camera image you are looking for is temporarily unavailable.</html>"
    with pytest.raises(Exception):
        expanded.camera_frame(london, fresh=True)
    assert expanded.store.camera_health([london])[london]["verdict"] in ("error", "web_page")
    gif = BytesIO()
    import random
    noise = random.Random(5)
    Image.frombytes("RGB", (64, 48), bytes(noise.randrange(256) for _ in range(64 * 48 * 3))).save(gif, "GIF")
    expanded.fetcher.image = gif.getvalue()  # King County labels some GIFs image/jpeg
    frame = expanded.camera_frame(london, fresh=True)
    with Image.open(frame["path"]) as saved:
        assert saved.format == "PNG"


def test_failing_cameras_go_last_and_a_down_network_is_named(expanded):  # noqa: F811
    # Two Tallinn cameras: by distance from the first, it would come first; failing, it comes after the working one.
    first, second = expanded.cameras(feed="cameras_tallinn", limit=2)["cameras"]
    expanded.store.record_camera(first["id"], first["feed"], False, "card")
    expanded.store.record_camera(second["id"], second["feed"], True, "working")
    near = expanded.cameras(feed="cameras_tallinn", near=[first["latitude"], first["longitude"]], radius_km=50, limit=1000)["cameras"]
    ids = [row["id"] for row in near]
    assert ids.index(first["id"]) > ids.index(second["id"])
    labelled = {row["id"]: row for row in near}
    assert labelled[first["id"]]["health"].startswith("publisher card") and labelled[first["id"]]["working"] is False
    assert expanded.broken_cameras([first["id"], second["id"]]) == {first["id"]}
    # Five failures and none working: the whole network is treated as down.
    for index in range(5):
        expanded.store.record_camera(f"cameras_nsw:dead{index}", "cameras_nsw", False, "web_page")
    states = network_states(expanded.store)
    assert states["cameras_nsw"]["down"] and not states[first["feed"]]["down"]
    assert "cameras_nsw:other" in expanded.broken_cameras(["cameras_nsw:other"])


def test_a_failure_is_forgotten_after_six_hours():
    moment = camera_health.now()
    old = {"ok": 0, "verdict": "card", "at": (moment - timedelta(hours=7)).isoformat()}
    recent = {**old, "at": (moment - timedelta(minutes=20)).isoformat()}
    assert not failing(old, moment) and failing(recent, moment)
    assert label(recent, moment) == "publisher card, 20 min ago" and label(None) == ""


def test_a_still_camera_showing_a_card_is_passed_over(expanded, monkeypatch):  # noqa: F811
    groups = expanded._playable_groups("")
    stills = groups["still"]
    assert len(stills) >= 2
    cards = {stills[0]["id"]}
    monkeypatch.setattr(expanded, "camera_frame",
                        lambda event_id, fresh=False: {"publisher_card": event_id in cards})
    assert not expanded._still_works(stills[0]["id"]) and expanded._still_works(stills[1]["id"])


def test_the_check_tests_each_network_and_reports_plainly(expanded):  # noqa: F811
    value = check_cameras(expanded, feed="cameras_london", sample=2, streams=1, decode=False)
    assert [item["feed"] for item in value["networks"]] == ["cameras_london"]
    assert value["checked"] >= 1 and value["working"] == value["checked"]
    text = explore_report(value)
    assert text.startswith("Camera check:") and "✓ Transport for London" in text and "Source:" in text
    expanded.fetcher.image = card_bytes("card-lyon")
    family = check_cameras(expanded, feed="cameras_caltrans", sample=1, streams=0, decode=False)
    assert {item["feed"] for item in family["networks"]} >= {"cameras_caltrans_1", "cameras_caltrans_7"}
    assert all(item["state"] == "down" for item in family["networks"] if item["checked"])
    assert "publisher's 'unavailable' card" in "\n".join(report_lines(family))
    named = check_cameras(expanded, query="London", sample=1, streams=0, decode=False)
    assert [item["feed"] for item in named["networks"]] == ["cameras_london"]  # the network named for the place
    with pytest.raises(ValueError, match="No connected camera network"):
        check_cameras(expanded, feed="cameras_nowhere")


@pytest.mark.parametrize("text,arguments", [
    ("test the cameras", {}), ("test all the public cameras", {}), ("are the cameras actually working?", {}),
    ("test cameras in Texas", {"query": "Texas"}), ("are the cameras in Delaware working", {"query": "Delaware"}),
    ("which cameras are broken?", {}),
])
def test_asking_whether_the_cameras_work(text, arguments):
    assert explore_request(text) == {"tool": "intelligence_camera_check", "arguments": arguments}


@pytest.mark.parametrize("text", ["test the camera", "check the camera", "is the camera working"])
def test_the_owners_own_camera_is_not_the_public_camera_check(text):
    assert (explore_request(text) or {}).get("tool") != "intelligence_camera_check"


def test_checking_cameras_in_a_place_still_looks_at_them():
    # "Check cameras in Alabama" asked the camera look what the cameras show before the health check existed.
    from app.intelligence.tools import direct_plan
    names = ["intelligence_camera_check", "intelligence_camera_look", "intelligence_cameras", "intelligence_camera_play"]
    assert direct_plan("check cameras in Alabama", names)["tool"] == "intelligence_camera_look"
    assert direct_plan("test the cameras in Alabama", names)["tool"] == "intelligence_camera_check"


# ------------------------------------------------------------------ the Global Intelligence window
from tests.test_gui_intelligence import explorer, pump_until  # noqa: E402,F401 -- fixture


def test_explore_buttons_are_grouped_once_each():
    from app.gui_intelligence_explore import ACTIONS, GROUPS
    grouped = [label for _heading, labels in GROUPS for label in labels]
    assert sorted(grouped) == sorted(label for label, _tool, _build in ACTIONS) and len(grouped) == len(set(grouped))


def test_the_cameras_tab_tests_cameras_and_shows_what_was_checked(explorer):  # noqa: F811
    from tests.test_intelligence_resources import ExtendedFetcher
    window, _gui = explorer
    window.manager.fetcher = ExtendedFetcher()
    window.manager.refresh(['cameras_london'])
    window.layer.set('cameras_london')
    window.notebook.select(window.tabs['cameras'])
    pump_until(window, lambda: not window._busy and window.camera_rows)
    identifier = 'cameras_london:JamCams_test'
    assert window.camera_tree.set(identifier, 'checked') == 'not checked yet'
    assert window.camera_buttons['Test cameras'].winfo_exists()
    window.test_cameras()
    pump_until(window, lambda: not window._busy and 'Camera check:' in window.camera_message.get())
    pump_until(window, lambda: not window._busy and window.camera_tree.set(identifier, 'checked').startswith('✓ working'))
    assert 'Every sampled network worked' in window.camera_message.get()
    # A failed check: the camera is labelled, and "Working only" leaves it out.
    window.manager.store.record_camera(identifier, 'cameras_london', False, 'card')
    window.camera_working.set(True)
    window.camera_page(0)
    pump_until(window, lambda: not window._busy and identifier not in window.camera_rows)
    window.camera_working.set(False)
    window.camera_page(0)
    pump_until(window, lambda: not window._busy and identifier in window.camera_rows)
    assert window.camera_tree.set(identifier, 'checked').startswith('✗ publisher card')
    # The Sources tab sums each network's checks; a new check alone is enough to redraw it.
    window.render(force=True)
    assert window.source_tree.set('cameras_london', 'cameras') == '1/2 working'  # its clip still worked in the check
    window.manager.store.record_camera(identifier + '#stream', 'cameras_london', False, 'stream_dead')
    window.render()
    assert window.source_tree.set('cameras_london', 'cameras') == '0/2 working'
    assert window.source_tree.heading('#0', 'text') == 'Source'


def test_the_atlas_marks_cameras_whose_last_check_failed(expanded):  # noqa: F811
    identifier = "cameras_london:JamCams_test"
    flags = {point[0]: point[5] for point in expanded.points()["points"]}
    assert "b" not in flags[identifier]
    expanded.store.record_camera(identifier, "cameras_london", False, "card")
    flags = {point[0]: point[5] for point in expanded.points()["points"]}
    assert flags[identifier].endswith("b")
    script = (Path(__file__).parents[1] / "app/assets/intelligence-map/map.js").read_text(encoding="utf-8")
    assert "row.flags.includes('b')" in script and "last check failed" in script
