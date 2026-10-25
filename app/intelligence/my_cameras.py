"""The owner's own cameras: registered one exact address at a time, never discovered.

Stage 4 of the live network: HAYAT plays cameras the owner adds by hand (a doorbell, a
driveway or shop camera on the home network) in the same player as public cameras. Rules:

* Exact addresses only. Nothing is scanned, guessed or discovered on the network.
* A login never travels in the address, in chat, in a tool result or in a file: the owner
  types it into the My cameras window, it goes to the Windows credential store, and the
  local player receives it as a libVLC login option when it opens the stream.
* Own cameras stay off the browser atlas and out of public-camera searches.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: Where the registry lives; the player worker reads it to check an address is registered.
DEFAULT_REGISTRY = Path(__file__).resolve().parents[2] / "data" / "memory" / "my-cameras.json"
SCHEMES = {"rtsp", "rtsps", "http", "https"}
SCOPE = "camera:view"


def kind_of(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.lower()
    if parts.scheme.startswith("rtsp"):
        return "RTSP stream"
    if path.endswith(".m3u8"):
        return "HLS stream"
    if "mjpg" in path or "mjpeg" in path or "video.cgi" in path:
        return "MJPEG stream"
    if path.endswith((".jpg", ".jpeg", ".png")) or "snapshot" in path:
        return "Snapshot"
    return "Stream"


def check_url(url: str) -> str:
    """The address as given, or ValueError saying why it cannot be registered."""
    url = str(url or "").strip()
    if not url or len(url) > 1000 or any(character.isspace() for character in url):
        raise ValueError("Give the camera's full stream address, for example rtsp://192.168.1.20:554/stream1.")
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 -- raises on a malformed port
    except ValueError as error:
        raise ValueError(f"That is not a valid address: {error}") from None
    if parts.scheme not in SCHEMES or not parts.hostname:
        raise ValueError("Use an rtsp://, rtsps://, http:// or https:// address with a host.")
    if parts.username or parts.password:
        raise ValueError("Leave the login out of the address; type it into the login fields instead, so it is stored "
                         "in the Windows credential store and never in a file.")
    return url


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "camera"


class MyCameras:
    def __init__(self, path: Path | str = DEFAULT_REGISTRY, secrets: Any = None) -> None:
        self.path, self.secrets = Path(path), secrets
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- registry
    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return []
        return [item for item in data.get("cameras", []) if isinstance(item, dict) and item.get("id")]

    def _save(self, cameras: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_suffix(".partial")
        partial.write_text(json.dumps({"cameras": cameras}, indent=2), encoding="utf-8")
        partial.replace(self.path)

    @staticmethod
    def _public(camera: dict[str, Any]) -> dict[str, Any]:
        parts = urlsplit(camera["url"])
        return {"id": camera["id"], "name": camera["name"], "kind": camera["kind"], "host": parts.hostname,
                "address": camera["url"], "login": camera.get("login", False), "latitude": camera.get("latitude"),
                "longitude": camera.get("longitude"), "added_at": camera.get("added_at")}

    def add(self, name: str, url: str, latitude: float | None = None, longitude: float | None = None,
            username: str = "", password: str = "") -> dict[str, Any]:
        name = " ".join(str(name or "").split())[:60]
        if not name:
            raise ValueError("Give the camera a name, such as Front door.")
        url = check_url(url)
        if (latitude is None) != (longitude is None) or latitude is not None and not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("Give both latitude and longitude, or neither.")
        if (username or password) and self.secrets is None:
            raise ValueError("The credential store is not available, so a camera login cannot be kept safely.")
        with self._lock:
            cameras = self._load()
            identifier = "mine:" + slug(name)
            if any(camera["id"] == identifier for camera in cameras):
                raise ValueError(f"A camera called {name} is already registered; remove it first or choose another name.")
            if any(camera["url"] == url for camera in cameras):
                raise ValueError("That address is already registered.")
            camera = {"id": identifier, "name": name, "url": url, "kind": kind_of(url), "login": bool(username or password),
                      "latitude": latitude, "longitude": longitude, "added_at": datetime.now(UTC).isoformat(timespec="seconds")}
            if camera["login"]:
                self.secrets.store(self._secret(identifier), json.dumps({"username": username, "password": password}), scopes=[SCOPE])
            cameras.append(camera)
            self._save(cameras)
        return {"my_cameras": True, "added": self._public(camera),
                "basis": "Registered by its exact address. The login, if any, is in the Windows credential store, not in a file."}

    def list(self) -> dict[str, Any]:
        cameras = [self._public(camera) for camera in self._load()]
        return {"my_cameras": True, "cameras": cameras, "count": len(cameras),
                "basis": "Only cameras you registered by hand. HAYAT never searches your network for cameras."}

    def remove(self, camera_id: str) -> dict[str, Any]:
        with self._lock:
            cameras = self._load()
            kept = [camera for camera in cameras if camera["id"] != camera_id]
            if len(kept) == len(cameras):
                raise ValueError(f"No registered camera {camera_id}.")
            removed = next(camera for camera in cameras if camera["id"] == camera_id)
            if removed.get("login") and self.secrets is not None:
                try:
                    self.secrets.delete(self._secret(camera_id))
                except Exception:  # an already missing secret is the goal anyway
                    log_swallowed(_log, "removing a camera login from the credential store")
            self._save(kept)
        return {"my_cameras": True, "removed": camera_id, "name": removed["name"]}

    def find(self, text: str) -> dict[str, Any] | None:
        """A registered camera named in a sentence ("front door" -> Front door)."""
        wanted = slug(text)
        cameras = self._load()
        exact = next((camera for camera in cameras if camera['id'] == text or slug(camera['name']) == wanted), None)
        if exact:
            return exact
        matches = [camera for camera in cameras if wanted and (wanted in slug(camera['name']) or slug(camera['name']) in wanted)]
        if len(matches) > 1:
            raise ValueError('More than one of your cameras matches. Choose its full registered name in My cameras.')
        return matches[0] if matches else None

    # ----------------------------------------------------------------- playing
    def play(self, event_id: str) -> dict[str, Any]:
        """What the chat card needs to open the player; the login stays out of this result."""
        camera = self.find(event_id)
        if camera is None:
            raise ValueError("That is not one of your registered cameras. Open Studios → My cameras to add it.")
        return {"intelligence": True, "my_camera": True, "tool": "my_camera_play", "event_id": camera["id"], "title": camera["name"],
                "video_url": camera["url"], "feed": "my_cameras", "mode": "stream", "live": True,
                "media_label": camera["kind"], "provider": "Your camera", "place": camera["name"], "source_url": "",
                "login": camera.get("login", False), "alternatives": [], "has_still": False,
                "notice": "Your own camera, played locally. Nothing is recorded or sent anywhere."}

    def login(self, camera_id: str) -> tuple[str, str]:
        """For the local player only: the camera's login, leased from the credential store for a moment."""
        camera = self.find(camera_id)
        if camera is None or not camera.get("login") or self.secrets is None:
            return "", ""
        token = self.secrets.issue_lease(self._secret(camera["id"]), SCOPE, ttl_seconds=30)
        data = json.loads(self.secrets.redeem(token, SCOPE))
        return str(data.get("username", "")), str(data.get("password", ""))

    @staticmethod
    def _secret(camera_id: str) -> str:
        return "my_camera_" + camera_id.split(":", 1)[-1].replace("-", "_")


