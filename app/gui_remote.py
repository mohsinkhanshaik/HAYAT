"""Secure Access & Memory controls: phone and remote access, device pairing,
revocation, and the memory/resource mode.

Split out of app/gui.py. HayatGUI inherits these methods, so they still run as
``gui._set_remote_mode(...)`` against the window's own state and widgets.
"""
from __future__ import annotations

import threading
from tkinter import messagebox, simpledialog
from typing import Any

from app.gui_panels import human_bytes


class RemoteAccessMixin:
    """Owner-only access and resource controls for the desktop window."""

    def _start_web_access(self) -> None:
        """Bring phone/browser access up alongside the window, if enabled."""
        if self._closed or getattr(self.runtime, "safe_mode", False):
            return
        # Never bind a real network port from inside the test harness, which
        # builds the window with the owner's own .env in scope.
        import os

        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        try:
            status = self._remote_access.start_configured()
            server, message = self._remote_access.server, ""
        except Exception as exc:
            status = self._remote_access.status()
            server = None
            message = f"Phone access could not start: {type(exc).__name__}: {exc}"
        self._web_server = server
        self._paint_remote_controls()
        if status.get("serving"):
            message = f"Phone access is on at {status['url']}"
        if message:
            self._append("system", message)
            if server is not None:
                self._append("system", "Use Copy Phone Login to copy the protected local passphrase. Phone "
                                       "requests run with reduced remote authority — reading, research, "
                                       "and documents; anything that changes the system is asked here.")

    def _paint_remote_controls(self) -> None:
        if not hasattr(self, "remote_access_var"):
            return
        try:
            status = self._remote_access.status()
            mode = status.get("mode", "off").replace("_", " ").title()
            devices = status.get("pki", {}).get("active_devices", 0)
            if status.get("serving"):
                detail = f"{mode} · {status.get('url')} · {devices} paired"
                if status.get("mode") == "anywhere":
                    detail += " · external route not changed"
            else:
                detail = f"Off · {devices} paired device{'s' if devices != 1 else ''}"
            self.remote_access_var.set(f"Remote access: {detail}")
            resource = self._resource_controller.status()
            self.resource_mode_var.set(
                f"Memory mode: {resource['mode'].title()} · {resource['num_ctx'] // 1024}k context · "
                f"model resident {resource['keep_alive']}"
            )
            folds = getattr(self, "_fold_memory", None)
            if folds is not None:
                folds.summarise("Secure Access & Memory", f"Remote {mode.lower() if status.get('serving') else 'off'}"
                                f" · {devices} paired · {resource['mode'].title()} memory")
        except Exception as exc:
            self.remote_access_var.set(f"Remote access: unavailable ({type(exc).__name__})")

    def _owner_control(self, title: str, work, completed) -> None:
        """Run owner-only infrastructure work away from Tk's UI thread."""
        def run() -> None:
            try:
                result = work()
                self._post(completed, result)
            except Exception as exc:
                self._post(self._append, "error", f"{title}\n{type(exc).__name__}: {exc}")
                self._post(self._paint_remote_controls)
                self._record_failure(title, exc)

        threading.Thread(target=run, name="hayat-owner-control", daemon=True).start()

    def _set_remote_mode(self, mode: str) -> None:
        self.remote_access_var.set(f"Remote access: switching to {mode.replace('_', ' ')}...")
        self._owner_control("Remote access", lambda: self._remote_access.set_mode(mode),
                            self._remote_mode_changed)

    def _remote_mode_changed(self, status: dict[str, Any]) -> None:
        self._web_server = self._remote_access.server
        self._paint_remote_controls()
        if status.get("serving"):
            note = f"Remote access is {status['mode'].replace('_', ' ')} at {status['url']}."
            if status.get("mode") == "anywhere":
                note += " mTLS is required; no firewall/router/UPnP rule was created."
        else:
            note = "Remote access is off; no listener is running."
        self._append("system", note)

    def _copy_remote_login(self) -> None:
        """One local click: start same-Wi-Fi access and copy browser login."""
        if self._remote_access.server is None:
            self.remote_access_var.set("Remote access: starting secure same-Wi-Fi access...")
            self._owner_control(
                "Phone login", lambda: self._remote_access.set_mode("same_wifi"),
                self._remote_login_started,
            )
            return
        self._copy_remote_login_ready()

    def _remote_login_started(self, _status: dict[str, Any]) -> None:
        self._web_server = self._remote_access.server
        self._paint_remote_controls()
        self._copy_remote_login_ready()

    def _copy_remote_login_ready(self) -> None:
        try:
            credentials = self._remote_access.owner_access_credentials()
            payload = f"HAYAT URL: {credentials['url']}\nPassphrase: {credentials['passphrase']}"
            self.root.clipboard_clear()
            self.root.clipboard_append(payload)
            self.root.update_idletasks()
            messagebox.showinfo(
                "HAYAT phone login",
                f"The URL and passphrase were copied locally.\n\n{credentials['url']}\n\n"
                "Open it in any browser on the same Wi-Fi; no phone app or paid service is required.",
                parent=self.root,
            )
            self._append(
                "system", "Phone login copied locally. The passphrase was not logged or sent anywhere.")
        except Exception as exc:
            self._append("error", f"Phone login could not be prepared: {type(exc).__name__}: {exc}")

    def _pair_remote_device(self) -> None:
        name = simpledialog.askstring("Pair device", "Device name (for example: Mohsin phone):",
                                      parent=self.root)
        if not name:
            return
        self.remote_access_var.set("Remote access: creating an encrypted device bundle...")
        self._owner_control("Pair device", lambda: self._remote_access.pair_device(name),
                            self._paired_remote_device)

    def _paired_remote_device(self, result: dict[str, Any]) -> None:
        self._paint_remote_controls()
        message = (
            f"Device: {result['name']}\nBundle: {result['bundle']}\n"
            f"Bundle password (shown once): {result['password']}\n"
            f"Server CA certificate: {result['server_ca']}\n\n"
            "Import the PKCS#12 bundle and trust the HAYAT CA on that device. "
            "Keep the password private."
        )
        messagebox.showinfo("HAYAT device paired", message, parent=self.root)
        self._append("system", f"Paired {result['name']}. The encrypted bundle is at "
                               f"{result['bundle']}; its password was shown once locally.")

    def _revoke_remote_device(self) -> None:
        active = [item for item in self._remote_access.status().get("pki", {}).get("devices", [])
                  if not item.get("revoked_at")]
        if not active:
            self._append("system", "There are no active paired devices to revoke.")
            return
        choices = "\n".join(f"{item['id']} · {item['name']}" for item in active)
        device_id = simpledialog.askstring(
            "Revoke device", f"Enter the device id to revoke:\n\n{choices}", parent=self.root,
        )
        if not device_id:
            return
        self._owner_control("Revoke device", lambda: self._remote_access.revoke_device(device_id),
                            self._remote_device_revoked)

    def _remote_device_revoked(self, revoked: bool) -> None:
        self._web_server = self._remote_access.server
        self._paint_remote_controls()
        self._append("system", "Device revoked and TLS trust reloaded."
                     if revoked else "That active device id was not found.")

    def _disconnect_remote_devices(self) -> None:
        self._owner_control("Disconnect devices", self._remote_access.disconnect_all,
                            self._remote_devices_disconnected)

    def _remote_devices_disconnected(self, _result: Any = None) -> None:
        self._web_server = self._remote_access.server
        self._paint_remote_controls()
        self._append("system", "All remote sessions were disconnected.")

    def _set_resource_mode(self, mode: str) -> None:
        result = self._resource_controller.set_mode(mode)
        self._paint_remote_controls()
        self._append("system", f"Memory mode is {result['mode'].title()}: "
                               f"{result['num_ctx']} token context, model keep-alive "
                               f"{result['keep_alive']}.")

    def _release_hayat_memory(self) -> None:
        self.resource_mode_var.set("Memory mode: releasing HAYAT caches and resident models...")
        self._owner_control("Release HAYAT memory", self._resource_controller.release_memory,
                            self._memory_released)

    def _memory_released(self, result: dict[str, Any]) -> None:
        self._paint_remote_controls()
        released = human_bytes(result.get("released_process_bytes", 0))
        models = result.get("models", {}).get("count", 0)
        errors = result.get("models", {}).get("errors", [])
        message = f"Released {released} from HAYAT's working set; unloaded {models} local model(s)."
        if errors:
            message += " Ollama was unavailable, so no process was forced or killed."
        self._append("system", message)

    def _show_remote(self) -> None:
        """Report access and memory state without opening a modal prompt."""
        def describe() -> str:
            status = self._remote_access.status()
            pki = status.get("pki", {})
            resource = self._resource_controller.status()
            lines = [
                f"mode             {status['mode'].replace('_', ' ')}",
                f"listener         {status.get('url') or 'off'}",
                f"authority        {status.get('authority')}",
                f"mutual TLS       {'required' if status.get('mutual_tls') else 'not active'}",
                f"paired devices   {pki.get('active_devices', 0)} active · {pki.get('revoked_devices', 0)} revoked",
                f"external route   {'configured' if status.get('external_route_configured') else 'not changed by HAYAT'}",
                "",
                f"resource mode    {resource['mode']}",
                f"model residency  {resource['keep_alive']}",
                f"context ceiling  {resource['num_ctx']} tokens",
                f"HAYAT RSS        {human_bytes(resource['memory']['rss_bytes'])}",
                f"system RAM       {resource['memory']['system_percent']:.1f}% used",
                "",
                "Anywhere readiness is not internet reachability. A public IP/router path is still "
                "required, and CGNAT cannot be bypassed without an external relay.",
            ]
            for device in pki.get("devices", []):
                state = "revoked" if device.get("revoked_at") else "active"
                lines.append(f"device           {device.get('id')} · {device.get('name')} · {state}")
            return "\n".join(lines)

        self._background("Secure Access & Memory", describe)

    def _show_different_network(self) -> None:
        """Explain the real path from another network, without fake readiness."""
        def describe() -> str:
            status = self._remote_access.status()
            network = status.get("network_readiness") or {}
            paired = int((status.get("pki") or {}).get("active_devices", 0) or 0)
            lines = [
                "DIFFERENT-NETWORK ACCESS",
                f"  client certificates   {paired} paired",
                f"  TLS listener          {'ready' if status.get('mutual_tls') else 'not active'}",
                f"  public endpoint       {status.get('public_endpoint') or 'not configured'}",
                f"  outside reachability  {'verified' if network.get('outside_reachability_verified') else 'not verified locally'}",
                f"  detected path         {str(network.get('path') or 'unknown').replace('_', ' ')}",
                "",
                "NO-INSTALL PHONE FLOW",
                "  1. Pair the phone once and import HAYAT's client certificate.",
                "  2. Configure a public IPv6/DNS path or an explicit router port-forward.",
                "  3. Put that host in web.public_endpoint, then enable Anywhere mTLS.",
                "  4. Open the HTTPS address in the phone browser; no HAYAT app is needed.",
                "",
                "A $0, fully private path is possible when your internet connection provides a routable address. "
                "If the router is behind CGNAT, no local setting can make inbound traffic cross it; a relay or VPN endpoint is then physically required.",
                "",
                str(network.get("privacy") or ""),
            ]
            candidates = network.get("global_ipv6_candidates") or []
            if candidates:
                lines.append("Global IPv6 candidates: " + ", ".join(candidates[:4]))
            return "\n".join(lines)

        self._background("Different-network access", describe)
