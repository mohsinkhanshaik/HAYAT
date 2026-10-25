"""Shell command classification.

A model-generated command is untrusted input. This module parses it, extracts
risk signals, and classifies it against narrow safe-command profiles. The
boundary is an allowlist: a command is ALLOW only when every segment matches a
known-safe profile and no risk signal is present. Everything else is ASK, and a
hard-deny set is DENY regardless of profile.

This is defence in depth, not an operating-system sandbox. It cannot prove
arbitrary shell text safe; it refuses to guess instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

from app.config import PROJECT_ROOT


class Decision(StrEnum):
    ALLOW = "ALLOW"
    ASK = "ASK"
    DENY = "DENY"


RANK = {Decision.ALLOW: 0, Decision.ASK: 1, Decision.DENY: 2}

# Separators that start a new command segment. Order matters: longest first.
_SEPARATORS = ("&&", "||", ";", "|", "&", "\n", "\r")

# Interpreters that execute inline code supplied as an argument.
_INLINE_CODE_FLAGS = {"-c", "-command", "/c", "/k", "-e", "-encodedcommand", "-file", "-executionpolicy"}
_INTERPRETERS = {
    "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
    "bash", "sh", "zsh", "wsl", "wsl.exe", "python", "python3", "py", "py.exe",
    "python.exe", "node", "node.exe", "perl", "ruby", "deno",
}

# Binaries whose primary use is proxy execution or credential access.
_DENIED_BINARIES = {
    "bitsadmin", "certutil", "mshta", "regsvr32", "rundll32", "wmic", "msbuild",
    "installutil", "cscript", "wscript", "at", "schtasks", "psexec", "runas",
    "vssadmin", "cipher", "bcdedit", "diskpart", "reg", "regedit", "netsh",
    "takeown", "icacls", "cacls", "sc", "mimikatz", "procdump",
}

# Tools capable of network egress or retrieval.
_NETWORK_TOKENS = {
    "curl", "curl.exe", "wget", "invoke-webrequest", "iwr", "invoke-restmethod",
    "irm", "start-bitstransfer", "bitsadmin", "certutil", "ftp", "scp", "sftp",
    "rsync", "nc", "ncat", "telnet", "requests.get", "urlopen", "httpx",
}

# Tokens that execute a previously retrieved artefact.
_EXECUTION_TOKENS = {
    "iex", "invoke-expression", "start-process", "saps", "bash", "sh", "cmd",
    "powershell", "pwsh", "python", "node", "./", ".\\", "&",
}

_DOWNLOAD_FLAGS = {"-outfile", "-o", "--output", "-infile", "-t", "--upload-file", "/transfer"}

# Constructs that build or evaluate code at runtime. Benign shell workflows very
# rarely need these; obfuscation always does.
_OBFUSCATION_PATTERNS = [
    (re.compile(r"""['"][^'"]*['"]\s*\+\s*['"]"""), "string_concatenation"),
    (re.compile(r"(?i)\[\s*char\s*\]"), "char_cast"),
    (re.compile(r"(?i)\[\s*convert\s*\]\s*::\s*frombase64string"), "base64_decode"),
    (re.compile(r"(?i)\[\s*system\.text\.encoding\s*\]"), "encoding_cast"),
    (re.compile(r"(?i)\b-join\b"), "join_operator"),
    (re.compile(r"(?i)\b(?:iex|invoke-expression)\b"), "invoke_expression"),
    (re.compile(r"(?i)\bexec\s*\(|\beval\s*\("), "eval_call"),
    (re.compile(r"(?i)\$\{[^}]*\}\s*\+"), "variable_concatenation"),
]

_ENCODED_FLAG = re.compile(r"(?i)(?:^|\s)[-/](?:e|en|enc|enco|encod|encode|encoded|encodedcommand)(?:\s|$|:)")
_SUBSTITUTION = re.compile(r"\$\(|`|\$\{")
_REDIRECT = re.compile(r"(?i)(?:>>?|\b(?:out-file|tee-object|set-content|add-content)\b)")
_ELEVATION = re.compile(r"(?i)\b(?:runas|-verb\s+runas|sudo|start-process[^|;]*-verb\s+runas)\b")
_SYSTEM_DRIVER_OR_TOOL_REMOVAL = re.compile(
    r"(?i)(?:\bpnputil(?:\.exe)?\s+/(?:delete-driver|disable-device|remove-device)\b|"
    r"\b(?:disable-pnpdevice|uninstall-pnpdevice|remove-windowsdriver)\b|"
    r"\bdism(?:\.exe)?\b[^\r\n]*(?:/remove-driver|/disable-feature)\b|"
    r"\b(?:remove-item|del|erase)\b[^\r\n]*(?:\\windows\\system32|\\windows\\syswow64))"
)
_OUTSIDE_PROJECT = re.compile(r"(?i)(?:^|[\s\"'=(])(?:[a-z]:[\\/]|\\\\|~[\\/]|\$env:|\$\{env:|/etc/|/root/)")
_PARENT_ESCAPE = re.compile(r"(?:^|[\s\"'=(])\.\.[\\/]")

