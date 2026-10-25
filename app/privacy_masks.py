"""Masks: layers that hide where the owner is, checked rather than assumed.

The owner asked (2026-10-17) to be untrackable with several masks, without renaming any Wi-Fi and without
a third-party VPN app. So everything here runs from HAYAT itself:

1. **Tor for HAYAT's own web reading.** Tor's Expert Bundle (hash-pinned, from dist.torproject.org) runs
   from runtime/tor as a local SOCKS proxy on 127.0.0.1:9052. While it is on, HAYAT's public fetcher
   (research, world feeds, market data, pages) reaches the internet through three Tor relays, so sites
   see a Tor exit instead of the owner's address. Local traffic (the brain on this computer, the router
   page) never goes through it. Many sites slow down or block Tor, and HAYAT keeps its robots.txt and
   rate rules either way: the mask hides the location, it does not change the manners.
2. **A masked browser for the owner's own browsing.** The Edge or Chrome already on the computer, started
   by HAYAT in a separate profile with its proxy set to HAYAT's Tor: names are resolved through Tor (no
   DNS leak), WebRTC may not use any connection outside the proxy, and nothing is shared with the owner's
   normal profile. No app is installed and no Windows setting is changed. It hides the address; it does
   not have Tor Browser's protection against fingerprinting, and the reports say so.
3. **HAYAT's location guard** (app/location_privacy.py): filters supported outgoing location fields.

A VPN, if one is ever connected, is detected by its adapter and tested for DNS and IPv6 leaks, but none
is recommended or installed. What no mask fixes is said too: accounts you sign into, a phone's GPS,
browser fingerprinting and what you type yourself still identify you.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import subprocess
import tarfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


_log = logging.getLogger("hayat.privacy_masks")
_ROOT = Path(__file__).resolve().parents[1]

TOR_VERSION = "15.0.25"
#: Tor Expert Bundle for 64-bit Windows; the hash is the one in Tor's signed sha256sums-signed-build.txt, and the
#: same on dist.torproject.org and archive.torproject.org (checked 2026-10-17).
TOR_SOURCE = (f"https://dist.torproject.org/torbrowser/{TOR_VERSION}/tor-expert-bundle-windows-x86_64-{TOR_VERSION}.tar.gz",
              "c1988c701de2f24f7d098d1d6d7f507b58f27740d097f29c246e0524d639b252")
#: Only these leave the archive: the daemon and its address-to-country tables. Nothing else is extracted.
TOR_MEMBERS = {"tor/tor.exe": "tor.exe", "data/geoip": "geoip", "data/geoip6": "geoip6"}
MAX_TOR_DOWNLOAD = 64 * 1024 * 1024
SOCKS_PORT = 9052  # not 9050 (a system Tor) or 9150 (Tor Browser)
SETTINGS_FILE = _ROOT / "data" / "privacy-masks.json"

VPN_WORDS = ("wireguard", "wintun", "tap-windows", "tap adapter", "openvpn", "nordlynx", "proton", "mullvad",
             "cloudflare warp", "warp interface", "windscribe", "riseup", "calyx", "expressvpn", "surfshark",
             "tunnelbear", "private internet access", "ivpn", "vpn")
#: The masked browser: the owner's own Edge or Chrome, found from its App Paths registration.
BROWSERS = {"edge": "msedge.exe", "chrome": "chrome.exe"}
#: Windows puts these three on every IPv6 adapter with no DNS of its own; they never answer, so they leak nothing.
_PLACEHOLDER_DNS = {"fec0:0:0:ffff::1", "fec0:0:0:ffff::2", "fec0:0:0:ffff::3"}


def local_dns(server: str) -> bool:
    """A DNS server on the local network (the router, a 5G gateway), reached outside any VPN tunnel.

    IPv4 and IPv6 alike (the first check read only IPv4, so a router's fd00::/fe80:: DNS went unseen; hard-tested
    2026-10-17). A public server such as 1.1.1.1 is reached through the VPN's default route, so it is not a leak.
    """
    import ipaddress

    text = str(server or "").split("%", 1)[0].strip()
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    if str(address) in _PLACEHOLDER_DNS or address.is_loopback or address.is_unspecified:
        return False
    if isinstance(address, ipaddress.IPv4Address) and address in ipaddress.ip_network("100.64.0.0/10"):
        return False  # carrier-grade NAT: inside the provider, reached through the default route
    return address.is_private or address.is_link_local


class Route:
    """Which proxy HAYAT's public fetcher uses. A changed version makes each fetcher rebuild its connection."""

    def __init__(self) -> None:
        self.proxy: str | None = None
        self.version = 0
        self._lock = threading.Lock()
        self._starter: Callable[[], Any] | None = None

    def set(self, proxy: str | None) -> None:
        with self._lock:
            if proxy != self.proxy:
                self.proxy = proxy
                self.version += 1

    def snapshot(self) -> tuple[str | None, int]:
        with self._lock:
            return self.proxy, self.version

    def set_starter(self, starter: Callable[[], Any] | None) -> None:
        """What brings the proxy up when a masked request finds it down (TorMask.ensure_started)."""
        self._starter = starter

    def ensure_ready(self) -> None:
        """Start the mask a request is about to use. Raises when it cannot; never switches to direct.

        With background autostart off, a launch with Tor remembered on routed
        every search to 127.0.0.1:9052 and started nothing there: each provider
        failed with "WinError 10061 ... actively refused" (2026-10-25).
        """
        starter = self._starter
        if starter is not None and self.proxy is not None:
            starter()


