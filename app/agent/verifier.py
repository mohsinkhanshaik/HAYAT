from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.config import PROJECT_ROOT
from app.tools.registry import ToolResult


@dataclass(slots=True)
class Verification:
    passed: bool
    reason: str
    evidence: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Verifier:
    @staticmethod
    def _url_matches(actual: str, target: str) -> bool:
        try:
            a, b = urlsplit(actual), urlsplit(target)
            if a.scheme not in {'http', 'https'} or b.scheme not in {'http', 'https'}:
                return False
            if a.username or a.password or b.username or b.password:
                return False
            # A normal HTTP→HTTPS upgrade is valid; never accept a downgrade.
            if a.scheme != b.scheme and not (b.scheme == 'http' and a.scheme == 'https'):
                return False
            host_a = (a.hostname or '').removeprefix('www.').casefold()
            host_b = (b.hostname or '').removeprefix('www.').casefold()
            if not host_a or host_a != host_b:
                return False
            port_a, port_b = a.port or (443 if a.scheme == 'https' else 80), b.port or (443 if b.scheme == 'https' else 80)
            if port_a != port_b and not (b.scheme == 'http' and b.port is None and a.scheme == 'https' and port_a == 443):
                return False
            path = b.path.rstrip('/')
            if path and a.path.rstrip('/') != path and not a.path.startswith(path + '/'):
                return False
            actual_query = parse_qs(a.query, keep_blank_values=True)
            return all(actual_query.get(key) == values for key, values in parse_qs(b.query, keep_blank_values=True).items())
        except (ValueError, TypeError):
            return False

    def check(self, expected: dict[str, Any], result: ToolResult) -> Verification:
        if not result.success:
            return Verification(False, f"Tool failed: {result.error}", result.to_dict())
        kind = expected.get("type", "tool_success")
        output = result.output if isinstance(result.output, dict) else {"value": result.output}
        if kind == 'invention_run_finished':
            state = output.get('state')
            passed = state in {'TESTED', 'TEST_FAILED', 'NEEDS_VALIDATION'} and bool(output.get('rounds'))
            return Verification(passed, f'Workbench run state={state}; validates run completion only, not invention novelty.', output)
        if kind == "tool_success":
            if result.tool in {"generate_image", "generate_video"}:
                return self.check({"type": "image_created" if result.tool == "generate_image" else "video_created"}, result)
            if output.get("ok") is False:
                return Verification(False, str(output.get("error") or output.get("detail") or "Tool reported failure."), output)
            if output.get('requires_user_choice') is True:
                return Verification(False, str(output.get('reason') or 'An explicit user choice is still required.'), output)
            if 'exit_code' in output and (type(output['exit_code']) is not int or output['exit_code'] != 0):
                return Verification(False, f"Process exited with code {output['exit_code']}; command success was not verified.", output)
            return Verification(True, "Tool returned success.", output)
        if kind == "minimum_count":
            actual = int(output.get("count", 0))
            target = int(expected.get("value", 1))
            return Verification(actual >= target, f"Observed count {actual}; required at least {target}.", actual)
        if kind == "file_exists":
            requested_path = expected.get("path") or output.get("path")
            if not requested_path:
                return Verification(False, "file_exists criterion requires a path in the expectation or tool output.", output)
            path = Path(requested_path)
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            exists = path.is_file()
            return Verification(exists, f"File {'exists' if exists else 'does not exist'}: {path}", str(path))
        if kind == "exists_true":
            exists = output.get("exists") is True
            return Verification(exists, f"exists={exists}", output)
        if kind == "content_contains":
            needle = str(expected.get("value", ""))
            content = str(output.get("content", ""))
            passed = bool(needle) and needle in content
            return Verification(passed, f"Read-back {'contains' if passed else 'does not contain'} expected text.", needle)
        if kind == "stdout_contains":
            needle = str(expected.get("value", ""))
            stdout = str(output.get("stdout", ""))
            exit_code = output.get("exit_code")
            passed = exit_code == 0 and bool(needle) and needle in stdout
            return Verification(passed, f"exit_code={exit_code}; expected output present={needle in stdout}", stdout)
        if kind == "exit_code_zero":
            passed = type(output.get('exit_code')) is int and output.get("exit_code") == 0
            return Verification(passed, f"exit_code={output.get('exit_code')}", output)
        if kind == "process_running":
            running = bool(output.get("running")) and isinstance(output.get("pid"), int)
            return Verification(running, f"process_started={running}; pid={output.get('pid')}", output)
        if kind == "application_window_exists":
            target = str(expected.get("application") or output.get("application") or "")
            window = output.get("window") if isinstance(output.get("window"), dict) else None
            visible = bool(window and window.get('visible') is True)
            alive = output.get('running') is True or ('running' not in output and output.get('focused') is True and visible)
            passed = bool(target) and output.get("application") == target and alive and visible
            return Verification(passed, f"application={output.get('application')}; running={output.get('running')}; visible_window={bool(window and window.get('visible'))}", output)
        if kind == "target_window_focused":
            target = str(expected.get("application") or output.get("application") or "")
            passed = bool(target) and output.get("application") == target and output.get("focused") is True
            return Verification(passed, f"application={output.get('application')}; focused={output.get('focused')}", output)
        if kind == "ui_text_contains":
            target = str(expected.get("application") or output.get("application") or "")
            needle = str(expected.get("value") or "")
            observed = str(output.get("text") or "")
            passed = bool(target and needle) and output.get("application") == target and output.get("focused") is True and needle in observed and output.get("text_present") is True
            return Verification(passed, f"application={output.get('application')}; focused={output.get('focused')}; requested_text_present={bool(needle and needle in observed)}", {"window": output.get("window"), "requested_text": needle, "observed_text": observed})
        if kind == "target_window_absent":
            target = str(expected.get("application") or output.get("application") or "")
            passed = bool(target) and output.get("application") == target and output.get("closed") is True and output.get("target_window_exists") is False
            return Verification(passed, f"application={output.get('application')}; closed={output.get('closed')}; target_window_exists={output.get('target_window_exists')}", output)
        if kind == "browser_destination":
            actual = str(output.get("url") or output.get("after_url") or "")
            target = str(expected.get("value") or "")
            passed = self._url_matches(actual, target or actual) if actual else False
            return Verification(passed, f"browser_destination={actual!r}; expected={target!r}", actual)
        if kind == "download_complete":
            path_value = str(output.get("path") or "")
            path = Path(path_value) if path_value else None
            exists = bool(path and path.is_file())
            complete = output.get("complete") is True
            downloaded = output.get("bytes_downloaded")
            expected_bytes = expected.get("bytes") or output.get("expected_bytes")
            size_matches = expected_bytes is None or (isinstance(downloaded, int) and downloaded == int(expected_bytes))
            passed = exists and complete and size_matches
            return Verification(passed, f"file_exists={exists}; complete={complete}; size_matches={size_matches}", output)
        if kind == "image_created":
            path_value = str(output.get("path") or "")
            path = Path(path_value) if path_value else None
            exists = bool(path and path.is_file())
            png = False
            if path and exists:
                try:
                    with path.open("rb") as source:
                        png = source.read(8) == b"\x89PNG\r\n\x1a\n"
                except OSError:
                    pass
            passed = output.get("ok") is True and exists and png
            return Verification(
                passed,
                f"generation_ok={output.get('ok') is True}; file_exists={exists}; valid_png={png}",
                {"path": path_value, "seed": output.get("seed"), "created_at": output.get("created_at")},
            )
        if kind == "video_created":
            path_value = str(output.get("path") or "")
            path = Path(path_value) if path_value else None
            exists = bool(path and path.is_file())
            try:
                size = path.stat().st_size if path and exists else 0
            except OSError:
                size = 0
            passed = output.get("ok") is True and size > 0
            return Verification(passed, f"generation_ok={output.get('ok') is True}; file_exists={exists}; nonempty={size > 0}", output)
        if kind == "camera_observation":
            # A camera observation is only satisfied by evidence that the device
            # actually produced a frame AND that the frame was interpreted. A
            # camera_status call (which never opens the device) must not pass,
            # and neither must a capture whose vision step was unavailable.
            frame = output.get("frame") if isinstance(output.get("frame"), dict) else None
            vision = output.get("vision") if isinstance(output.get("vision"), dict) else {}
            ocr = output.get("ocr") if isinstance(output.get("ocr"), dict) else {}
            captured = bool(frame and int(frame.get("width") or 0) > 0 and int(frame.get("height") or 0) > 0)
            interpretation = str(vision.get("interpretation") or "").strip()
            ocr_text = str(ocr.get("text") or "").strip()
            # A frame measured as too dark, covered or washed out is an
            # interpretation too, and the honest one: "nothing to see" beats
            # a vision model describing darkness as abstract art.
            visibility = output.get("visibility") if isinstance(output.get("visibility"), dict) else {}
            unseeable = visibility.get("state") in {"dark", "covered", "overexposed"} and \
                visibility.get("brightness") is not None
            interpreted = bool(interpretation) or bool(ocr_text) or unseeable
            passed = captured and interpreted
            return Verification(
                passed,
                f"frame_captured={captured}; vision_interpreted={bool(interpretation)}; ocr_text_present={bool(ocr_text)}",
                {"observed_at": output.get("observed_at"), "frame": frame, "interpretation": interpretation[:2000], "ocr_text": ocr_text[:1000], "raw_frame_retained": output.get("raw_frame_retained")},
            )
        if kind == "vision_interpretation":
            path = Path(str(output.get("path", "")))
            interpretation = str(output.get("interpretation", "")).strip()
            passed = path.is_file() and bool(interpretation) and bool(output.get("model"))
            return Verification(passed, f"screenshot_exists={path.is_file()}; interpretation_present={bool(interpretation)}", {"path": str(path), "interpretation": interpretation, "model": output.get("model")})
        if kind == "url_matches":
            actual = str(output.get("url", ""))
            target = str(expected.get("value", ""))
            passed = bool(target) and self._url_matches(actual, target)
            return Verification(passed, f"Observed destination {actual!r} matches {target!r}: {passed}", actual)
        if kind == "research_complete":
            complete = output.get("complete") is True
            sources = output.get("sources") if isinstance(output.get("sources"), list) else []
            citations = output.get("citations") if isinstance(output.get("citations"), list) else []
            answer = str(output.get("answer", "")).strip()
            passed = complete and bool(answer) and bool(sources) and bool(citations)
            return Verification(passed, f"complete={complete}; sources={len(sources)}; citations={len(citations)}; answer_present={bool(answer)}", {"run_id": output.get("run_id"), "sources": len(sources), "citations": len(citations), "confidence": output.get("confidence")})
        return Verification(False, f"Unknown verification type: {kind}", output)

    def check_goal(self, criteria: list[dict[str, Any]], history: list[dict[str, Any]]) -> Verification:
        if not criteria:
            return Verification(True, "No explicit final-state criteria were required.", {})
        outputs = [event.get("result", {}).get("output", {}) for event in history if event.get("result", {}).get("success") is True]
        results: list[dict[str, Any]] = []
        for criterion in criteria:
            kind = criterion.get("type")
            expected = {**criterion, 'application': criterion.get('target') or criterion.get('application')}
            candidates = [output for output in outputs if isinstance(output, dict) and (not criterion.get("target") or output.get("application") == criterion.get("target") or output.get("target_application") == criterion.get("target"))]
            # Current-state goals must use the latest relevant observation.
            # An earlier navigation/close/text readback cannot prove the state
            # after a subsequent navigation/reopen/edit.
            state_keys = {
                'browser_destination': {'url', 'after_url'}, 'url_matches': {'url', 'after_url'},
                'application_window_exists': {'application', 'target_application'},
                'target_window_absent': {'application', 'target_application'},
                'target_window_focused': {'application', 'target_application'},
                'ui_text_contains': {'text', 'text_present', 'closed', 'running'},
                'content_contains': {'content'}, 'stdout_contains': {'stdout', 'exit_code'},
            }
            if kind in state_keys:
                relevant = [output for output in candidates if state_keys[kind].intersection(output)]
                verification = self.check(expected, ToolResult('goal_state', True, relevant[-1])) if relevant else None
            else:
                verification = next((checked for output in reversed(candidates) if (checked := self.check(expected, ToolResult("goal_state", True, output))).passed), None)
            if verification is None:
                evidence = candidates[-1] if candidates else None
                verification = Verification(False, f"No observed final state satisfied: {criterion.get('description', kind)}", evidence)
            results.append({"criterion": criterion, "verification": verification.to_dict()})
        passed = all(item["verification"]["passed"] for item in results)
        return Verification(passed, f"{sum(item['verification']['passed'] for item in results)}/{len(results)} user-goal criteria satisfied.", results)