#: Absolute paths that are inside the project, which the pattern above cannot
#: tell apart from any other absolute path. It matches `[a-z]:[\\/]`, so
#: `Get-ChildItem D:\AI` -- the project root itself -- was flagged as reaching
#: outside the project and made an ordinary listing stop and ask.
_ABSOLUTE_PATH = re.compile(r"(?i)(?:^|[\s\"'=(])([a-z]:[\\/][^\s\"';|]*)")


def _reaches_outside_project(command: str) -> bool:
    """Whether an absolute path in this command leaves the project.

    Only the drive-letter form is resolved. UNC paths, `~`, `$env:`, /etc and
    /root stay flagged unconditionally: those are not project paths under any
    reading, and a policy that tried to reason about them would be guessing.
    """
    if _PARENT_ESCAPE.search(command):
        return True
    if not _OUTSIDE_PROJECT.search(command):
        return False

    paths = _ABSOLUTE_PATH.findall(command)
    if not paths:
        return True  # matched on \\, ~, $env:, /etc, /root -- outside by definition

    # Every drive path has to be inside, and any *other* outside-marker in the
    # command still counts. Stripping the drive paths leaves those visible.
    remainder = _ABSOLUTE_PATH.sub(" ", command)
    if _OUTSIDE_PROJECT.search(remainder):
        return True
    for raw in paths:
        try:
            resolved = Path(raw.strip("\"'")).resolve()
            resolved.relative_to(PROJECT_ROOT.resolve())
        except (ValueError, OSError):
            return True
    return False


#: Signals that describe how safe commands were *joined*, not what they do.
#:
#: These only apply once every segment has already matched a known-safe
#: read-only profile, so `Get-Process | Select-Object -First 3` was being sent
#: to Mohsin for approval because it contained a pipe -- two harmless commands,
#: still harmless with one feeding the other. The genuinely dangerous use of a
#: pipe is feeding something into an interpreter, and that is caught
#: separately and unconditionally as `nested_interpreter`.
#:
#: Everything else still escalates. Redirects write files, substitution builds
#: commands at runtime, elevation changes who you are -- none of those are
#: merely composition, and none are in this set.
_COMPOSITION_ONLY = frozenset({"multiple_segments", "pipeline"})


@dataclass(slots=True)
class CommandClassification:
    """Structured result of classifying one shell command."""

    decision: Decision
    reason: str
    signals: list[str] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)
    matched_profiles: list[str] = field(default_factory=list)
    normalized: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "signals": sorted(self.signals),
            "segments": self.segments,
            "matched_profiles": self.matched_profiles,
        }


def split_segments(command: str) -> list[str]:
    """Split on shell separators while respecting single and double quotes."""
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        character = command[index]
        if quote:
            current.append(character)
            if character == quote:
                quote = None
            index += 1
            continue
        if character in "'\"":
            quote = character
            current.append(character)
            index += 1
            continue
        matched = next((item for item in _SEPARATORS if command.startswith(item, index)), None)
        if matched:
            segments.append("".join(current).strip())
            current = []
            index += len(matched)
            continue
        current.append(character)
        index += 1
    segments.append("".join(current).strip())
    return [segment for segment in segments if segment]


def tokenize(segment: str) -> list[str]:
    """Whitespace tokenizer that keeps quoted runs together."""
    tokens = re.findall(r"""[^\s"']+|"[^"]*"|'[^']*'""", segment)
    return [token.strip("\"'") for token in tokens if token.strip("\"'")]


def normalize_for_matching(command: str) -> str:
    """Lowercase, unify separators, and drop quoting so literal paths surface."""
    value = command.lower().replace("\\", "/")
    value = value.replace('"', " ").replace("'", " ")
    value = re.sub(r"/\./", "/", value)
    return re.sub(r"\s+", " ", value).strip()


