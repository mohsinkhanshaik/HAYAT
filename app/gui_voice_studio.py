"""Voice Studio page: live microphone and speaker levels, the voice pipeline, conversation and settings.

Everything shown is read from the running voice system: its state machine
(listening, transcribing, thinking, speaking), the measured input/output
levels, the audio devices it reports, and the voice session's own transcript.
Controls change real settings only; nothing here is decorative.
"""
from __future__ import annotations

import collections
import math
import time
import tkinter as tk
from tkinter import ttk
from typing import Any

from app.gui_resources import StringVar

#: The four stages a spoken request passes through, keyed by voice states.
STAGES = (("listening", "Listening", "Capturing your voice"), ("transcribing", "Transcribing", "Converting to text"),
          ("thinking", "Thinking", "Reasoning and tools"), ("speaking", "Speaking", "Reading the reply"))
STAGE_OF = {"listening": 0, "transcribing": 1, "thinking": 2, "acting": 2, "verifying": 2, "approval_required": 2,
            "speaking": 3}
STATE_TEXT = {"ready": "Ready", "idle": "Idle", "listening": "Listening…", "transcribing": "Transcribing…",
              "thinking": "Thinking…", "acting": "Using tools…", "verifying": "Checking the result…",
              "approval_required": "Waiting for your approval", "speaking": "Speaking…", "completed": "Done",
              "error": "Voice error", "stopped": "Voice off"}
SAMPLES = (("Research", "What's the latest news about earthquakes?"), ("Intelligence", "Show global intelligence"),
           ("Business", "Show today's business brief"), ("Tasks", "What are my open tasks?"))
INPUT_COLOUR, OUTPUT_COLOUR = "#4FA3FF", "#B388FF"


def decibels(level: float) -> str:
    return "-∞ dB" if level <= 0.001 else f"{max(-60.0, 20 * math.log10(level)):.0f} dB"


