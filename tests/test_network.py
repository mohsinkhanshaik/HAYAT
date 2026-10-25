"""The Wi-Fi router and home network: read from Windows and the gateway's own status page, never guessed."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.network import (NetworkInspector, cellular_quality, channel_advice, network_request, parse_wifi_scan, randomized,
                         report)
from tests.test_csi import WLAN

IP_CONFIG = {"interface": "Wi-Fi", "ipv4": "192.168.12.159", "prefix": 24, "gateway": "192.168.12.1",
             "dns": ["192.168.12.1"], "neighbours": [
                 {"IPAddress": "192.168.12.1", "LinkLayerAddress": "18-A5-FF-F5-BF-0E", "State": "Reachable"},
                 {"IPAddress": "192.168.12.203", "LinkLayerAddress": "00-C3-F4-08-08-B0", "State": "Stale"},
                 {"IPAddress": "192.168.12.228", "LinkLayerAddress": "5A-05-D9-4D-99-94", "State": "Stale"},
                 {"IPAddress": "192.168.12.255", "LinkLayerAddress": "FF-FF-FF-FF-FF-FF", "State": "Permanent"}]}
TMI = {"device": {"manufacturer": "Arcadyan", "model": "TMO-G4AR", "friendlyName": "5G Gateway", "softwareVersion": "1.00.18",
                  "updateState": "latest", "isMeshSupported": True},
       "signal": {"5g": {"bands": ["n41"], "bars": 4.0, "cid": 312, "gNBID": 1059038, "rsrp": -92, "rsrq": -10, "rssi": -81,
                         "sinr": 19, "antennaUsed": "Internal_omni"},
                  "generic": {"registration": "registered", "roaming": False}},
       "time": {"upTime": 60266}}
SCAN = """
Interface name : Wi-Fi
There are 2 networks currently visible.

SSID 1 : TMOBILE-BF0E
    Network type            : Infrastructure
    Authentication          : WPA3-Personal
    Encryption              : CCMP
    BSSID 1                 : 18:a5:ff:f5:bf:11
         Signal             : 81%
         Radio type         : 802.11ax
         Band               : 5 GHz
         Channel            : 161
         Bss Load:
             Connected Stations:         6
             Channel Utilization:        14 (5 %)
SSID 2 : NEIGHBOUR
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    BSSID 1                 : 22:33:44:55:66:77
         Signal             : 40%
         Radio type         : 802.11ac
         Band               : 5 GHz
         Channel            : 161
SSID 3 :
    BSSID 1                 : 22:33:44:55:66:88
         Signal             : 30%
         Band               : 5 GHz
         Channel            : 36
