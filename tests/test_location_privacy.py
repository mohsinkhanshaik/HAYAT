"""Location privacy: HAYAT reads the owner's location; nothing it sends gives it away; the owner sees who else can.

Windows settings, browser profiles and the public-IP service are stand-ins; the photos and the place
list are real files made in tmp_path.
"""
import hashlib
import io
import json
import sqlite3
from contextlib import closing
import tarfile
from pathlib import Path

import pytest

from app import location_privacy as lp
from app.location_privacy import LocationPrivacy, LocationShield, PlaceIndex, privacy_request

HOME = (42.4734, -83.2219)  # a fix the tests pretend Windows gave
PLACES = ("lat,lon,name,admin1,admin2,cc\n42.47337,-83.22187,Southfield,Michigan,Oakland County,US\n"
          "39.80172,-89.64371,Springfield,Illinois,Sangamon County,US\n51.50853,-0.12574,London,England,,GB\n")


def places(tmp_path) -> PlaceIndex:
    path = tmp_path / "places.csv"
    path.write_text(PLACES, encoding="utf-8")
    return PlaceIndex(path)


def photo(tmp_path, name="trip.jpg", gps=True) -> Path:
    from PIL import Image

    image = Image.new("RGB", (32, 24), (200, 120, 40))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    if gps:
        exif[0x8825] = {1: "N", 2: (42.0, 28.0, 24.2), 3: "W", 4: (83.0, 13.0, 18.8)}
    path = tmp_path / name
    image.save(path, exif=exif.tobytes())
    return path


# -- place names on this computer ----------------------------------------------------------

def test_the_place_is_named_on_this_computer(tmp_path):
    found = places(tmp_path).nearest(*HOME)
    assert found["label"] == "Southfield, Michigan, United States" and found["county"] == "Oakland County"
    assert found["distance_km"] < 1 and "nothing was sent" in found["source"]


def test_a_place_far_from_any_listed_town_says_near(tmp_path):
    found = places(tmp_path).nearest(41.0, -85.0)
    assert found["label"].startswith("near Southfield") and "km)" in found["label"]


def test_installing_keeps_only_a_file_whose_hash_matches(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        data = PLACES.encode()
        info = tarfile.TarInfo(lp.PLACES_MEMBER)
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
        evil = tarfile.TarInfo("../../escape.txt")  # never extracted: only the places list is read, by name
        evil.size = 3
        archive.addfile(evil, io.BytesIO(b"bad"))
    package = buffer.getvalue()
    monkeypatch.setattr(lp, "PLACES_SOURCE", ("https://files.pythonhosted.org/x.tar.gz", hashlib.sha256(package).hexdigest()))
    index = PlaceIndex(tmp_path / "places" / "places.csv")
    assert index.install(lambda url: b"tampered")["installed"] is False and not index.available()
    result = index.install(lambda url: package)
    assert result["installed"] and result["places"] == 3 and index.nearest(51.5, -0.1)["city"] == "London"
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path.parent / "escape.txt").exists()
    assert index.install(lambda url: b"unused")["status"] == "already installed"


def test_the_pinned_source_is_pypi():
    url, digest = lp.PLACES_SOURCE
    assert url.startswith("https://files.pythonhosted.org/") and len(digest) == 64


# -- photos ------------------------------------------------------------------------------------

def test_gps_tags_are_read_and_removed_from_a_copy(tmp_path):
    original = photo(tmp_path)
    lat, lon = lp.photo_gps(original)
    assert lat == pytest.approx(42.4734, abs=1e-3) and lon == pytest.approx(-83.2219, abs=1e-3)
    clean = lp.strip_gps(original, tmp_path / "clean")
    assert lp.photo_gps(clean) is None and lp.photo_gps(original)  # the original is untouched
    from PIL import Image

    with Image.open(clean) as image:
        assert image.getexif().get(0x010F) == "PhoneMaker"  # other details kept
    assert lp.photo_gps(photo(tmp_path, "plain.jpg", gps=False)) is None


# -- the guard ---------------------------------------------------------------------------------

def shield_at_home() -> LocationShield:
    shield = LocationShield()
    shield.remember_fix(*HOME)
    return shield


@pytest.mark.parametrize("body", ["I'm at 42.47341, -83.22190, come over",
                                  "https://www.google.com/maps/?q=42.4735,-83.2218",
                                  "meet at 42.4801 -83.2150 tonight"])
