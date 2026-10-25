"""Tools that prepare an outward action for the owner to confirm. None of them can carry it out.

``form_fill_prepare`` fills a web form (sign-up, registration, application,
contact) and stops before sending it. ``publish_website_prepare`` packages
the business website for Netlify. Each hands a full preview to the
confirmation gate; the action runs only when the owner confirms it from the
confirmation window or by saying "confirm action N" at this computer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.safety.confirmations import KINDS, ConfirmationGate, fingerprint
from app.tools.registry import ToolRegistry

EVIDENCE_DIR = "data/artifacts/confirm"


class ActionPreparer:
    def __init__(self, gate: ConfirmationGate, browser: Any = None, launchpad: Any = None, publisher: Any = None):
        self.gate, self.browser, self.launchpad, self.publisher = gate, browser, launchpad, publisher

    def form(self, url: str, fields: dict[str, Any], submit: str = "") -> dict:
        if self.browser is None:
            raise RuntimeError("The browser is not available in this session.")
        if not fields:
            raise ValueError("Say which fields to fill and with what, e.g. {\"Email\": \"me@example.com\"}.")
        plan = self.browser.prepare_form(url, dict(fields), submit, EVIDENCE_DIR)
        site = urlparse(plan["url"]).netloc or plan["url"]
        details = [(field["label"], "(you type it in the confirmation window)" if field["secret"] else field["value"])
                   for field in plan["fields"]]
        details.append(("Button HAYAT will press", plan["submit_label"]))
        notes = ["HAYAT filled these on the page and read them back. Nothing has been sent."]
        if plan["secrets"]:
            notes.append("HAYAT never fills passwords or similar. You type them in the confirmation window; they are "
                         "used once and never stored.")
        if plan["kind"] in {"register", "login"}:
            notes.append("This acts as you on that website. Check it is the site you meant: " + site + ".")
        return self.gate.propose(
            plan["kind"], f"{KINDS[plan['kind']]} on {site}", plan["url"], details,
            lambda secrets: self.browser.submit_prepared(plan, secrets, EVIDENCE_DIR),
            secrets_needed=plan["secrets"], evidence=[plan["evidence"]] if plan["evidence"] else [], notes=notes,
            fingerprint_value=fingerprint(plan["url"], [(f["label"], f["value"]) for f in plan["fields"]]))

    def publish(self, venture: str | int | None = None) -> dict:
        if self.launchpad is None or self.publisher is None:
            raise RuntimeError("Publishing is not available in this session.")
        record = self.launchpad.published(venture)
        if not record["website"] or not Path(record["website"]).is_file():
            self.launchpad.website(record["venture_id"])
            record = self.launchpad.published(record["venture_id"])
        folder = Path(record["website"]).parent
        files = sorted(path for path in folder.rglob("*") if path.is_file())
        site = record["site"]
        details = [("Business", record["business"]),
                   ("Where", "Netlify free hosting (your account)"),
                   ("Address", site["url"] + " (update)" if site else "a new address on netlify.app"),
                   ("Files", ", ".join(f"{path.relative_to(folder).as_posix()} ({path.stat().st_size:,} bytes)"
                                       for path in files))]
        notes = ["Anyone on the internet will be able to see this page once it is published.",
                 "Open the website file first if you have not checked it: " + str(folder / "index.html")]
        if not self.publisher.ready():
            notes.append("Publishing isn't set up yet: in Business, Launch, press Publishing setup first, or "
                         "confirming will fail without sending anything.")

        def run(_secrets: dict[str, str]) -> dict:
            result = self.publisher.publish(folder, record["business"], site["site_id"] if site else "")
            self.launchpad.remember_publish(record["venture_id"], "netlify", result["site_id"], result["site_url"])
            return {**result, "url": result["site_url"]}
        return self.gate.propose("publish", f"Publish the website for {record['business']}", str(folder / "index.html"),
                                 details, run, evidence=[str(folder / "index.html")], notes=notes,
                                 fingerprint_value=fingerprint([(p.name, p.stat().st_size) for p in files]))

    def status(self) -> dict:
        return {"pending": self.gate.pending(), "recent": self.gate.history(10),
                "publishing_ready": bool(self.publisher and self.publisher.ready())}


def register_action_tools(registry: ToolRegistry, preparer: ActionPreparer) -> None:
    registry.register(
        "form_fill_prepare",
        "Fill a web form (sign-up, account or business registration, application, contact form) and STOP before "
        "sending it. Give the page URL, the fields as {label: value}, and optionally the button's name. The owner "
        "sees every value and a screenshot, types any password themselves, and confirms before HAYAT presses the "
        "button. Never fills passwords or card details. Payment forms and CAPTCHAs are refused.",
        {"type": "object", "properties": {
            "url": {"type": "string"}, "fields": {"type": "object", "additionalProperties": {"type": ["string", "number", "boolean"]}},
            "submit": {"type": "string"}}, "required": ["url", "fields"], "additionalProperties": False},
        "medium", "browser_type", preparer.form)
    registry.register(
        "publish_website_prepare",
        "Prepare publishing the business's one-page website to the owner's free Netlify hosting. Shows the files and "
        "address and waits for the owner to confirm; after publishing, the live page is fetched and compared.",
        {"type": "object", "properties": {"venture": {"type": "string"}}, "required": [], "additionalProperties": False},
        "low", "filesystem_read", preparer.publish)
    registry.register(
        "pending_actions",
        "List outward actions waiting for the owner's confirmation and recent results (sent, failed, verified). "
        "Only the owner can confirm; this tool cannot.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "low", "system_read", preparer.status)
