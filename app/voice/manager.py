from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Callable

from app.config import PROJECT_ROOT
from app.output import get_output
from app.voice.devices import DeviceManager
from app.voice.interruption import AnyEvent, CancellationController, WindowsHotkey
from app.voice.microphone import MicrophoneRecorder
from app.voice.models import Transcription, VoiceResponse, VoiceState
from app.voice.session import ApprovalPhraseParser, VoiceSession
from app.voice.stt import SpeechToTextProvider, create_stt_provider
from app.voice.tts import TextToSpeechProvider, WindowsSapiTTS, create_tts_provider
from app.voice.vad import EnergyVoiceActivityDetector
from app.voice.wakeword import WakeWordDetector
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.runtime import Runtime


class VoiceApprovalController:
    def __init__(self, tts: TextToSpeechProvider, timeout_seconds: float, state_callback: Callable[[VoiceState], None]) -> None:
        self.tts = tts
        self.timeout_seconds = timeout_seconds
        self.state_callback = state_callback
        self.pending: dict | None = None
        self._decision: bool | None = None
        self._event = threading.Event()
        self._lock = threading.Lock()

    def request(self, tool_name: str, reason: str, arguments: dict) -> bool:
        with self._lock:
            self.pending = {"tool": tool_name, "reason": reason, "arguments": arguments}
            self._decision = None
            self._event.clear()
        self.state_callback(VoiceState.APPROVAL)
        self.tts.speak(f"Approval required for {tool_name}. Say approve or yes continue to proceed, or say deny.")
        decided = self._event.wait(self.timeout_seconds)
        with self._lock:
            decision = bool(self._decision) if decided else False
            self.pending = None
            self._decision = None
        return decision

    def answer(self, text: str) -> bool | None:
        parsed = ApprovalPhraseParser.parse(text)
        if parsed is None:
            return None
        with self._lock:
            if self.pending is None:
                return None
            self._decision = parsed
            self._event.set()
        return parsed

    def cancel(self) -> None:
        with self._lock:
            if self.pending is not None:
                self._decision = False
                self._event.set()


class SpokenResponseFormatter:
    @staticmethod
    def compact_text(text: str, limit: int = 280) -> str:
        text = re.sub(r"https?://\S+", "the link shown on screen", text)
        text = re.sub(r"(?:[A-Za-z]:\\|/)[^\s]+", "the path shown on screen", text)
        text = re.sub(r"[`#*_]+", "", text)
        text = " ".join(text.split())
        if len(text) > limit:
            text = text[:limit].rsplit(" ", 1)[0] + "."
        return text

    @staticmethod
    def format(outcome, original_request: str) -> VoiceResponse:
        if not outcome.complete:
            if getattr(outcome, "needs_user_input", False):
                spoken = "You have unsaved changes. Should I save them, discard them, or cancel?"
            elif "cancel" in outcome.summary.lower():
                spoken = "Cancelled."
            else:
                spoken = "I couldn't complete that task. The details are on screen."
            return VoiceResponse(outcome.summary, spoken, outcome.task_id or None, False)

        lowered = original_request.lower()
        if any(item.get("tool") == "notepad_edit" for item in outcome.history):
            spoken = "Done. I verified the text in Notepad."
        elif any(item.get("tool") == "close_application" for item in outcome.history):
            spoken = "Done. The intended window is closed."
        elif "screen" in lowered and outcome.history:
            output = outcome.history[-1].get("result", {}).get("output", {})
            interpretation = str(output.get("interpretation", "")) if isinstance(output, dict) else ""
            spoken = SpokenResponseFormatter.compact_text(interpretation) if interpretation else "I captured the screen, but couldn't interpret it."
        elif ("create" in lowered or "write" in lowered) and "file" in lowered:
            spoken = "Done. The file was created successfully."
        elif re.search(r"\b(?:run|execute|start)\b.*\btests?\b", lowered):
            combined = " ".join(str(item.get("result", {}).get("output", {})) for item in outcome.history)
            match = re.search(r"(\d+)\s+passed", combined, re.IGNORECASE)
            spoken = f"Done. All {match.group(1)} tests passed." if match else "Done. The tests completed successfully."
        elif any(word in lowered for word in ("open ", "launch ", "bring up")):
            spoken = "Done. It's open."
        elif any(item.get("tool") == "camera_observe" for item in outcome.history):
            step = next((item for item in reversed(outcome.history) if item.get("tool") == "camera_observe"), None)
            data = step.get("result", {}).get("output", {}) if step else {}
            vision = data.get("vision") if isinstance(data, dict) and isinstance(data.get("vision"), dict) else {}
            interpretation = str(vision.get("interpretation") or "").strip()
            view = data.get("visibility") if isinstance(data, dict) and isinstance(data.get("visibility"), dict) else {}
            if view.get("message"):
                # Too dark, covered or washed out: say so, briefly.
                spoken = SpokenResponseFormatter.compact_text(view["message"])
            else:
                spoken = SpokenResponseFormatter.compact_text(interpretation) if interpretation else "I captured a frame, but couldn't interpret it."
        elif any(item.get("tool") in {"research_topic", "capture_web_page"} for item in outcome.history):
            # Matching on the tool actually used (like notepad_edit/
            # close_application above) rather than keywords in the request
            # text: the previous keyword list ("research", "look up", "check
            # whether", "learn ") didn't include "search", so "Search the
            # internet for X" fell through to the generic "Done. The task
            # completed successfully." with the real answer sitting unread
            # in the tool output.
            step = next((item for item in outcome.history if item.get("tool") in {"research_topic", "capture_web_page"}), None)
            data = step.get("result", {}).get("output", {}) if step else {}
            sources = len(data.get("sources", [])) if isinstance(data, dict) else 0
            confidence = data.get("confidence") if isinstance(data, dict) else None
            answer = str(data.get("answer") or "").strip() if isinstance(data, dict) else ""
            snippet = SpokenResponseFormatter.compact_text(answer, 200) if answer else ""
            if snippet:
                spoken = f"{snippet} Full sources are on screen."
            else:
                spoken = f"I checked {sources} source{'s' if sources != 1 else ''}. The validated result is on screen."
            if isinstance(confidence, (int, float)) and confidence < 0.55:
                spoken = f"{snippet} The evidence is uncertain though -- details are on screen." if snippet else f"I checked {sources} sources, but the evidence is uncertain. The details are on screen."
        elif any(item.get("tool") == "generate_image" for item in outcome.history):
            spoken = "Done. I created the image, verified it, and saved it in the local image library."
        elif any(str(item.get("tool") or "").startswith(("connector_", "mcp_")) for item in outcome.history):
            spoken = "Done. The connected service responded, and I verified the result."
        else:
            # Last resort. Name the verified work rather than asserting a
            # generic success the listener cannot check -- spoken output is
            # especially bad for this, since there is no screen to fall back on.
            verified = [
                str(item.get("tool")).replace("_", " ")
                for item in outcome.history
                if item.get("tool") and item.get("verification", {}).get("passed")
            ]
            unique = list(dict.fromkeys(verified))
            if not unique:
                spoken = "That finished, but nothing reported a verified result."
            elif len(unique) == 1:
                spoken = f"Done. {unique[0].capitalize()} completed and verified."
            else:
                spoken = f"Done. {len(verified)} steps completed and verified."
        written_paths = []
        for item in outcome.history:
            if item.get("tool") not in {"write_file", "append_file"}:
                continue
            output = item.get("result", {}).get("output", {})
            if isinstance(output, dict) and output.get("path"):
                written_paths.append(str(output["path"]))
        return VoiceResponse(
            outcome.summary, spoken, outcome.task_id or None, True,
            {"action_tools": [item.get("tool") for item in outcome.history], "written_paths": written_paths},
        )


