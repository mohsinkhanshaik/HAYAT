"""Checking an email address honestly: its domain's public mail setup, whether the address is published,
and -- for the owner's own address only -- where the owner is now.

Why this exists: on 2026-10-05 a reply claimed an "IP trace" put the owner's address
mohsinkhanshaik@hamuzair.com in Charlotte, NC. Nothing had run; the real Windows fix was in another
state. Background learning then saved "find current location of <address>" as a research skill. That
question has no honest research answer: an email address carries no location. Mail goes to the
domain's mail provider, wherever its servers are, and HAYAT does not trace who holds an address.

What can be checked, and is useful for someone sending business mail from that address:

* The domain's mail setup, from public DNS: which provider receives its mail (MX), whether SPF says
  which servers may send as it, whether DMARC tells receivers what to do with forgeries, and which
  common DKIM selectors are published. Missing SPF or DMARC is a usual reason mail lands in spam.
* Whether the address is printed on the domain's home or contact page, where address harvesters find
  it. Read with HAYAT's public fetcher, which honours robots.txt.
* For the owner's own address ("check my email address ..."), the answer to the question that was
  really asked: where this computer is now, from the Windows location service.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger("hayat.email_check")

ADDRESS = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@([A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+)$")
#: MX host fragment -> who runs the mail. First match wins.
PROVIDERS = (
    ("aspmx.l.google.com", "Google Workspace (Gmail)"), ("google.com", "Google Workspace (Gmail)"),
    ("googlemail.com", "Google Workspace (Gmail)"), ("protection.outlook.com", "Microsoft 365 (Outlook)"),
    ("outlook.com", "Microsoft 365 (Outlook)"), ("secureserver.net", "GoDaddy email"), ("titan.email", "Titan email"),
    ("zoho", "Zoho Mail"), ("hostinger", "Hostinger email"), ("yahoodns.net", "Yahoo Mail"),
    ("icloud.com", "iCloud Mail"), ("protonmail", "Proton Mail"), ("messagingengine.com", "Fastmail"),
    ("emailsrvr.com", "Rackspace email"), ("mimecast", "Mimecast"), ("pphosted.com", "Proofpoint"),
    ("amazonaws.com", "Amazon SES"), ("mailgun.org", "Mailgun"), ("namecheap", "Namecheap Private Email"),
    ("ovh.net", "OVH email"), ("ionos", "IONOS email"), ("yandex", "Yandex Mail"),
)
DKIM_SELECTORS = ("google", "selector1", "selector2", "default", "k1", "k2", "dkim", "s1", "s2", "mail", "smtp", "zoho",
                  "zmail", "fm1", "protonmail")  # zmail: Zoho's default; fm1: Fastmail; protonmail: Proton
NO_LOCATION = ("An email address carries no location. Mail is delivered to the domain's mail provider, wherever its "
               "servers are, so nothing about the address says where its holder is, and HAYAT does not trace who holds "
               "an address.")


def _dns_resolver(name: str, kind: str) -> list[str]:
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5
    try:
        answer = resolver.resolve(name, kind)
    except dns.resolver.NXDOMAIN as exc:
        raise LookupError("no such domain") from exc
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except dns.exception.Timeout as exc:
        raise TimeoutError("DNS did not answer") from exc
    if kind == "MX":
        return [f"{record.preference} {str(record.exchange).rstrip('.')}" for record in answer]
    return [b"".join(record.strings).decode("utf-8", "replace") for record in answer]


def _runs(host: str, fragment: str) -> bool:
    """Whether an MX host belongs to a provider: "mx.zoho.com" is Zoho, "notgoogle.com.evil.net" is not Google."""
    labels = host.split(".")
    if "." in fragment:
        return host == fragment or host.endswith("." + fragment)
    return any(label == fragment or label.startswith(fragment) for label in labels[:-1])


#: Mechanisms that cost a DNS lookup when a receiver checks SPF (RFC 7208 §4.6.4 allows ten).
_SPF_LOOKUPS = re.compile(r"(?i)(?:^|\s)[+?~-]?(?:include:|a\b|a:|a/|mx\b|mx:|mx/|ptr\b|ptr:|exists:|redirect=)")


def spf_problem(record: str) -> str:
    """What is wrong with a single SPF record, or '' when nothing visible is."""
    terms = record.split()[1:]
    final = next((term.lower() for term in reversed(terms) if term.lower().lstrip("+?~-") == "all"), None)
    if final in ("all", "+all"):
        return "it ends in +all, which lets any server on the internet send mail as this domain"
    lookups = len(_SPF_LOOKUPS.findall(record))
    if lookups > 10:
        return f"it needs {lookups} DNS lookups; receivers stop at 10 and then fail SPF"
    if final == "?all":
        return "it ends in ?all, which tells receivers nothing about other senders"
    if final is None and not any(term.lower().startswith("redirect=") for term in terms):
        return "it has no all rule at the end, so other senders are not ruled out"
    return ""


class EmailAddressCheck:
    def __init__(self, resolve: Callable[[str, str], list[str]] | None = None,
                 fetch: Callable[[str], bytes] | None = None, locate: Callable[[], dict[str, Any]] | None = None,
                 own_addresses: Callable[[], set[str]] | None = None) -> None:
        self.resolve = resolve or _dns_resolver
        self.fetch, self.locate = fetch, locate
        self.own_addresses = own_addresses or (lambda: set())

    def mail_setup(self, domain: str) -> dict[str, Any]:
        """The domain's public mail records. The lookups run side by side (one after another, about 20 of them took
        1.5-2 s, and up to 5 s each when a server is slow; hard-tested 2026-10-17)."""
        from concurrent.futures import ThreadPoolExecutor

        result: dict[str, Any] = {"domain": domain}
        names = [domain, "_dmarc." + domain] + [f"{selector}._domainkey.{domain}" for selector in DKIM_SELECTORS]
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="hayat-dns") as pool:
            mx_job = pool.submit(self.resolve, domain, "MX")
            txt_jobs = [pool.submit(self._txt, name) for name in names]
            try:
                rows = mx_job.result()
            except LookupError:
                for job in txt_jobs:
                    job.cancel()
                return {**result, "exists": False}
            except Exception as exc:
                for job in txt_jobs:
                    job.cancel()
                return {**result, "error": f"DNS could not be read: {exc}"}
            txt, dmarc_txt, *dkim_txt = [job.result() for job in txt_jobs]
        mx = []
        for row in rows:
            parts = str(row).split()
            try:
                preference = int(parts[0])
            except (IndexError, ValueError):
                continue
            mx.append((preference, parts[1].rstrip(".").lower() if len(parts) > 1 else ""))
        mx.sort()
        result["exists"] = True
        # RFC 7505 "null MX" (0 .): the domain says it accepts no mail at all. It used to be read as "no such domain".
        result["null_mx"] = bool(mx) and all(not host for _, host in mx)
        result["mx"] = [host for _, host in mx if host]
        result["provider"] = next((name for fragment, name in PROVIDERS if any(_runs(host, fragment) for host in result["mx"])),
                                  "its own or an unrecognised mail server" if result["mx"] else None)
        spf = [record for record in txt if record.lower().startswith("v=spf1") and record[6:7] in ("", " ")]
        result["spf"] = spf[0] if len(spf) == 1 else None
        result["spf_problem"] = ("two SPF records: receivers treat that as an error, so merge them" if len(spf) > 1
                                 else spf_problem(spf[0]) if spf else "none published")
        dmarc = [record for record in dmarc_txt if record.lower().replace(" ", "").startswith("v=dmarc1")]
        result["dmarc"] = dmarc[0] if len(dmarc) == 1 else None
        result["dmarc_problem"] = "two DMARC records: receivers ignore both, so keep one" if len(dmarc) > 1 else ""
        policy = re.search(r"(?i)(?:^|;)\s*p\s*=\s*(none|quarantine|reject)", dmarc[0]) if len(dmarc) == 1 else None
        result["dmarc_policy"] = policy[1].lower() if policy else None
        result["dkim_selectors_found"] = [selector for selector, records in zip(DKIM_SELECTORS, dkim_txt)
                                          if any(re.search(r"(?:^|;)\s*p\s*=\s*[A-Za-z0-9+/]", record) for record in records)]
        return result

    def _txt(self, name: str) -> list[str]:
        try:
            return self.resolve(name, "TXT")
        except Exception:
            log_swallowed(_log, f"reading TXT records for {name}")
            return []

    def published(self, domain: str, address: str) -> dict[str, Any]:
        if self.fetch is None:
            return {"checked": [], "found_on": [], "note": "Web pages were not read."}
        checked, found, notes = [], [], []
        pattern = re.compile(re.escape(address).replace(r"\@", "@").replace("@", r"\s*(?:@|\[at\]|\(at\))\s*"), re.I)
        for url in (f"https://{domain}/", f"https://{domain}/contact", f"https://{domain}/contact-us"):
            try:
                content = self.fetch(url).decode("utf-8", "replace")
            except PermissionError:
                notes.append(f"{url}: robots.txt asks agents not to read it, so HAYAT did not")
                continue
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status not in (404, 410):
                    notes.append(f"{url}: could not be read" + (f" (HTTP {status})" if status else ""))
                continue
            checked.append(url)
            if pattern.search(content):
                found.append(url)
        return {"checked": checked, "found_on": found, "notes": notes}

    def check(self, address: str, mine: bool = False) -> dict[str, Any]:
        address = str(address or "").strip().strip("<>").lower()
        match = ADDRESS.match(address)
        if not match:
            raise ValueError("That does not look like an email address (name@domain).")
        domain = match[1]
        mine = bool(mine) or address in {item.lower() for item in self.own_addresses()}
        mail = self.mail_setup(domain)
        published = self.published(domain, address) if mail.get("exists") else {"checked": [], "found_on": []}
        location: dict[str, Any] = {"answer": NO_LOCATION}
        if mine and self.locate is not None:
            try:
                location["you_now"] = self.locate()
            except Exception as exc:
                location["you_now_error"] = f"{type(exc).__name__}: {exc}"[:200]
        elif not mine:
            location["note"] = 'For your own address, say "check my email address ..." to also see where you are now.'
        return {"address": address, "mine": mine, "mail": mail, "published": published, "location": location,
                "advice": advice(mail, published), "kind": "email_address_check",
                "basis": "Public DNS records of the domain and its public web pages (robots.txt honoured). Nothing was "
                         "sent to the address and no person was looked up."}


def advice(mail: dict[str, Any], published: dict[str, Any]) -> list[str]:
    if mail.get("exists") is False:
        return [f"{mail['domain']} does not exist in DNS, so nothing can be delivered to this address."]
    if mail.get("error"):
        return []
    tips = []
    if mail.get("null_mx"):
        tips.append(f"{mail['domain']} publishes a null MX record: it states that it accepts no mail, so nothing can be "
                    "delivered to this address.")
    elif not mail.get("mx"):
        tips.append("No mail server (MX) is published, so this address cannot receive mail.")
    problem = mail.get("spf_problem", "")
    if not mail.get("spf"):
        tips.append("Merge the two SPF records into one." if problem.startswith("two") else
                    "Publish an SPF record (TXT v=spf1 ...) listing your mail provider; without it, mail you send is "
                    "more likely to land in spam.")
    elif problem:
        tips.append(f"Fix the SPF record: {problem}. End it with -all or ~all after your provider's include.")
    if mail.get("dmarc_problem"):
        tips.append("Delete one of the two DMARC records at _dmarc." + mail["domain"] + "; with two, receivers use neither.")
    elif not mail.get("dmarc"):
        tips.append("Publish a DMARC record at _dmarc." + mail["domain"] + " (start with p=none and a report address), so "
                    "receivers know what to do with mail that forges your domain.")
    elif mail.get("dmarc_policy") == "none":
        tips.append("DMARC is only monitoring (p=none); once your real mail passes, move to p=quarantine.")
    if not mail.get("dkim_selectors_found") and not mail.get("null_mx"):
        tips.append("No DKIM key was found under the common selectors; your provider's admin page shows how to turn "
                    "DKIM signing on (it may use a selector HAYAT did not try).")
    if published.get("found_on"):
        tips.append("The address is printed on " + ", ".join(published["found_on"]) + ", where address harvesters "
                    "collect it for spam; a contact form or an image keeps it off.")
    return tips


def email_check_request(text: str) -> dict[str, Any] | None:
    """'check my email address x@y.com', 'where is x@y.com', 'find current location of x@y.com'."""
    value = " ".join(str(text or "").split())
    found = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", value)
    if not found:
        return None
    # Only the words just before the address count, so "email x@y.com about the location of the meeting"
    # stays a message and is not read as a request to locate x@y.com.
    before = value[:found.start()].lower().rstrip(" :") + " "
    after = value[found.end():].lower().strip(" ?.!")
    owner_words = r"(?:(?:my|our|the|this)\s+)?(?:own\s+)?(?:e-?mail\s+)?(?:address\s+)?"
    asks_location = (re.search(r"(?:where\s+is|where's|locate|trace|track|find|get|what\s+is|what's|show)\s+(?:me\s+)?(?:the\s+)?"
                               r"(?:current\s+|exact\s+|real\s+)?(?:location|whereabouts|ip(?:\s+address)?)\s+(?:of|for)\s+"
                               + owner_words + "$", before)
                     or re.search(r"(?:where\s+is|where's|locate|trace|track)\s+" + owner_words + "$", before)
                     or (re.fullmatch(r"(?:where\s+is\s+)?", before.strip()) and re.fullmatch(r"(?:'s\s+)?(?:current\s+)?location", after)))
    asks_check = (re.search(r"(?:check|inspect|audit|test|diagnose|look\s+up|lookup|verify)\s+(?:the\s+)?(?:mail\s+|e-?mail\s+)?"
                            r"(?:setup|set-up|settings|deliverability|dns|spf|dmarc|dkim|records?)?\s*(?:of|for|on)?\s*"
                            + owner_words + "$", before)
                  or re.fullmatch(r"is\s+" + owner_words, before) and re.search(r"set\s*up|configured|ok|working|safe", after))
    if not (asks_location or asks_check):
        return None
    mine = bool(re.search(r"\b(?:my|mine|our)\b", before + " " + after))
    return {"tool": "email_address_check", "arguments": {"address": found[0].lower(), "mine": mine}}


def direct_email_check_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    request = email_check_request(text)
    if not request or "email_address_check" not in set(names):
        return None
    return [{"description": "Check the address's domain mail setup and where it is published"
                            + (", and read where this computer is now" if request["arguments"]["mine"] else "") + ".",
             "tool": "email_address_check", "arguments": request["arguments"],
             "expected": {"type": "tool_success"}, "max_retries": 0}]


def register_email_check_tools(registry: Any, fetch: Callable[[str], bytes] | None = None,
                               own_addresses: Callable[[], set[str]] | None = None) -> EmailAddressCheck:
    def locate() -> dict[str, Any]:
        result = registry.execute("device_location", {"wait_seconds": 8})
        if not result.success:
            raise RuntimeError(result.error or "the location tool failed")
        return result.output
    checker = EmailAddressCheck(fetch=fetch, own_addresses=own_addresses,
                                locate=locate if "device_location" in registry.names() else None)
    registry.register(
        "email_address_check",
        "Check an email address's domain mail setup: MX provider, SPF, DMARC and DKIM records, what to fix so mail "
        "is not marked as spam, and whether the address is printed on the domain's website. An address has no "
        "location and its holder is never traced; mine=true (the owner's own address) adds the owner's location.",
        {"type": "object", "properties": {"address": {"type": "string", "maxLength": 254},
                                          "mine": {"type": "boolean"}},
         "required": ["address"], "additionalProperties": False},
        "low", "public_api_get", checker.check)
    return checker