def registered(url: str, path: Path | str = DEFAULT_REGISTRY) -> str:
    """The player's check: the exact address of a registered camera, or ''."""
    return url if any(camera["url"] == url for camera in MyCameras(path)._load()) else ""


def register_my_camera_tools(registry: Any, cameras: MyCameras) -> None:
    def obj(properties, required=()):
        return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
    registry.register("my_camera_list", "List the owner's own registered cameras (name, kind, host). No logins are shown.",
                      obj({}), "low", "filesystem_read", cameras.list)
    registry.register("my_camera_add", "Register one of the owner's own cameras by its exact stream address (rtsp/http/https). "
                      "Logins are never taken here: the owner types them in Studios → My cameras.",
                      obj({"name": {"type": "string", "minLength": 1, "maxLength": 60},
                           "url": {"type": "string", "minLength": 8, "maxLength": 1000},
                           "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                           "longitude": {"type": "number", "minimum": -180, "maximum": 180}}, ["name", "url"]),
                      "medium", "filesystem_write_project", cameras.add)
    registry.register("my_camera_remove", "Remove one of the owner's registered cameras and its stored login.",
                      obj({"camera_id": {"type": "string", "maxLength": 80}}, ["camera_id"]), "medium", "filesystem_write_project",
                      cameras.remove)
    registry.register("my_camera_play", "Play one of the owner's own registered cameras in HAYAT's local player.",
                      obj({"event_id": {"type": "string", "maxLength": 80}}, ["event_id"]), "low", "filesystem_read", cameras.play)


def my_camera_report(tool: str, value: dict[str, Any]) -> str:
    if tool == "my_camera_list":
        if not value.get("cameras"):
            return "You have no registered cameras yet. Add one in Studios → My cameras (its exact address, and a login if it needs one)."
        return "\n".join([f"Your cameras ({value['count']}):"] + [f"• {item['name']} · {item['kind']} · {item['host']}"
                                                                      + (" · login stored" if item["login"] else "")
                                                                      for item in value["cameras"]] + [value["basis"]])
    if tool == "my_camera_add":
        item = value["added"]
        return f"Added {item['name']} ({item['kind']}, {item['host']}). Say \"play my {item['name'].lower()} camera\" to watch it."
    if tool == "my_camera_remove":
        return f"Removed {value['name']} and its stored login."
    return f"Playing your camera {value.get('title')} locally. {value.get('notice', '')}"


# ---------------------------------------------------------------------- chat
_LEAD = r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
_LIST = re.compile(_LEAD + r"(?:show|list|what\s+are)\s+(?:me\s+)?my\s+(?:own\s+)?cam(?:era)?s\s*[?.!]*$", re.I)
_PLAY = re.compile(_LEAD + r"(?:play|show|open|watch|view)\s+(?:me\s+)?my\s+(?P<name>.{1,40}?)\s+cam(?:era)?\s*[?.!]*$", re.I)


def my_camera_request(text: str, cameras: MyCameras | None = None) -> dict[str, Any] | None:
    value = " ".join(str(text or "").split())
    if _LIST.match(value):
        return {"tool": "my_camera_list", "arguments": {}}
    match = _PLAY.match(value)
    if match and (cameras is None or cameras.find(match["name"]) is not None):
        return {"tool": "my_camera_play", "arguments": {"event_id": match["name"]}}
    return None
