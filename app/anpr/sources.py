"""Which video sources the recogniser may read: the owner's files, devices and registered cameras.

Accepted: an image or video file on this computer, a USB webcam by index, and
an RTSP camera the owner has registered as authorized whose address is on a
private network (home, office or parking-site LAN). Refused: web streams and
public cameras, including the public traffic cameras in Global Intelligence.
Reading plates from cameras the owner does not control would track other
people's cars, and that needs the camera operator's legal authority.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
VIDEO_TYPES = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv", ".mpg", ".mpeg", ".ts"}


@dataclass(frozen=True)
class Source:
    kind: str          # image | video | webcam | rtsp
    target: str | int  # path, device index or URL
    label: str


def _private(host: str) -> bool:
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return False
    if not addresses:
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not (ip.is_private or ip.is_loopback or ip.is_link_local) or ip.is_multicast:
            return False
    return True


def redact(url: str) -> str:
    """An RTSP address without its password, for display and records."""
    parts = urlsplit(url)
    if parts.password:
        netloc = f"{parts.username}:***@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
        return parts._replace(netloc=netloc).geturl()
    return url


def check_source(value: str | int, authorized_cameras: list[str] | tuple[str, ...] = ()) -> Source:
    """A Source the recogniser may open, or PermissionError/ValueError saying exactly why not."""
    if isinstance(value, int) or str(value).strip().isdigit() or str(value).lower().startswith("webcam:"):
        index = int(str(value).lower().removeprefix("webcam:"))
        if not 0 <= index <= 16:
            raise ValueError("A webcam is a device number from 0 to 16.")
        return Source("webcam", index, f"Webcam {index}")
    text = str(value).strip()
    scheme = urlsplit(text).scheme.lower()
    if scheme in ("rtsp", "rtsps"):
        if text not in authorized_cameras:
            raise PermissionError("Register this RTSP camera as one you own or are authorized to use (Settings → "
                                  "Authorized cameras) before reading plates from it.")
        host = urlsplit(text).hostname or ""
        if not _private(host):
            raise PermissionError(f"{host} is not on a private network. Plates are read only from cameras on your own "
                                  "network (home, office or parking-site LAN), not from public or internet cameras.")
        return Source("rtsp", text, redact(text))
    # Any other URL (a one-letter "scheme" is a Windows drive, C:\...).
    if len(scheme) > 1 or text.startswith(("intelligence:", "cameras_", "livestreams:")):
        raise PermissionError("Web streams and public cameras (including Global Intelligence's traffic cameras) are not "
                              "accepted. Use your own files, a USB webcam, or a registered RTSP camera on your network.")
    path = Path(text).expanduser()
    if not path.is_file():
        raise ValueError(f"No such file: {path}")
    suffix = path.suffix.lower()
    if suffix in IMAGE_TYPES:
        return Source("image", str(path), path.name)
    if suffix in VIDEO_TYPES:
        return Source("video", str(path), path.name)
    raise ValueError(f"Unsupported file type {suffix or '(none)'}; use an image or a video.")