ROUTE = Route()


# ------------------------------------------------------------------ the computer's own connection

_NET_SCRIPT = r"""
$adapters = @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object Status -eq 'Up' |
    Select-Object Name, InterfaceDescription)
$route = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Sort-Object { $_.RouteMetric + $_.InterfaceMetric } | Select-Object -First 1 InterfaceAlias)
$dns = @(Get-DnsClientServerAddress -ErrorAction SilentlyContinue |
    Where-Object { $_.ServerAddresses } | ForEach-Object { @{alias = $_.InterfaceAlias; servers = @($_.ServerAddresses)} })
$v6 = @(Get-NetIPAddress -AddressFamily IPv6 -ErrorAction SilentlyContinue |
    Where-Object { "$($_.PrefixOrigin)" -in 'RouterAdvertisement','Dhcp','4','3' -and $_.IPAddress -notlike 'fe80*' } |
    Select-Object -ExpandProperty InterfaceAlias -Unique)
@{adapters = $adapters; route = $route; dns = $dns; ipv6 = $v6} | ConvertTo-Json -Depth 4 -Compress
"""


def _powershell(script: str, timeout: float = 20) -> str:
    completed = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                               capture_output=True, text=True, timeout=timeout,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return completed.stdout


def network_view(run: Callable[[str, float], str] = _powershell) -> dict[str, Any]:
    try:
        data = json.loads(run(_NET_SCRIPT, 20) or "{}")
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        return {"available": False, "reason": f"Windows networking could not be read: {exc}"}

    def as_list(value: Any) -> list[Any]:
        return value if isinstance(value, list) else [] if value in (None, "") else [value]
    adapters = [{"name": row.get("Name", ""), "description": row.get("InterfaceDescription", "")}
                for row in as_list(data.get("adapters")) if isinstance(row, dict)]
    route = next((row.get("InterfaceAlias") for row in as_list(data.get("route")) if isinstance(row, dict)), None)
    dns = {row.get("alias", ""): as_list(row.get("servers")) for row in as_list(data.get("dns")) if isinstance(row, dict)}
    return {"available": True, "adapters": adapters, "default_route": route, "dns": dns,
            "ipv6_public": [str(alias) for alias in as_list(data.get("ipv6"))]}


def vpn_state(view: dict[str, Any]) -> dict[str, Any]:
    vpns = [row for row in view.get("adapters", [])
            if any(word in f"{row['name']} {row['description']}".lower() for word in VPN_WORDS)]
    names = {row["name"] for row in vpns}
    return {"connected": bool(vpns), "adapters": vpns, "routes_traffic": view.get("default_route") in names,
            "default_route": view.get("default_route")}