"""


def inspector(tmp_path, *, gateway_json=TMI, gateway_page="<html><title>T-Mobile Gateway</title></html>", ip=IP_CONFIG):
    asked = []

    def get_json(url):
        asked.append(url)
        if gateway_json is None:
            raise OSError("404")
        return gateway_json

    def get_text(url):
        asked.append(url)
        return gateway_page

    made = NetworkInspector(tmp_path, powershell=lambda script, timeout: json.dumps(ip),
                            netsh=lambda arguments: SCAN if "networks" in arguments else WLAN,
                            get_json=get_json, get_text=get_text, latency=lambda host, port: 12.5 if host.startswith("192.") else 30.0,
                            fetch=lambda url: b"Registry,Assignment,Organization Name\n" + b"".join(
                                f"MA-L,{i:06X},Maker {i}\n".encode() for i in range(1200)) + b"MA-L,00C3F4,Samsung Electronics\n")
    made.asked = asked
    return made


def test_the_router_is_read_from_windows_and_its_own_status_page(tmp_path):
    view = inspector(tmp_path).router()
    router = view["router"]
    assert router["maker"] == "Arcadyan" and router["model"] == "TMO-G4AR" and router["firmware"] == "1.00.18"
    assert router["mac"] == "18:a5:ff:f5:bf:0e" and router["admin_title"] == "T-Mobile Gateway"
    five_g = router["cellular"]["5g"]
    assert five_g["sinr"] == 19 and five_g["quality"] == {"rsrp": "fair", "rsrq": "excellent", "sinr": "good", "overall": "fair"}
    assert view["wifi"]["ssid"] == "HOME" and view["ip"]["gateway"] == "192.168.12.1"
    assert view["latency_ms"] == {"router": 12.5, "internet": 30.0, "internet_measured": True}
    assert view["summary"].startswith("Arcadyan TMO-G4AR at 192.168.12.1; Wi-Fi 'HOME' on 5 GHz channel 161")
    assert "5G n41: SINR 19 dB" in view["summary"] and "up 16 h 44 min" in view["summary"]


def test_only_the_gateway_is_ever_contacted(tmp_path):
    made = inspector(tmp_path)
    made.router()
    assert made.asked and all(url.startswith("http://192.168.12.1/") for url in made.asked)
    public = inspector(tmp_path, ip={**IP_CONFIG, "gateway": "8.8.8.8"})
    public.router()
    assert public.asked == []  # a gateway that is not a private address is never fetched


def test_a_router_without_a_status_page_still_reports_the_connection(tmp_path):
    view = inspector(tmp_path, gateway_json=None, gateway_page="<title>NETGEAR Router</title>").router(measure_internet=False)
    assert view["router"]["admin_title"] == "NETGEAR Router" and "cellular" not in view["router"]
    assert view["summary"].startswith("NETGEAR Router at 192.168.12.1") and "internet unreachable" not in view["summary"]


def test_no_gateway_means_not_connected(tmp_path):
    view = inspector(tmp_path, ip={"interface": "Wi-Fi"}).router()
    assert view["connected"] is False and "not connected to a router" in view["summary"]


def test_nearby_networks_and_channel_crowding(tmp_path):
    networks = parse_wifi_scan(SCAN)
    assert [row["ssid"] for row in networks] == ["TMOBILE-BF0E", "NEIGHBOUR", "(hidden network)"]
    assert networks[0]["stations"] == 6 and networks[0]["utilization_percent"] == 5 and networks[1]["channel"] == 161
    advice = channel_advice(networks, networks[0])
    # 161 and 36 each have one other network: no reason to move, so no suggestion.
    assert advice["sharing"] == 1 and advice["least_crowded"] == 161 and "NEIGHBOUR" in advice["summary"]
    assert "least crowded" not in advice["summary"]
    busier = networks + [{**networks[1], "bssid": "22:33:44:55:66:99", "ssid": "SECOND"}]
    moved = channel_advice(busier, networks[0])
    assert moved["sharing"] == 2 and moved["least_crowded"] == 36 and "Channel 36 looks least crowded" in moved["summary"]
    view = inspector(tmp_path).nearby()
    assert view["count"] == 3 and view["networks"][0]["ssid"] == "TMOBILE-BF0E"


def test_devices_come_from_the_neighbour_cache_with_makers_after_the_ieee_update(tmp_path):
    made = inspector(tmp_path)
    before = made.devices()
    assert [row["ip"] for row in before["devices"]] == ["192.168.12.1", "192.168.12.203", "192.168.12.228"]
    assert before["count"] == 2 and before["devices"][0]["role"] == "router"
    assert before["devices"][2]["private_address"] is True and "update device vendor names" in before["note"]
    updated = made.update_vendors()
    assert updated["organisations"] == 1202 and (tmp_path / "oui.csv").is_file()
    after = made.devices()
    assert after["devices"][1]["vendor"] == "Samsung Electronics"
    assert after["devices"][2]["vendor"] is None  # a randomised address has no maker to look up
    with pytest.raises(ValueError, match="incomplete"):
        NetworkInspector(tmp_path / "x", fetch=lambda url: b"MA-L,000000,Tiny\n").update_vendors()


def test_ratings_and_private_addresses():
    assert cellular_quality({"rsrp": -75, "rsrq": -8, "sinr": 25})["overall"] == "excellent"
    assert cellular_quality({"rsrp": -110, "sinr": 25})["overall"] == "poor"
    assert randomized("5a:05:d9:4d:99:94") and not randomized("00:c3:f4:08:08:b0")


@pytest.mark.parametrize("text, tool", [
    ("check my router", "network_router"), ("how is my internet?", "network_router"), ("5g signal", "network_router"),
    ("which wifi am I connected to", "network_router"), ("who is on my wifi", "network_devices"),
    ("what devices are connected to my network", "network_devices"), ("show nearby wifi networks", "network_nearby_wifi"),
    ("best wifi channel", "network_nearby_wifi"), ("update device vendor names", "network_vendor_update"),
])
def test_owner_phrases_reach_the_network_tools(text, tool):
    assert network_request(text) == {"tool": tool, "arguments": {}}


@pytest.mark.parametrize("text", ["what is a router", "list my devices", "which router should I buy", "open notepad"])
def test_other_sentences_stay_out(text):
    assert network_request(text) is None


def test_chat_report_and_runtime_wiring(tmp_path):
    view = inspector(tmp_path).router()
    text = report([{"tool": "network_router", "result": {"success": True, "output": view}, "verification": {"passed": True}}])
    assert text.startswith("Arcadyan TMO-G4AR") and "Firmware 1.00.18 (latest)" in text
    assert "Admin page: http://192.168.12.1/ (T-Mobile Gateway)" in text
    from app.intent.router import IntentRouter
    from app.session import SessionState

    assert IntentRouter(None).route("check my router", SessionState()).entities == {"modality": "network"}


def test_the_router_tab_paints_a_refresh(shared_tk, tmp_path):
    from app.gui_network import RouterPanel
    from app.gui_theme import Palette
    import tkinter as tk

    made = inspector(tmp_path)
    found = {"network_router": made.router(), "network_nearby_wifi": made.nearby(), "network_devices": made.devices()}
    studio = SimpleNamespace(palette=Palette, registry=SimpleNamespace(network=made),
                             status_var=SimpleNamespace(set=lambda value: None))

    def card(parent, title):
        frame = tk.Frame(parent)
        tk.Label(frame, text=title).pack()
        return frame
    studio._card = card
    frame = tk.Frame(shared_tk)
    panel = RouterPanel(frame, studio)
    try:
        # Before the first reading: the tables say what they are waiting for and the readouts are not grey boxes.
        assert panel.nearby._hayat_empty.cget("text").startswith("Reading the Wi-Fi networks")
        assert panel.devices._hayat_empty.cget("text").startswith("Reading the devices")
        assert all(label.cget("bg") == Palette.PANEL for label in panel.metrics.values())
        panel.paint(found)
        assert not panel.nearby._hayat_empty.winfo_manager()
        assert panel.router_name.get() == "Arcadyan TMO-G4AR · 192.168.12.1"
        assert panel.metrics["sinr"].cget("text") == "SINR 19 dB · good"
        assert len(panel.nearby.get_children()) == 3 and len(panel.devices.get_children()) == 3
        assert str(panel.admin_button.cget("state")) == "normal"
        panel.paint({**found, "network_devices": {"devices": []}, "network_nearby_wifi": {"error": "wlan service off"}})
        assert panel.devices._hayat_empty.cget("text").startswith("No devices seen yet")
        assert len(panel.nearby.get_children()) == 3  # a failed read keeps the last good list
        panel.nearby.delete(*panel.nearby.get_children())
        panel.paint({**found, "network_nearby_wifi": {"error": "wlan service off"}})
        assert panel.nearby._hayat_empty.cget("text") == "Could not read nearby Wi-Fi: wlan service off"
    finally:
        frame.destroy()
