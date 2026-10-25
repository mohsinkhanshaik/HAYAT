"""Parse provider XML with encoding-aware declaration rejection."""
from __future__ import annotations

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException


def fromstring(content):
    try:
        return ElementTree.fromstring(content, forbid_dtd=True,
                                      forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise ValueError('XML with document type or entity declarations is not accepted.') from exc


def iterparse(source, events=('end',)):
    try:
        yield from ElementTree.iterparse(source, events=events, forbid_dtd=True,
                                         forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise ValueError('XML with document type or entity declarations is not accepted.') from exc