class CommandPolicy:
    """Parse, classify, and decide on a shell command."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        policy = config.get("command_policy", {})
        self.safe_profiles: dict[str, dict[str, Any]] = policy.get("safe_profiles", {})
        self.protected_resources: list[str] = [
            str(item).lower() for item in policy.get("protected_resources", [])
        ]
        self.denied_binaries = {
            str(item).lower() for item in policy.get("denied_binaries", [])
        } | _DENIED_BINARIES
        self.blocked_patterns = [re.compile(item) for item in config.get("blocked_command_patterns", [])]
        self.ask_patterns = [re.compile(item) for item in config.get("ask_command_patterns", [])]
        self._protected_regexes = [
            re.compile(rf"(?:^|[\s\"'=(/\\:]){re.escape(item)}(?:$|[\s\"')/\\;,|&])")
            for item in self.protected_resources
        ]

    # ------------------------------------------------------------------ signals

    def _protected_hits(self, normalized: str) -> list[str]:
        return [
            resource
            for resource, pattern in zip(self.protected_resources, self._protected_regexes)
            if pattern.search(normalized)
        ]

    def _obfuscation_hits(self, command: str) -> list[str]:
        return [name for pattern, name in _OBFUSCATION_PATTERNS if pattern.search(command)]

    def signals(self, command: str, segments: list[str]) -> tuple[set[str], list[str]]:
        """Return (signal names, detail notes) for one command."""
        normalized = normalize_for_matching(command)
        found: set[str] = set()
        notes: list[str] = []

        if len(segments) > 1:
            found.add("multiple_segments")
        if "|" in command and not re.search(r"\|\|", command):
            found.add("pipeline")
        if _REDIRECT.search(command):
            found.add("redirect")
        if _SUBSTITUTION.search(command):
            found.add("command_substitution")
        if _ENCODED_FLAG.search(command) and any(
            interpreter in normalized for interpreter in ("powershell", "pwsh")
        ):
            found.add("encoded_command")
            notes.append("encoded PowerShell payload")
        if _ELEVATION.search(command):
            found.add("elevation")
        if _SYSTEM_DRIVER_OR_TOOL_REMOVAL.search(command):
            found.add("system_driver_or_tool_removal")
            notes.append("system driver/tool removal is an immutable deny")
        if _reaches_outside_project(command):
            found.add("outside_project_path")

        if obfuscation := self._obfuscation_hits(command):
            found.add("obfuscation")
            notes.extend(obfuscation)

        if protected := self._protected_hits(normalized):
            found.add("protected_resource")
            notes.extend(f"protected:{item}" for item in protected)

        tokens_by_segment = [tokenize(segment) for segment in segments]
        flat = [token.lower() for tokens in tokens_by_segment for token in tokens]

        for tokens in tokens_by_segment:
            if not tokens:
                continue
            head = tokens[0].lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            base = head[:-4] if head.endswith(".exe") else head
            if base in self.denied_binaries:
                found.add("denied_binary")
                notes.append(f"denied_binary:{base}")
            if head in _INTERPRETERS and any(
                token.lower() in _INLINE_CODE_FLAGS for token in tokens[1:]
            ):
                found.add("nested_interpreter")
            if any(
                token.lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1] in _INTERPRETERS
                for token in tokens[1:]
            ):
                found.add("nested_interpreter")

        network = [token for token in flat if token in _NETWORK_TOKENS]
        if network:
            found.add("network_tool")
            notes.extend(f"network:{item}" for item in network)

        if network and any(token in _DOWNLOAD_FLAGS for token in flat):
            found.add("file_transfer")

        if network:
            downloads_to_disk = any(token in _DOWNLOAD_FLAGS for token in flat)
            executes = any(
                token in _EXECUTION_TOKENS or token.startswith("./") or token.startswith(".\\")
                for token in flat
            )
            piped_to_execution = bool(re.search(r"(?i)\|\s*(?:iex|invoke-expression|bash|sh|python)", command))
            if piped_to_execution or (downloads_to_disk and executes) or (len(segments) > 1 and executes):
                found.add("download_execute")

        for pattern in self.blocked_patterns:
            if pattern.search(command):
                found.add("legacy_blocked")
        for pattern in self.ask_patterns:
            if pattern.search(command):
                found.add("legacy_ask")

        return found, notes

    # ------------------------------------------------------------------ profiles

    def _match_profile(self, tokens: list[str]) -> str | None:
        if not tokens:
            return None
        head = tokens[0].lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        head = head[:-4] if head.endswith(".exe") else head
        profile = self.safe_profiles.get(head)
        if profile is None:
            return None
        arguments = [token for token in tokens[1:]]
        lowered = [token.lower() for token in arguments]

        denied_flags = {str(item).lower() for item in profile.get("denied_flags", [])}
        # Compared on the part before "=", because a flag and its value are one
        # token when written that way. `--index-url http://host` was caught and
        # `--index-url=http://host` was not, which is the form people actually
        # type -- so the flag that keeps installs on PyPI could be sidestepped
        # by a single character.
        if denied_flags and any(token.split("=", 1)[0] in denied_flags for token in lowered):
            return None

        allowed_sub = profile.get("subcommands")
        if allowed_sub is not None:
            positional = [token for token in lowered if not token.startswith("-") and not token.startswith("/")]
            if not positional:
                return head if profile.get("bare_allowed", True) else None
            if positional[0] not in {str(item).lower() for item in allowed_sub}:
                return None

        allowed_modules = profile.get("modules")
        if allowed_modules is not None and "-m" in lowered:
            index = lowered.index("-m")
            if index + 1 >= len(lowered):
                return None
            module = lowered[index + 1]
            if module not in {str(item).lower() for item in allowed_modules}:
                return None
            remainder = lowered[index + 2 :]
            module_subs = profile.get("module_subcommands", {}).get(module)
            if module_subs is not None:
                positional = [token for token in remainder if not token.startswith("-")]
                if positional and positional[0] not in {str(item).lower() for item in module_subs}:
                    return None
        elif allowed_modules is not None and profile.get("require_module", False):
            return None

        return head

    # ------------------------------------------------------------------ decision

    def classify(self, command: str) -> CommandClassification:
        raw = str(command or "")
        if not raw.strip():
            return CommandClassification(
                Decision.DENY, "Empty command.", ["empty"], [], [], ""
            )

        segments = split_segments(raw)
        normalized = normalize_for_matching(raw)
        found, notes = self.signals(raw, segments)

        hard_deny = {
            "legacy_blocked": "Command matches a protected-system rule.",
            "encoded_command": "Encoded PowerShell payloads cannot be reviewed and are denied.",
            "obfuscation": "Command builds or evaluates code at runtime, which prevents review.",
            "protected_resource": "Command references a protected credential or key resource.",
            "download_execute": "Retrieving and executing remote content is never allowed.",
            "denied_binary": "Command invokes a proxy-execution or system-control binary.",
            "elevation": "Administrator elevation requires a separate explicit approval path.",
            "system_driver_or_tool_removal": "System drivers and Windows tools cannot be removed or disabled.",
        }
        for signal, reason in hard_deny.items():
            if signal in found:
                detail = "; ".join(notes) if notes else signal
                return CommandClassification(
                    Decision.DENY, f"{reason} ({detail})", sorted(found), segments, [], normalized
                )

        if "network_tool" in found and "protected_resource" in found:
            return CommandClassification(
                Decision.DENY, "Sensitive data must not be sent over the network.",
                sorted(found), segments, [], normalized,
            )

        profiles = [self._match_profile(tokenize(segment)) for segment in segments]
        matched = [item for item in profiles if item]
        all_profiled = all(item is not None for item in profiles) and bool(profiles)

        escalating = found - {"legacy_ask"} - _COMPOSITION_ONLY
        if "legacy_ask" in found:
            return CommandClassification(
                Decision.ASK, "Command is destructive or changes installed software.",
                sorted(found), segments, matched, normalized,
            )
        if not all_profiled:
            unknown = [
                segment for segment, profile in zip(segments, profiles) if profile is None
            ]
            return CommandClassification(
                Decision.ASK,
                f"Command is outside every known-safe profile: {unknown[0][:120]!r}",
                sorted(found), segments, matched, normalized,
            )
        if escalating:
            return CommandClassification(
                Decision.ASK,
                f"Safe commands combined with elevated-risk shell features: {', '.join(sorted(escalating))}.",
                sorted(found), segments, matched, normalized,
            )
        return CommandClassification(
            Decision.ALLOW,
            f"Every segment matches a known-safe profile ({', '.join(matched)}).",
            sorted(found), segments, matched, normalized,
        )


def summarize(classifications: Iterable[CommandClassification]) -> dict[str, int]:
    counts = {item.value: 0 for item in Decision}
    for classification in classifications:
        counts[classification.decision.value] += 1
    return counts
