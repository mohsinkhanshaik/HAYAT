"""Everyday questions about local files, answered by the file tools.

"list the files in app/voice" used to reach the model as conversation, and the
model wrote a plausible listing of files that do not exist. "read
config/settings.yaml" was taken for a website. A request that names a local
path and asks to list, read or find is recognised here and planned directly
onto list_folder, read_file or search_files, so the answer is what is on disk.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: File extensions that make "settings.yaml" a file rather than a host name.
FILE_EXTENSIONS = (
    "txt|md|markdown|rst|py|pyi|ipynb|json|jsonl|yaml|yml|toml|ini|cfg|conf|env|log|csv|tsv|xml|html?|css|"
    "js|jsx|ts|tsx|java|kt|c|h|cpp|hpp|cs|go|rs|rb|php|sh|ps1|bat|sql|pdf|docx?|xlsx?|pptx?"
)
LOCAL_FILE = re.compile(rf"(?i)(?<![\w/.])(?:[a-z]:[\\/])?[\w.\\/-]*\w\.(?:{FILE_EXTENSIONS})\b")

#: "python files" -> *.py, and so on. Only unambiguous kinds.
KINDS = {
    "python": "*.py", "py": "*.py", "markdown": "*.md", "md": "*.md", "text": "*.txt", "txt": "*.txt",
    "json": "*.json", "yaml": "*.yaml", "yml": "*.yml", "csv": "*.csv", "log": "*.log", "pdf": "*.pdf",
    "javascript": "*.js", "typescript": "*.ts", "powershell": "*.ps1", "html": "*.html", "toml": "*.toml",
    "word": "*.docx", "excel": "*.xlsx",
}

#: The owner's own folders, by the names people use for them.
HOME_FOLDERS = {"downloads": "Downloads", "documents": "Documents", "desktop": "Desktop",
                "pictures": "Pictures", "music": "Music", "videos": "Videos"}

_POLITE = r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
_PATH = r"(?P<path>(?:[a-z]:[\\/])?[\w.\\/-]+?)"
_END = r"(?:\s+(?:folder|directory|dir))?\s*[.!?]*$"

_LIST = re.compile(
    _POLITE + r"(?:list|show(?:\s+me)?|what(?:'s|\s+is|\s+are)?|which)\s+(?:all\s+)?(?:the\s+)?"
    r"(?:files|contents|items|things)(?:\s+(?:are\s+)?(?:in|inside|under|of)\s+(?:the\s+|my\s+)?"
    r"(?:folder\s+|directory\s+)?" + _PATH + r")?" + _END,
    re.IGNORECASE,
)
_WHATS_IN = re.compile(
    _POLITE + r"(?:what(?:'s|s|\s+is)\s+in|ls)\s+(?:the\s+|my\s+)?(?:folder\s+|directory\s+)?" + _PATH + _END,
    re.IGNORECASE,
)
_FIND = re.compile(
    _POLITE + r"(?:find|search\s+for|list|show(?:\s+me)?|locate)\s+(?:all\s+|every\s+)?(?:the\s+)?"
    r"(?P<kind>[a-z]+)\s+files\s+(?:in|inside|under|within)\s+(?:the\s+|my\s+)?(?:folder\s+|directory\s+)?"
    + _PATH + _END,
    re.IGNORECASE,
)
_READ = re.compile(
    _POLITE + r"(?:read|show(?:\s+me)?|open\s+and\s+read|print|display|cat|what(?:'s|s|\s+is)\s+in)\s+"
    r"(?:out\s+)?(?:the\s+|my\s+)?(?:file\s+|contents\s+of\s+)?(?P<path>\S+)\s*[.!?]*$",
    re.IGNORECASE,
)
#: "Read the file D:\x\note.txt and tell me the secret word": a read with a
#: question after it. Without this the trailing question made every pattern
#: miss, the request reached the model as conversation, and the model answered
#: "The secret word is Cipher. I read note.txt directly from your drive" -- it
#: had read nothing; the file said "lighthouse". The file's text is the answer.
_READ_THEN = re.compile(
    _POLITE + r"(?:read|open(?:\s+and\s+read)?|look\s+(?:at|in|into|through)|check|go\s+through|scan|view)\s+"
    r"(?:the\s+|my\s+|this\s+)?(?:file\s+|document\s+|contents\s+of\s+)?"
    rf"(?P<path>\"[^\"\r\n]+\"|'[^'\r\n]+'|(?:[a-z]:[\\/])?[\w.\\/-]*\w\.(?:{FILE_EXTENSIONS}))"
    r"\s*(?:,\s*|\s+)(?:and|then|&|to)?\s*(?:tell|show|say|give|explain|summari[sz]e|find|list|what|which|who|how|"
    r"is|are|does|do|check|count|answer)\b.*$",
    re.IGNORECASE,
)
_PROJECT = re.compile(r"(?i)\b(?:this|the|my|our)\s+(?:ai\s+)?project\b")
_QUOTED_OR_DRIVE = r'(?P<path>"[^"\r\n]+"|\'[^\'\r\n]+\'|[a-z]:[\\/][^\r\n]+)'
_READ_SPACED = re.compile(
    _POLITE + r"(?:read|show(?:\s+me)?|open\s+and\s+read|print|display|cat)\s+"
    r"(?:the\s+|my\s+)?(?:file\s+)?" + _QUOTED_OR_DRIVE + r"\s*[.!?]*$", re.I)
_LIST_SPACED = re.compile(
    _POLITE + r"(?:list|show(?:\s+me)?)\s+(?:the\s+|my\s+)?"
    r"(?:files|contents|items)\s+(?:in|inside|under|of)\s+"
    r"(?:the\s+|my\s+)?(?:folder\s+|directory\s+)?" + _QUOTED_OR_DRIVE + r"\s*[.!?]*$", re.I)


def _clean(path: str | None) -> str:
    path = (path or ".").strip().strip("\"'`").rstrip(".,;:!?") or "."
    if path.lower() in {"project", "here", "."}:
        return "."
    if path.lower() in HOME_FOLDERS:
        return str(Path.home() / HOME_FOLDERS[path.lower()])
    return path


def _plausible_folder(path: str) -> bool:
    """A path, not a web address."""
    return bool(re.fullmatch(r"(?:[a-z]:[\\/])?[\w .\\/-]+", path, re.IGNORECASE)) and not re.search(
        r"(?i)\b(?:http|www)\b", path)


def local_file_request(text: str) -> dict[str, Any] | None:
    """Entities for a list/read/find request about a local path, or None."""
    value = " ".join(str(text or "").split())
    if not value or re.search(r"(?i)https?://|www\.", value):
        return None
    value = _PROJECT.sub("project", value)
    if match := _READ_SPACED.match(value):
        path = _clean(match.group("path"))
        if re.search(rf"(?i)\.(?:{FILE_EXTENSIONS})$", path):
            return {"file_operation": "read", "path": path}
    if match := _LIST_SPACED.match(value):
        path = _clean(match.group("path"))
        if _plausible_folder(path):
            return {"file_operation": "list", "path": path}
    if match := _FIND.match(value):
        kind = match.group("kind").lower()
        path = _clean(match.group("path"))
        if kind in KINDS and _plausible_folder(path):
            return {"file_operation": "find", "path": path, "pattern": KINDS[kind]}
    for pattern in (_LIST, _WHATS_IN):
        if match := pattern.match(value):
            written = match.group("path")
            path = _clean(written)
            if LOCAL_FILE.fullmatch(path):
                return {"file_operation": "read", "path": path}
            # "files in tests" says it is a folder. "what's in lunch" does
            # not, so that form needs a separator, a known folder, or the word.
            named_as_folder = (pattern is _LIST or path != written or "/" in path or "\\" in path
                               or re.search(r"(?i)\b(?:folder|directory)\b", value))
            if _plausible_folder(path) and named_as_folder:
                return {"file_operation": "list", "path": path}
    if match := _READ.match(value):
        path = _clean(match.group("path"))
        if LOCAL_FILE.fullmatch(path):
            return {"file_operation": "read", "path": path}
    if match := _READ_THEN.match(value):
        path = _clean(match.group("path"))
        if re.search(rf"(?i)\.(?:{FILE_EXTENSIONS})$", path):
            return {"file_operation": "read", "path": path}
    return None