def test_a_message_with_the_owners_coordinates_is_held(body):
    arguments, held, _ = shield_at_home().screen("email_send", "send_message", {"to": "a@b.com", "body": body})
    assert held and "precise coordinates" in held and "share my location once" in held


def test_other_coordinates_and_city_names_pass():
    shield = shield_at_home()
    for body in ("The Eiffel Tower is at 48.8584, 2.2945", "I live in Southfield", "Version 1.234, 5.678 is out"):
        assert shield.screen("email_send", "send_message", {"body": body})[1] is None


def test_without_a_fix_nothing_is_held():
    assert LocationShield().screen("email_send", "send_message", {"body": "42.47341, -83.22190"})[1] is None


def test_sharing_once_allows_one_send_then_the_guard_returns():
    shield = shield_at_home()
    message = {"body": "pick me up at 42.47341, -83.22190"}
    shield.allow_once(5)
    assert shield.screen("whatsapp_send", "send_message", message)[1] is None
    assert shield.screen("whatsapp_send", "send_message", message)[1]
    assert [event["action"] for event in shield.events] == ["allowed", "held"]


def test_a_web_search_with_the_owners_coordinates_is_held_too():
    held = shield_at_home().screen("web_search", "web_search", {"query": "restaurants near 42.4734,-83.2219"})[1]
    assert held and "to a website" in held


def test_a_public_lookup_near_the_owner_gets_a_rounded_position():
    arguments, held, notes = shield_at_home().screen("intelligence_weather", "public_web_read",
                                                     {"lat": 42.47341, "lon": -83.2219})
    assert held is None and arguments == {"lat": 42.5, "lon": -83.2} and "11 km" in notes[0]
    far, _, notes = shield_at_home().screen("intelligence_weather", "public_web_read", {"lat": 35.68, "lon": 139.69})
    assert far == {"lat": 35.68, "lon": 139.69} and not notes


def test_photos_attached_to_a_message_are_sent_without_gps(tmp_path):
    original = photo(tmp_path)
    plain = photo(tmp_path, "plain.jpg", gps=False)
    arguments, held, notes = LocationShield().screen("email_send", "send_message",
                                                     {"body": "pics", "attachments": [str(original), str(plain)]})
    assert held is None and arguments["attachments"][1] == str(plain)
    assert arguments["attachments"][0] != str(original) and lp.photo_gps(arguments["attachments"][0]) is None
    assert notes == ["GPS location removed from trip.jpg before sending."]


def test_local_tools_are_not_screened():
    arguments = {"body": "42.47341, -83.22190"}
    assert shield_at_home().screen("memory_save", "filesystem_write_project", arguments) == (arguments, None, [])


def test_the_registry_holds_a_send_and_notes_a_stripped_photo(tmp_path):
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(PermissionManager({"default": "ALLOW"}, tmp_path))
    sent = []
    registry.register("fake_send", "send", {"type": "object", "properties": {"body": {"type": "string"},
                                                                           "attachments": {"type": "array"}}},
                      "low", "filesystem_read", lambda body, attachments=None: sent.append((body, attachments)) or {"sent": True})
    registry.get("fake_send").permission = "send_message"
    registry.location_shield = shield_at_home()
    held = registry.execute("fake_send", {"body": "I'm at 42.47341, -83.22190"})
    assert not held.success and held.permission == "LOCATION_PRIVACY" and not sent
    done = registry.execute("fake_send", {"body": "hello", "attachments": [str(photo(tmp_path))]})
    assert done.success and done.output["location_privacy"] == ["GPS location removed from trip.jpg before sending."]
    assert lp.photo_gps(sent[0][1][0]) is None


# -- who can learn the location ---------------------------------------------------------------

WINDOWS = {"value": "Allow",
           "apps": {"Microsoft.WindowsMaps_8wekyb3d8bbwe": {"Value": "Allow", "LastUsedTimeStop": 0},
                    "5319275A.WhatsAppDesktop_cv1g1gvanyjgm": {"Value": "Prompt"}},
           "desktop": {"C:#Program Files#Adobe#Acrobat#AcroCEF.exe": {"LastUsedTimeStop": 134367192406116848}}}


