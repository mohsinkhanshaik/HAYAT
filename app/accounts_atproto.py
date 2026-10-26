"""Bluesky sign-in with the browser: AT Protocol OAuth, with no app registration.

The AT Protocol lets a program on your own computer sign in as a "localhost client": its client ID is
`http://localhost?redirect_uri=…&scope=…`, so nothing has to be registered anywhere. The sign-in:

1. finds where your account lives: your handle → your DID (resolveHandle) → your PDS (your DID document) →
   the PDS's authorization server (its OAuth metadata);
2. sends the sign-in request to that server first (PAR), with PKCE and a DPoP key made for this sign-in;
3. opens the server's sign-in page in your browser; you sign in there and approve, and the tab comes back to
   HAYAT on this computer (app/accounts.Loopback);
4. exchanges the code for tokens bound to the DPoP key, and checks they belong to the DID it looked up.

Every request then carries a fresh DPoP proof signed with that key (ES256); the key is kept with the tokens in
Windows Credential Manager. Servers hand out "nonces" that proofs must repeat: a request refused for a missing one
is retried once with it. The app-password way stays available beside this.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from typing import Any
from urllib.parse import quote, urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

SCOPE = "atproto transition:generic"
RESOLVER = "https://bsky.social/xrpc/com.atproto.identity.resolveHandle"
PLC = "https://plc.directory/"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def new_key() -> str:
    """A P-256 key for DPoP, as PEM (kept only in the credential store)."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode("ascii")


