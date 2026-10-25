"""Voice, local-model and public-source controls, with background work off Tk."""
from __future__ import annotations

import queue
import threading
from app.gui_resources import StringVar

import tkinter as tk
from tkinter import ttk

from app.gui_theme import DarkScrolledText
from app.gui_hologram import NeuralWorkspace
from app.gui_icons import apply_icons
from app.gui_windows import place_over



def plain_error(error: str) -> str:
    """A failure said the way the owner can act on it, not as a Windows error code."""
    text = str(error or "")
    if "10061" in text or "actively refused" in text or "Connection refused" in text or "ConnectError" in text:
        return ("HAYAT's model service isn't running, so no models can be listed or loaded. Start HAYAT normally (it starts the "
                "service) or run the Doctor, then press Refresh models.")
    if "timed out" in text.lower():
        return "The model service did not answer in time; it may be loading a model. Wait a moment and press Refresh models."
    return text


def _plain(value):
    """A status value said in plain words: Colibri returns {'detail': ...} dictionaries."""
    if isinstance(value, dict) and isinstance(value.get('detail'), str):
        return value['detail']
    from app.plain_output import describe

    return value if isinstance(value, str) else describe(value)

class VoiceFocus(NeuralWorkspace):
    VOICE_SIZE_LIMIT = 120

    def _digit(self, event):
        # This focused view has no numbered capability cards.
        return None

    def _step_key(self, direction):
        self._velocity, self._target = (0.0, 0.0), None
        self._idle_after = self._clock() + self.IDLE_DELAY
        self.angle += direction * .15
        self.redraw()
        return "break"

    def _geometry(self, w, h):
        return w/2, h*.43, min(w*.7, h*1.05), True

    def redraw(self):
        w, h = self._size()
        self._draw_backdrop(w, h)
        self.delete("scene")
        self._positions = {}
        cx, cy, radius, _ = self._geometry(w, h)
        self._draw_voice(cx, cy, radius)
        self.create_text(w/2, h-18, text="Drag / arrows: rotate • scroll: zoom • Enter: start / stop voice",
                         fill=self.palette.MUTED, font=("Segoe UI", 9), tags="scene")


