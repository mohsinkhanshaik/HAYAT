"""Publish the business website to Netlify's free hosting, then check it is really live.

Only ever runs through the owner's confirmation (app/safety/confirmations.py).
The owner's Netlify personal access token lives in the Windows credential
store under ``netlify_token`` (scope ``publish:netlify``) and is leased for
one deploy at a time. After deploying, the live page is fetched and compared
with the local file, so "published" means it was seen online, not that an API
said so.
"""
from __future__ import annotations

import hashlib
import io
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

import httpx

API = "https://api.netlify.com/api/v1"
SECRET = "netlify_token"
SCOPE = "publish:netlify"


def _normal(html: str) -> str:
    return re.sub(r"\s+", " ", html or "").strip()


def site_digest(html: str) -> str:
    return hashlib.sha256(_normal(html).encode("utf-8")).hexdigest()[:16]


class NetlifyPublisher:
    """Create a site once, deploy a folder as a ZIP, wait until ready, fetch it back."""

    def __init__(self, secrets: Any, client: Callable[[], httpx.Client] | None = None, poll_seconds: float = 2.0,
                 wait_seconds: float = 90.0) -> None:
        self.secrets = secrets
        self.client = client or (lambda: httpx.Client(timeout=60.0, follow_redirects=True))
        self.poll_seconds, self.wait_seconds = poll_seconds, wait_seconds

    def ready(self) -> bool:
        if self.secrets is None:
            return False
        try:
            return any(item.get("name") == SECRET for item in self.secrets.list())
        except Exception:
            return False

    def store_token(self, token: str) -> dict:
        token = (token or "").strip()
        if self.secrets is None:
            raise ValueError("Publishing can't be set up in safe mode: there is no credential store.")
        if len(token) < 20 or " " in token:
            raise ValueError("That doesn't look like a Netlify personal access token. Create one under Netlify, "
                             "User settings, Applications, Personal access tokens.")
        self.secrets.store(SECRET, token, scopes=[SCOPE])
        return {"stored": True, "where": "Windows credential store", "secret": SECRET}

    def _token(self) -> str:
        if not self.ready():
            raise PermissionError("Publishing isn't set up. In Business, Launch, press Publishing setup and paste a "
                                  "Netlify personal access token.")
        lease = self.secrets.issue_lease(SECRET, SCOPE, ttl_seconds=60)
        return self.secrets.redeem(lease, SCOPE)

    @staticmethod
    def zip_folder(folder: Path) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(Path(folder).rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(folder).as_posix())
        return buffer.getvalue()

    def publish(self, folder: Path, name: str, site_id: str = "") -> dict[str, Any]:
        """Deploy ``folder`` and return where it went, after checking the live page matches."""
        folder = Path(folder)
        index = folder / "index.html"
        if not index.is_file():
            raise FileNotFoundError("Make the website first: there is no index.html to publish.")
        expected = site_digest(index.read_text(encoding="utf-8"))
        token = self._token()
        headers = {"Authorization": f"Bearer {token}", "User-Agent": "HAYAT-local-assistant"}
        with self.client() as http:
            if not site_id:
                slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:40] or "my-business"
                for attempt in range(3):
                    candidate = slug if attempt == 0 else f"{slug}-{hashlib.sha1(f'{slug}{time.time()}'.encode()).hexdigest()[:5]}"
                    response = http.post(f"{API}/sites", headers=headers, json={"name": candidate})
                    if response.status_code == 422:
                        continue  # the name is taken; try a suffixed one
                    response.raise_for_status()
                    site_id = response.json()["id"]
                    break
                else:
                    raise RuntimeError("Netlify refused every site name tried. Nothing was published.")
            response = http.post(f"{API}/sites/{site_id}/deploys", headers={**headers, "Content-Type": "application/zip"},
                                 content=self.zip_folder(folder))
            response.raise_for_status()
            deploy = response.json()
            deadline = time.monotonic() + self.wait_seconds
            while deploy.get("state") not in {"ready", "error"} and time.monotonic() < deadline:
                time.sleep(self.poll_seconds)
                deploy = http.get(f"{API}/deploys/{deploy['id']}", headers=headers).json()
            if deploy.get("state") != "ready":
                raise RuntimeError(f"The deploy did not finish (state: {deploy.get('state', 'unknown')}). "
                                   "Check it in your Netlify account.")
            url = deploy.get("ssl_url") or deploy.get("url") or ""
            site = http.get(f"{API}/sites/{site_id}", headers=headers).json()
            url = site.get("ssl_url") or site.get("url") or url
            live = http.get(url, headers={"Cache-Control": "no-cache"})
        matches = live.status_code == 200 and site_digest(live.text) == expected
        return {"site_id": site_id, "site_url": url, "deploy_id": deploy.get("id"), "status_code": live.status_code,
                "matches": matches,
                "verdict": "live and matches your file" if matches else
                           "deployed, but the live page did not match yet: open it and check"}