def proof(key_pem: str, method: str, url: str, nonce: str = "", access_token: str = "") -> str:
    """A DPoP proof (RFC 9449): a JWT signed with the sign-in's own key, naming this request."""
    key = serialization.load_pem_private_key(key_pem.encode("ascii"), password=None)
    numbers = key.public_key().public_numbers()
    jwk = {"kty": "EC", "crv": "P-256", "x": _b64(numbers.x.to_bytes(32, "big")), "y": _b64(numbers.y.to_bytes(32, "big"))}
    header = {"typ": "dpop+jwt", "alg": "ES256", "jwk": jwk}
    parts = urlsplit(url)
    payload: dict[str, Any] = {"jti": secrets.token_urlsafe(16), "htm": method.upper(),
                               "htu": f"{parts.scheme}://{parts.netloc}{parts.path}", "iat": int(time.time())}
    if nonce:
        payload["nonce"] = nonce
    if access_token:
        payload["ath"] = _b64(hashlib.sha256(access_token.encode("ascii")).digest())
    signing_input = _b64(json.dumps(header, separators=(",", ":")).encode()) + "." + \
        _b64(json.dumps(payload, separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    return signing_input + "." + _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def localhost_client_id(redirect: str) -> str:
    return f"http://localhost?redirect_uri={quote(redirect, safe='')}&scope={quote(SCOPE, safe='')}"


class AtprotoAccounts:
    """Mixed into AccountManager: the Bluesky browser sign-in and DPoP-bound requests."""

    # ---- where the account lives
    def _bluesky_where(self, handle: str) -> dict[str, str]:
        handle = handle.strip().lstrip("@").lower()
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", handle):
            raise ValueError("Type your Bluesky handle first, such as name.bsky.social.")
        did = self._json("GET", RESOLVER + "?handle=" + quote(handle)).get("did", "")
        if did.startswith("did:plc:"):
            document = self._json("GET", PLC + did)
        elif did.startswith("did:web:"):
            document = self._json("GET", f"https://{did[8:]}/.well-known/did.json")
        else:
            raise PermissionError(f"Bluesky could not find the account {handle}.")
        pds = next((service.get("serviceEndpoint", "") for service in document.get("service") or []
                    if service.get("id", "").endswith("#atproto_pds")), "")
        if not pds.startswith("https://"):
            raise PermissionError(f"{handle}'s account server could not be found.")
        resource = self._json("GET", pds.rstrip("/") + "/.well-known/oauth-protected-resource")
        issuer = (resource.get("authorization_servers") or [""])[0]
        if not issuer.startswith("https://"):
            raise PermissionError(f"{urlsplit(pds).netloc} does not offer OAuth sign-in; use an app password instead.")
        meta = self._json("GET", issuer.rstrip("/") + "/.well-known/oauth-authorization-server")
        for name in ("pushed_authorization_request_endpoint", "authorization_endpoint", "token_endpoint"):
            if not str(meta.get(name, "")).startswith("https://"):
                raise PermissionError(f"{urlsplit(issuer).netloc} has no {name.replace('_', ' ')}.")
        return {"handle": handle, "did": did, "pds": pds.rstrip("/"), "issuer": issuer, "par": meta["pushed_authorization_request_endpoint"],
                "authorize": meta["authorization_endpoint"], "token": meta["token_endpoint"]}

    # ---- DPoP requests with nonce retry
    def _dpop_post(self, url: str, form: dict[str, str], key: str, nonce: str = "") -> tuple[dict[str, Any], str]:
        """A form POST to the authorization server; returns (body, the nonce to use next)."""
        for _attempt in range(2):
            response = self._call("POST", url, data=form, headers={"DPoP": proof(key, "POST", url, nonce),
                                                                   "Accept": "application/json"})
            fresh = response.headers.get("DPoP-Nonce", "") if hasattr(response, "headers") else ""
            body = response.json() if getattr(response, "content", b"") else {}
            if getattr(response, "status_code", 200) == 400 and body.get("error") == "use_dpop_nonce" and fresh:
                nonce = fresh
                continue
            if getattr(response, "status_code", 200) >= 400:
                raise PermissionError(f"Bluesky refused the sign-in ({body.get('error_description') or body.get('error') or response.status_code}).")
            return body, fresh or nonce
        raise PermissionError("Bluesky kept asking for a new proof; try again.")

    def _bluesky_start(self, handle: str, redirect: str, state: str, challenge: str) -> tuple[str, dict[str, Any]]:
        """Send the sign-in request ahead (PAR); return the page to open and what the exchange needs."""
        where = self._bluesky_where(handle)
        key = new_key()
        client_id = localhost_client_id(redirect)
        body, nonce = self._dpop_post(where["par"], {"client_id": client_id, "response_type": "code", "redirect_uri": redirect,
                                                     "scope": SCOPE, "state": state, "code_challenge": challenge,
                                                     "code_challenge_method": "S256", "login_hint": where["handle"]}, key)
        if not body.get("request_uri"):
            raise PermissionError("Bluesky did not accept the sign-in request.")
        link = f"{where['authorize']}?client_id={quote(client_id, safe='')}&request_uri={quote(body['request_uri'], safe='')}"
        return link, {**where, "key": key, "nonce": nonce, "client_id": client_id}

    def _bluesky_finish(self, context: dict[str, Any], code: str, verifier: str, redirect: str) -> dict[str, Any]:
        body, nonce = self._dpop_post(context["token"], {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                                                         "client_id": context["client_id"], "code_verifier": verifier},
                                      context["key"], context["nonce"])
        if body.get("sub") != context["did"]:
            raise PermissionError("Bluesky's tokens were for a different account than the handle given; nothing was kept.")
        if not body.get("access_token"):
            raise PermissionError("Bluesky did not issue a token.")
        value = {"method": "oauth", "access_token": body["access_token"], "refresh_token": body.get("refresh_token", ""),
                 "expires_at": time.time() + float(body.get("expires_in") or 900), "did": context["did"],
                 "handle": context["handle"], "pds": context["pds"], "token_endpoint": context["token"],
                 "client_id": context["client_id"], "dpop_key": context["key"], "auth_nonce": nonce, "pds_nonce": ""}
        self._need_broker()
        self.broker.store("account.bluesky", json.dumps(value), scopes=["account:bluesky"])
        return value

    def _bluesky_oauth_refresh(self, value: dict[str, Any]) -> dict[str, Any]:
        if not value.get("refresh_token"):
            raise PermissionError("The Bluesky sign-in has ended; sign in again.")
        body, nonce = self._dpop_post(value["token_endpoint"], {"grant_type": "refresh_token", "refresh_token": value["refresh_token"],
                                                                "client_id": value["client_id"]},
                                      value["dpop_key"], value.get("auth_nonce", ""))
        if not body.get("access_token") or body.get("sub", value["did"]) != value["did"]:
            raise PermissionError("Bluesky did not renew the sign-in; sign in again.")
        value.update(access_token=body["access_token"], refresh_token=body.get("refresh_token") or value["refresh_token"],
                     expires_at=time.time() + float(body.get("expires_in") or 900), auth_nonce=nonce)
        self.broker.store("account.bluesky", json.dumps(value), scopes=["account:bluesky"])
        return value

    def _bluesky_xrpc(self, method: str, nsid: str, **kwargs: Any) -> Any:
        """A call to the account's own server with a DPoP-bound token (a refused nonce is retried once)."""
        self._token("bluesky")  # renews first when it is about to expire
        value = self._account_value("bluesky")
        url = f"{value['pds']}/xrpc/{nsid}"
        nonce = value.get("pds_nonce", "")
        for _attempt in range(2):
            headers = {"Authorization": f"DPoP {value['access_token']}", "Accept": "application/json",
                       "DPoP": proof(value["dpop_key"], method, url, nonce, value["access_token"])}
            response = self._call(method, url, headers=headers, **kwargs)
            fresh = response.headers.get("DPoP-Nonce", "") if hasattr(response, "headers") else ""
            if getattr(response, "status_code", 200) == 401 and "use_dpop_nonce" in str(
                    getattr(response, "headers", {}).get("WWW-Authenticate", "")) and fresh:
                nonce = fresh
                continue
            if fresh and fresh != value.get("pds_nonce"):
                value["pds_nonce"] = fresh
                self.broker.store("account.bluesky", json.dumps(value), scopes=["account:bluesky"])
            if getattr(response, "status_code", 200) >= 400:
                detail = ""
                try:
                    detail = str(response.json().get("message") or response.json().get("error") or "")[:160]
                except (ValueError, AttributeError):
                    pass
                raise RuntimeError(f"Bluesky answered {response.status_code}" + (f": {detail}" if detail else ""))
            return response.json() if getattr(response, "content", b"") else {}
        raise RuntimeError("Bluesky kept asking for a new proof; try again.")
