from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
import time

from app.windows.com_apartment import configure_gui_sta
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

# Must happen before importing either Tk/tkdnd or pywinauto. Both libraries
# now see the same COM model instead of pywinauto recovering from a mismatch.
configure_gui_sta()

def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Portable private local-first AI agent")
    parser.add_argument("--goal", help="Run one goal non-interactively, then exit")
    parser.add_argument("--no-model", action="store_true", help="Use deterministic planning only")
    parser.add_argument("--safe-mode", action="store_true",
                        help="Skip optional connectors, custom skills and background startup; retain existing permissions")
    parser.add_argument("--json", action="store_true", help="Print the outcome as JSON")
    parser.add_argument("--scheduler-daemon", action="store_true", help="Run enabled scheduled jobs until stopped")
    parser.add_argument("--voice", action="store_true", help="Run the voice assistant in the configured mode")
    parser.add_argument("--combined", action="store_true", help="Run the typed CLI with voice listening in the background")
    parser.add_argument("--tray", action="store_true", help="Run voice mode from the system tray")
    parser.add_argument("--gui", action="store_true", help="Run the desktop chat, voice, status, and trace interface")
    parser.add_argument("--cli-only", action="store_true", help="Run the typed terminal interface without opening a GUI")
    parser.add_argument("--web", action="store_true", help="Run the local web server for this PC and same-Wi-Fi devices")
    parser.add_argument("--web-host", default=None, help="Bind address for --web (use 'lan' for same-Wi-Fi access or 127.0.0.1 for this PC only; wildcard/public bindings are rejected)")
    parser.add_argument("--web-port", type=int, default=None, help="Port for --web (default 8765)")
    parser.add_argument("--browser-bridge-token", action="store_true", help="Print the local browser-extension pairing token")
    parser.add_argument("--secret-set", metavar="NAME", help="Store a connector/MCP secret using hidden terminal input")
    parser.add_argument("--secret-scope", action="append", default=[], metavar="SCOPE", help="Allowed scope for --secret-set; repeat as needed")
    parser.add_argument("--secret-delete", metavar="NAME", help="Delete a secret from the protected credential backend")
    args = parser.parse_args()
    if args.gui and args.cli_only:
        parser.error("--gui and --cli-only cannot be used together")
    if args.safe_mode and any((args.web, args.voice, args.combined, args.tray,
                              args.scheduler_daemon, args.browser_bridge_token, args.secret_set, args.secret_delete)):
        parser.error("--safe-mode supports GUI, CLI and single goals; restart normally for optional services")
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        from app.logging import configure_process_logging

        configure_process_logging(role="scheduler" if args.scheduler_daemon else "web" if args.web else "")

    # GUI modules pull in Tk/tkdnd, which may not be installed on a headless
    # Linux server. CLI/web/bootstrap must still work there. On Windows the
    # reserve must happen before runtime imports pywinauto so OLE drag/drop
    # and COM agree on STA.
    if args.gui:
        try:
            from app.gui import reserve_window
        except ImportError as exc:
            parser.error(f"The GUI is unavailable on {platform.system()}: {exc}. Use --cli-only.")
        reserve_window()

    from app.cli import run_cli
    from app.runtime import build_runtime
    if args.safe_mode:
        from functools import partial
        build_runtime = partial(build_runtime, safe_mode=True)
    if args.browser_bridge_token:
        runtime = build_runtime(interactive_approvals=False, use_model=False, start_background_services=False)
        try:
            print(runtime.browser_extension.pairing_token())
            from app.config import PROJECT_ROOT

            print(f"Load {PROJECT_ROOT / 'browser_extension'} as an unpacked extension, "
                  "then paste this token into its options page.")
            return 0
        finally:
            runtime.close()
    if args.secret_set:
        if not args.secret_scope:
            parser.error("--secret-set requires at least one --secret-scope")
        from getpass import getpass
        value = getpass(f"Secret value for {args.secret_set}: ")
        runtime = build_runtime(interactive_approvals=False, use_model=False, start_background_services=False)
        try:
            result = runtime.secret_broker.store(args.secret_set, value, scopes=args.secret_scope)
            print(json.dumps(result, indent=2))
            return 0
        finally:
            runtime.close()
    if args.secret_delete:
        runtime = build_runtime(interactive_approvals=False, use_model=False, start_background_services=False)
        try:
            removed = runtime.secret_broker.delete(args.secret_delete)
            print("Secret deleted." if removed else "No stored secret had that name.")
            return 0
        finally:
            runtime.close()
    if args.scheduler_daemon:
        runtime = build_runtime(interactive_approvals=False, use_model=not args.no_model)
        try:
            if not runtime.scheduler.scheduler.running:
                runtime.scheduler.start()
            print("AI scheduler daemon ready. ASK actions will be queued for later approval.", flush=True)
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0
        finally:
            runtime.close()
    if args.gui:
        runtime = build_runtime(interactive_approvals=False, use_model=not args.no_model)
        try:
            from app.gui import run_gui
            return run_gui(runtime)
        finally:
            if getattr(runtime, "learner", None) is not None:
                runtime.close()
    if args.web:
        from app.config import load_settings

        runtime = build_runtime(interactive_approvals=False, use_model=not args.no_model)
        server = None
        try:
            from app.web.server import WebServer

            web = load_settings().get("web", {}) if isinstance(load_settings(), dict) else {}
            host = args.web_host or web.get("host") or "127.0.0.1"
            port = int(args.web_port or web.get("port") or 8770)
            token = os.environ.get("HAYAT_WEB_TOKEN") or web.get("token") or None
            server = WebServer(runtime, host=host, port=port, token=token)
            server.start()
            print(f"HAYAT web access: {server.url()}")
            print(f"Passphrase: {server.token}")
            if not token:
                print("(generated for this run -- set HAYAT_WEB_TOKEN in .env to keep it stable)")
            local_only = server.host in ("127.0.0.1", "::1")
            print("Bound to", server.host,
                  "-- this machine only." if local_only
                  else f"-- same-LAN clients only ({', '.join(server.network_policy.describe())}).")
            print("Every request runs with reduced 'remote owner' authority. Ctrl+C to stop.")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0
        finally:
            if server is not None:
                try:
                    server.stop()
                except Exception:
                    log_swallowed(_log, "stopping the web server")
            runtime.close()
    if args.voice or args.combined or args.tray:
        runtime = build_runtime(interactive_approvals=False, use_model=not args.no_model)
        try:
            if runtime.voice is None:
                print("Voice runtime is unavailable.", file=sys.stderr)
                return 1
            if args.tray:
                from app.voice.tray import VoiceTray
                return VoiceTray(runtime.voice).run()
            if args.combined:
                runtime.voice.start_background()
                return run_cli(runtime, close_runtime=False)
            runtime.voice.run_forever()
            return 0
        finally:
            runtime.close()
    if not args.goal:
        return run_cli()
    if args.json:
        # Machine-readable output must contain one JSON document. Operational
        # narration remains available in GUI/CLI, but is routed to a private
        # sink for this one-shot process rather than corrupting stdout.
        from io import StringIO
        from app.output import OutputCoordinator, set_output
        set_output(OutputCoordinator(stream=StringIO()))
    # A one-shot process exits immediately, so starting the idle learner here
    # only wastes network budget and can interleave progress with --json.
    runtime = build_runtime(
        interactive_approvals=False, use_model=not args.no_model,
        start_background_services=False,
    )
    try:
        reply = runtime.assistant.handle(args.goal, source="command_line")
        if args.json:
            print(json.dumps({"response": reply.display_response, "spoken_response": reply.spoken_response, "intent": reply.intent.to_dict(), "task_id": reply.task_id, "complete": reply.complete}, indent=2, default=str))
        else:
            print(reply.display_response)
        return 0 if reply.complete is not False else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