class VoiceManager:
    def __init__(
        self,
        runtime: "Runtime",
        config: dict,
        stt: SpeechToTextProvider | None = None,
        tts: TextToSpeechProvider | None = None,
        recorder: MicrophoneRecorder | None = None,
    ) -> None:
        self.runtime = runtime
        self.config = config
        self.devices = DeviceManager()
        vad_config = config.get("vad", {})
        self.vad = EnergyVoiceActivityDetector(float(vad_config.get("energy_threshold", 0.0035)))
        self.recorder = recorder or MicrophoneRecorder(config.get("microphone", {}), self.devices, self.vad)
        self.stt = stt or create_stt_provider(config.get("stt", {}))
        self.tts = tts or create_tts_provider(config.get("tts", {}), self.devices)
        if tts is None:
            # Arabic in a reply is spoken by the Arabic voice, never spelled out by the English one.
            from app.voice.tts import BilingualTTS

            languages = lambda: getattr(self.runtime, "languages", None)
            self.tts = BilingualTTS(
                self.tts, lambda: getattr(self._tutor(), "voice", None),
                voice_for=lambda code: languages().speaker(code) if languages() is not None else None,
                language_supplier=lambda: getattr(getattr(self.runtime, "session", None), "reply_language", "en"),
            )
        shared_session = getattr(getattr(runtime, "session", None), "session_id", None)
        self.session = VoiceSession.create(runtime.memory, shared_session)
        self.cancellation = CancellationController.create()
        self.hotkey = WindowsHotkey(str(config.get("push_to_talk_key", "ctrl+space")),
                                    exclusive=bool(config.get("push_to_talk_exclusive", True)))
        wake_config = config.get("wake_word", {})
        self.wakeword = WakeWordDetector(str(wake_config.get("phrase", "hey ai")), list(wake_config.get("aliases", [])))
        # Opt-in low-power gate: a tiny model scores audio frames and only a
        # detection starts recording and transcription. None means phrase mode.
        self.wake_gate = None
        self._wake_gate_note: str | None = None
        if str(wake_config.get("engine", "phrase")).lower() == "openwakeword":
            from app.voice.wake_engine import OpenWakeWordGate

            self.wake_gate = OpenWakeWordGate(str(wake_config.get("model", "hey_jarvis")),
                                              float(wake_config.get("threshold", 0.5)))
        self.mode = str(config.get("mode", "push_to_talk"))
        #: Set when the mode changes, so a listener parked in a blocking
        #: wait ends that wait instead of ignoring the change.
        self.mode_changed = threading.Event()
        self.muted = not bool(config.get("spoken_responses", {}).get("enabled", True))
        self.state = VoiceState.IDLE
        self._state_callbacks: list[Callable[[VoiceState], None]] = []
        self._runner_thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._task_thread: threading.Thread | None = None
        self._task_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._log_lock = threading.Lock()
        self._desired_running = False
        self._started_monotonic: float | None = None
        self._started_at: str | None = None
        self._last_heartbeat_monotonic: float | None = None
        self._last_heartbeat_at: str | None = None
        self._restart_count = 0
        self._recovery_count = 0
        self._consecutive_failures = 0
        self._last_error: str | None = None
        self._last_device_probe_monotonic = 0.0
        self._last_device_inventory: dict | None = None
        self._last_notice_text: str | None = None
        self._last_notice_monotonic = 0.0
        self._last_recognition: dict = {}
        self._wake_armed = False
        approval_config = config.get("approvals", {})
        self.approvals = VoiceApprovalController(self.tts, float(approval_config.get("timeout_seconds", 45)), self.set_state)
        self._approvals_enabled = bool(approval_config.get("enabled", True))
        self._previous_approval_callback = self.runtime.registry.approval_callback
        self._active = False
        self._resume_after_privacy = False
        self._last_spoken_text = ""
        self.response_callback: Callable[[VoiceResponse], None] = self._default_response
        self.log_path = PROJECT_ROOT / "data" / "logs" / f"voice-{datetime.now(UTC).date().isoformat()}.jsonl"
        self._default_log_path = self.log_path

    def add_state_callback(self, callback: Callable[[VoiceState], None]) -> None:
        self._state_callbacks.append(callback)

    def use_reference_voice(self, config: dict) -> dict:
        """Select a supplied local reference for this session; preserve the normal voice."""
        from app.voice.cloning import LocalVoiceCloneTTS
        from app.voice.tts import BilingualTTS
        if self._task_lock.locked():
            raise RuntimeError("Finish the current voice turn before switching voices.")
        candidate = LocalVoiceCloneTTS({**self.config.get("tts", {}), **config}, self.devices)
        status = candidate.status()
        if not status["available"]:
            candidate._synth.close()
            raise RuntimeError(" ".join(status["blockers"]))
        with self._lifecycle_lock:
            primary = self.tts.primary if isinstance(self.tts, BilingualTTS) else self.tts
            self.tts.stop()
            if getattr(self, "_normal_tts", None) is None:
                self._normal_tts = primary
            if hasattr(primary, "close"):
                primary.close()
            if isinstance(self.tts, BilingualTTS):
                self.tts.primary = candidate
            else:
                self.tts = candidate
            self.approvals.tts = self.tts
        return status

    def restore_normal_voice(self) -> bool:
        from app.voice.tts import BilingualTTS
        original = getattr(self, "_normal_tts", None)
        if original is None:
            return False
        if self._task_lock.locked():
            raise RuntimeError("Finish the current voice turn before switching voices.")
        with self._lifecycle_lock:
            primary = self.tts.primary if isinstance(self.tts, BilingualTTS) else self.tts
            primary.close()
            if isinstance(self.tts, BilingualTTS):
                self.tts.primary = original
            else:
                self.tts = original
            self.approvals.tts = self.tts
            self._normal_tts = None
        return True

    def set_state(self, state: VoiceState) -> None:
        self.state = state
        for callback in list(self._state_callbacks):
            try:
                callback(state)
            except Exception:
                # A detached GUI panel must not take the microphone process
                # down. The state remains authoritative even if one observer
                # has disappeared.
                log_swallowed(_log, "voice state callback")
                continue
        self._log("state", state=state.value)

    def status(self) -> dict:
        now = time.monotonic()
        reliability = self.config.get("reliability", {})
        return {
            "version": int(self.config.get("version", 2)),
            "enabled": bool(self._runner_thread and self._runner_thread.is_alive()),
            "mode": self.mode,
            "state": self.state.value,
            "muted": self.muted,
            "push_to_talk_key": self.hotkey.combination,
            "stt_provider": self.stt.name,
            "tts_provider": self.tts.name,
            "reference_voice_active": getattr(getattr(self.tts, "primary", self.tts), "name", "") == "xtts",
            "session_id": self.session.session_id,
            "task_running": self.runtime.agent.is_running,
            "approval_pending": self.approvals.pending is not None,
            "privacy_blocked": self._privacy_blocked(),
            "supervised": bool(reliability.get("supervised", True)),
            "desired_running": self._desired_running,
            "started_at": self._started_at,
            "uptime_seconds": round(now - self._started_monotonic, 3) if self._started_monotonic is not None else 0.0,
            "last_heartbeat_at": self._last_heartbeat_at,
            "heartbeat_age_seconds": round(now - self._last_heartbeat_monotonic, 3) if self._last_heartbeat_monotonic is not None else None,
            "restart_count": self._restart_count,
            "recovery_count": self._recovery_count,
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
            "input_available": (
                None if self._last_device_inventory is None
                else bool(self._last_device_inventory.get("microphones"))
            ),
            "last_capture": dict(getattr(self.recorder, "last_capture", {}) or {}),
            "last_recognition": dict(self._last_recognition),
            "capabilities": ["barge_in", "echo_rejection", "continuous_conversation",
                             "scoped_voice_approvals", "shared_context", "local_stt", "local_tts",
                             "watchdog_recovery", "device_reprobe", "daily_log_rollover"],
        }

    def levels(self) -> dict[str, float]:
        """Live 0..1 loudness in and out, for display. Cheap enough per frame."""
        meter = getattr(self.recorder, "meter", None)
        envelope = getattr(self.tts, "envelope", None)
        try:
            heard = float(meter.read()) if meter is not None else 0.0
            spoken = float(envelope.read()) if envelope is not None else 0.0
        except Exception:
            heard = spoken = 0.0
        return {"input": heard, "output": spoken}

    def _privacy_blocked(self) -> bool:
        return bool(getattr(getattr(self.runtime, "authority", None), "privacy_mode", False))

    def process_transcript(self, transcript: str) -> VoiceResponse:
        transcript = " ".join(transcript.strip().split())
        if not transcript:
            return VoiceResponse("No speech was recognized.", "I didn't catch that.")
        immediate = self._immediate_command(transcript)
        if immediate:
            return immediate
        return self._execute(transcript)

    def submit_transcript(self, transcript: str) -> VoiceResponse | None:
        transcript = " ".join(transcript.strip().split())
        if not transcript:
            response = VoiceResponse("No speech was recognized.", "I didn't catch that.")
            self._emit(response)
            return response
        get_output().emit(f"You: {transcript}", source="voice_transcript")
        immediate = self._immediate_command(transcript)
        if immediate:
            self._emit(immediate)
            return immediate
        if self._task_thread and self._task_thread.is_alive():
            response = VoiceResponse("A task is already running.", "I'm still working. Say cancel task if you want me to stop.")
            self._emit(response)
            return response

        def worker() -> None:
            with self._task_lock:
                response = self._execute(transcript)
            self._emit(response)

        self._task_thread = threading.Thread(target=worker, name="ai-voice-task", daemon=True)
        self._task_thread.start()
        return None

    def _immediate_command(self, transcript: str) -> VoiceResponse | None:
        if self.approvals.pending is not None:
            self.session.record("user", transcript, kind="voice_approval")
            decision = self.approvals.answer(transcript)
            if decision is True:
                return VoiceResponse("Voice approval accepted for the pending action.", "Approved. Continuing.")
            if decision is False:
                return VoiceResponse("Voice approval denied for the pending action.", "Denied. The action was cancelled.")
            return VoiceResponse("Approval phrase was not explicit enough.", "Please say approve, yes continue, or deny.")

        normalized = transcript.lower().strip(" .!?")
        remember_match = re.fullmatch(r"remember(?: that)? (.+)", transcript.strip(), re.IGNORECASE)
        if remember_match:
            fact = remember_match.group(1).strip().rstrip(".")
            memory_id = self.runtime.memory.store_memory("semantic", fact, 0.9, "voice_user")
            self.session.record("user", transcript, kind="voice_memory")
            self.session.record("assistant", "Remembered.", memory_id=memory_id)
            return VoiceResponse(f"Remembered as semantic memory {memory_id}: {fact}", "Okay. I'll remember that.")
        if normalized in {"stop", "stop talking", "be quiet", "pause speaking"}:
            self.tts.stop()
            return VoiceResponse("Speech playback stopped. The current agent task was not cancelled.", "")
        if normalized in {"cancel", "cancel task", "cancel current task", "never mind", "nevermind"}:
            self.tts.stop()
            self.approvals.cancel()
            cancelled = self.runtime.agent.cancel_current()
            self._log("cancellation", task_was_running=cancelled)
            return VoiceResponse("Cancellation requested." if cancelled else "No agent task was running.", "Cancelling." if cancelled else "There is no active task.")
        if normalized in {"mute", "mute voice"}:
            self.muted = True
            self.tts.stop()
            return VoiceResponse("Voice output muted.", "")
        if normalized in {"unmute", "unmute voice"}:
            self.muted = False
            return VoiceResponse("Voice output unmuted.", "Voice output is on.")
        if normalized in {"status", "show status", "voice status"}:
            status = self.status()
            return VoiceResponse(json.dumps(status, indent=2), f"Voice is {status['state']}. The assistant is {'working' if status['task_running'] else 'ready'}.")
        if normalized in {"volume up", "speak louder"} and isinstance(self.tts, WindowsSapiTTS):
            self.tts.volume = min(1.0, self.tts.volume + 0.1)
            return VoiceResponse(f"Voice volume is {self.tts.volume:.1f}.", "Volume increased.")
        if normalized in {"volume down", "speak quieter"} and isinstance(self.tts, WindowsSapiTTS):
            self.tts.volume = max(0.0, self.tts.volume - 0.1)
            return VoiceResponse(f"Voice volume is {self.tts.volume:.1f}.", "Volume decreased.")
        return None

    def _execute(self, transcript: str) -> VoiceResponse:
        self.activate()
        self._log_transcription(transcript)
        self.set_state(VoiceState.THINKING)
        self._log("agent_started", transcription_to_agent_ms=0)
        if self.config.get("progress_speech", {}).get("enabled", True) and not self.muted:
            self.tts.speak("Working on it.")
        started = time.perf_counter()
        try:
            assistant = getattr(self.runtime, "assistant", None)
            if assistant is not None:
                self.set_state(VoiceState.ACTING)
                reply = assistant.handle(transcript, source="voice")
                self.set_state(VoiceState.VERIFYING if reply.task_id else VoiceState.THINKING)
                metadata = {"intent": reply.intent.intent.value, "agent_duration_ms": int((time.perf_counter() - started) * 1000)}
                spoken = reply.spoken_response
                if reply.outcome is not None:
                    formatted = SpokenResponseFormatter.format(reply.outcome, transcript)
                    spoken = formatted.spoken_response
                    metadata.update(formatted.metadata)
                response = VoiceResponse(reply.display_response, spoken, reply.task_id, reply.complete, metadata)
                self.set_state(VoiceState.COMPLETED if reply.complete is not False else VoiceState.ERROR)
                self._log("agent_finished", task_id=reply.task_id, complete=reply.complete, duration_ms=response.metadata["agent_duration_ms"])
                return response
            if self._is_conversation(transcript):
                answer = self.runtime.conversation.reply(transcript)
                response = VoiceResponse(answer, SpokenResponseFormatter.compact_text(answer), complete=True)
                self.session.record("assistant", answer, spoken=response.spoken_response, kind="voice_conversation")
                self.set_state(VoiceState.COMPLETED)
                return response
            self.set_state(VoiceState.ACTING)
            goal = self.session.contextual_goal(transcript)
            outcome = self.runtime.manager.route(goal)
            response = SpokenResponseFormatter.format(outcome, transcript)
            response.metadata["agent_duration_ms"] = int((time.perf_counter() - started) * 1000)
            self.session.record("assistant", response.display_response, task_id=response.task_id, spoken=response.spoken_response)
            self.set_state(VoiceState.COMPLETED if outcome.complete else VoiceState.ERROR)
            self._log("agent_finished", task_id=outcome.task_id, complete=outcome.complete, duration_ms=response.metadata["agent_duration_ms"])
            return response
        except Exception as exc:
            self.set_state(VoiceState.ERROR)
            self._log("error", error=f"{type(exc).__name__}: {exc}")
            return VoiceResponse(f"Voice task failed: {type(exc).__name__}: {exc}", "Something went wrong. The error is on screen.", complete=False)

    @staticmethod
    def _is_conversation(transcript: str) -> bool:
        lowered = transcript.lower().strip()
        if any(phrase in lowered for phrase in ("on my screen", "in the browser", "in this project", "the failures", "this error", "believe that", "evidence supports that", "sources support that", "which source", "last verified")):
            return False
        return lowered.startswith(("what were we", "what did we", "do you remember", "who are", "how are", "why do you"))

    def _tutor(self):
        return getattr(getattr(self.runtime, "learning", None), "language", None)

    def _arabic_hearing(self) -> str | None:
        """Whether this utterance needs the Arabic-capable model, and in which mode.

        The default English model (base.en) cannot hear Arabic at all. Arabic
        mode listens with language detection, so "English please" still works;
        a speaking exercise in a lesson listens for Arabic only.
        """
        session = getattr(self.runtime, "session", None)
        pending = getattr(session, "pending_learning", None) or {}
        tutor = self._tutor()
        if pending.get("type") == "language" and tutor is not None:
            exercise = tutor.current(pending.get("course", "arabic")) or {}
            if exercise.get("skill") == "speaking":
                return "ar"
        if getattr(session, "reply_language", "en") != "en":
            return "auto"
        # Hear other languages too: someone speaking Urdu is transcribed in
        # Urdu (and explained in English) instead of turned into English noise.
        if bool(self.config.get("stt", {}).get("detect_other_languages", True)):
            return "auto"
        return None

    def transcribe(self, audio) -> Transcription:
        self.set_state(VoiceState.TRANSCRIBING)
        hearing = self._arabic_hearing()
        listener = getattr(self._tutor(), "listener", None)
        if hearing and listener is not None and getattr(listener, "ready", lambda: False)():
            stt_config = self.config.get("stt", {})
            result = listener.transcribe(audio, self.cancellation.shutdown, language=None if hearing == "auto" else "ar",
                                         hotwords=str(stt_config.get("hotwords", "")) if hearing == "auto" else "",
                                         prompt=str(stt_config.get("initial_prompt", "")) if hearing == "auto" else "")
        else:
            result = self.stt.transcribe(audio, self.cancellation.shutdown)
        self._last_recognition = {"duration_ms": result.duration_ms, "language": result.language,
                                  "confidence": self._recognition_confidence(result),
                                  "text_length": len(result.text), "checked_at": datetime.now(UTC).isoformat()}
        self._log("stt", **self._last_recognition)
        return result

    @staticmethod
    def _recognition_confidence(result) -> float | None:
        """Normal STT uses confidence; the optional tutor listener uses probability."""
        value = getattr(result, "confidence", None)
        if value is None:
            value = getattr(result, "probability", None)
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) and 0 <= number <= 1 else 0.0

    def configure_listening(self, *, silence_seconds: float, energy_threshold: float) -> dict:
        """Tune the running detector and next capture without restarting voice."""
        pause, threshold = float(silence_seconds), float(energy_threshold)
        if not math.isfinite(pause) or not 0.3 <= pause <= 2.5:
            raise ValueError("End-of-turn pause must be between 0.3 and 2.5 seconds.")
        if not math.isfinite(threshold) or not 0.001 <= threshold <= 0.05:
            raise ValueError("Speech threshold must be between 0.001 and 0.05.")
        self.config.setdefault("vad", {}).update(silence_seconds=pause, energy_threshold=threshold)
        self.vad.set_threshold(threshold)
        detector = getattr(self.recorder, "vad", None)
        if detector is not None and detector is not self.vad:
            detector.set_threshold(threshold)
        return {"silence_seconds": pause, "energy_threshold": threshold}

    def runtime_has_window(self) -> bool:
        """Whether a desktop window is showing this session's output."""
        return getattr(self.runtime, "gui", None) is not None

    def run_forever(self) -> None:
        with self._lifecycle_lock:
            # Direct CLI use calls run_forever without start_background.
            # A background runner that lost a race with stop(), however, must
            # not clear shutdown and resurrect itself.
            if not self._desired_running:
                if threading.current_thread() is self._runner_thread:
                    return
                self._desired_running = True
            self.cancellation.shutdown.clear()
            if self._started_monotonic is None:
                self._started_monotonic = time.monotonic()
                self._started_at = datetime.now(UTC).isoformat()
        if not self._probe_devices():
            with self._lifecycle_lock:
                self._desired_running = False
                self.cancellation.shutdown.set()
            self.set_state(VoiceState.ERROR)
            self._notify_voice_issue(self._last_error or "Microphone input is unavailable.", force=True)
            return
        self.activate()
        # "Press Ctrl+C to exit" is true of the console and meaningless in the
        # desktop window, which has no Ctrl+C -- and the window shows this
        # line too, because both read the same output coordinator.
        ready = f"Voice ready. Mode: {self.mode}. Push-to-talk: {self.hotkey.combination}."
        if not self.runtime_has_window():
            ready += " Press Ctrl+C to exit."
        get_output().emit(ready, source="voice")
        try:
            while not self.cancellation.shutdown.is_set():
                self._heartbeat()
                if self._privacy_blocked():
                    self.set_state(VoiceState.STOPPED)
                    self.cancellation.shutdown.wait(0.2)
                    continue
                # Cleared each time round, so a mode change that arrived
                # during the last cycle ends that cycle and nothing more.
                try:
                    self.mode_changed.clear()
                    mode = self.mode
                    if mode == "wake_word":
                        self._wake_cycle()
                    elif mode == "continuous":
                        self._continuous_cycle()
                    else:
                        self._push_to_talk_cycle()
                    if self._consecutive_failures:
                        self._recovery_count += 1
                        self._log("listener_recovered", failures=self._consecutive_failures)
                    self._consecutive_failures = 0
                    self._last_error = None
                    self._maybe_probe_devices()
                except Exception as exc:
                    if self.cancellation.shutdown.is_set():
                        break
                    self._consecutive_failures += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self.set_state(VoiceState.ERROR)
                    self._log("listener_error", error=self._last_error,
                              consecutive_failures=self._consecutive_failures)
                    self._notify_voice_issue(self._last_error)
                    self._probe_devices()
                    reliability = self.config.get("reliability", {})
                    initial = max(0.01, float(reliability.get("initial_backoff_seconds", 0.25)))
                    maximum = max(initial, float(reliability.get("maximum_backoff_seconds", 30.0)))
                    backoff = min(maximum, initial * (2 ** min(self._consecutive_failures - 1, 10)))
                    self.cancellation.shutdown.wait(backoff)
        except KeyboardInterrupt:
            self._desired_running = False
            self.cancellation.shutdown.set()
        finally:
            self.set_state(VoiceState.STOPPED)

    def _heartbeat(self) -> None:
        self._last_heartbeat_monotonic = time.monotonic()
        self._last_heartbeat_at = datetime.now(UTC).isoformat()

    def _maybe_probe_devices(self) -> None:
        interval = max(5.0, float(self.config.get("reliability", {}).get("device_reprobe_seconds", 300.0)))
        if time.monotonic() - self._last_device_probe_monotonic >= interval:
            self._probe_devices()

    def _probe_devices(self) -> bool:
        """Refresh default/selected audio devices without changing policy."""
        try:
            available = self.devices.list_devices()
            self._last_device_inventory = available
            if not available.get("microphones"):
                self._last_error = (
                    "No Windows recording device is available (default input is -1). "
                    "The microphone/audio controller must be enabled or repaired."
                )
                self._last_device_probe_monotonic = time.monotonic()
                self._log("device_probe", microphones=0,
                          speakers=len(available.get("speakers", [])), success=False,
                          error=self._last_error)
                return False
            requested_input = self.config.get("microphone", {}).get("device", "auto")
            self.recorder.device = self.devices.resolve(requested_input, "input")
            if hasattr(self.tts, "output_device"):
                requested_output = self.config.get("tts", {}).get("output_device", "auto")
                self.tts.output_device = self.devices.resolve(requested_output, "output")
            self._last_device_probe_monotonic = time.monotonic()
            self._log("device_probe", microphones=len(available.get("microphones", [])),
                      speakers=len(available.get("speakers", [])), success=True)
            return True
        except Exception as exc:
            self._last_device_inventory = None
            self._last_device_probe_monotonic = time.monotonic()
            self._log("device_probe", success=False, error=f"{type(exc).__name__}: {exc}")
            return False

    def _push_to_talk_cycle(self) -> None:
        self.set_state(VoiceState.READY)
        # While push-to-talk is on, the combination belongs to HAYAT: it must
        # not also open popups in the application that has focus.
        claim = getattr(self.hotkey, "claim_keys", None)
        if claim is not None:
            claim(True)
        # Ends on the hotkey, on shutdown, or on someone switching modes.
        if not self.hotkey.wait_for_press(AnyEvent(self.cancellation.shutdown, self.mode_changed)):
            return
        if self.mode_changed.is_set():
            return  # not a key press; go round and pick up the new mode
        self._log("push_to_talk_pressed", hotkey=self.hotkey.combination)
        self.tts.stop()
        self.set_state(VoiceState.LISTENING)
        audio = self.recorder.record_while(self.hotkey.pressed, self.cancellation.shutdown)
        self.hotkey.wait_for_release(self.cancellation.shutdown)
        if not len(audio.samples):
            self.set_state(VoiceState.READY)
            self._notify_voice_issue("Push-to-talk was detected, but the microphone returned no audio.", force=True)
            return
        result = self.transcribe(audio)
        if result.text:
            self.submit_transcript(result.text)
        else:
            self.set_state(VoiceState.READY)
            self._notify_voice_issue(
                "Audio was captured, but no words were recognized. Hold the hotkey, speak, then release it.",
                force=True,
            )

    #: Things said to stop a conversation rather than to be acted on. Checked
    #: before the transcript reaches the assistant, because "stop" arriving as
    #: a task is how a stop request becomes another thing to interrupt.
    _STOP_PHRASES = frozenset({
        "stop", "stop listening", "stop it", "that's enough", "thats enough",
        "shut up", "be quiet", "quiet", "enough", "cancel", "never mind",
        "nevermind", "hayat stop", "stop hayat", "pause", "sleep",
    })

    def _continuous_cycle(self) -> None:
        """Hands-free back-and-forth: listen, answer, listen again.

        No hotkey and no wake phrase -- Mohsin talks, HAYAT answers, and the
        turn ends when he stops speaking. Voice activity detection decides
        where an utterance ends, which is why silence_seconds matters more
        here than anywhere else: too short and it cuts him off mid-sentence,
        too long and every reply feels late.

        Two things make an always-listening mode safe to offer. It stops on a
        spoken word, so ending it does not require finding the keyboard. And
        HAYAT stops speaking the moment the microphone hears speech again, so
        he can interrupt an answer he has already heard enough of rather than
        waiting politely for it to finish.
        """
        self.set_state(VoiceState.LISTENING)
        vad = self.config.get("vad", {})
        audio = self.recorder.record_until_silence(
            self.cancellation.shutdown,
            float(vad.get("speech_start_timeout", 5)),
            float(vad.get("silence_seconds", 0.8)),
        )
        if not len(audio.samples):
            capture = getattr(self.recorder, "last_capture", {}) or {}
            observed_peak = float(capture.get("observed_peak", 0.0) or 0.0)
            if observed_peak >= max(0.001, float(vad.get("energy_threshold", 0.0035)) * 0.5):
                self._notify_voice_issue(
                    "The microphone heard a quiet signal but did not detect speech. Move closer or lower the VAD threshold.",
                )
            return

        # Speech while HAYAT is talking is an interruption, not an overlap.
        # Remember that it may also be the speaker feeding back into the mic;
        # the transcript is checked below before it can become a new task.
        was_speaking = self.tts.is_speaking
        if was_speaking:
            self.tts.stop()

        result = self.transcribe(audio)
        text = (result.text or "").strip()
        if not text:
            self._notify_voice_issue(
                "Speech audio was captured, but transcription returned no words. Please repeat the sentence.",
            )
            return
        confidence = self._recognition_confidence(result)
        if self._probably_noise(text, confidence):
            # Whisper turns background noise into stock phrases ("Thank you.",
            # "you"). In hands-free mode those used to become turns.
            self._log("noise_rejected", text_length=len(text), confidence=confidence)
            self.set_state(VoiceState.READY)
            return

        if was_speaking and not self._is_stop_phrase(text) and self._looks_like_speaker_echo(text):
            self._log("echo_rejected", text_length=len(text))
            self.set_state(VoiceState.READY)
            return

        if self._is_stop_phrase(text):
            self._speak("Stopping.")
            self.set_mode("push")
            get_output().emit(
                "Continuous listening off. Push-to-talk is back on.", source="voice",
            )
            return

        self.submit_transcript(text)

    #: What Whisper typically "hears" in a room with no one speaking.
    _NOISE_PHRASES = frozenset({
        "you", "thank you", "thanks for watching", "thank you for watching", "bye", "bye bye",
        "so", "uh", "um", "hmm", "mm", "oh", "the", "i", "a",
        "subtitles by the amara org community", "please subscribe", "music",
    })
    # Not "yes", "yeah" or "okay": those answer approval questions by voice.

    def _probably_noise(self, text: str, confidence: float | None) -> bool:
        """A hands-free transcript that is more likely the room than Mohsin."""
        # Keep letters from every supported language. ASCII-only filtering
        # wrongly classified Hindi, Chinese and Cyrillic speech as empty noise.
        cleaned = "".join(char if char.isalnum() or char.isspace() else " " for char in text.casefold())
        cleaned = " ".join(cleaned.split())
        if not cleaned:
            return True
        floor = float(self.config.get("vad", {}).get("minimum_confidence", 0.3))
        if confidence is not None and (not math.isfinite(confidence) or confidence < floor):
            return True
        return cleaned in self._NOISE_PHRASES and (confidence is None or confidence < 0.75)

    def _is_stop_phrase(self, text: str) -> bool:
        """Whether this was said to end the conversation.

        Normalised first, so "hayat, stop." and "bro stop" are recognised the
        same as "stop" -- the address and punctuation should not be the
        difference between stopping and starting a task called "stop".
        """
        from app.intent.normalize import normalize

        cleaned = normalize(text).text.lower().strip(" .!?,")
        return cleaned in self._STOP_PHRASES or text.lower().strip(" .!?,") in self._STOP_PHRASES

    def _looks_like_speaker_echo(self, transcript: str) -> bool:
        """Reject HAYAT hearing its own most recent spoken answer."""
        if not self._last_spoken_text:
            return False
        words = lambda value: {item for item in re.findall(r"[^\W_]+", value.casefold()) if len(item) > 2}
        heard, spoken = words(transcript), words(self._last_spoken_text)
        if not heard or not spoken:
            return False
        similarity = len(heard & spoken) / max(1, len(heard))
        threshold = float(self.config.get("interruption", {}).get("echo_rejection_similarity", 0.60))
        return similarity >= threshold

    def _wake_cycle(self) -> None:
        self.set_state(VoiceState.LISTENING)
        vad = self.config.get("vad", {})
        if self.wake_gate is not None:
            ready, reason = self.wake_gate.ready()
            if ready:
                self._gated_wake_cycle(vad)
                return
            if self._wake_gate_note != reason:
                # Said once, not every cycle: the phrase mode carries on.
                self._wake_gate_note = reason
                self._log("wake_engine_unavailable", reason=reason)
                get_output().emit(f"Low-power wake word unavailable ({reason}); listening for the wake phrase instead.",
                                  source="voice")
        audio = self.recorder.record_until_silence(self.cancellation.shutdown, float(vad.get("speech_start_timeout", 5)), float(vad.get("silence_seconds", 0.8)))
        if not len(audio.samples):
            return
        result = self.transcribe(audio)
        if self._wake_armed:
            self._wake_armed = False
            if result.text:
                self.submit_transcript(result.text)
            return
        command = self.wakeword.extract_command(result.text)
        if command is None:
            return
        if command:
            self.submit_transcript(command)
        else:
            self._wake_armed = True
            self._speak("Yes?")

    def _gated_wake_cycle(self, vad: dict) -> None:
        """Wait for the wake model, then record and transcribe one command."""
        heard = self.recorder.listen_for(self.wake_gate.detector(), self.cancellation.shutdown,
                                         timeout=float(vad.get("wake_listen_seconds", 30.0)))
        if not heard or self.cancellation.shutdown.is_set():
            return
        self._log("wake_word_detected", engine="openwakeword", model=self.wake_gate.model)
        self._speak("Yes?")
        audio = self.recorder.record_until_silence(self.cancellation.shutdown, float(vad.get("speech_start_timeout", 5)),
                                                   float(vad.get("silence_seconds", 0.8)))
        if not len(audio.samples):
            return
        result = self.transcribe(audio)
        if result.text:
            self.submit_transcript(result.text)

    def start_background(self) -> bool:
        if self._privacy_blocked():
            self._resume_after_privacy = True
            self._last_error = "Privacy mode is blocking microphone access."
            return False
        if not self._probe_devices():
            with self._lifecycle_lock:
                self._desired_running = False
                self.cancellation.shutdown.set()
            self._notify_voice_issue(self._last_error or "Microphone input is unavailable.", force=True)
            return False
        started_listener = False
        if hasattr(self.stt, "reopen"):
            self.stt.reopen()
        with self._lifecycle_lock:
            self._desired_running = True
            self.cancellation.shutdown.clear()
            if not (self._runner_thread and self._runner_thread.is_alive()):
                self._runner_thread = threading.Thread(target=self.run_forever, name="ai-voice-listener", daemon=True)
                self._runner_thread.start()
                started_listener = True
            if self.config.get("reliability", {}).get("supervised", True) and not (
                self._watchdog_thread and self._watchdog_thread.is_alive()
            ):
                self._watchdog_thread = threading.Thread(
                    target=self._watchdog_loop, name="ai-voice-watchdog", daemon=True,
                )
                self._watchdog_thread.start()
        if started_listener and self.config.get("warm_stt_on_start", True) and hasattr(self.stt, "warmup"):
            threading.Thread(target=self._warm_stt, name="ai-stt-warmup", daemon=True).start()
        return bool(self._runner_thread and self._runner_thread.is_alive())

    def _watchdog_loop(self) -> None:
        interval = max(0.05, float(self.config.get("reliability", {}).get("watchdog_seconds", 5.0)))
        while self._desired_running and not self.cancellation.shutdown.wait(interval):
            runner = self._runner_thread
            if runner is not None and runner.is_alive():
                continue
            with self._lifecycle_lock:
                if not self._desired_running or self.cancellation.shutdown.is_set():
                    return
                self._restart_count += 1
                self._log("watchdog_restart", restart_count=self._restart_count)
                self._runner_thread = threading.Thread(
                    target=self.run_forever, name="ai-voice-listener", daemon=True,
                )
                self._runner_thread.start()

    def pause_for_privacy(self) -> None:
        """Stop microphone capture immediately and remember whether to resume."""
        self._resume_after_privacy = bool(self._runner_thread and self._runner_thread.is_alive())
        self.stop()

    def resume_after_privacy(self) -> None:
        resume = self._resume_after_privacy
        self._resume_after_privacy = False
        if resume:
            self.start_background()

    def _warm_stt(self) -> None:
        started = time.perf_counter()
        try:
            self.stt.warmup()
            self._log("stt_warmup", duration_ms=int((time.perf_counter() - started) * 1000))
        except Exception as exc:
            self._log("stt_warmup_error", error=f"{type(exc).__name__}: {exc}")

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._desired_running = False
            self.cancellation.shutdown.set()
        self.approvals.cancel()
        self.runtime.agent.cancel_current()
        getattr(self.hotkey, "release", lambda: None)()   # Ctrl+Space goes back to the other applications
        self.tts.stop()
        if hasattr(self.stt, "close"):
            self.stt.close()
        if hasattr(self.tts, "close"):
            self.tts.close()
        if self._runner_thread and self._runner_thread.is_alive() and self._runner_thread is not threading.current_thread():
            self._runner_thread.join(timeout=1.5)
        if self._watchdog_thread and self._watchdog_thread.is_alive() and self._watchdog_thread is not threading.current_thread():
            self._watchdog_thread.join(timeout=1.5)
        if self._active:
            self.runtime.registry.approval_callback = self._previous_approval_callback
            self._active = False

    def activate(self) -> None:
        if self._approvals_enabled and not self._active:
            self._previous_approval_callback = self.runtime.registry.approval_callback
            self.runtime.registry.approval_callback = self.approvals.request
        self._active = True

    def set_mode(self, mode: str) -> None:
        normalized = {
            "push": "push_to_talk", "push_to_talk": "push_to_talk",
            "wake": "wake_word", "wake_word": "wake_word",
            # Hands-free back-and-forth. Kept switchable at runtime rather than
            # only in config, because the point of an always-listening mode is
            # that Mohsin turns it on for a conversation and off afterwards.
            "continuous": "continuous", "hands_free": "continuous", "talk": "continuous",
        }.get(mode.lower())
        if not normalized:
            raise ValueError("Voice mode must be push, wake, or continuous.")
        # getattr, because set_mode is reachable before __init__ has finished
        # assigning the field -- and a mode setter that raises on a
        # half-built manager is a worse failure than the one it was fixing.
        changed = normalized != getattr(self, "mode", None)
        self.mode = normalized
        if normalized != "push_to_talk" and hasattr(getattr(self, "hotkey", None), "claim_keys"):
            self.hotkey.claim_keys(False)
        if changed and getattr(self, "mode_changed", None) is not None:
            # Wake the listener. Without this the loop stays parked in
            # wait_for_press until the hotkey is pressed, so switching to
            # hands-free from the window or the CLI appeared to do nothing at
            # all -- the mode had changed and nothing was looking at it.
            self.mode_changed.set()

    def set_input_device(self, requested: str | int | None) -> int | None:
        self.recorder.device = self.devices.resolve(requested, "input")
        return self.recorder.device

    def set_output_device(self, requested: str | int | None) -> int | None:
        if not hasattr(self.tts, "output_device"):
            raise RuntimeError("The configured TTS provider does not support output-device selection.")
        self.tts.output_device = self.devices.resolve(requested, "output")
        return self.tts.output_device

    def test_speaker(self, wait: bool = True) -> None:
        self.tts.speak("Voice system is working.", wait=wait)

    def probe_microphone(self, seconds: float = 0.75) -> dict:
        diagnostics = self.devices.diagnose_input()
        if not diagnostics["available"]:
            return diagnostics | {"captured": False}
        return diagnostics | self.recorder.probe(seconds)

    def _notify_voice_issue(self, message: str, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and message == self._last_notice_text and now - self._last_notice_monotonic < 60.0:
            return
        self._last_notice_text = message
        self._last_notice_monotonic = now
        get_output().emit(f"Voice: {message}", source="voice_error")
        self._log("voice_notice", message=message)

    def _emit(self, response: VoiceResponse) -> None:
        self.response_callback(response)
        self._speak(response.spoken_response)

    def _speak(self, text: str) -> None:
        if not self.muted and text:
            self._last_spoken_text = text
            self.set_state(VoiceState.SPEAKING)
            started = time.perf_counter()
            self.tts.speak(text)
            def monitor() -> None:
                while self.tts.is_speaking and not self.cancellation.shutdown.is_set():
                    time.sleep(0.02)
                self._log("tts", duration_ms=int((time.perf_counter() - started) * 1000), stopped=self.cancellation.shutdown.is_set())
                if not self.cancellation.shutdown.is_set() and self.state is VoiceState.SPEAKING:
                    self.set_state(VoiceState.READY)
            threading.Thread(target=monitor, name="ai-tts-timing", daemon=True).start()

    @staticmethod
    def _default_response(response: VoiceResponse) -> None:
        get_output().emit(f"AI: {response.display_response}", source="assistant")

    def _log_transcription(self, transcript: str) -> None:
        privacy = self.config.get("privacy", {})
        if privacy.get("log_transcriptions", False):
            self._log("transcription", transcription=transcript)
        else:
            digest = hashlib.sha256(transcript.encode("utf-8")).hexdigest()[:12]
            self._log("transcription", text_length=len(transcript), digest=digest, content_logged=False)

    def _log(self, event: str, **fields) -> None:
        try:
            with self._log_lock:
                # A process that stays alive across midnight gets a new daily
                # file. Explicit test/operator log paths remain untouched.
                if self.log_path == self._default_log_path:
                    current = PROJECT_ROOT / "data" / "logs" / f"voice-{datetime.now(UTC).date().isoformat()}.jsonl"
                    self.log_path = current
                    self._default_log_path = current
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                payload = {"timestamp": datetime.now(UTC).isoformat(), "session_id": self.session.session_id, "event": event, **fields}
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, default=str) + "\n")
        except OSError:
            # Telemetry is valuable but must never become a single point of
            # failure for an unattended microphone service.
            return
