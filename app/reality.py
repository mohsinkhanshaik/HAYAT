"""Narrow contact boundaries; real contacts and clearly labelled roleplay stay separate."""
from __future__ import annotations

import re

_CONTACT = re.compile(
    r"(?i)^(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?:call|phone|contact|connect|reach|put\s+(?:me|us)\s+in\s+touch)\b")


def contact_boundary(text: str) -> str | None:
    value = ' '.join(str(text or '').split())
    if not _CONTACT.match(value):
        return None
    if re.search(r'(?i)\b(?:pretend|role[ -]?play|imagine|fictional conversation|write a scene|script)\b', value):
        return None
    if (re.search(r'(?i)\bja(?:a)?d(?:oo?|u)\b', value)
            and re.search(r'(?i)\b(?:film|movie|krish|krrish|koi[.\s]*mil[.\s]*gaya)\b', value)):
        # Official publisher metadata: https://www.youtube.com/watch?v=ID7oIDuXtf4
        return ("You mean Jaadoo, the fictional alien from Koi... Mil Gaya, starring Hrithik Roshan. "
                "I can't call a fictional character. If you want a pretend conversation with Jaadoo, we can clearly label it as roleplay.")
    if (re.search(r'(?i)\b(?:extraterrestrials?|aliens?)\b', value)
            and not re.search(r'(?i)\b(?:account|api|server|service|company|app|software|game|contact named|'
                              r'person|friend|colleague|brother|sister|phone number)\b', value)):
        return ("If you mean extraterrestrial life, I don't have a verified way to contact it. "
                "If Alien is the name of a real person or service, tell me which one you mean.")
    return None
