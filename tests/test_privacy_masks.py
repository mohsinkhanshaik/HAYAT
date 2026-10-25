"""Masks: a VPN checked for leaks, Tor for HAYAT's own web reading, and the location guard.

Windows networking, the Tor process and the internet are stand-ins; the archive is a real tar.gz made here.
"""
import hashlib
import io
import json
import tarfile

import httpx
import pytest

from app import privacy_masks as pm
from app.privacy_masks import PrivacyMasks, TorMask, leak_checks, masks_request, network_view, vpn_state

WIFI = {"name": "Wi-Fi", "description": "MediaTek Wi-Fi 6E MT7922"}
PROTON = {"name": "ProtonVPN", "description": "WireGuard Tunnel"}


def view(adapters, route, dns, ipv6=()):
    return {"available": True, "adapters": adapters, "default_route": route, "dns": dns, "ipv6_public": list(ipv6)}


# -- the computer's own connection ---------------------------------------------------------------

def test_windows_networking_is_read_whether_powershell_gives_one_item_or_many():
    single = json.dumps({"adapters": {"Name": "Wi-Fi", "InterfaceDescription": "MediaTek"}, "route": {"InterfaceAlias": "Wi-Fi"},
                         "dns": {"alias": "Wi-Fi", "servers": "192.168.12.1"}, "ipv6": "Wi-Fi"})
    result = network_view(lambda script, timeout: single)
    assert result["adapters"] == [{"name": "Wi-Fi", "description": "MediaTek"}] and result["default_route"] == "Wi-Fi"
    assert result["dns"] == {"Wi-Fi": ["192.168.12.1"]} and result["ipv6_public"] == ["Wi-Fi"]
    assert network_view(lambda script, timeout: "not json")["available"] is False


def test_without_a_vpn_the_answer_is_hayats_own_masks_not_a_vpn_app():
    state = vpn_state(view([WIFI], "Wi-Fi", {"Wi-Fi": ["192.168.12.1"]}))
    assert state["connected"] is False
    found = leak_checks(view([WIFI], "Wi-Fi", {}), state)[0]
    assert "your own internet address" in found["what"] and "masked browser" in found["fix"]
    assert "VPN" not in found["fix"]  # the owner uses no third-party VPN app (2026-10-17)


def test_a_vpn_that_carries_everything_without_leaks_is_ok():
    network = view([WIFI, PROTON], "ProtonVPN", {"ProtonVPN": ["10.2.0.1"]})
    state = vpn_state(network)
    assert state["connected"] and state["routes_traffic"]
    assert [row["level"] for row in leak_checks(network, state)] == ["ok"]


def test_dns_and_ipv6_leaks_around_a_vpn_are_found():
    network = view([WIFI, PROTON], "ProtonVPN", {"ProtonVPN": ["10.2.0.1"], "Wi-Fi": ["192.168.12.1"]}, ipv6=["Wi-Fi"])
    found = leak_checks(network, vpn_state(network))
    texts = " ".join(row["what"] for row in found)
    assert "DNS leak" in texts and "192.168.12.1" in texts and "IPv6 leak" in texts


def test_a_vpn_that_is_up_but_not_carrying_traffic_is_named():
    network = view([WIFI, PROTON], "Wi-Fi", {"ProtonVPN": ["10.2.0.1"]})
    found = leak_checks(network, vpn_state(network))
    assert "goes out through Wi-Fi" in found[0]["what"] and "kill-switch" in found[0]["fix"]


# -- Tor for HAYAT -------------------------------------------------------------------------------------