def leak_checks(view: dict[str, Any], vpn: dict[str, Any]) -> list[dict[str, Any]]:
    if not view.get("available"):
        return [{"level": "info", "what": view.get("reason", "Networking could not be read."), "fix": ""}]
    if not vpn["connected"]:
        return [{"level": "watch", "what": "Your browsers and apps reach websites with your own internet address, "
                 "which places you near your town.",
                 "fix": "Browse what should stay hidden in HAYAT's masked browser (your Edge or Chrome through Tor)."}]
    found = []
    vpn_names = {row["name"] for row in vpn["adapters"]}
    if not vpn["routes_traffic"]:
        found.append({"level": "watch", "what": f"A VPN adapter is up ({', '.join(sorted(vpn_names))}) but your traffic "
                      f"goes out through {vpn['default_route'] or 'another adapter'}.",
                      "fix": "Reconnect the VPN, and switch on its 'send all traffic' or kill-switch option."})
    outside = {alias: servers for alias, servers in view.get("dns", {}).items()
               if alias not in vpn_names and any(local_dns(server) for server in servers)}
    if outside and vpn["routes_traffic"]:
        servers = sorted({str(server) for values in outside.values() for server in values if local_dns(server)})
        found.append({"level": "watch", "what": "Possible DNS leak: " + ", ".join(servers)
                      + " is configured outside the VPN. Adapter settings do not prove which resolver carried a request.",
                      "fix": "Turn on the VPN's DNS-leak protection, or its kill switch."})
    v6_outside = [alias for alias in view.get("ipv6_public", []) if alias not in vpn_names]
    v6_inside = [alias for alias in view.get("ipv6_public", []) if alias in vpn_names]
    if v6_outside and not v6_inside:
        found.append({"level": "watch", "what": "Possible IPv6 leak: " + ", ".join(v6_outside) + " has a public IPv6 address "
                      "outside the VPN adapter. End-to-end IPv6 routing is unverified.",
                      "fix": "Turn on the VPN's IPv6-leak protection, or switch IPv6 off for that adapter."})
    if not found:
        found.append({"level": "ok", "what": "The observed default route uses the VPN; no DNS/IPv6 exposure indication "
                      "was found in adapter settings. This is not a packet-level leak test.", "fix": ""})
    return found


# ------------------------------------------------------------------ Tor for HAYAT's own web reading

