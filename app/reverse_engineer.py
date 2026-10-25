"""Reverse engineering for HAYAT, built on REA (morluto/rea, MIT) run as a local MCP server.

REA ("reverse engineer anything") inspects binaries, applications and captures and reports findings with
evidence: native executables, Go/Rust/WASM/EVM/SQLite, .NET assemblies, Android APKs, JavaScript and Electron
apps, websites and HAR captures, and process behaviour. It runs locally and calls no model of its own; HAYAT
drives it. It is pinned in runtime/rea and launched with HAYAT's bundled Node, with install scripts disabled.

Why a module of its own, not the plain MCP gateway: REA is stateful. You open a target once, then every
analysis call works on that open target until it is closed, so a one-process-per-call bridge cannot work.
``ReaSession`` keeps one REA process alive and talks to it over stdio from a dedicated thread.

Governance is HAYAT's, not REA's own. Read-only analysis (overview, listing, decompiling, tracing, searching,
comparing) runs through the passive ``mcp_read`` path. Everything that opens a target, runs or interacts with
it, writes files, or changes analysis notes (open/close, extract, every capture/observe/execute/replay,
import/export, the set_/unset_ notes) runs through ``mcp_write``, which needs the owner's confirmation. Scope
is the owner's own or authorised software; REA enforces its own per-call approvals for side effects on top.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, Callable

from app.config import PROJECT_ROOT

#: Only HAYAT's own Node: everything HAYAT loads lives in its folder, so a missing runtime/node is reported
#: (setup is needed), never silently replaced by whatever Node is on the system PATH.
NODE = PROJECT_ROOT / "runtime" / "node" / "node.exe"
#: REA's home, cache and temporary folders, inside HAYAT's folder. Ghidra's JVM and REA otherwise write to the
#: user profile and %TEMP% (found 2026-10-17: Java's hsperfdata in %TEMP%).
REA_HOME = PROJECT_ROOT / "data" / "rea-home"
REA_SCRIPT = PROJECT_ROOT / "runtime" / "rea" / "node_modules" / "rea-agents" / "scripts" / "rea.mjs"
REA_VERSION = "6.7.0"


def native_environment(env: dict[str, str]) -> dict[str, str]:
    """Discover the project-local engines while respecting explicitly configured paths."""
    environment = dict(env)
    for name, directory, marker in (
        ('GHIDRA_INSTALL_DIR', 'ghidra', 'support/analyzeHeadless.bat'),
        ('JAVA_HOME', 'java', 'bin/javac.exe'),
        ('REA_WABT_BIN_DIRECTORY', 'wabt/bin', 'wasm-validate.exe'),
    ):
        target = PROJECT_ROOT / 'runtime' / directory
        if not environment.get(name) and (target / marker).is_file():
            environment[name] = str(target.resolve())
    jar = PROJECT_ROOT / 'runtime' / 'jadx' / 'jadx-headless-mcp-0.7.1-all.jar'
    if not environment.get('REA_JADX_MCP_JAR') and jar.is_file():
        environment['REA_JADX_MCP_JAR'] = str(jar.resolve())
    environment.update(contained_folders())
    return environment


def contained_folders(home: Path | None = None) -> dict[str, str]:
    """Home, profile, cache and temp variables for REA's process, all under HAYAT's folder (created on demand)."""
    home = (home or PROJECT_ROOT / "data" / "rea-home").resolve()
    folders = {"HOME": home, "USERPROFILE": home, "APPDATA": home / "AppData" / "Roaming",
               "LOCALAPPDATA": home / "AppData" / "Local", "XDG_CACHE_HOME": home / "cache",
               "XDG_CONFIG_HOME": home / "config", "XDG_DATA_HOME": home / "data",
               "TEMP": home / "tmp", "TMP": home / "tmp"}
    for path in folders.values():
        path.mkdir(parents=True, exist_ok=True)
    return {name: str(path) for name, path in folders.items()}

#: Tools that change something: they open or close a target, run or interact with it, write files, or edit
#: analysis notes. Each needs the owner's confirmation. Everything REA exposes that is not here is read-only.
WRITE_TOOLS = frozenset({
    "open_binary", "close_binary", "binary_session",
    "extract_artifact",
    "goto_address", "set_current_document",
    "set_address_name", "set_addresses_names", "set_comment", "set_inline_comment",
    "set_bookmark", "unset_bookmark",
    "capture_process_scenario", "capture_browser_scenario", "capture_electron_scenario", "capture_web_screenshot",
    "observe_web_session", "observe_javascript_runtime",
    "prepare_node_characterization", "execute_node_characterization",
    "run_controlled_replay", "run_replay_machine",
    "import_evidence_bundle", "export_evidence_bundle", "import_managed_reconstruction", "import_reference_source",
    "record_unknown", "update_unknown",
    'cutter_command', 'gdb_console', 'rizin_debug_command', 'collect_adb_bugreport', 'recover_javascript_sources',
})
#: Prefixes that always mean a change, so a future REA release's new set_/capture_/import_ tool is gated too.
WRITE_PREFIXES = ("set_", "unset_", "capture_", "observe_", "execute_", "import_", "export_", "extract_", "run_",
                  'annotate_', 'load_', 'unload_', 'resume_', 'instrument_', 'start_', 'stop_', 'close_',
                  'pull_', 'push_', 'install_', 'uninstall_', 'attach_', 'detach_', 'connect_', 'disconnect_')

#: A few friendly operations the chat and the GUI use, each mapped to the REA tool and its default arguments.
READ_SHORTCUTS: dict[str, tuple[str, dict[str, Any]]] = {
    "overview": ("binary_overview", {}),
    "procedures": ("list_procedures", {}),
    "strings": ("list_strings", {}),
    "names": ("list_names", {}),
    "segments": ("list_segments", {}),
    "artifact": ("inspect_artifact", {}),
    "managed": ("inspect_managed_artifact", {}),
}


def is_write_tool(name: str) -> bool:
    name = str(name or "")
    return name in WRITE_TOOLS or name.startswith(WRITE_PREFIXES)


_MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
_URL = __import__("re").compile(r"(?i)^https?://[^\s]+$")


def is_url(value: str) -> bool:
    return bool(_URL.match(str(value or "").strip()))


def _default_fetch(url: str, max_bytes: int) -> bytes:
    """HAYAT's public fetcher: honours robots.txt, follows no login, bounded in size. Used to pull a link to a
    local file so REA can open it."""
    from app.research.fetcher import PublicFetcher

    fetcher = PublicFetcher("HAYAT-ReverseEngineer/1.0", timeout=30)
    return bytes(fetcher.fetch(url, max_bytes).content)


def available() -> bool:
    """Whether REA is installed in HAYAT's folder and can be launched with the bundled Node."""
    return NODE.is_file() and REA_SCRIPT.is_file()