def tor_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in (("tor/tor.exe", b"MZ fake tor"), ("data/geoip", b"geo"), ("data/geoip6", b"geo6"),
                           ("tor/pluggable_transports/lyrebird.exe", b"not needed"), ("../../escape.txt", b"bad")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture
def archive(monkeypatch):
    data = tor_archive()
    monkeypatch.setattr(pm, "TOR_SOURCE", ("https://dist.torproject.org/x.tar.gz", hashlib.sha256(data).hexdigest()))
    return data


def test_setting_up_tor_keeps_only_the_daemon_and_its_country_tables(tmp_path, archive):
    tor = TorMask(tmp_path / "runtime" / "tor", tmp_path / "data" / "tor", settings=tmp_path / "masks.json")
    assert tor.install(lambda url: b"tampered")["installed"] is False and not tor.installed()
    result = tor.install(lambda url: archive)
    assert result["installed"] and sorted(path.name for path in (tmp_path / "runtime" / "tor").iterdir()) == [
        "geoip", "geoip6", "tor.exe"]
    assert not list(tmp_path.rglob("escape.txt")) and not list(tmp_path.rglob("lyrebird.exe"))
    assert tor.install(lambda url: b"unused")["status"] == "already installed"


def test_a_tor_download_defender_blocks_is_not_kept(tmp_path, archive):
    scanner = type("Scanner", (), {"scan": lambda self, path: {"clean": False, "reason": "Trojan:Test"}})()
    tor = TorMask(tmp_path / "tor", tmp_path / "state", settings=tmp_path / "masks.json", scanner=scanner)
    result = tor.install(lambda url: archive)
    assert result["installed"] is False and "Trojan:Test" in result["reason"] and not tor.installed()


def test_a_missing_clean_antimalware_verdict_does_not_allow_install(tmp_path, archive):
    from types import SimpleNamespace

    tor = TorMask(tmp_path / "tor", tmp_path / "state", settings=tmp_path / "masks.json",
                  scanner=SimpleNamespace(scan=lambda path: {"status": "unknown"}))
    assert not tor.install(lambda url: archive)["installed"]
    assert not tor.installed()


def test_the_pinned_tor_source_is_torproject():
    url, digest = pm.TOR_SOURCE
    assert url.startswith("https://dist.torproject.org/torbrowser/") and len(digest) == 64


class FakeTor:
    """Stands in for tor.exe: writes the bootstrap lines Tor writes to its log."""

    def __init__(self, log, finish=True):
        self.log, self.alive = log, True
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("[notice] Bootstrapped 5%\n" + ("[notice] Bootstrapped 100% (done): Done\n" if finish else ""))
        self.terminated = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive, self.terminated = False, True

    def wait(self, timeout=None):
        return 0


def test_masking_hayat_routes_its_web_reading_through_tor_and_is_remembered(tmp_path, archive):
    route = pm.Route()
    launched = []
    tor = TorMask(tmp_path / "tor", tmp_path / "state", route=route, settings=tmp_path / "masks.json",
                  launch=lambda command, **options: launched.append(command) or FakeTor(tmp_path / "state" / "tor.log"))
    assert "set up tor" in tor.start()["reason"]
    tor.install(lambda url: archive)
    result = tor.start(timeout=5)
    assert result["masked"] and route.proxy == "socks5://127.0.0.1:9052" and tor.wanted()
    assert launched[0][0].endswith("tor.exe") and launched[0][1] == "-f"
    torrc = (tmp_path / "state" / "torrc").read_text()
    assert "SocksPort 127.0.0.1:9052" in torrc and "ClientOnly 1" in torrc
    process = tor.process
    tor.stop(remember=False)  # HAYAT closing: Tor stops, the choice is kept for next time
    assert route.proxy is not None and process.terminated and tor.wanted()
    assert tor.status()["state"] == "blocked" and not tor.status()["masked"]
    tor.start(timeout=5)
    tor.stop()  # the owner unmasking
    assert not tor.wanted()


def test_tor_that_never_finishes_connecting_keeps_required_proxy(tmp_path, archive):
    route = pm.Route()
    tor = TorMask(tmp_path / "tor", tmp_path / "state", route=route, settings=tmp_path / "masks.json",
                  launch=lambda command, **options: FakeTor(tmp_path / "state" / "tor.log", finish=False))
    tor.install(lambda url: archive)
    result = tor.start(timeout=1)
    assert result["masked"] is False and "had not finished connecting" in result["reason"] and route.proxy is not None
    assert tor.status()["routing_required"] and tor.wanted()


def test_the_public_fetcher_follows_the_mask():
    from app.research.fetcher import PublicFetcher

    fetcher = PublicFetcher("HAYAT-test/1.0")
    try:
        direct = fetcher.client
        assert type(direct._transport_for_url(httpx.URL("https://example.org"))._pool).__name__ == "ConnectionPool"
        pm.ROUTE.set("socks5://127.0.0.1:9052")
        masked = fetcher.client
        assert masked is not direct
        assert type(masked._transport_for_url(httpx.URL("https://example.org"))._pool).__name__ == "SOCKSProxy"
        pm.ROUTE.set(None)
        assert type(fetcher.client._transport_for_url(httpx.URL("https://example.org"))._pool).__name__ == "ConnectionPool"
    finally:
        pm.ROUTE.set(None)
        fetcher.close()


# -- the layers together ------------------------------------------------------------------------------

def masks(tmp_path, *, masked=False, network=None, **options):
    tor = TorMask(tmp_path / "tor", tmp_path / "state", route=pm.Route(), settings=tmp_path / "masks.json")
    if masked:
        tor.route.set("socks5://127.0.0.1:9052")
        tor.state.mkdir(parents=True, exist_ok=True)
        tor.process = FakeTor(tor.log)
    seen = {"success": True, "city": "Detroit", "region": "Michigan", "country": "United States",
            "connection": {"isp": "T-Mobile USA, Inc."}}
    exit_node = {"success": True, "city": "Frankfurt", "country": "Germany", "connection": {"org": "Tor exit"}}
    options.setdefault("find", lambda exe: rf"C:\Browsers\{exe}")
    options.setdefault("profiles", tmp_path / "profiles")
    return PrivacyMasks(tor, network=network or (lambda: view([WIFI], "Wi-Fi", {"Wi-Fi": ["192.168.12.1"]})),
                        direct_json=lambda url: seen, masked_json=lambda url: exit_node, **options)


class Window:
    """Stands in for a browser process."""

    def __init__(self, command, **options):
        self.command, self.open = command, True

    def poll(self):
        return None if self.open else 0


def test_the_status_counts_the_masks_and_says_what_each_side_sees(tmp_path):
    bare = masks(tmp_path).status()
    assert bare["masks_on"] == 1 and [layer["on"] for layer in bare["layers"]] == [False, False, True]
    assert bare["computer_seen_as"]["place"] == "Detroit, Michigan, United States"
    assert bare["computer_seen_as"]["network"] == "T-Mobile USA, Inc" and bare["hayat_seen_as"]["checked"] is False
    covered = masks(tmp_path, masked=True, launch=Window)
    covered.open_masked_browser("edge")
    status = covered.status()
    assert status["masks_on"] == 3 and status["hayat_seen_as"]["place"] == "Frankfurt, Germany"
    assert status["layers"][1]["detail"] == "1 window open through Tor" and "fingerprinting" in status["limits"]
    covered.browsers[0].open = False  # the owner closed it
    assert covered.status()["masks_on"] == 2


def test_privacy_mode_asks_no_website(tmp_path):
    asked = []
    result = masks(tmp_path, privacy=lambda: True).status()
    result_masked = PrivacyMasks(TorMask(tmp_path / "t", tmp_path / "s", route=pm.Route(), settings=tmp_path / "m.json"),
                                 network=lambda: view([WIFI], "Wi-Fi", {}), direct_json=lambda url: asked.append(url),
                                 privacy=lambda: True).status()
    assert not asked and result["computer_seen_as"]["checked"] is False and result_masked["masks_on"] == 1


def test_the_masked_browser_is_the_owners_own_browser_through_tor_in_its_own_profile(tmp_path):
    windows = []
    launcher = masks(tmp_path, launch=lambda command, **options: windows.append(command) or Window(command))
    refused = launcher.open_masked_browser("edge")
    assert refused["opened"] is False and "mask HAYAT" in refused["reason"] and not windows  # never without Tor
    launcher.tor.route.set("socks5://127.0.0.1:9052")
    launcher.tor.state.mkdir(parents=True, exist_ok=True)
    launcher.tor.process = FakeTor(launcher.tor.log)
    result = launcher.open_masked_browser("chrome", "https://example.org/")
    command = windows[0]
    assert command[0] == r"C:\Browsers\chrome.exe" and command[-1] == "https://example.org/"
    assert f"--user-data-dir={tmp_path / 'profiles' / 'chrome'}" in command
    assert "--proxy-server=socks5://127.0.0.1:9052" in command
    assert "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1" in command  # names only through Tor
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in command
    assert result["opened"] and "not Tor Browser" in result["note"]
    with pytest.raises(ValueError):
        launcher.open_masked_browser("firefox-with-a-vpn-app")
    with pytest.raises(ValueError):
        launcher.open_masked_browser("edge", "file:///C:/secret.txt")
    missing = masks(tmp_path, masked=True, find=lambda exe: None).open_masked_browser("edge")
    assert missing["opened"] is False and "not registered" in missing["reason"]


@pytest.mark.parametrize("text, tool", [
    ("set up tor", "tor_setup"), ("Mask HAYAT with Tor", "hayat_mask_on"), ("route hayat through tor", "hayat_mask_on"),
    ("make hayat untrackable", "hayat_mask_on"), ("turn off tor", "hayat_mask_off"), ("unmask hayat", "hayat_mask_off"),
    ("How untrackable am I?", "privacy_masks_status"), ("am i using a vpn", "privacy_masks_status"),
    ("is my vpn leaking", "privacy_masks_status"), ("open masked browser", "masked_browser_open"),
    ("browse anonymously", "masked_browser_open"), ("open a masked chrome browser", "masked_browser_open"),
])
def test_requests_reach_the_mask_tools(text, tool):
    assert masks_request(text)["tool"] == tool


@pytest.mark.parametrize("text", ["who can see my location", "where am I", "what is tor", "open edge location settings",
                                  "open proton vpn", "get a free vpn"])
def test_other_requests_are_left_alone(text):
    assert masks_request(text) is None


def test_the_assistant_answers_how_untrackable_am_i(monkeypatch):
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        names = set(runtime.registry.names())
        assert {"privacy_masks_status", "tor_setup", "hayat_mask_on", "hayat_mask_off", "masked_browser_open"} <= names
        assert "vpn_open_page" not in names
        layers = runtime.registry.privacy_masks
        monkeypatch.setattr(layers, "network", lambda: view([WIFI], "Wi-Fi", {"Wi-Fi": ["192.168.12.1"]}))
        monkeypatch.setattr(layers, "direct_json", lambda url: {"success": True, "city": "Detroit", "country": "United States"})
        reply = runtime.assistant.handle("how untrackable am I?").display_response
        assert "Masks on: 1 of 3" in reply and "your own internet address" in reply and 'Say "mask HAYAT"' in reply
        assert "Proton" not in reply and "Riseup" not in reply
    finally:
        runtime.close()
