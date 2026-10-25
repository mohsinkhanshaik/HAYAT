from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionState:
    """Shared text/voice reference and task state for one assistant session."""

    session_id: str = field(default_factory=lambda: f"session-{uuid.uuid4().hex[:12]}")
    active_application: str | None = None
    active_window: dict[str, Any] | None = None
    last_opened_application: str | None = None
    last_file: str | None = None
    active_customer: str | None = None
    last_video: dict[str, Any] = field(default_factory=dict)
    last_browser_tab: dict[str, Any] | None = None
    last_target: str | None = None
    last_selected_object: str | None = None
    last_task: Any | None = None
    last_intent: Any | None = None
    last_user_input: str = ""
    #: Substantive subject survives acknowledgements and correction-only turns.
    conversation_topic: str = ""
    research_audit: dict[str, Any] = field(default_factory=dict)
    #: "For the next two answers, reply with one word": {"rule": ..., "remaining": 2 or None}.
    standing_instruction: dict[str, Any] = field(default_factory=dict)
    #: Which path answered the last turn: "exact", "brain", or the router's classification source.
    last_answer_source: str = ""
    last_response: str = ""
    last_correction: str = ""
    pending_action: dict[str, Any] | None = None
    pending_learning: dict[str, Any] | None = None
    #: The language replies are written and spoken in: "en" unless Mohsin asks for "ar".
    reply_language: str = "en"
    #: A software or language install HAYAT explained and is waiting for Mohsin's yes on.
    pending_install: dict[str, Any] | None = None
    #: Repeated yes after a model pull reports its status instead of asking again.
    model_pull_followup: bool = False
    #: Languages HAYAT has already offered to learn in this session.
    offered_languages: set[str] = field(default_factory=set)
    #: HAYAT offered to do something ("want me to search for it?"); "yes" accepts.
    pending_offer: dict[str, Any] | None = None
    last_camera_observation: dict[str, Any] | None = None
    #: The last verified public camera list, so "play the second one" names a real camera.
    last_cameras: list[dict[str, Any]] = field(default_factory=list)
    #: The public camera playing in desktop chat, so "pause" or "next" can control it.
    active_camera: str | None = None
    #: Which happened last, "list" or "play": decides whether a bare "play" picks or resumes.
    camera_context: str = ""
    camera_context_at: float = 0.0
    active_turn_id: str | None = None
    response_mode: str = "NORMAL"
    effective_modes: list[str] = field(default_factory=lambda: ["BALANCED"])
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "session_id": self.session_id,
                "active_application": self.active_application,
                "active_window": self.active_window,
                "last_opened_application": self.last_opened_application,
                "last_file": self.last_file,
                "active_customer": self.active_customer,
                "last_video": dict(self.last_video),
                "last_browser_tab": self.last_browser_tab,
                "last_target": self.last_target,
                "last_selected_object": self.last_selected_object,
                "last_task_goal": getattr(self.last_task, "goal", None),
                "last_task_complete": getattr(self.last_task, "complete", None),
                "last_user_input": self.last_user_input,
                "conversation_topic": self.conversation_topic,
                "research_audit": dict(self.research_audit),
                "standing_instruction": dict(self.standing_instruction),
                "last_response": self.last_response,
                "last_correction": self.last_correction,
                "pending_action": self.pending_action,
                "pending_learning": self.pending_learning,
                "reply_language": self.reply_language,
                "pending_install": self.pending_install,
                "last_camera_observation": self.last_camera_observation,
                "last_cameras": list(self.last_cameras),
                "active_camera": self.active_camera,
                "response_mode": self.response_mode,
                "effective_modes": list(self.effective_modes),
            }

    def update_from_outcome(self, outcome: Any) -> None:
        with self._lock:
            self.last_task = outcome
            for event in getattr(outcome, "history", []):
                if event.get('result', {}).get('success') is not True:
                    continue
                if 'verification' in event and event['verification'].get('passed') is not True:
                    continue
                output = event.get("result", {}).get("output", {})
                if not isinstance(output, dict):
                    continue
                self.remember_camera_result(event.get('tool', ''), output)
                if event.get('tool', '').startswith('customer_') and output.get('customer_reference'):
                    self.active_customer = str(output['customer_reference'])
                if event.get('result', {}).get('success'):
                    self.remember_video_result(event.get('tool', ''), output)
                application = output.get("application") or output.get("target_application")
                if application:
                    self.active_application = str(application)
                    self.last_opened_application = str(application)
                    self.last_target = str(application)
                if output.get("window"):
                    window = output["window"]
                    self.active_window = dict(window) if isinstance(window, dict) else {"title": str(window)}
                if output.get("closed") is True and application == self.active_application:
                    self.active_application = None
                    self.active_window = None
                if output.get("observed_at") and isinstance(output.get("vision"), dict):
                    vision = output["vision"]
                    frame = output.get("frame") if isinstance(output.get("frame"), dict) else {}
                    interpretation = str(vision.get("interpretation") or "").strip()
                    if interpretation:
                        self.last_camera_observation = {
                            "observed_at": output["observed_at"],
                            "interpretation": interpretation,
                            "width": frame.get("width"),
                            "height": frame.get("height"),
                            "device_index": output.get("device_index"),
                            "model": vision.get("model"),
                            "provider": vision.get("provider"),
                            "raw_frame_retained": bool(output.get("raw_frame_retained")),
                            "network_used": False,
                        }
                if output.get("path"):
                    self.last_file = str(output["path"])
                if output.get("url"):
                    self.last_browser_tab = {"url": output.get("url"), "title": output.get("title")}

    def referenced_application(self) -> str | None:
        return self.active_application or self.last_opened_application

    def remember_camera_result(self, tool: str, output: dict[str, Any]) -> None:
        """Verified public camera lists and playback become the target of follow-ups."""
        import time
        from app.intelligence.camera_chat import remember_cameras
        with self._lock:
            if tool == 'intelligence_cameras' and output.get('cameras'):
                self.last_cameras = remember_cameras(output['cameras'])
                self.camera_context, self.camera_context_at = 'list', time.monotonic()
            elif tool == 'intelligence_camera_play' and output.get('event_id'):
                self.active_camera = str(output['event_id'])
                self.camera_context, self.camera_context_at = 'play', time.monotonic()
            elif tool == 'intelligence_camera_control' and output.get('camera_control'):
                self.camera_context_at = time.monotonic()
                if output['camera_control'] == 'stop':
                    self.active_camera = None
                elif output.get('event_id'):
                    self.active_camera = str(output['event_id'])

    def remember_video_result(self, tool: str, output: dict[str, Any]) -> None:
        """Only verified video tool results establish a media reference."""
        if tool not in {'search_play_video', 'inspect_video_link', 'download_video', 'video_preview', 'analyze_video_clips',
                        'create_video_clip', 'make_video_clips', 'play_video', 'assess_video', 'edit_video_clip'}:
            return
        with self._lock:
            # A newer video makes "play it" and "pause" mean that video, not a camera.
            self.last_cameras, self.active_camera, self.camera_context = [], None, ''
            current = dict(self.last_video)
            path = output.get('source_path') or output.get('path')
            if path and tool != 'video_preview':
                if current.get('path') != path:
                    current = {}
                current['path'] = str(path)
            download = output.get('download') or {}
            url = output.get('url') or download.get('url')
            if url:
                current['url'] = str(url)
            if tool in {'inspect_video_link', 'search_play_video'} and not path:
                # A newly inspected link must not inherit an unrelated file.
                current = {'url': str(url)} if url else {}
            if output.get('clips'):
                current['clips'] = [{'path': str(clip['path']), 'title': str(clip.get('title', ''))}
                                    for clip in output['clips'][:10] if clip.get('path')]
                if current['clips']:
                    current['selected_path'] = current['clips'][0]['path']
            analysis = output.get('analysis') or output
            if analysis.get('report_path'):
                current['analysis_path'] = str(analysis['report_path'])
            if tool in {'video_preview', 'create_video_clip', 'play_video', 'edit_video_clip'} and output.get('path'):
                current['selected_path'] = str(output['path'])
                current.setdefault('path', str(output['path']))
            self.last_video = current