def test_hayats_own_reads_are_named_as_hayat_not_flagged(tmp_path):
    windows = {"value": "Allow", "apps": {}, "desktop": {
        "C:#Windows#System32#WindowsPowerShell#v1.0#powershell.exe": {"LastUsedTimeStop": 134367192406116848},
        "C:#Windows#System32#netsh.exe": {"LastUsedTimeStop": 134367192406116848}}}
    report = privacy(tmp_path, apps=lambda: lp.windows_location_apps(lambda base: windows)).check(scan_photos=False)
    texts = [row["what"] for row in report["findings"]]
    assert not any("these apps read your location" in text for text in texts)
    assert any(text.startswith("HAYAT's own reads show up as powershell.exe") for text in texts)


def test_windows_apps_that_used_the_location_are_listed_with_when():
    result = lp.windows_location_apps(lambda base: WINDOWS)
    assert result["service"] == "Allow" and [row["app"] for row in result["used"]] == ["AcroCEF.exe"]
    assert result["used"][0]["last_used"].startswith("2026-10-")
    assert {row["app"] for row in result["allowed"]} == {"WindowsMaps", "AcroCEF.exe"}


def test_browser_sites_allowed_the_location_are_found(tmp_path):
    local, roaming = tmp_path / "local", tmp_path / "roaming"
    edge = local / "Microsoft/Edge/User Data/Default"
    edge.mkdir(parents=True)
    (edge / "Preferences").write_text(json.dumps({"profile": {"content_settings": {"exceptions": {"geolocation": {
        "https://www.loopnet.com:443,*": {"setting": 1}, "https://blocked.example:443,*": {"setting": 2}}}}}}))
    firefox = roaming / "Mozilla/Firefox/Profiles/abc.default"
    firefox.mkdir(parents=True)
    with closing(sqlite3.connect(lp.Path(firefox / "permissions.sqlite"))) as db:
        db.execute("CREATE TABLE moz_perms (origin TEXT, type TEXT, permission INTEGER)")
        db.execute("INSERT INTO moz_perms VALUES ('https://maps.example', 'geo', 1)")
        db.commit()
    sites = lp.browser_sites(local, roaming)
    assert {(row["browser"], row["site"], row["allowed"]) for row in sites} == {
        ("Edge", "https://www.loopnet.com", True), ("Edge", "https://blocked.example", False),
        ("Firefox", "https://maps.example", True)}


def privacy(tmp_path, **options) -> LocationPrivacy:
    shield = shield_at_home()
    defaults = dict(places=places(tmp_path), apps=lambda: lp.windows_location_apps(lambda base: WINDOWS),
                    sites=lambda: [{"browser": "Chrome", "profile": "Default", "site": "https://chatgpt.com", "allowed": True}],
                    photo_folders=[tmp_path],
                    get_json=lambda url: {"success": True, "city": "Detroit", "region": "Michigan", "country": "United States",
                                          "latitude": 42.33, "longitude": -83.05, "connection": {"isp": "T-Mobile"}})
    defaults.update(options)
    return LocationPrivacy(shield, **defaults)


def test_the_check_lists_every_way_the_location_gets_out(tmp_path):
    photo(tmp_path)
    report = privacy(tmp_path).check()
    text = " ".join(row["what"] for row in report["findings"])
    assert "these apps read your location: AcroCEF.exe (" in text and "chatgpt.com" in text
    assert "near Detroit, Michigan, United States (21 km from you), through T-Mobile" in text
    assert "1 photos in your folders carry GPS tags" in text
    # The owner keeps every Wi-Fi name as it is (2026-10-17): no rename advice, the network name is not even read.
    everything = json.dumps(report)
    assert "_nomap" not in everything and "Rename" not in everything and "wifi" not in report
    assert any(row.get("action") == "masks" and "masked browser" in row["fix"] for row in report["findings"])
    assert report["protections"][0].startswith("Place names come from GeoNames on this computer")


def test_privacy_mode_asks_no_website(tmp_path):
    asked = []
    report = privacy(tmp_path, get_json=lambda url: asked.append(url), privacy=lambda: True).check(scan_photos=False)
    assert not asked and report["public_ip"]["checked"] is False


# -- device location uses the local names and arms the guard ------------------------------------

