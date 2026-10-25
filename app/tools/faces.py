"""Tools: recognise only the people the owner has enrolled, in the owner's own images.

Enrolment is the owner adding a photo of a consenting person under a name. After
that, `recognize` reports, for a picture the owner supplies, which faces are
enrolled people and which are unknown -- never who an unknown person is. Nothing
here searches any outside source or the public camera network. See
app/perception/faces.py.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from app.perception.faces import FaceRecognizer
from app.tools.registry import ToolRegistry

_CONTROLS = ContextVar('local_face_controls', default=None)


@contextmanager
def face_controls(*, check=None):
    previous = _CONTROLS.get()
    def guard():
        for callback in (previous, check):
            if callback is not None and callback() is False:
                raise InterruptedError('Local face operation cancelled.')
    token = _CONTROLS.set(guard)
    try:
        yield
    finally:
        _CONTROLS.reset(token)

#: "Set up face recognition", "download the face models", "enable face recognition": fetch YuNet + SFace.
_SETUP = re.compile(
    r"(?i)^(?:please\s+)?(?:set\s*up|install|download|get|enable|turn\s+on)\s+(?:the\s+)?"
    r"(?:face\s+(?:recognition|recognize|recognise)(?:\s+models?)?|face\s+models?|"
    r"(?:opencv\s+)?(?:yunet|sface)(?:\s+and\s+(?:yunet|sface))?(?:\s+models?)?)\s*[.!?]*$")


def face_setup_request(text: str) -> bool:
    return bool(_SETUP.match(" ".join(str(text or "").split())))


def direct_face_setup_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    if not face_setup_request(text) or "face_models_setup" not in set(names):
        return None
    return [{"description": "Download the OpenCV face models (YuNet + SFace) into the HAYAT folder.",
             "tool": "face_models_setup", "arguments": {}, "expected": {"type": "tool_success"}, "max_retries": 0}]


def register_face_tools(registry: ToolRegistry, recognizer: FaceRecognizer | None = None) -> FaceRecognizer:
    recognizer = recognizer or FaceRecognizer()

    def guard_for_call():
        ticket, control = registry.interlock.ticket(), _CONTROLS.get()
        def check():
            registry.interlock.check(ticket)
            if control is not None:
                control()
        return check

    def local_path(path):
        if (not isinstance(path, str) or path.startswith(('\\\\', '//'))
                or re.match(r'(?i)^(?:https?|file|smb|ftp|data):', path)):
            raise ValueError('Choose a local image file; URLs and network shares are not supported.')
        return registry.permissions.validate_project_path(path, write=False)

    def enroll(name, path, consent):
        if consent is not True:
            return {'enrolled': False, 'reason': 'Explicit consent is required before local enrollment.'}
        check = guard_for_call()
        check()
        from app.safety.permissions import PermissionDecision
        permission = registry.permissions.check('filesystem_read', {'path': path})
        if (permission.decision is PermissionDecision.DENY
                or not registry._authorize(registry.get('face_recognize'), 'face_recognize', permission, {'path': path})):
            raise PermissionError('Reading this enrollment image was not authorized by filesystem_read policy.')
        return recognizer.enroll(name, local_path(path), consent=True, check=check)

    def recognize(path):
        check = guard_for_call()
        check()
        value = recognizer.recognize(local_path(path), check=check)
        check()
        return value

    registry.register(
        "face_enroll",
        "Enrol a consenting person for later recognition, from a photo of just them, under a name you choose. "
        "Stores sensitive numeric templates with Windows account encryption, not the photo, until removal. "
        "Requires explicit consent=true and exactly one detected face in a bounded still image. Duplicate templates do not consume sample slots. "
        "Never infer consent. Needs verified local OpenCV models.",
        {"type": "object", "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 120},
                                          "path": {"type": "string", "minLength": 1, "maxLength": 2048},
                                          "consent": {"type": "boolean"}},
         "required": ["name", "path", "consent"], "additionalProperties": False},
        "low",
        "filesystem_write_project",
        enroll,
    )
    registry.register(
        "face_recognize",
        "In a local image you supply, report which faces are people you have enrolled and which are unknown. "
        "Close ties are ambiguous. Scores are raw cosine similarity, not calibrated identity probabilities. "
        "One report uses one consistent gallery snapshot. Accepts one still image, up to 25 MiB and 24 million pixels. "
        "Never searches the web or names an unknown person. Missing/unverified models leave detection only.",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        "low",
        "filesystem_read",
        recognize,
    )
    registry.register(
        "face_list_enrolled",
        "List the people you have enrolled (names and how many signatures each has). Shows no image and no signature.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "low",
        "system_read",
        recognizer.people,
    )
    registry.register(
        "face_forget",
        "Remove one enrolled person by name, deleting their stored signatures from this machine.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False},
        "low",
        "filesystem_write_project",
        lambda name: recognizer.forget(name),
    )
    registry.register(
        "face_clear_all",
        "Remove every enrolled person, clearing the whole local face gallery.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "low",
        "filesystem_write_project",
        recognizer.clear,
    )
    registry.register(
        "face_recognition_status",
        "Report whether face detection and enrolled-person recognition are available, where the optional OpenCV "
        "models go, and who is currently enrolled.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "low",
        "system_read",
        recognizer.status,
    )
    registry.register(
        "face_models_setup",
        "Download the two OpenCV Zoo face models (YuNet for finding and aligning faces, SFace for the signature; "
        "Apache-2.0, about 37 MB) into data/models/faces inside the HAYAT folder, so enrolled people can be told "
        "apart. Each file is kept only if its SHA-256 matches the published one; models already present are left alone.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "low",
        "public_download",
        recognizer.install_models,
    )
    return recognizer