def installed_version() -> str:
    """Read the installed version again after an in-place setup or upgrade.

    Falls back to the REA_VERSION the module was written against when the package cannot be read."""
    manifest = REA_SCRIPT.parent.parent / "package.json"
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or REA_VERSION)
    except (OSError, ValueError):
        return REA_VERSION


class ReaError(RuntimeError):
    """A failure reported by REA or by the bridge, said plainly."""


class ReaSession:
    """One long-lived REA MCP process, driven from a dedicated asyncio thread so its open-target state lasts.

    Everything async stays on the one thread that owns the event loop; ``call`` and ``list_tools`` are the only
    members other threads touch, and they hand work to that loop and wait for the result.
    """

    def __init__(self, env: dict[str, str] | None = None, *, node: Path = NODE, script: Path = REA_SCRIPT,
                 start_timeout: float = 60.0) -> None:
        from app.safety.sandbox import ProcessSandbox

        self._node, self._script = node, script
        self._env = native_environment(env if env is not None else ProcessSandbox().environment())
        self._start_timeout = start_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: Any = None
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._stop = threading.Event()
        self._stopped: asyncio.Event | None = None
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._thread is not None and not self._thread.is_alive():
                self._thread = self._loop = self._session = None
            if self._thread is not None and self._stop.is_set():
                raise ReaError('REA is still shutting down. Retry after it stops.')
            if self._thread is None:
                if not self._node.is_file() or not self._script.is_file():
                    raise ReaError("REA is not installed in runtime/rea; run scripts/setup_rea.py first.")
                self._start_error = None
                self._stop.clear()
                self._ready.clear()
                self._thread = threading.Thread(target=self._run, name="hayat-rea", daemon=True)
                self._thread.start()
        if not self._ready.wait(self._start_timeout):
            raise ReaError("REA did not start in time.")
        if self._start_error is not None:
            raise ReaError(f"REA could not start: {self._start_error}")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except BaseException as exc:  # noqa: BLE001 -- surfaced to start() through _start_error
            self._start_error = exc
            self._ready.set()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    async def _main(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stopped = asyncio.Event()
        parameters = StdioServerParameters(command=str(self._node), args=[str(self._script), "--mcp"], env=self._env)
        async with AsyncExitStack() as stack:
            reader, writer = await stack.enter_async_context(stdio_client(parameters))
            session = await stack.enter_async_context(ClientSession(reader, writer))
            async with asyncio.timeout(self._start_timeout):
                await session.initialize()
            self._session = session
            self._ready.set()
            if not self._stop.is_set():
                await self._stopped.wait()

    def close(self) -> None:
        self._stop.set()
        loop, thread = self._loop, self._thread
        if loop is not None and self._stopped is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._stopped.set)
        if thread is not None:
            thread.join(timeout=15)
        if thread is None or not thread.is_alive():
            self._thread = self._loop = self._session = None
            self._ready.clear()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self._session is not None

    # -- calls ---------------------------------------------------------------
    def _submit(self, coro: Any, timeout: float) -> Any:
        if self._loop is None or self._session is None:
            raise ReaError("REA is not running.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout)
        except asyncio.TimeoutError as exc:
            future.cancel()
            raise ReaError(f"REA did not answer within {timeout:g}s.") from exc

    def list_tools(self, timeout: float = 30.0) -> list[dict[str, Any]]:
        from app.integrations.mcp_catalog import discover_tools

        self.start()
        return self._submit(discover_tools(self._session), timeout)

    def call(self, tool: str, arguments: dict[str, Any] | None = None, timeout: float = 180.0) -> dict[str, Any]:
        self.start()
        result = self._submit(self._session.call_tool(tool, dict(arguments or {})), timeout)
        return result.model_dump(mode="json")


_MAX_ARGUMENT_BYTES = 64_000
_MAX_RESULT_BYTES = 8 * 1024 * 1024


def _plain_arguments(tool: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Arguments as plain JSON, checked before anything reaches REA (hard-tested 2026-10-17: a non-JSON value
    surfaced as a raw serializer error from inside the MCP client)."""
    if not isinstance(tool, str) or not tool.strip() or len(tool) > 80:
        raise ReaError("Name one REA tool (at most 80 characters).")
    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise ReaError(f"Arguments for {tool} must be a JSON object.")
    try:
        text = json.dumps(arguments, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ReaError(f"Arguments for {tool} must be plain JSON values ({exc}).") from None
    if len(text.encode("utf-8")) > _MAX_ARGUMENT_BYTES:
        raise ReaError(f"Arguments for {tool} exceed {_MAX_ARGUMENT_BYTES:,} bytes.")
    return json.loads(text)


def _text_of(payload: dict[str, Any]) -> str:
    """The text blocks of an MCP tool result, joined; REA returns its findings as text content."""
    parts = []
    for block in payload.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts)


class ReverseEngineer:
    """HAYAT's governed view of REA: a session it starts on demand, read/write classification, and plain results.

    ``read`` runs an analysis tool and returns its findings. ``act`` runs a changing tool (opening a target,
    extracting files, running a capture, writing a note); the registry puts ``act`` behind the confirmation
    gate, so nothing here widens a permission. The session is shared, so a target opened by ``act('open_binary',
    …)`` stays open for later ``read`` calls.
    """

    def __init__(self, session: ReaSession | Callable[[], ReaSession] | None = None,
                 fetch: Callable[[str, int], bytes] | None = None) -> None:
        self._make = session if callable(session) else (lambda: session) if session is not None else ReaSession
        self._session: ReaSession | None = session if isinstance(session, ReaSession) else None
        self._fetch = fetch
        self._open_target: str | None = None
        self._lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._closing = False

    def _session_now(self) -> ReaSession:
        with self._lock:
            if self._closing:
                raise ReaError('REA is shutting down.')
            if self._session is None:
                self._session = self._make()
            return self._session

    def _run(self, tool: str, arguments: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        arguments = _plain_arguments(tool, arguments)
        if not self._operation_lock.acquire(blocking=False):
            raise ReaError('REA is busy with another analysis. Retry after it finishes; no new operation was submitted.')
        try:
            try:
                payload = self._session_now().call(tool, arguments, timeout)
            except ReaError:
                raise
            except Exception as exc:  # MCP transport/protocol errors (an unknown tool, a refused request)
                raise ReaError(f"REA could not run {tool}: {str(exc)[:300] or type(exc).__name__}") from None
            if self._closing:
                raise ReaError('REA closed before the operation finished.')
            # Same bounds as the generic MCP gateway: at most 8 MiB, and credential-shaped values redacted, so a key
            # embedded in an analysed binary never lands in chat history or memory as plain text.
            from app.integrations.mcp_catalog import safe_output
            if len(json.dumps(payload, default=str).encode('utf-8')) > _MAX_RESULT_BYTES:
                raise ReaError(f'REA returned more than {_MAX_RESULT_BYTES // (1024 * 1024)} MiB for {tool}; '
                               'narrow the request (one procedure, a filter, a smaller range).')
            payload = safe_output(payload)
            text = _text_of(payload)
            failed = bool(payload.get('isError', payload.get('is_error', False)))
            if failed:
                raise ReaError(text or f"REA tool {tool} failed.")
            if tool == 'open_binary':
                self._open_target = str((arguments or {}).get('path') or '')
            elif tool == 'close_binary':
                self._open_target = None
            return {"reverse_engineer": True, "tool": tool, "ok": True, "report": text,
                    "structured": payload.get("structuredContent", payload.get('structured_content')), "open_target": self._open_target}
        finally:
            self._operation_lock.release()

    def tools(self) -> dict[str, Any]:
        rows = []
        for tool in self._session_now().list_tools():
            rows.append({"name": tool["name"], "writes": is_write_tool(tool["name"]),
                         "summary": (tool.get("description") or "").split("\n", 1)[0][:160],
                         "description": tool.get("description") or '',
                         "input_schema": tool.get('inputSchema') or tool.get('input_schema') or {'type': 'object', 'properties': {}},
                         "annotations": tool.get('annotations') or {}})
        rows.sort(key=lambda row: (row["writes"], row["name"]))
        return {"reverse_engineer": True, "count": len(rows), "version": installed_version(),
                "tools": rows, "basis": "REA (morluto/rea, MIT), run locally; HAYAT gates every changing tool."}

    def status(self, check_engine: bool = False) -> dict[str, Any]:
        report = {"reverse_engineer": True, "installed": available(), "version": installed_version(),
                "open_target": self._open_target,
                "running": bool(self._session and self._session.running),
                "basis": "REA runs locally from runtime/rea with HAYAT's bundled Node; it calls no model."}
        if check_engine:
            from app.rea_diagnostics import engine_report
            from app.safety.sandbox import ProcessSandbox
            report['engine_check'] = engine_report(node=NODE, script=REA_SCRIPT, root=PROJECT_ROOT,
                environment=native_environment(ProcessSandbox().environment()))
        return report

    def read(self, tool: str, arguments: dict[str, Any] | None = None, timeout: float = 180.0) -> dict[str, Any]:
        if is_write_tool(tool):
            raise PermissionError(f"{tool} changes something; it runs through your confirmation, not a read.")
        return self._run(tool, arguments, timeout)

    def shortcut(self, name: str, timeout: float = 180.0) -> dict[str, Any]:
        if name not in READ_SHORTCUTS:
            raise ValueError(f"Unknown overview {name!r}; try one of {', '.join(sorted(READ_SHORTCUTS))}.")
        tool, arguments = READ_SHORTCUTS[name]
        return self._run(tool, dict(arguments), timeout)

    def act(self, tool: str, arguments: dict[str, Any] | None = None, timeout: float = 600.0) -> dict[str, Any]:
        """A changing tool, run after the owner's confirmation (the registry gates this). Opening a target is
        remembered so later reads know what is open. open_binary with a URL downloads the resource into HAYAT's
        folder first (REA opens only local files), so a plain link works too."""
        if not is_write_tool(tool):
            raise ValueError(f"{tool} only reads; run it directly rather than asking for confirmation.")
        arguments = dict(arguments or {})
        if tool == "open_binary" and is_url(str(arguments.get("path", ""))):
            url = arguments["path"]
            local = self._download_target(url)
            result = self._run(tool, {**arguments, "path": str(local)}, timeout)
            self._open_target = url  # show the link the owner gave, not the cache path
            result["source_url"], result["open_target"] = url, url
            return result
        return self._run(tool, arguments, timeout)

    def _download_target(self, url: str) -> Path:
        """Fetch a link to a local file inside HAYAT's folder, so REA can open it. Public, robots-respected and
        size-bounded; nothing is executed, only saved and then read."""
        fetch = self._fetch or _default_fetch
        content = fetch(url, _MAX_DOWNLOAD_BYTES)
        if not content:
            raise ReaError(f"Nothing could be downloaded from {url}.")
        folder = (REA_HOME / "downloads")
        folder.mkdir(parents=True, exist_ok=True)
        from urllib.parse import unquote, urlsplit
        name = Path(unquote(urlsplit(url).path)).name or "download"
        name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "download"
        import hashlib
        target = folder / f"{hashlib.sha256(url.encode()).hexdigest()[:12]}-{name}"
        target.write_bytes(content)
        return target

    def close(self) -> None:
        with self._lock:
            self._closing = True
            session, self._session = self._session, None
        if session is not None:
            # Shutdown may interrupt a long read, but must not submit close_binary concurrently with it.
            idle = self._operation_lock.acquire(blocking=False)
            try:
                if idle and session.running:
                    try:
                        session.call("close_binary", {}, timeout=30)
                    except Exception:  # noqa: BLE001, S110 -- the process is torn down next
                        pass
            finally:
                if idle:
                    self._operation_lock.release()
                session.close()
        self._open_target = None


# ------------------------------------------------------------------ tools

class ReverseEngineerTools:
    """Chat/GUI tools: reads run straight through; a changing tool is proposed to the confirmation gate."""

    def __init__(self, engineer: ReverseEngineer, gate: Any) -> None:
        self.engineer, self.gate = engineer, gate

    def prepare(self, tool: str, arguments: dict[str, Any] | None = None, timeout: float = 600.0) -> dict[str, Any]:
        """Propose one changing REA tool for the owner's confirmation. Nothing runs until they confirm."""
        if not is_write_tool(tool):
            raise ValueError(f"{tool} only reads; run it directly rather than asking for confirmation.")
        arguments = dict(arguments or {})
        if self.gate is None:
            raise PermissionError("The confirmation window is unavailable, so a changing action cannot be prepared.")
        title = {"open_binary": "Open a reverse-engineering target", "close_binary": "Close the open target",
                 "extract_artifact": "Extract files from the target"}.get(tool, f"Reverse engineering: {tool}")
        target = str(arguments.get("path") or self.engineer.status().get("open_target") or tool)
        details = [("Operation", tool)] + [(key, str(value)[:300]) for key, value in arguments.items()]
        from app.safety.confirmations import fingerprint
        waiting = [{"field": name, "value": value} for name, value in details]
        for pending in self.gate.pending():
            if pending["kind"] == "reverse_engineer" and pending["title"] == title and pending["details"] == waiting:
                return {**pending, "already_waiting": True,
                        "how_to_confirm": "This exact action is already waiting in the confirmation window."}
        return self.gate.propose(
            "reverse_engineer", title, target, details,
            lambda _secrets: self.engineer.act(tool, arguments, timeout),
            notes=["REA runs this locally on software you own or are authorised to examine. Nothing has run yet."],
            fingerprint_value=fingerprint(details))


def register_reverse_engineer_tools(registry: Any, engineer: ReverseEngineer | None = None, *,
                                    gate: Any = None, fetcher: Any = None) -> ReverseEngineer:
    """Register the governed reverse-engineering tools. Reads are passive; ``act`` goes through the confirmation gate."""
    engineer = engineer or ReverseEngineer()
    tools = ReverseEngineerTools(engineer, gate)
    tool_arg = {"type": "object", "properties": {
        "tool": {"type": "string", "maxLength": 80},
        "arguments": {"type": "object", "additionalProperties": True},
        "timeout": {"type": "number", "minimum": 1, "maximum": 1800}}, "required": ["tool"]}

    registry.register(
        "reverse_engineer_status",
        "Whether local REA is installed and what target is open; check_engine also verifies Ghidra/JDK readiness without opening a target.",
        {"type": "object", "properties": {'check_engine': {'type': 'boolean'}}}, "low", "system_read", engineer.status)
    registry.register(
        "reverse_engineer_tools",
        "List the reverse-engineering operations REA offers and which ones change something (so need confirmation).",
        {"type": "object", "properties": {}}, "low", "mcp_read", engineer.tools)
    registry.register(
        "reverse_engineer_overview",
        "A read-only look at the target already open in REA: 'overview' (binary), 'procedures', 'strings', 'names', "
        "'segments', 'artifact' (an archive/app package) or 'managed' (a .NET assembly). Open a target first.",
        {"type": "object", "properties": {"of": {"type": "string", "enum": sorted(READ_SHORTCUTS)},
                                          "timeout": {"type": "number", "minimum": 1, "maximum": 1800}},
         "required": ["of"]},
        "low", "mcp_read", lambda of, timeout=180.0: engineer.shortcut(of, timeout))
    registry.register(
        "reverse_engineer_read",
        "Run one read-only REA analysis tool on the open target (e.g. procedure_pseudo_code, procedure_assembly, "
        "search_procedures, search_strings, trace_feature, xrefs, analyze_function, analyze_javascript_application, "
        "inspect_artifact). Does not open, run, extract or change the target.",
        tool_arg, "medium", "mcp_read",
        lambda tool, arguments=None, timeout=180.0: engineer.read(tool, arguments, timeout))
    registry.register(
        "reverse_engineer_act",
        "Prepare one REA tool that changes something, for the owner to confirm: open_binary/close_binary to load or "
        "release a target, extract_artifact, a capture/observe/execute/replay, an import/export, or a note "
        "(set_comment, set_address_name). Nothing runs until the owner confirms. Scope is software the owner owns "
        "or is authorised to examine.",
        tool_arg, "medium", "mcp_write",
        lambda tool, arguments=None, timeout=600.0: tools.prepare(tool, arguments, timeout))
    from app.rea_links import PublicAppLink
    from app.research.fetcher import PublicFetcher
    from app.safety.permissions import PermissionDecision
    def analyze_sources(path):
        result = registry.execute('reverse_engineer_read', {'tool': 'analyze_javascript_application',
            'arguments': {'input_path': path, 'format': 'directory'}})
        if not result.success:
            # REA retains completed analysis when its full response exceeds MCP's budget.
            # Query that exact evidence, never an unrelated session's latest observation.
            raw = str(result.error or '')
            try:
                failure = json.loads(raw[raw.index('{'):])['error']
                details = failure.get('details') or {}
                reference = (details.get('reported_limits') or {}).get('evidence_reference') or {}
            except (ValueError, KeyError, TypeError):
                reference, details, failure = {}, {}, {}
            if (failure.get('code') == 'resource_constraint' and details.get('resource') == 'transport'
                    and reference.get('kind') == 'retained-evidence'
                    and re.fullmatch(r'ev_[a-f0-9]{64}', str(reference.get('evidence_id', '')))):
                summary = registry.execute('reverse_engineer_read', {'tool': 'inspect_analysis_view',
                    'arguments': {'source': reference, 'view': {'kind': 'summary'}}})
                if summary.success:
                    projected = json.loads(summary.output.get('report') or '{}').get('normalized_result') or {}
                    if (projected.get('parent_evidence_id') == reference['evidence_id']
                            and projected.get('parent_operation') == 'analyze_javascript_application'):
                        return {'analysis_completed': True, 'full_result_transport_limited': True,
                                'retained_evidence': reference, 'retained_evidence_lifetime': 'current REA session only',
                                'summary': summary.output,
                                'scope': 'Completed analysis; this is a bounded retained-evidence summary. '
                                         'Inspect its coverage and unknowns. Fetched sources and this summary remain on disk.'}
            raise ReaError(result.error)
        return result.output
    collector = PublicAppLink(fetcher or PublicFetcher('HAYAT-REA-Link/1.0', timeout=30), analyze_sources,
        REA_HOME / 'public-links', allowed=lambda:
        not getattr(getattr(registry, 'authority', None), 'privacy_mode', False)
        and all(registry.permissions.check(category).decision is PermissionDecision.ALLOW
                for category in ('public_web_read', 'filesystem_write_project')))
    original_fetch = engineer._fetch
    def native_fetch(url, maximum):
        if (getattr(getattr(registry, 'authority', None), 'privacy_mode', False)
                or registry.permissions.check('public_web_read').decision is PermissionDecision.DENY):
            raise PermissionError('Native link download is blocked by privacy or network policy.')
        from app.models.streaming import check_cancelled
        check_cancelled()
        return (original_fetch(url, maximum) if original_fetch else
                collector.fetcher.fetch(url, max_bytes=maximum).content)
    engineer._fetch = native_fetch
    def inspect_public_link(url):
        from app.rea_links import source_controls
        ticket = registry.interlock.ticket()
        registry.permissions.validate_project_path(REA_HOME / 'public-links', write=True)
        with source_controls(check=lambda: registry.interlock.check(ticket)):
            return collector.inspect(url)
    registry.register('reverse_engineer_link',
        'Inspect a public web-app or JavaScript URL with bounded same-origin source collection and real static REA analysis. '
        'Does not execute scripts, recover private/backend code or download native installers; app-store links show only public page source.',
        {'type': 'object', 'properties': {'url': {'type': 'string', 'maxLength': 4000}}, 'required': ['url'], 'additionalProperties': False},
        'medium', 'public_web_read', inspect_public_link)
    engineer.tools_helper = tools
    return engineer


# ------------------------------------------------------------------ chat

import re  # noqa: E402 -- kept beside the patterns it serves

TOOL_NAMES = ("reverse_engineer_status", "reverse_engineer_tools", "reverse_engineer_overview",
              "reverse_engineer_read", "reverse_engineer_act", "reverse_engineer_link")
_LEAD = r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
#: A path on Windows ("D:\\apps\\thing.exe", quoted or not) or POSIX ("/opt/app"), or a bare file name with a
#: known reverse-engineering extension.
#: A link (http/https), a Windows or POSIX path (quoted or not), or a bare file name with a known extension.
_TARGET = (r"(?P<target>https?://[^\s\"']+|\"[^\"]+\"|'[^']+'|[A-Za-z]:\\[^\n]+?|/[^\n]+?|[\w .+-]+?"
           r"\.(?:exe|dll|so|dylib|bin|elf|apk|aab|ipa|app|asar|wasm|jar|class|pyc|o|a|sys|node|msi|dmg|pkg|hex|sqlite|db))")
_RE_VERB = (r"(?:reverse[\s-]?engineer|decompile|disassemble|analy[sz]e|inspect|crack\s+open|take\s+apart|"
            r"reverse|re)\b")
_OPEN = re.compile(_LEAD + _RE_VERB + r"\s+(?:the\s+|this\s+|my\s+)?(?:binary|executable|program|app|application|"
                   r"file|apk|dll|exe|library|assembly|firmware|bundle)?\s*" + _TARGET + r"\s*[.?!]*$", re.IGNORECASE)
_OPEN_PLAIN = re.compile(_LEAD + _RE_VERB + r"\s+" + _TARGET + r"\s*[.?!]*$", re.IGNORECASE)
_WHATS_IN = re.compile(_LEAD + r"what(?:'s|\s+is)\s+(?:inside|in)\s+(?:the\s+|this\s+|my\s+)?" + _TARGET + r"\s*[.?!]*$",
                       re.IGNORECASE)
_STATUS = re.compile(_LEAD + r"(?:reverse[\s-]?engineer(?:ing)?\s+status|what\s+can\s+(?:you|hayat)\s+reverse[\s-]?engineer|"
                     r"(?:list\s+)?reverse[\s-]?engineer(?:ing)?\s+tools)\s*[.?!]*$", re.IGNORECASE)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1]
    return value.strip()


def reverse_engineer_request(text: str) -> dict[str, Any] | None:
    """Chat forms: 'reverse engineer <path>', 'decompile <file>', 'what's inside <app>', 'reverse engineering tools'."""
    raw = " ".join(str(text or "").split())
    if not raw or len(raw) > 400:
        return None
    if re.search(r"(?i)reverse[\s-]?engineer(?:ing)?\s+tools", raw) or _STATUS.match(raw):
        if re.search(r"(?i)\btools\b", raw):
            return {"tool": "reverse_engineer_tools", "arguments": {}}
        return {"tool": "reverse_engineer_status", "arguments": {}}
    for pattern in (_OPEN, _WHATS_IN, _OPEN_PLAIN):
        if match := pattern.match(raw):
            target = _unquote(match["target"])
            # A one-word "analyse" with no path is not ours; it needs a file to point REA at.
            if target and (("\\" in target) or ("/" in target) or "." in target):
                from app.rea_links import native_link
                if is_url(target) and not native_link(target):
                    return {'tool': 'reverse_engineer_link', 'arguments': {'url': target}}
                return {"tool": "reverse_engineer_act", "arguments": {"tool": "open_binary", "arguments": {"path": target}}}
    # Read-only follow-ups on the target already open: an overview, or one named procedure.
    if match := _OVERVIEW.match(raw):
        of = next((key for key, words in _OVERVIEW_WORDS.items() if re.search(words, raw, re.IGNORECASE)), "overview")
        return {"tool": "reverse_engineer_overview", "arguments": {"of": of}}
    if match := _DECOMPILE_NAME.match(raw):
        name = _unquote(match["name"])
        tool = "procedure_assembly" if re.search(r"(?i)\b(?:assembly|disassembl)", raw) else "procedure_pseudo_code"
        return {"tool": "reverse_engineer_read", "arguments": {"tool": tool, "arguments": {"procedure": name}}}
    return None


_OVERVIEW_WORDS = {"procedures": r"(?i)\b(?:functions?|procedures?|methods?|routines?)\b",
                   "strings": r"(?i)\bstrings?\b", "names": r"(?i)\b(?:symbols?|names?|imports?|exports?)\b",
                   "segments": r"(?i)\b(?:segments?|sections?|memory\s+map)\b",
                   "artifact": r"(?i)\b(?:artifact|archive|package|contents?)\b",
                   "managed": r"(?i)\b(?:\.net|dotnet|managed|assembly\s+members?)\b"}
_OVERVIEW = re.compile(_LEAD + r"(?:show(?:\s+me)?|list|give\s+me|what(?:'s|\s+are|\s+is)|read)\s+(?:the\s+|all\s+(?:the\s+)?|its\s+)?"
                       r"(?:functions?|procedures?|methods?|routines?|strings?|symbols?|names?|imports?|exports?|segments?|"
                       r"sections?|memory\s+map|artifact|archive|package|contents?|overview|\.net|dotnet|managed\s+members?)"
                       r"(?:\s+(?:in|of|from)\s+(?:it|the\s+(?:binary|target|file|app)))?\s*[.?!]*$", re.IGNORECASE)
# Only unambiguous reverse-engineering verbs here. "what does X do" is deliberately NOT matched: it collides
# with self-knowledge questions like "what does invoice_create do?" (a HAYAT tool), which must route there.
_DECOMPILE_NAME = re.compile(_LEAD + r"(?:decompile|disassemble|show(?:\s+me)?\s+(?:the\s+)?(?:pseudo\s*code|assembly|asm|code)\s+(?:of|for)|"
                             r"pseudo\s*code\s+(?:of|for))\s+(?:the\s+)?(?:function\s+|procedure\s+|method\s+)?"
                             r"(?P<name>\"[^\"]+\"|'[^']+'|[A-Za-z_][\w:.$@]{1,120})(?:\s+(?:function|procedure|method))?\s*[.?!]*$",
                             re.IGNORECASE)


def report(history: list[dict[str, Any]]) -> str | None:
    """Turn a reverse-engineering tool result into a chat reply."""
    sections = []
    for event in history or []:
        if not str(event.get("tool", "")).startswith("reverse_engineer"):
            continue
        result = event.get("result") or {}
        if not result.get("success") or not (event.get("verification") or {}).get("passed"):
            continue
        value = result.get("output") or {}
        if not isinstance(value, dict):
            continue
        if value.get("kind") == "reverse_engineer" and value.get("how_to_confirm"):
            # A changing action (open/extract/…) proposed to the confirmation window; nothing has run.
            extra = " (already waiting)" if value.get("already_waiting") else ""
            sections.append(f"Prepared: {value.get('title', 'reverse-engineering action')}{extra}. {value['how_to_confirm']}")
            continue
        if not value.get("reverse_engineer"):
            continue
        if "tools" in value:
            reads = sum(1 for row in value["tools"] if not row["writes"])
            sections.append(f"REA offers {value['count']} operations ({reads} read-only, "
                            f"{value['count'] - reads} that change something and need your confirmation).")
        elif "installed" in value:
            where = f"target open: {value['open_target']}" if value.get("open_target") else "no target open"
            sections.append(f"Reverse-engineering engine: {'installed' if value['installed'] else 'not installed'} "
                            f"(REA {value['version']}), {where}.")
        else:
            opened = value.get("open_target")
            head = f"Opened {opened}." if opened and event.get("arguments", {}).get("tool") == "open_binary" else ""
            body = str(value.get("report") or "").strip()
            if len(body) <= 500_000:
                try:
                    body = json.dumps(json.loads(body), indent=2, ensure_ascii=False)
                except ValueError:
                    pass  # REA can also return ordinary text, which keeps its own layout
            if len(body) > 3500:
                body = body[:3500] + '\n[Report shortened for chat.]'
            sections.append("\n".join(part for part in (head, body) if part)
                            or "Done; REA returned nothing to show.")
    return "\n\n".join(sections) or None


def direct_plan(text: str, names: Any) -> dict[str, Any] | None:
    found = reverse_engineer_request(text)
    if not found or found["tool"] not in set(names):
        return None
    opening = found["tool"] == "reverse_engineer_act"
    return {"description": ("Open a target in the reverse-engineering engine for the owner's confirmation."
                            if opening else "Report the reverse-engineering engine's state."),
            "tool": found["tool"], "arguments": found["arguments"],
            "expected": {"type": "tool_success"}, "max_retries": 0}