class TorMask:
    """Installs and runs Tor from HAYAT's folder, and points HAYAT's public fetcher at it."""

    def __init__(self, folder: Path | None = None, state: Path | None = None, *, route: Route = ROUTE,
                 settings: Path | None = None, launch: Callable[..., Any] | None = None,
                 port: int = SOCKS_PORT, scanner: Any = None) -> None:
        self.folder = Path(folder or (_ROOT / "runtime" / "tor"))
        self.state = Path(state or (_ROOT / "data" / "tor"))
        self.route, self.settings, self.port = route, Path(settings if settings is not None else SETTINGS_FILE), port
        self.launch = launch or subprocess.Popen
        self.scanner = scanner
        self.process: Any = None
        self._lock = threading.Lock()
        self._demand_lock = threading.Lock()
        self._retry_after = 0.0
        self._demand_failure = ""

    #: After a failed on-demand start, requests fail at once with its reason
    #: for this long instead of each waiting out another bootstrap.
    DEMAND_RETRY_SECONDS = 30.0

    def ready(self) -> bool:
        return self.running() and self.bootstrapped() >= 100

    def ensure_started(self, timeout: float = 90) -> None:
        """Bring Tor up for a masked request that found it down; raise a plain reason when it cannot.

        Reads stay on Tor either way: a failure here fails the request closed.
        """
        if self.route.proxy is None or self.ready():
            return
        with self._demand_lock:  # one start at a time; the others wait and reuse its result
            if self.ready():
                return
            if time.monotonic() < self._retry_after:
                raise PermissionError(self._demand_failure)
            status = self.start(timeout=timeout)
            if status.get("masked"):
                self._retry_after, self._demand_failure = 0.0, ""
                return
            self._demand_failure = ("Tor is on for HAYAT's web reading but could not connect: "
                                    f"{status.get('reason') or 'Tor did not start'} Nothing was read directly. "
                                    'Try again shortly, or say "unmask" to read without Tor.')
            self._retry_after = time.monotonic() + self.DEMAND_RETRY_SECONDS
            raise PermissionError(self._demand_failure)

    @property
    def log(self) -> Path:
        return self.state / "tor.log"

    def installed(self) -> bool:
        return all((self.folder / name).is_file() for name in TOR_MEMBERS.values())

    def install(self, fetch: Callable[[str], bytes] | None = None) -> dict[str, Any]:
        if self.installed():
            return {"installed": True, "status": "already installed", "version": TOR_VERSION, "path": str(self.folder)}
        url, expected = TOR_SOURCE
        data = (fetch or _fetch_https)(url)
        if hashlib.sha256(data).hexdigest() != expected:
            return {"installed": False, "reason": "The Tor download did not match its published SHA-256, so it was not kept."}
        if self.scanner is not None:
            scratch = self.state / "tor-download.tar.gz"
            scratch.parent.mkdir(parents=True, exist_ok=True)
            scratch.write_bytes(data)
            try:
                verdict = self.scanner.scan(scratch)
            finally:
                scratch.unlink(missing_ok=True)
            if verdict.get("clean") is not True:
                return {"installed": False, "reason": f"Windows Defender blocked the Tor download: {verdict.get('reason')}"}
        self.folder.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member_name, target in TOR_MEMBERS.items():  # read by name; nothing else is extracted
                member = archive.getmember(member_name)
                if not member.isfile():
                    return {"installed": False, "reason": f"{member_name} in the archive is not a plain file."}
                partial = self.folder / (target + ".partial")
                partial.write_bytes(archive.extractfile(member).read())
                os.replace(partial, self.folder / target)
        return {"installed": True, "status": "downloaded", "version": TOR_VERSION, "path": str(self.folder),
                "bytes_downloaded": len(data), "source": url}

    def _torrc(self) -> Path:
        self.state.mkdir(parents=True, exist_ok=True)
        torrc = self.state / "torrc"
        lines = [f"SocksPort 127.0.0.1:{self.port}", f"DataDirectory {self.state / 'state'}",
                 f"GeoIPFile {self.folder / 'geoip'}", f"GeoIPv6File {self.folder / 'geoip6'}",
                 f"Log notice file {self.log}", "ClientOnly 1", "AvoidDiskWrites 1",
                 "ClientRejectInternalAddresses 1", "ClientDNSRejectInternalAddresses 1"]
        torrc.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return torrc

    def bootstrapped(self) -> int:
        try:
            text = self.log.read_text(encoding="utf-8", errors="replace")[-20000:]
        except OSError:
            return 0
        found = re.findall(r"Bootstrapped (\d+)%", text)
        return int(found[-1]) if found else 0

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, timeout: float = 120) -> dict[str, Any]:
        """Start Tor, wait until it has built its circuits, then send HAYAT's web reading through it."""
        with self._lock:
            # Privacy intent takes effect before setup/bootstrap. A failed or
            # stopped daemon must not silently put pending reads on the direct route.
            self.route.set(f"socks5://127.0.0.1:{self.port}")
            self._remember(True)
            if not self.installed():
                return {"masked": False, "reason": 'Tor is not set up yet: say "set up tor" first (about 21 MB).'}
            if not self.running():
                self.log.unlink(missing_ok=True)
                self.process = self.launch([str(self.folder / "tor.exe"), "-f", str(self._torrc())],
                                           cwd=str(self.folder), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.bootstrapped() >= 100 and self.running():
                self.route.set(f"socks5://127.0.0.1:{self.port}")
                self._remember(True)
                return {"masked": True, "proxy": self.route.proxy, "note": "HAYAT's web reading now goes through three "
                        "Tor relays; sites see a Tor exit, not your address."}
            if not self.running():
                return {"masked": False, "routing_required": True,
                        "reason": "Tor stopped while starting; public reads remain routed to Tor and fail closed. Its log is " + str(self.log)}
            time.sleep(0.5)
        return {"masked": False, "reason": f"Tor had not finished connecting after {timeout:.0f} s "
                f"({self.bootstrapped()}%); public reads remain routed to Tor and fail closed.", "routing_required": True}

    def stop(self, remember: bool = True) -> dict[str, Any]:
        if remember:
            self.route.set(None)  # only an explicit unmask switches back to direct
        with self._lock:
            if self.running():
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except Exception:
                    self.process.kill()
            self.process = None
        if remember:
            self._remember(False)
        return {"masked": False, "note": "HAYAT's web reading goes out directly again." if remember else
                "Tor stopped; the required proxy remains in place until an explicit unmask."}

    def _remember(self, on: bool) -> None:
        try:
            data = json.loads(self.settings.read_text(encoding="utf-8")) if self.settings.is_file() else {}
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["tor_for_hayat"] = bool(on)
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        partial = self.settings.with_suffix(".partial")
        partial.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(partial, self.settings)

    def wanted(self) -> bool:
        try:
            data = json.loads(self.settings.read_text(encoding="utf-8"))
            return isinstance(data, dict) and data.get("tor_for_hayat") is True
        except (OSError, ValueError):
            return False

    def status(self) -> dict[str, Any]:
        required = self.route.proxy is not None
        running = self.running()
        bootstrapped = self.bootstrapped() if running else 0
        return {"installed": self.installed(), "version": TOR_VERSION, "running": self.running(),
                "bootstrapped": bootstrapped, "masked": required and running and bootstrapped >= 100,
                "routing_required": required, "state": "ready" if required and running and bootstrapped >= 100 else
                "blocked" if required else "off",
                "socks_port": self.port, "remembered_on": self.wanted()}


def _fetch_https(url: str) -> bytes:
    import httpx

    chunks, total = [], 0
    with httpx.stream("GET", url, timeout=120, follow_redirects=True, headers={"User-Agent": "HAYAT-tor-setup/1.0"}) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes(1024 * 1024):
            total += len(chunk)
            if total > MAX_TOR_DOWNLOAD:
                raise OSError("The download was far larger than the Tor bundle.")
            chunks.append(chunk)
    return b"".join(chunks)


def _direct_json(url: str) -> Any:
    """What websites see of the computer itself: a direct request, never through Tor."""
    import httpx

    response = httpx.get(url, timeout=10, headers={"User-Agent": "HAYAT-privacy-check/1.0"}, trust_env=False)
    response.raise_for_status()
    return response.json()


# ------------------------------------------------------------------ the layers together

class PrivacyMasks:
    def __init__(self, tor: TorMask | None = None, *, network: Callable[[], dict[str, Any]] = network_view,
                 direct_json: Callable[[str], Any] = _direct_json, masked_json: Callable[[str], Any] | None = None,
                 privacy: Callable[[], bool] = lambda: False, guard_on: Callable[[], bool] = lambda: True,
                 launch: Callable[..., Any] | None = None, find: Callable[[str], str | None] | None = None,
                 profiles: Path | None = None) -> None:
        self.tor = tor or TorMask()
        self.network, self.direct_json, self.masked_json = network, direct_json, masked_json
        self.privacy, self.guard_on = privacy, guard_on
        self.launch = launch or subprocess.Popen
        if find is None:
            from app.location_privacy import _registered_program as find
        self.find = find
        self.profiles = Path(profiles or (self.tor.state / "browsers"))
        self.browsers: list[Any] = []

    def _seen(self, get: Callable[[str], Any] | None) -> dict[str, Any]:
        if get is None:
            return {"checked": False}
        try:
            data = get("https://ipwho.is/")
        except Exception as exc:
            return {"checked": False, "reason": f"{type(exc).__name__}"}
        if not isinstance(data, dict) or data.get("success") is False:
            return {"checked": False, "reason": "no answer"}
        connection = data.get("connection") if isinstance(data.get("connection"), dict) else {}
        place = ", ".join(str(part) for part in (data.get("city"), data.get("region"), data.get("country")) if part)
        return {"checked": True, "place": place, "network": str(connection.get("isp") or connection.get("org") or "").rstrip(".")}

    def status(self, check_internet: bool = True) -> dict[str, Any]:
        view = self.network()
        vpn = vpn_state(view) if view.get("available") else {"connected": False, "adapters": [], "routes_traffic": False}
        tor = self.tor.status()
        online = check_internet and not self.privacy()
        computer = self._seen(self.direct_json) if online and not tor.get("routing_required") else {
            "checked": False, "reason": "direct probe suppressed while Tor is required" if tor.get("routing_required") else "privacy mode"}
        hayat = (self._seen(self.masked_json) if online and tor["masked"] else
                 {"checked": False, "reason": "same as the computer" if not tor["masked"] else "privacy mode"})
        browsing = self.masked_browsers()
        layers = [
            {"layer": "Tor for HAYAT's web reading (3 relays)", "on": tor["masked"],
             "detail": ("On: research, world feeds, market data and pages go through Tor" if tor["masked"] else
                        "Tor required but not ready: supported public reads fail closed" if tor.get("routing_required") else
                        "Off" if tor["installed"] else "Not set up (about 21 MB)")},
            {"layer": "Masked browser for your browsing", "on": bool(browsing) and tor["masked"],
             "detail": (f"{len(browsing)} window{'s' if len(browsing) != 1 else ''} open through Tor" if browsing and tor["masked"]
                        else "Needs Tor on first" if not tor["masked"] else "None open: your Edge or Chrome through Tor")},
            {"layer": "HAYAT's location guard", "on": bool(self.guard_on()),
             "detail": "Filters supported outgoing location fields; other tools and apps need separate checks"},
        ]
        return {"kind": "privacy_masks", "layers": layers, "masks_on": sum(1 for layer in layers if layer["on"]),
                "vpn": vpn, "leaks": leak_checks(view, vpn), "tor": tor, "computer_seen_as": computer,
                "hayat_seen_as": hayat,
                "limits": "No mask hides you from accounts you sign into, a phone's GPS, browser fingerprinting or what "
                          "you type yourself. Masks hide where your connection comes from, not who you are.",
                "basis": "Read from Windows networking (nothing changed); one direct request and, with Tor on, one through "
                         "Tor, to ipwho.is. Direct probing is suppressed while Tor is required.",
                "coverage": {"tor_routed": ["PublicFetcher research, feeds and supported page reads", "explicit masked browser"],
                             "not_verified": ["external connectors and MCP servers", "ordinary browser automation",
                                              "native media players and download/extractor subprocesses", "other applications on Windows"],
                             "onion_reads": "Validated v3 onion addresses through PublicFetcher; Tor required",
                             "guaranteed_untrackable": False}}

    def masked_browsers(self) -> list[Any]:
        self.browsers = [process for process in self.browsers if process.poll() is None]
        return self.browsers

    def open_masked_browser(self, browser: str = "edge", url: str = "https://check.torproject.org/") -> dict[str, Any]:
        """The owner's own Edge or Chrome, in a separate profile, through HAYAT's Tor."""
        browser = str(browser or "edge").lower()
        if browser not in BROWSERS:
            raise ValueError(f"Choose one of: {', '.join(BROWSERS)}.")
        if not str(url).startswith(("https://", "http://")):
            raise ValueError("Open a web address (https://...).")
        from app.research.safety import validate_public_url
        url = validate_public_url(str(url), via_tor=True)
        if not self.tor.status()["masked"]:
            return {"opened": False, "reason": 'Tor is not on: say "mask HAYAT" first, so the browser has something to go through.'}
        program = self.find(BROWSERS[browser])
        if not program:
            return {"opened": False, "reason": f"{BROWSERS[browser]} is not registered on this computer."}
        profile = self.profiles / browser
        profile.mkdir(parents=True, exist_ok=True)
        command = [program, f"--user-data-dir={profile}", f"--proxy-server=socks5://127.0.0.1:{self.tor.port}",
                   "--proxy-bypass-list=<-loopback>", "--disable-quic", "--disable-background-networking",
                   "--disable-sync", "--disable-extensions",
                   # Every name goes through the proxy, never this computer's resolver: no DNS leak.
                   "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
                   # WebRTC may not reveal the real address by going around the proxy.
                   "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                   "--no-first-run", "--no-default-browser-check", url]
        self.browsers.append(self.launch(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        return {"opened": True, "browser": browser, "profile": str(profile), "page": url,
                "note": f"Opened a masked {browser.title()} window: sites in it see a Tor exit, not your address, and it "
                        "shares nothing with your normal profile. It is not Tor Browser: fingerprinting protection is "
                        "the browser's own."}


# ------------------------------------------------------------------ requests and tools

def masks_request(text: str) -> dict[str, Any] | None:
    value = " ".join(str(text or "").split()).lower().strip(" ?.!")
    if re.fullmatch(r"(?:please\s+)?(?:set\s*up|install|download|get)\s+tor(?:\s+for\s+hayat)?", value):
        return {"tool": "tor_setup", "arguments": {}}
    if re.fullmatch(r"(?:please\s+)?(?:mask|hide)\s+hayat(?:'s)?(?:\s+traffic)?(?:\s+with\s+tor)?|(?:please\s+)?(?:route|send)\s+"
                    r"hayat(?:'s)?(?:\s+traffic)?\s+(?:through|via|over)\s+tor|(?:please\s+)?(?:turn|switch)\s+on\s+tor"
                    r"|make\s+hayat\s+untrackable|(?:turn|switch)\s+on\s+(?:the\s+)?(?:tor\s+)?mask", value):
        return {"tool": "hayat_mask_on", "arguments": {}}
    if re.fullmatch(r"(?:please\s+)?(?:unmask|stop\s+masking)\s+hayat|(?:please\s+)?(?:turn|switch)\s+off\s+(?:tor|the\s+(?:tor\s+)?mask)", value):
        return {"tool": "hayat_mask_off", "arguments": {}}
    browser = re.fullmatch(r"(?:please\s+)?(?:open|start|launch)\s+(?:a\s+|the\s+)?(?:masked|private|anonymous|hidden|tor)\s+"
                           r"(edge|chrome)?\s*(?:browser|window)?(?:\s+(?:through|via|over)\s+tor)?|browse\s+(?:anonymously|privately|through\s+tor)",
                           value)
    if browser:
        return {"tool": "masked_browser_open", "arguments": {"browser": browser[1] or "edge"}}
    if re.search(r"\b(?:vpn\s+status|am\s+i\s+(?:on|using)\s+(?:a\s+)?vpn|is\s+my\s+vpn\s+(?:on|working|leaking)|leak\s+test|dns\s+leak|"
                 r"how\s+(?:untrackable|trackable|masked|hidden)\s+am\s+i|my\s+masks?|privacy\s+masks?|"
                 r"am\s+i\s+(?:untrackable|masked|hidden))\b", value):
        return {"tool": "privacy_masks_status", "arguments": {}}
    return None


def direct_masks_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    request = masks_request(text)
    if not request or request["tool"] not in set(names):
        return None
    return [{"description": "Privacy masks: " + request["tool"].replace("_", " ") + ".", "tool": request["tool"],
             "arguments": request["arguments"], "expected": {"type": "tool_success"}, "max_retries": 0}]


def register_privacy_mask_tools(registry: Any, masks: PrivacyMasks) -> PrivacyMasks:
    registry.privacy_masks = masks
    registry.register(
        "privacy_masks_status",
        "How hidden you are, layer by layer: whether a VPN is connected and carries your traffic (with DNS and IPv6 "
        "exposure checks), Tor readiness and required routing for supported search/public reads, and HAYAT's location "
        "guard. Reports uncovered tools and anonymity limits. Direct IP probing is suppressed when Tor is required. Read-only.",
        {"type": "object", "properties": {"check_internet": {"type": "boolean"}}, "additionalProperties": False},
        "low", "system_read", masks.status)
    registry.register(
        "tor_setup",
        f"Download Tor {TOR_VERSION} (Expert Bundle, about 21 MB, SHA-256 pinned) into runtime/tor inside the HAYAT folder, "
        "so HAYAT's own web reading can be masked through Tor. Only tor.exe and its country tables are kept.",
        {"type": "object", "properties": {}, "additionalProperties": False}, "low", "public_download",
        lambda: masks.tor.install())
    registry.register(
        "hayat_mask_on",
        "Require Tor immediately for supported public reads and search, then start its local daemon. Bootstrap or proxy "
        "failure stays fail-closed without direct fallback. Local services stay local; this is not whole-computer anonymity.",
        {"type": "object", "properties": {}, "additionalProperties": False}, "low", "application_launch",
        lambda: masks.tor.start())
    registry.register(
        "hayat_mask_off",
        "Stop Tor and send HAYAT's web reading out directly again.",
        {"type": "object", "properties": {}, "additionalProperties": False}, "low", "application_launch",
        lambda: masks.tor.stop())
    registry.register(
        "masked_browser_open",
        "Open the owner's own Edge or Chrome in a separate profile that goes through HAYAT's Tor (Tor must be on): sites "
        "see a Tor exit, names are resolved through Tor and WebRTC cannot go around it. Nothing is installed and no "
        "Windows setting changes. Not Tor Browser: fingerprinting protection is the browser's own.",
        {"type": "object", "properties": {"browser": {"type": "string", "enum": sorted(BROWSERS)},
                                          "url": {"type": "string", "maxLength": 500}},
         "additionalProperties": False},
        "low", "application_launch",
        lambda browser="edge", url="https://check.torproject.org/": masks.open_masked_browser(browser, url))
    return masks