class VoicePage:
    """Builds the voice page inside ``page`` and refreshes it from ``update``."""

    def __init__(self, studio: Any, page: tk.Frame) -> None:
        self.studio, self.page = studio, page
        self.gui, self.palette = studio.gui, studio.palette
        self.voice = getattr(self.gui.runtime, "voice", None)
        self.history = {"input": collections.deque([0.0] * 90, maxlen=90), "output": collections.deque([0.0] * 90, maxlen=90)}
        # State changes arrive on the voice thread; a deque append is safe to share.
        self.events: collections.deque = collections.deque(maxlen=40)
        if self.voice is not None and hasattr(self.voice, "add_state_callback"):
            self.voice.add_state_callback(lambda state: self.events.append((time.strftime("%H:%M:%S"), str(getattr(state, "value", state)))))
        self._conversation_signature = None
        self._conversation_checked = 0.0
        self._events_shown = None
        self._compact = None
        self._build()

    # ----------------------------------------------------------------- layout
    def _build(self) -> None:
        p, page = self.palette, self.page
        head = tk.Frame(page, bg=p.BLACK)
        head.pack(fill="x", pady=(0, 6))
        tk.Label(head, text="Voice Studio", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 17), anchor="w").pack(side="left")
        self.subtitle = tk.Label(head, text="  Speak naturally with HAYAT · voice, transcription and replies run locally",
                                 bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w")
        self.subtitle.pack(side="left", pady=(6, 0))
        self.body = tk.Frame(page, bg=p.BLACK)
        self.body.pack(fill="both", expand=True)
        self.main = tk.Frame(self.body, bg=p.BLACK)
        self.main.pack(side="left", fill="both", expand=True)
        self.side = tk.Frame(self.body, bg=p.BLACK, width=330)
        top = tk.Frame(self.main, bg=p.BLACK)
        top.pack(fill="x")
        top.columnconfigure(0, weight=2, uniform="top")
        top.columnconfigure(1, weight=3, uniform="top")
        top.columnconfigure(2, weight=2, uniform="top")
        self.cards = {"input": self._audio_card(top, "input", "Microphone input", INPUT_COLOUR),
                      "output": self._audio_card(top, "output", "Speaker output", OUTPUT_COLOUR)}
        self.cards["input"]["frame"].grid(row=0, column=0, sticky="nsew")
        self.cards["output"]["frame"].grid(row=0, column=2, sticky="nsew")
        from app.gui_studio import VoiceFocus
        self.scene = VoiceFocus(top, p, lambda key: None, lambda key: self.studio.voice_action("toggle"),
                                on_motion=self._motion_changed)
        self.scene.voice_levels = self.voice.levels if self.voice is not None else None
        self.scene.set_motion(self.gui.depth_nav.motion)
        self.scene.configure(height=230)
        self.scene.grid(row=0, column=1, sticky="nsew", padx=10)
        self.pipeline = tk.Canvas(self.main, height=84, bg=p.PANEL, highlightthickness=1, highlightbackground=p.LINE)
        self.pipeline.pack(fill="x", pady=(8, 6))
        self.pipeline.bind("<Configure>", lambda _event: self._draw_pipeline(self._stage))
        self._stage = None
        from app.gui_intelligence_parts import FlowRow
        controls = FlowRow(self.main, p.BLACK, gap=6, height=44)
        controls.pack(fill="x", pady=(0, 6))
        self.toggle = controls.add(ttk.Button(controls, text="Start voice", style="HayatAccent.TButton",
                                              command=lambda: self.studio.voice_action("toggle")))
        from app.gui_voice import VOICE_MODES
        self.mode_buttons = {}
        for action, title in VOICE_MODES.items():
            self.mode_buttons[action] = controls.add(ttk.Button(controls, text=title, style="Hayat.TButton",
                                                                command=lambda a=action: self.studio.voice_action(a)))
        for title, action in (("Test microphone", self.gui._probe_microphone), ("Test speaker", self.gui._test_speaker),
                              ("Stop speaking", lambda: self.studio.voice_action("quiet"))):
            controls.add(ttk.Button(controls, text=title, style="Hayat.TButton", command=action))
        from app.gui_3d_controls import camera_menu
        controls.add(camera_menu(controls, self.scene))
        if callable(getattr(self.gui, '_stop_and_correct', None)):
            controls.add(ttk.Button(controls, text='Stop & correct', command=self.gui._stop_and_correct, style='Hayat.TButton'))
        self.reference_button = controls.add(ttk.Button(controls, text="Reference voice…", style="Hayat.TButton",
                                                        command=self.open_reference_voice))
        self.listening_button = controls.add(ttk.Button(controls, text="Listening settings…", style="Hayat.TButton",
                                                        command=self.open_listening_settings))
        if self.voice is None or not hasattr(self.voice, "configure_listening"):
            self.listening_button.state(["disabled"])
        if self.voice is None or not hasattr(self.voice, "use_reference_voice"):
            self.reference_button.state(["disabled"])
        self.detail = StringVar(self.page, "Voice off")
        tk.Label(self.main, textvariable=self.detail, bg=p.BLACK, fg=p.HOLO, anchor="w", justify="left",
                 font=("Segoe UI", 9)).pack(fill="x")
        conversation = self._card(self.main, "Conversation")
        conversation.pack(fill="both", expand=True, pady=(6, 0))
        from app.gui_theme import DarkScrolledText
        self.transcript = DarkScrolledText(conversation, height=6, wrap="word", bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 10),
                                           padx=6, pady=4, bd=0, highlightthickness=0, state="disabled")
        self.transcript.pack(fill="both", expand=True, pady=(4, 0))
        self.transcript.tag_configure("you", foreground=p.ROYAL_SOFT, font=("Segoe UI Semibold", 9))
        self.transcript.tag_configure("hayat", foreground=p.GOLD, font=("Segoe UI Semibold", 9))
        self.transcript.tag_configure("muted", foreground=p.MUTED)
        self._build_side()
        page.bind("<Configure>", self._responsive, add="+")

    def _motion_changed(self, enabled: bool) -> None:
        # Live refresh follows the workspace motion setting. Update that same
        # setting when the voice view's menu or Space key changes it, so the
        # next refresh preserves the owner's choice.
        shared_motion = getattr(self.gui, '_set_workspace_motion', None)
        if callable(shared_motion):
            shared_motion(enabled)

    def _card(self, parent: tk.Misc, title: str) -> tk.Frame:
        p = self.palette
        card = tk.Frame(parent, bg=p.PANEL, padx=12, pady=8, highlightthickness=1, highlightbackground=p.LINE)
        tk.Label(card, text=title, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 11), anchor="w").pack(fill="x")
        return card

    def _audio_card(self, parent: tk.Misc, key: str, title: str, colour: str) -> dict:
        p = self.palette
        frame = tk.Frame(parent, bg=p.PANEL, padx=12, pady=8, highlightthickness=1, highlightbackground=p.LINE)
        head = tk.Frame(frame, bg=p.PANEL)
        head.pack(fill="x")
        from app.gui_icons import icon_image
        tk.Label(head, image=icon_image(head, "voice", colour, 26), bg=p.PANEL).pack(side="left", padx=(0, 8))
        words = tk.Frame(head, bg=p.PANEL)
        words.pack(side="left", fill="x", expand=True)
        tk.Label(words, text=title, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 11), anchor="w").pack(fill="x")
        state = StringVar(self.page, "Off")
        tk.Label(words, textvariable=state, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w").pack(fill="x")
        dot = tk.Canvas(head, width=12, height=12, bg=p.PANEL, highlightthickness=0)
        dot.pack(side="right", anchor="n", pady=4)
        wave = tk.Canvas(frame, height=70, bg=p.PANEL, highlightthickness=0)
        wave.pack(fill="x", pady=(6, 4))
        device = StringVar(self.page, "")
        picker = ttk.Combobox(frame, textvariable=device, state="readonly")
        picker.pack(fill="x")
        picker.bind("<<ComboboxSelected>>", lambda _event, which=key: self.choose_device(which))
        meter_row = tk.Frame(frame, bg=p.PANEL)
        meter_row.pack(fill="x", pady=(6, 0))
        level = StringVar(self.page, decibels(0))
        tk.Label(meter_row, textvariable=level, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8), width=7).pack(side="right")
        meter = tk.Canvas(meter_row, height=10, width=60, bg=p.PANEL_2, highlightthickness=0)
        meter.pack(side="left", fill="x", expand=True)
        tk.Label(frame, text="Input level" if key == "input" else "Output level", bg=p.PANEL, fg=colour,
                 font=("Segoe UI", 8), anchor="w").pack(fill="x")
        return {"frame": frame, "state": state, "dot": dot, "wave": wave, "device": device, "picker": picker,
                "meter": meter, "level": level, "colour": colour, "devices": {}}

    def _build_side(self) -> None:
        p = self.palette
        config = self._card(self.side, "Voice configuration")
        config.pack(fill="x", pady=(0, 8))
        self.config_vars = {}
        for key, title in (("tts", "Voice (text to speech)"), ("stt", "Speech to text"), ("mode", "Listening mode"),
                           ("hotkey", "Push-to-talk key")):
            tk.Label(config, text=title, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(6, 0))
            self.config_vars[key] = StringVar(self.page, "—")
            tk.Label(config, textvariable=self.config_vars[key], bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 10),
                     anchor="w", justify="left", wraplength=290).pack(fill="x")
        tk.Label(config, text="Voice speed (this session)", bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(8, 0))
        speed_row = tk.Frame(config, bg=p.PANEL)
        speed_row.pack(fill="x")
        tts = getattr(self.voice, "tts", None)
        self.speed = tk.DoubleVar(self.page, float(getattr(tts, "speed", 1.0) or 1.0))
        self.speed_label = StringVar(self.page, f"{self.speed.get():.1f}×")
        self.speed_scale = ttk.Scale(speed_row, from_=0.5, to=2.0, variable=self.speed, command=lambda _value: self.set_speed())
        self.speed_scale.pack(side="left", fill="x", expand=True)
        tk.Label(speed_row, textvariable=self.speed_label, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 9), width=5).pack(side="right")
        if tts is None or not hasattr(tts, "speed"):
            self.speed_scale.state(["disabled"])
        doing = self._card(self.side, "What HAYAT is doing")
        doing.pack(fill="x", pady=(0, 8))
        self.doing = tk.Frame(doing, bg=p.PANEL)
        self.doing.pack(fill="x", pady=(4, 0))
        samples = self._card(self.side, "Sample voice commands")
        samples.pack(fill="x")
        grid = tk.Frame(samples, bg=p.PANEL)
        grid.pack(fill="x", pady=(4, 0))
        for index, (title, phrase) in enumerate(SAMPLES):
            tile = tk.Frame(grid, bg=p.PANEL_2, padx=8, pady=6, cursor="hand2")
            tile.grid(row=index // 2, column=index % 2, sticky="nsew", padx=(0, 6) if index % 2 == 0 else 0, pady=(0, 6))
            grid.columnconfigure(index % 2, weight=1, uniform="sample")
            parts = (tk.Label(tile, text=title, bg=p.PANEL_2, fg=p.GOLD_SOFT, font=("Segoe UI Semibold", 9), anchor="w", cursor="hand2"),
                     tk.Label(tile, text=f"“{phrase}”", bg=p.PANEL_2, fg=p.WHITE, font=("Segoe UI", 8), anchor="w", justify="left",
                              wraplength=130, cursor="hand2"))
            for part in parts:
                part.pack(fill="x")
            for widget in (tile, *parts):
                widget.bind("<Button-1>", lambda _event, text=phrase: self.try_sample(text))

    def open_reference_voice(self) -> None:
        from app.gui_voice_reference import open_reference_voice
        open_reference_voice(self)

    def open_listening_settings(self) -> None:
        if self.voice is None or not hasattr(self.voice, "configure_listening"):
            return
        previous = getattr(self, "_listening_window", None)
        if previous is not None and previous.winfo_exists():
            previous.lift()
            return
        p = self.palette
        window = tk.Toplevel(self.page)
        self._listening_window = window
        window.title("HAYAT · Listening settings")
        window.configure(bg=p.PANEL, padx=18, pady=16)
        window.transient(self.page.winfo_toplevel())
        window.bind("<Escape>", lambda _e: window.destroy())
        tk.Label(window, text="Let HAYAT hear your whole thought", bg=p.PANEL, fg=p.WHITE,
                 font=("Segoe UI Semibold", 13)).pack(anchor="w")
        tk.Label(window, text="Longer pauses allow you to think between words. Lower thresholds help quiet speech; "
                 "higher thresholds reject more room noise. Changes apply to the next capture in this session.",
                 wraplength=410, justify="left", bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 10)).pack(fill="x", pady=(8, 10))
        vad = self.voice.config.get("vad", {})
        pause = StringVar(window, str(vad.get("silence_seconds", 0.8)))
        threshold = StringVar(window, str(vad.get("energy_threshold", 0.0035)))
        for title, variable in (("End-of-turn pause (0.3–2.5 seconds)", pause), ("Speech threshold (0.001–0.05)", threshold)):
            tk.Label(window, text=title, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 2))
            ttk.Entry(window, textvariable=variable).pack(fill="x")
        note = StringVar(window, "")
        tk.Label(window, textvariable=note, bg=p.PANEL, fg=p.GOLD_SOFT, wraplength=410, justify="left").pack(fill="x", pady=10)
        def apply() -> None:
            try:
                self.voice.configure_listening(silence_seconds=float(pause.get()), energy_threshold=float(threshold.get()))
            except ValueError as exc:
                note.set(str(exc))
                return
            note.set("Applied. Test the microphone, then try a full sentence with a natural pause.")
        ttk.Button(window, text="Apply", style="HayatAccent.TButton", command=apply).pack(side="right")
        ttk.Button(window, text="Test microphone", style="Hayat.TButton", command=self.gui._probe_microphone).pack(side="left")

    def _responsive(self, event: tk.Event) -> None:
        if event.widget is not self.page:
            return
        compact = event.width < 1100 or event.height < 700
        if compact != self._compact:
            self._compact = compact
            if compact:
                self.side.pack_forget()
                self.subtitle.pack_forget()
            else:
                self.side.pack(side="right", fill="y", padx=(10, 0), before=self.main)
                self.subtitle.pack(side="left", pady=(6, 0))
            self.scene.configure(height=170 if compact else 230)

    # ----------------------------------------------------------------- actions
    def try_sample(self, text: str) -> None:
        use = getattr(self.gui, "use_example", None)
        if callable(use):
            use(text)
            self.studio.set_status("Example put in the chat box. Or say it aloud while voice is on.", "voice")

    def set_speed(self) -> None:
        value = round(float(self.speed.get()) * 10) / 10
        self.speed_label.set(f"{value:.1f}×")
        tts = getattr(self.voice, "tts", None)
        if tts is not None and hasattr(tts, "speed"):
            tts.speed = value

    def choose_device(self, which: str) -> None:
        card = self.cards[which]
        index = card["devices"].get(card["device"].get())
        if self.voice is None or index is None:
            return
        try:
            (self.voice.set_input_device if which == "input" else self.voice.set_output_device)(index)
            self.studio.set_status(f"{'Microphone' if which == 'input' else 'Speaker'} set to {card['device'].get()}.", "voice")
        except Exception as exc:
            self.studio.set_status(f"Could not switch device: {exc}", "voice")

    def load_devices(self) -> None:
        """Fill the device pickers from what the audio system reports (only when the page opens)."""
        manager = getattr(self.voice, "devices", None)
        try:
            inventory = manager.list_devices() if manager is not None else None
        except Exception:
            inventory = None
        for key, field, default in (("input", "microphones", "default_input"), ("output", "speakers", "default_output")):
            card = self.cards[key]
            if not inventory:
                card["picker"].configure(values=["Audio devices unavailable"])
                card["device"].set("Audio devices unavailable")
                continue
            devices = {f"{device['name']} ({device.get('host_api', '')})".replace(" ()", ""): device["id"] for device in inventory[field]}
            card["devices"] = devices
            card["picker"].configure(values=list(devices))
            current = getattr(getattr(self.voice, "recorder", None), "device", None) if key == "input" else \
                getattr(getattr(self.voice, "tts", None), "output_device", None)
            wanted = current if isinstance(current, int) else inventory.get(default)
            card["device"].set(next((name for name, index in devices.items() if index == wanted), next(iter(devices), "No device")))

    # ----------------------------------------------------------------- live update
    def update(self, activity: dict, voice: dict) -> None:
        p = self.palette
        levels = self.voice.levels() if self.voice is not None else {}
        state = str(voice.get("state") or ("ready" if voice.get("enabled") else "stopped"))
        enabled = bool(voice.get("enabled"))
        for key, card in self.cards.items():
            level = max(0.0, min(1.0, float(levels.get(key, 0.0) or 0.0)))
            self.history[key].append(level)
            card["level"].set(decibels(level))
            self._draw_wave(card, self.history[key])
            meter = card["meter"]
            width = max(1, meter.winfo_width())
            meter.delete("all")
            meter.create_rectangle(0, 0, width * level, 10, fill=card["colour"], outline="")
            active = enabled and ((key == "input" and state in ("listening", "ready", "idle")) or (key == "output" and state == "speaking"))
            card["state"].set(("Listening…" if key == "input" else "Speaking…") if active and state in ("listening", "speaking")
                              else ("Ready" if enabled else "Off"))
            card["dot"].delete("all")
            card["dot"].create_oval(1, 1, 11, 11, fill="#4ADE80" if active else (p.GOLD if enabled else p.LINE), outline="")
        stage = STAGE_OF.get(state) if enabled else None
        if stage != self._stage:
            self._stage = stage
            self._draw_pipeline(stage)
        self.toggle.configure(text="Stop voice" if enabled else "Start voice")
        for mode, button in self.mode_buttons.items():
            button.configure(style="HayatAccent.TButton" if voice.get("mode") == mode else "Hayat.TButton")
        from app.gui_voice import voice_summary
        self.detail.set(f"{voice_summary(voice)} · {STATE_TEXT.get(state, state)}" + (f" · {voice['last_error']}" if voice.get("last_error") else ""))
        recognition = voice.get("last_recognition") or {}
        if recognition:
            score = recognition.get("confidence")
            observed = f"Last recognition: {recognition.get('language') or 'language unknown'}"
            if isinstance(score, (int, float)) and math.isfinite(score):
                observed += f" · recognition score {score:.0%}"
            observed += f" · {recognition.get('duration_ms', 0)} ms"
            self.detail.set(self.detail.get() + "\n" + observed)
        tts = getattr(self.voice, "tts", None)
        self.config_vars["tts"].set(f"{voice.get('tts_provider', 'unavailable')}" + (f" · {tts.voice_name}" if getattr(tts, "voice_name", None) else ""))
        stt = getattr(self.voice, "stt", None)
        self.config_vars["stt"].set(f"{voice.get('stt_provider', 'unavailable')}" + (f" · {stt.model_name}" if getattr(stt, "model_name", None) else ""))
        self.config_vars["mode"].set({"push_to_talk": "Push-to-talk", "continuous": "Hands-free", "wake_word": "Wake word"}.get(voice.get("mode"), "—"))
        self.config_vars["hotkey"].set(str(voice.get("push_to_talk_key") or "—"))
        self._paint_events()
        if time.monotonic() - self._conversation_checked > 2:
            self._conversation_checked = time.monotonic()
            self._paint_conversation()

    def _draw_wave(self, card: dict, history: collections.deque) -> None:
        canvas = card["wave"]
        canvas.delete("all")
        width, height = max(60, canvas.winfo_width()), int(canvas["height"])
        middle, step = height / 2, width / len(history)
        canvas.create_line(0, middle, width, middle, fill=self.palette.LINE)
        for index, level in enumerate(history):
            # Square-root scaling keeps quiet speech visible; the meter shows the true level.
            half = max(1.0, math.sqrt(level) * (middle - 3))
            x = index * step + step / 2
            canvas.create_line(x, middle - half, x, middle + half, fill=card["colour"], width=max(1, step - 1))

    def _draw_pipeline(self, active: int | None) -> None:
        canvas, p = self.pipeline, self.palette
        canvas.delete("all")
        width = max(300, canvas.winfo_width())
        slot = width / len(STAGES)
        canvas.create_line(slot / 2, 28, width - slot / 2, 28, fill=p.LINE, width=2)
        from app.gui_icons import icon_image
        for index, (key, title, detail) in enumerate(STAGES):
            x = slot * index + slot / 2
            on = active == index
            done = active is not None and index < active
            colour = p.GOLD if on else ("#4ADE80" if done else p.MUTED)
            canvas.create_oval(x - 18, 10, x + 18, 46, fill=p.PANEL_2, outline=colour, width=3 if on else 2)
            kind = {"listening": "voice", "transcribing": "file", "thinking": "brain", "speaking": "activity"}[key]
            image = icon_image(canvas, kind, colour, 18)
            canvas.create_image(x, 28, image=image)
            canvas.create_text(x, 58, text=title, fill=p.WHITE if on else p.MUTED, font=("Segoe UI Semibold", 9))
            canvas.create_text(x, 73, text=detail, fill=p.MUTED, font=("Segoe UI", 8))

    def _paint_events(self) -> None:
        events = list(self.events)[-7:]
        if events == self._events_shown:
            return
        self._events_shown = events
        p = self.palette
        for child in self.doing.winfo_children():
            child.destroy()
        if not events:
            tk.Label(self.doing, text="Nothing yet. Each voice step appears here with its time.", bg=p.PANEL, fg=p.MUTED,
                     font=("Segoe UI", 9), anchor="w", justify="left", wraplength=290).pack(fill="x")
            return
        for stamp, state in reversed(events):
            line = tk.Frame(self.doing, bg=p.PANEL)
            line.pack(fill="x", pady=1)
            tk.Label(line, text=stamp, bg=p.PANEL, fg=p.MUTED, font=("Cascadia Mono", 8)).pack(side="left")
            tk.Label(line, text=STATE_TEXT.get(state, state), bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 9), anchor="w").pack(side="left", padx=8)

    def _paint_conversation(self) -> None:
        session = getattr(self.voice, "session", None)
        try:
            history = session.memory.conversation_history(session.session_id, limit=14) if session is not None else []
        except Exception:
            history = []
        signature = [(item.get("timestamp"), item.get("role"), item.get("content")) for item in history]
        if signature == self._conversation_signature:
            return
        self._conversation_signature = signature
        box = self.transcript
        box.configure(state="normal")
        box.delete("1.0", "end")
        if not history:
            box.insert("end", "Nothing said in this voice session yet. Start voice and speak; your words and HAYAT's "
                              "replies appear here.", "muted")
        for item in history:
            role = "you" if item.get("role") == "user" else "hayat"
            stamp = str(item.get("timestamp") or "")[11:16]
            box.insert("end", ("You" if role == "you" else "HAYAT") + (f"  {stamp}" if stamp else "") + "\n", role)
            box.insert("end", str(item.get("content") or "")[:1200] + "\n\n")
        box.configure(state="disabled")
        box.see("end")