def test_device_location_names_the_place_locally_and_sends_nothing(tmp_path, monkeypatch):
    from app.tools.devices import DeviceInspector

    monkeypatch.setattr("app.tools.devices.os.name", "nt")
    urls = []
    inspector = DeviceInspector(get_json=lambda url: urls.append(url), places=places(tmp_path),
                                run=lambda script, timeout: json.dumps({"status": "Ready", "permission": "Granted",
                                                                        "unknown": False, "latitude": HOME[0],
                                                                        "longitude": HOME[1], "accuracy_m": 20}))
    result = inspector.device_location()
    assert result["place"]["label"] == "Southfield, Michigan, United States" and urls == []
    held = lp.SHIELD.screen("email_send", "send_message", {"body": f"at {HOME[0]}, {HOME[1]}"})[1]
    assert held  # the fix reached the guard, in memory
    missing = DeviceInspector(get_json=lambda url: urls.append(url), places=PlaceIndex(tmp_path / "none.csv"),
                              run=inspector.run).device_location()
    assert "place" not in missing and "set up private place names" in missing["place_note"] and urls == []


# -- asking for it --------------------------------------------------------------------------------

@pytest.mark.parametrize("text, tool", [
    ("Who can see my location?", "location_privacy_check"), ("Can anyone track my location?", "location_privacy_check"),
    ("which apps use my location", "location_privacy_check"), ("hide my location", "location_privacy_check"),
    ("Set up private place names", "location_places_setup"), ("share my location once", "location_share_once"),
    ('Remove the GPS data from "C:/Photos/IMG 1.JPG"', "photo_location_strip"),
])
def test_requests_reach_the_privacy_tools(text, tool):
    assert privacy_request(text)["tool"] == tool


@pytest.mark.parametrize("text", ["where am I", "what is my location", "send my location to Ahmed", "where is the location of the meeting"])
def test_reading_the_location_stays_with_the_location_tool(text):
    assert privacy_request(text) is None


def test_the_assistant_answers_who_can_see_my_location(monkeypatch):
    from app.runtime import build_runtime

    monkeypatch.setattr(lp.LocationPrivacy, "public_ip_view", lambda self: {"checked": False, "reason": "test"})
    monkeypatch.setattr(lp, "photos_with_gps", lambda folders, limit=600: {"scanned": 0, "limit": limit,
                                                                          "with_location": [], "count": 0, "folders": []})
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        assert runtime.registry.location_shield is lp.SHIELD
        assert {"location_privacy_check", "location_places_setup", "location_share_once",
                "photo_location_strip"} <= set(runtime.registry.names())
        reply = runtime.assistant.handle("who can see my location?").display_response
        assert "Who can learn where you are" in reply and "What HAYAT does to keep it private" in reply
    finally:
        runtime.close()


# -- opening the page that fixes a finding ----------------------------------------------------------

def test_the_windows_page_and_a_browsers_page_open_and_nothing_is_changed():
    launched = []
    result = lp.open_setting("windows", launch=launched.append)
    assert result["opened"] and launched == ["ms-settings:privacy-location"] and "does not change it" in result["note"]
    lp.open_setting("edge", launch=launched.append, find=lambda exe: r"C:\Edge\msedge.exe")
    assert launched[-1] == [r"C:\Edge\msedge.exe", "edge://settings/content/location"]
    missing = lp.open_setting("brave", launch=launched.append, find=lambda exe: None)
    assert missing["opened"] is False and "brave://settings/content/location" in missing["note"] and len(launched) == 2
    with pytest.raises(ValueError):
        lp.open_setting("registry", launch=launched.append)


def test_findings_name_the_page_that_fixes_them(tmp_path):
    report = privacy(tmp_path).check(scan_photos=False)
    actions = {row["what"][:30]: row.get("action") for row in report["findings"]}
    assert "windows" in actions.values() and "chrome" in actions.values()


@pytest.mark.parametrize("text, target", [("open my location settings", "windows"), ("Open Edge location settings", "edge"),
                                          ("open chrome's location permissions", "chrome")])
def test_asking_to_open_the_location_settings(text, target):
    assert privacy_request(text) == {"tool": "location_open_setting", "arguments": {"target": target}}


def test_the_assistant_opens_the_windows_location_page(monkeypatch):
    from app.runtime import build_runtime

    opened = []
    monkeypatch.setattr(lp, "open_setting", lambda target: opened.append(target) or {
        "opened": True, "target": target, "page": "ms-settings:privacy-location", "note": "Opened."})
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        runtime.assistant.handle("open my location settings")
        assert opened == ["windows"]
    finally:
        runtime.close()