class ControlStudio(tk.Toplevel):
    def __init__(self, gui):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        self.title("HAYAT · Voice, models & public research")
        self.transient(gui.root)
        self.configure(bg=gui.BLACK)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.bind("<Escape>", lambda event: (self.withdraw(), "break")[1])
        self.results = queue.Queue()
        self.busy = False
        self.model_rows = []
        self.current_page = "voice"
        self.page_status = {"voice": "Ready. Start voice when you want to speak.",
                            "models": "Refresh to inspect local model memory.",
                            "osint": "Ready. Enter a public source to inspect.",
                            "self": "Choose a check to read HAYAT's current state."}
        header = tk.Frame(self, bg=gui.BLACK, padx=16, pady=10)
        header.pack(fill="x")
        self.page_buttons = {}
        for key, title in (("voice", "Voice studio"), ("models", "Local models"), ("osint", "OSINT research"), ("self", "System & self")):
            button = ttk.Button(header, text=title, style="Hayat.TButton", command=lambda k=key: self.show(k))
            button.pack(side="left", padx=3)
            self.page_buttons[key] = button
        ttk.Button(header, text="Close", style="Hayat.TButton", command=self.withdraw).pack(side="right")
        self.status = StringVar(self, "Ready")
        self.status_area = tk.Frame(self, bg=gui.PANEL)
        self.status_area.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(self.status_area, mode="indeterminate",
                                        style="Hayat.Horizontal.TProgressbar")
        tk.Label(self.status_area, textvariable=self.status, bg=gui.PANEL, fg=gui.GOLD_SOFT, anchor="w",
                 wraplength=1200, justify="left", padx=16, pady=10, font=("Segoe UI", 9)).pack(side="bottom", fill="x")
        self.pages = {key: tk.Frame(self, bg=gui.BLACK, padx=16, pady=8) for key in self.page_buttons}
        self._build_voice()
        self._build_models()
        self._build_osint()
        self._build_self()
        self.bind("<Control-Tab>", lambda event: self._next_page(1))
        self.bind("<Control-Shift-Tab>", lambda event: self._next_page(-1))
        apply_icons(self, self.palette)

    def _next_page(self, direction):
        pages = list(self.pages)
        self.show(pages[(pages.index(self.current_page) + direction) % len(pages)])
        return "break"

    def label(self, parent, text, large=False):
        tk.Label(parent, text=text, bg=self.palette.BLACK, fg=self.palette.WHITE if large else self.palette.MUTED,
                 anchor="w", justify="left", wraplength=680,
                 font=("Segoe UI Semibold", 14) if large else ("Segoe UI", 9)).pack(fill="x", pady=(0, 8))

    def set_status(self, text, page=None):
        page = page or self.current_page
        self.page_status[page] = text
        if page == self.current_page:
            self.status.set(text)

    def _build_self(self):
        page = self.pages["self"]
        self.label(page, "What HAYAT can observe about itself", True)
        self.label(page, "Check current activity, computer readiness and measured improvement opportunities. Each result is a snapshot taken when you ask.")
        row = tk.Frame(page, bg=self.palette.BLACK)
        row.pack(fill="x", pady=8)
        for title, action in (("Refresh self", lambda: self.inspect_self("state")),
                              ("Computer readiness", lambda: self.inspect_self("computer")),
                              ("Improvement review", lambda: self.inspect_self("improve"))):
            ttk.Button(row, text=title, style="Hayat.TButton", command=action).pack(side="left", padx=3)
        self.self_result = DarkScrolledText(page, wrap="word", bg=self.palette.PANEL,
                              fg=self.palette.WHITE, font=("Segoe UI", 10), padx=12, pady=12, state="disabled")
        self.self_result.pack(fill="both", expand=True)

    def inspect_self(self, kind):
        def work():
            if kind == "improve":
                improvement = getattr(self.gui.runtime, "improvement", None)
                return improvement.report() if improvement else "Self-improvement is unavailable in this runtime."
            result = self.gui.runtime.registry.execute("get_agent_state" if kind == "state" else "computer_status", {})
            if not result.success:
                raise RuntimeError(result.error)
            if kind == "state":
                from app.self_view import describe_self
                return describe_self(result.output)
            data = result.output
            if not data.get("available"):
                return "Computer control unavailable\n\n" + str(data.get("reason", "No readiness evidence"))
            return (f"Desktop backend responded\n\nPrimary display: {data['screen']['width']} × {data['screen']['height']}\n"
                    f"Pointer: {data['pointer']['x']}, {data['pointer']['y']}\nFailsafe: {'on' if data['failsafe'] else 'off'}\n\n"
                    f"{data['verification']}\n\nNo keyboard or mouse input was sent.\n{data['scope']}")
        def complete(value):
            self.self_result.configure(state="normal")
            self.self_result.delete("1.0", "end")
            from app.plain_output import describe

            self.self_result.insert("end", value if isinstance(value, str) else describe(value))
            self.self_result.configure(state="disabled")
            self.set_status("Check complete · this is an observed snapshot.", "self")
        self.run_job("Reading " + kind, work, complete)

    def _build_voice(self):
        from app.gui_voice_studio import VoicePage
        self.voice_page = VoicePage(self, self.pages["voice"])
        # Names other code and tests use for the voice controls.
        self.voice_scene = self.voice_page.scene
        self.voice_toggle = self.voice_page.toggle
        self.voice_mode_buttons = self.voice_page.mode_buttons
        self.voice_detail = self.voice_page.detail

    def voice_action(self, action):
        self.set_status("Updating voice…", "voice")
        self.gui._request_voice(action, lambda value: self.set_status(value, "voice")
                                if self.winfo_exists() else None)

    def _build_models(self):
        page = self.pages["models"]
        self.label(page, "Local model control", True)
        self.label(page, "Installed means available on disk. Loaded means currently in memory. Loading warms a model for five minutes; HAYAT still chooses the model for each task.")
        footer = tk.Frame(page, bg=self.palette.BLACK)
        footer.pack(side="bottom", fill="x")
        table = tk.Frame(page, bg=self.palette.BLACK)
        table.pack(fill="both", expand=True)
        self.models = ttk.Treeview(table, columns=("state", "size", "vram"), show="tree headings", selectmode="browse")
        self.models.heading("#0", text="Model")
        self.models.column("#0", width=340, minwidth=180)
        for key, title in (("state", "State"), ("size", "Disk GiB"), ("vram", "VRAM GiB")):
            self.models.heading(key, text=title)
            self.models.column(key, width=100, minwidth=75, stretch=False)
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.models.yview)
        scrollbar.pack(side="right", fill="y")
        self.models.configure(yscrollcommand=scrollbar.set)
        self.models.pack(fill="both", expand=True)
        self.model_detail = StringVar(self, "Select a row to see its full model name.")
        tk.Label(footer, textvariable=self.model_detail, bg=self.palette.BLACK, fg=self.palette.HOLO,
                 wraplength=680, justify="left", anchor="w", font=("Segoe UI", 9)).pack(fill="x", pady=(8, 0))
        self.models.bind("<<TreeviewSelect>>", lambda event: self.model_detail.set(
            self.models.item(self.models.selection()[0], "text") if self.models.selection() else "Select a model."))
        row = tk.Frame(footer, bg=self.palette.BLACK)
        row.pack(fill="x", pady=12)
        for title, action in (("Refresh models", self.refresh_models), ("Load selected", lambda: self.change_model(True)),
                              ("Unload selected", lambda: self.change_model(False))):
            ttk.Button(row, text=title, style="Hayat.TButton", command=action).pack(side="left", padx=3)
        self.label(footer, "Load and unload run only when you click them.")
        from app.models.colibri_service import ColibriService
        if not hasattr(self.gui.runtime, 'colibri_service'):
            self.gui.runtime.colibri_service = ColibriService()
        self.colibri = self.gui.runtime.colibri_service
        colibri = tk.LabelFrame(footer, text='Colibri · optional local comparison', bg=self.palette.BLACK,
                               fg=self.palette.WHITE, padx=8, pady=6)
        colibri.pack(fill='x', pady=(0, 6))
        self.colibri_status = StringVar(self, 'Starts only on request. Default HAYAT models stay configured.')
        tk.Label(colibri, textvariable=self.colibri_status, bg=self.palette.BLACK, fg=self.palette.GOLD_SOFT,
                 wraplength=660, justify='left', anchor='w').pack(fill='x')
        row = tk.Frame(colibri, bg=self.palette.BLACK)
        row.pack(fill='x', pady=4)
        self.colibri_backend = StringVar(self, 'cpu')
        ttk.Combobox(row, textvariable=self.colibri_backend, values=('cpu', 'vulkan'), state='readonly', width=8).pack(side='left')
        for title, action in (('Start Colibri', self.start_colibri), ('Check status', self.check_colibri), ('Stop Colibri', self.stop_colibri)):
            ttk.Button(row, text=title, command=action).pack(side='left', padx=4)
        self.colibri_prompt = ttk.Entry(colibri)
        self.colibri_prompt.insert(0, 'Explain what a tool citation proves in two sentences.')
        self.colibri_prompt.pack(fill='x', pady=4)
        ttk.Button(colibri, text='Run test prompt', command=self.test_colibri).pack(anchor='w')

    def _colibri_job(self, title, work):
        def guarded():
            ticket = self.gui.runtime.registry.interlock.ticket()
            self.gui.runtime.registry.interlock.check(ticket)
            return work()
        self.run_job(title, guarded, lambda value: self.colibri_status.set(_plain(value)))

    def start_colibri(self):
        backend = self.colibri_backend.get()
        self._colibri_job('Starting Colibri', lambda: self.colibri.start(backend))

    def check_colibri(self):
        self.run_job('Checking Colibri', self.colibri.status, lambda value: self.colibri_status.set(_plain(value)))

    def stop_colibri(self):
        self.run_job('Stopping owned Colibri', self.colibri.stop, lambda value: self.colibri_status.set(_plain(value)))

    def test_colibri(self):
        prompt = self.colibri_prompt.get()
        def complete(value):
            self.colibri_status.set('Colibri returned a real local response; shown in chat.')
            self.gui._append('assistant', 'Colibri test\n' + str(getattr(value, 'content', value)))
        self.run_job('Testing Colibri', lambda: (self.gui.runtime.registry.interlock.ticket(), self.colibri.test(prompt))[1], complete)

    def refresh_models(self):
        from app.models.desktop import DesktopModels
        self.run_job("Checking installed and loaded models", DesktopModels(self.gui.runtime).inventory, self.paint_models)

    def paint_models(self, rows):
        selected = self.models.selection()
        name = self.models.item(selected[0], "text") if selected else ""
        self.model_rows = rows
        self.models.delete(*self.models.get_children())
        for i, row in enumerate(rows):
            iid = self.models.insert("", "end", iid=str(i), text=row["name"], values=(
                "Loaded" if row["loaded"] else "Installed", f"{row['size']/1024**3:.2f}", f"{row['vram']/1024**3:.2f}"))
            if row["name"] == name:
                self.models.selection_set(iid)
        self.set_status(f"{len(rows)} installed · {sum(row['loaded'] for row in rows)} loaded · refreshed now", "models")
        from app.gui_empty import show_empty
        show_empty(self.models, "No models installed in HAYAT's folder yet. Run setup (python -m scripts.setup) to download the brain.",
                   self.palette)

    def change_model(self, load):
        from app.models.desktop import DesktopModels
        selected = self.models.selection()
        if not selected:
            self.status.set("Select a model first.")
            return
        name = self.models.item(selected[0], "text")
        self.run_job(("Loading " if load else "Unloading ") + name,
                     lambda: DesktopModels(self.gui.runtime).change_residency(name, load), self.paint_models)

    def _build_osint(self):
        page = self.pages["osint"]
        self.label(page, "Understand a public source", True)
        self.label(page, "Enter a public page URL to inspect its article and provenance. For GitHub, use a repository URL or owner/repo; for papers, use the paper URL.")
        self.source_url = StringVar(self)
        self.source_entry = ttk.Entry(page, textvariable=self.source_url)
        self.source_entry.pack(fill="x", pady=6)
        self.source_entry.bind("<Return>", lambda event: (self.inspect_source(), "break")[1])
        row = tk.Frame(page, bg=self.palette.BLACK)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="Inspect source", style="HayatAccent.TButton", command=self.inspect_source).pack(side="left", padx=3)
        ttk.Button(row, text="GitHub repo", style="Hayat.TButton", command=lambda: self.inspect_source("github_research")).pack(side="left", padx=3)
        ttk.Button(row, text="Research paper", style="Hayat.TButton", command=lambda: self.inspect_source("paper_research")).pack(side="left", padx=3)
        row = tk.Frame(page, bg=self.palette.BLACK)
        row.pack(fill="x", pady=(0, 8))
        ttk.Button(row, text="Research a topic", style="Hayat.TButton", command=self.gui._research).pack(side="left", padx=3)
        ttk.Button(row, text="Saved research", style="Hayat.TButton",
                   command=lambda: (self.withdraw(), self.gui.show_view("research"))).pack(side="left", padx=3)
        self.source_result = DarkScrolledText(page, wrap="word", bg=self.palette.PANEL,
                            fg=self.palette.WHITE, font=("Segoe UI", 10), padx=12, pady=12, state="disabled")
        footer = tk.Frame(page, bg=self.palette.BLACK)
        footer.pack(side="bottom", fill="x", pady=(8, 0))
        self.source_result.pack(fill="both", expand=True)
        # A blank pane gave no hint of what Inspect source produces.
        self.source_result.configure(state="normal")
        self.source_result.insert("1.0", "Paste a public page, a GitHub repository (owner/repo) or a paper's URL above, then "
                                  "choose Inspect source, GitHub repo or Research paper.\n\nThe article's text, who "
                                  "published it, when, and where each part came from appear here. Nothing is posted or "
                                  "signed in to.")
        self.source_result.configure(state="disabled")
        from app.research.osint import toolkit_status
        status = toolkit_status()
        self.label(footer, f"Trafilatura {status['version']} · public pages, GitHub and papers · sources retain their provenance")

    def inspect_source(self, tool="public_source_profile"):
        url = self.source_url.get().strip()
        if not url:
            self.status.set("Enter a public URL first.")
            return
        def work():
            argument = "repository" if tool == "github_research" else "url"
            result = self.gui.runtime.registry.execute(tool, {argument: url})
            if not result.success:
                raise RuntimeError(result.error)
            return result.output
        self.run_job("Reading public source", work, self.paint_source)

    def paint_source(self, value):
        if isinstance(value, dict) and "linked_domains" in value:
            from app.research.osint import format_profile
            text = format_profile(value)
        else:
            from app.plain_output import describe
            text = describe(value) if isinstance(value, dict) else str(value)
        self.source_result.configure(state="normal")
        self.source_result.delete("1.0", "end")
        self.source_result.insert("end", text)
        self.source_result.configure(state="disabled")
        self.set_status("Source profile ready. Review its provenance before relying on its claims.", "osint")

    def run_job(self, title, work, complete):
        if self.busy:
            self.status.set("Wait for the current operation to finish.")
            return
        self.busy = True
        self.progress.pack(fill="x", padx=16, pady=(6, 0))
        self.progress.start(15)
        page = self.current_page
        self.set_status(title + "…", page)
        def run():
            try:
                self.results.put((page, complete, work(), None))
            except Exception as exc:
                self.gui._record_failure(title, exc)
                self.results.put((page, complete, None, str(exc)))
        threading.Thread(target=run, daemon=True, name="hayat-control-studio").start()

    def update_live(self, activity, voice, camera):
        while not self.results.empty():
            page, complete, value, error = self.results.get_nowait()
            self.busy = False
            self.progress.stop()
            self.progress.pack_forget()
            self.set_status(plain_error(error) if error else "Done", page)
            if error and page == "models":
                from app.gui_empty import show_empty
                self.models.delete(*self.models.get_children())
                show_empty(self.models, plain_error(error), self.palette)
            if not error:
                try:
                    complete(value)
                except Exception as exc:
                    self.gui._record_failure("Display studio result", exc)
                    self.set_status(f"Could not display result: {exc}", page)
        if self.winfo_viewable() and self.current_page == "voice":
            self.voice_scene.set_status(activity, voice, camera)
            self.voice_scene.set_motion(self.gui.depth_nav.motion) if self.voice_scene.motion != self.gui.depth_nav.motion else None
            self.voice_page.update(activity, voice)

    def show(self, key):
        opening = self.state() == "withdrawn"
        self.current_page = key
        self.status.set(self.page_status[key])
        for name, page in self.pages.items():
            page.pack_forget()
            self.page_buttons[name].configure(style="HayatAccent.TButton" if name == key else "Hayat.TButton")
        self.pages[key].pack(fill="both", expand=True)
        if opening:
            place_over(self, self.gui.root, (1300, 860))
        if key == "voice":
            self.voice_page.load_devices()
        self.deiconify()
        self.lift()
        if key == "osint":
            self.source_entry.focus_set()
        if key == "models" and not self.busy:
            self.refresh_models()
