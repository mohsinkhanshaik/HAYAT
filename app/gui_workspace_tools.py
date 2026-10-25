"""Responsive screen reading, person detection and Replica tool workspace."""
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from app.gui_resources import StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.replica import STAGES, CHECKERS


class WorkspaceTools(tk.Toplevel):
    def __init__(self, gui, page="screen"):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.p, self.registry = gui, type(gui), gui.runtime.registry
        self.results, self.closed, self.busy = queue.Queue(), False, False
        self._entries, self._browse_buttons, self._action_buttons = {}, {}, []
        self._screen_digest = None
        self._screen_generation = 0
        self._people_preview = None
        self._people_result = None
        self.title("HAYAT - Screen, computer & app studio")
        self.configure(bg=self.p.BLACK)
        self.minsize(780, 560)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", self._destroyed, add="+")
        self.bind("<Escape>", lambda _event: self.close())
        header = tk.Frame(self, bg=self.p.BLACK)
        header.pack(fill="x", padx=12, pady=8)
        self.status = tk.Label(header, text="Ready", anchor="w", bg=self.p.BLACK, fg=self.p.MUTED)
        self.status.pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(header, mode="indeterminate", length=140, style="Hayat.Horizontal.TProgressbar")
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._build_screen()
        self._build_people()
        self._build_replica()
        self.tabs.select({"screen": 0, "people": 1, "replica": 2}.get(page, 0))
        place_over(self, gui.root, (1120, 780))
        self.deiconify()
        self.timer = self.after(200, self.tick)

    def frame(self, title):
        frame = tk.Frame(self.tabs, bg=self.p.BLACK)
        self.tabs.add(frame, text=title)
        return frame

    def text(self, parent, height=12, *, code=False):
        # Results are plain sentences now; only the Raw result tab is code. Unset, Tk used its Courier
        # default and every result read like a typewriter printout.
        font = ("Consolas", 10) if code else ("Segoe UI", 10)
        box = DarkScrolledText(parent, height=height, wrap="word", bg=self.p.PANEL, fg=self.p.WHITE, relief="flat",
                               padx=10, pady=8, font=font)
        box.pack(fill="both", expand=True, pady=6)
        return box

    @staticmethod
    def show(box, text):
        if box.get("1.0", "end-1c") == text:
            return
        position = box.yview()
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", text)
        box.configure(state="disabled")
        box.yview_moveto(position[0] if position else 0)

    def button(self, parent, title, action, *, allow_busy=False):
        button = ttk.Button(parent, text=title, command=action)
        button.pack(side="left", padx=(0, 6), pady=4)
        if not allow_busy:
            self._action_buttons.append(button)
        return button

    def note(self, frame, text):
        label = tk.Label(frame, text=text, bg=self.p.BLACK, fg=self.p.MUTED, wraplength=700, justify="left", anchor="w")
        label.pack(fill="x", pady=8)
        label.bind("<Configure>", lambda event: label.configure(wraplength=max(100, event.width - 8)))

    def field(self, parent, title, value="", *, browse=None):
        row = tk.Frame(parent, bg=self.p.BLACK)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=title, width=21, anchor="w", bg=self.p.BLACK, fg=self.p.MUTED).pack(side="left")
        variable = StringVar(self, value)
        entry = ttk.Entry(row, textvariable=variable)
        entry.pack(side="left", fill="x", expand=True)
        self._entries[title] = entry
        if browse:
            self._browse_buttons[title] = self.button(row, "Browse...", browse)
        return variable

    def bar(self, parent):
        bar = tk.Frame(parent, bg=self.p.BLACK)
        bar.pack(fill="x")
        return bar

    def choice(self, parent, values):
        variable = StringVar(self, next(iter(values)))
        ttk.Combobox(parent, textvariable=variable, values=list(values), state="readonly", width=16).pack(side="left", padx=(0, 8))
        return variable

    def _build_screen(self):
        frame = self.frame("Live screen & computer")
        self.note(frame, "Read a window with local accessibility/OCR. Live sessions keep only the latest text. Stop clears it. Reading sends no input.")
        self.screen_window = self.field(frame, "Window title (optional)")
        bar = self.bar(frame)
        self.method = self.choice(bar, ("auto", "accessibility", "ocr"))
        self.button(bar, "Read once", lambda: self.run("screen_read", self.screen_args(), "screen"))
        self.button(bar, "Start live (10 min)", lambda: self.run("screen_read_start", self.screen_args(), "screen"))
        self.button(bar, "Stop", self.stop_screen, allow_busy=True)
        self.button(bar, "Computer readiness", lambda: self.run("computer_status", {}, "screen"))
        self.screen_state = tk.Label(frame, text="Stopped - no read requested", bg=self.p.BLACK, fg=self.p.GOLD, anchor="w")
        self.screen_state.pack(fill="x", pady=6)
        self.screen_text = self.text(frame)
        self.show(self.screen_text, "Choose a window, then Read once or Start live. Screen text is untrusted content.")

    def screen_args(self):
        return {"window": self.screen_window.get().strip(), "method": self.method.get()}

    def stop_screen(self):
        self._screen_generation += 1
        reader = getattr(self.registry, "screen_reader", None)
        if reader:
            reader.stop(timeout=0)
        self._screen_digest = None
        self.show(self.screen_text, "Stopped. Latest text cleared.")

    def _build_people(self):
        frame = self.frame("Person detection")
        self.note(frame, "Local person boxes and scores. No identities or demographic inference. Small, dark or occluded people can be missed.")
        self.image_path = self.field(frame, "Image path")
        bar = self.bar(frame)
        self.button(bar, "Choose image...", self.choose_image)
        self.button(bar, "Detect people", lambda: self.detect_people())
        self.button(bar, "Read camera once", lambda: self.detect_people(camera=True))
        self.button(bar, "Detector readiness", lambda: self.run("people_status", {}, "people"))
        self.people_confidence = StringVar(self, '0.50')
        ttk.Label(bar, text='Threshold').pack(side='left', padx=(6, 4))
        ttk.Entry(bar, textvariable=self.people_confidence, width=5).pack(side='left')
        known = tk.Frame(frame, bg=self.p.BLACK)
        known.pack(fill='x', pady=(3, 0))
        self.known_person_name, self.known_person_org = StringVar(self), StringVar(self)
        for title, variable in (('Known name', self.known_person_name), ('Organization', self.known_person_org)):
            ttk.Label(known, text=title).pack(side='left', padx=(0, 5))
            ttk.Entry(known, textvariable=variable, width=18).pack(side='left', fill='x', expand=True, padx=(0, 8))
        actions = self.bar(frame)
        self.button(actions, 'Research known name', self.research_known_person)
        self.button(actions, 'Image source checks', self.image_source_checks)
        self.button(actions, 'Enrolled people (local)', self.gui._open_enrolled_faces)
        self.button(actions, 'Image tools', lambda: self.gui._open_local_image_tools(path=self.image_path.get().strip()))
        body = tk.Frame(frame, bg=self.p.BLACK)
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1, minsize=70)
        body.rowconfigure(1, weight=1, minsize=100)
        self.people_canvas = tk.Canvas(body, bg=self.p.PANEL, height=160, highlightthickness=0)
        self.people_canvas.grid(row=0, column=0, sticky='nsew', pady=6)
        self.people_canvas.bind('<Configure>', lambda _event: self._draw_people_preview())
        results = ttk.Notebook(body, height=150)
        results.grid(row=1, column=0, sticky='nsew')
        overview, raw = tk.Frame(results, bg=self.p.BLACK), tk.Frame(results, bg=self.p.BLACK)
        results.add(overview, text='Detection explanation')
        results.add(raw, text='Raw result')
        self.people_text = self.text(overview, 7)
        self.people_raw = self.text(raw, 7, code=True)
        self.show(self.people_text, "Choose an image or explicitly read your camera. Detection runs on this machine.")

    def detect_people(self, *, camera=False):
        import math
        try:
            threshold = float(self.people_confidence.get())
            if not math.isfinite(threshold) or not .05 <= threshold <= .95:
                raise ValueError
        except ValueError:
            self.status.configure(text='Choose a detector threshold between 0.05 and 0.95.')
            return
        self.run('camera_people' if camera else 'detect_people',
                 {'confidence': threshold, **({} if camera else {'path': self.image_path.get().strip()})}, 'people')

    def _draw_people_preview(self):
        self.people_canvas.delete('all')
        if self._people_preview is None or not self._people_result:
            return
        from PIL import ImageTk
        preview = self._people_preview.copy()
        width, height = self.people_canvas.winfo_width(), self.people_canvas.winfo_height()
        preview.thumbnail((max(1, width), max(1, height)))
        self._photo = ImageTk.PhotoImage(preview, master=self)
        left, top = (width - preview.width) / 2, (height - preview.height) / 2
        self.people_canvas.create_image(left, top, image=self._photo, anchor='nw')
        scale_x = preview.width / self._people_result['width']
        scale_y = preview.height / self._people_result['height']
        for index, person in enumerate(self._people_result.get('people', []), 1):
            box = [left + person['box'][0] * scale_x, top + person['box'][1] * scale_y,
                   left + person['box'][2] * scale_x, top + person['box'][3] * scale_y]
            self.people_canvas.create_rectangle(*box, outline=self.p.GOLD, width=2)
            self.people_canvas.create_text(box[0] + 4, box[1] + 4,
                                           text=f"#{index} · {person['score'] * 100:.2f}% person",
                                           fill=self.p.GOLD, anchor='nw')

    def research_known_person(self):
        name = self.known_person_name.get().strip()
        if not name:
            self.status.configure(text='Enter a known name. Detection boxes cannot identify an unknown face.')
            return
        self.gui._open_searches()
        research = self.gui._research_studio
        research.open_person_studio()
        panel = research._person_studio
        panel.query.delete(0, 'end')
        panel.query.insert(0, name)
        panel.context.set(self.known_person_org.get().strip())
        self.status.configure(text='Known-name research opened. Check the supplied name/context, then press Research there.')

    def image_source_checks(self):
        self.gui._research_image()
        panel = self.gui._research_studio._image_research
        path = self.image_path.get().strip()
        if path:
            panel.read_image(path)
        self.status.configure(text='Image-source analysis opened. No face identification or automatic web search is performed.')

    def choose_image(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.webp")])
        if path:
            self.image_path.set(path)

    def _build_replica(self):
        outer = self.frame("Replica app studio")
        from app.gui_panels import ScrollableColumn
        self.replica_scroll = ScrollableColumn(outer, self.p, width=1000)
        self.replica_scroll.pack(fill="both", expand=True)
        frame = self.replica_scroll.body
        self.note(frame, "11 Replica workflows using HAYAT's model and six offline checkers. Files are backed up. Generated code is not executed or automatically deployed.")
        self.workspace = self.field(frame, "Project directory", "data/work/replica-project", browse=self.choose_workspace)
        self.reference = self.field(frame, "Public reference URL")
        bar = self.bar(frame)
        self.stage = self.choice(bar, STAGES)
        self.button(bar, "Prepare workflow", lambda: self.run("replica_prepare", self.replica_args(), "replica"))
        self.button(bar, "Generate files", self.generate)
        self.button(bar, "Catalog", lambda: self.run("replica_catalog", {}, "replica"))
        self.note(frame, "Workflow goal (required to generate): describe the app, behavior and constraints.")
        self.goal = self.text(frame, 3)
        bar = self.bar(frame)
        self.checker = self.choice(bar, CHECKERS)
        self.diff_mode = self.choice(bar, ("layout", "pixel"))
        self.diff_widget = bar.winfo_children()[-1]
        self.button(bar, "Run checker", self.check)
        self.check_path = self.field(frame, "Input file (relative)", "replica/features.csv", browse=lambda: self.choose_checker_file(self.check_path))
        self.other = self.field(frame, "Second image (diff)", browse=lambda: self.choose_checker_file(self.other))
        self.avoid = self.field(frame, "Original brand (sweep)")
        self.checker_help = tk.Label(frame, bg=self.p.BLACK, fg=self.p.MUTED, anchor="w", justify="left", wraplength=700)
        self.checker_help.pack(fill="x", pady=6)
        self.checker_help.bind("<Configure>", lambda event: self.checker_help.configure(wraplength=max(100, event.width - 8)))
        self._checker_paths = {}
        self._previous_checker = None
        self.checker.trace_add("write", lambda *_: self._checker_changed())
        self._checker_changed()
        self.replica_text = self.text(frame, 10)
        self.show(self.replica_text, "Prepare a stage to inspect instructions and prerequisites. A declared feature matrix alone is not proof that the app works.")
        self.replica_scroll.adopt(frame)

    def replica_args(self):
        return {"stage": self.stage.get(), "workspace": self.workspace.get().strip()}

    def choose_workspace(self):
        path = filedialog.askdirectory(parent=self, title="Choose a Replica project directory")
        if path:
            self.workspace.set(path)

    def choose_checker_file(self, variable):
        root = Path(self.workspace.get().strip()).resolve()
        path = filedialog.askopenfilename(parent=self, title="Choose an input inside the project", initialdir=str(root) if root.is_dir() else None)
        if path:
            try:
                relative = Path(path).resolve().relative_to(root)
            except ValueError:
                self.status.configure(text="Choose an input inside the project directory.")
                return
            variable.set(relative.as_posix())

    def _checker_changed(self):
        checker = self.checker.get()
        if self._previous_checker:
            self._checker_paths[self._previous_checker] = self.check_path.get()
        defaults = {"parity": "replica/features.csv", "contrast": "replica/design/tokens.json",
                    "reviews": "replica/reviews.csv", "listing": "replica/launch/listing.json"}
        self.check_path.set(self._checker_paths.get(checker, defaults.get(checker, "")))
        self._previous_checker = checker
        self._entries["Input file (relative)"].configure(state="disabled" if checker == "brand" else "normal")
        self._entries["Second image (diff)"].configure(state="normal" if checker == "image_diff" else "disabled")
        self._sync_picker_buttons()
        self._entries["Original brand (sweep)"].configure(state="normal" if checker in {"brand", "listing"} else "disabled")
        self.diff_widget.configure(state="readonly" if checker == "image_diff" else "disabled")
        descriptions = {"parity": "Checks the declared feature matrix, including required features; actual behavior needs separate testing.",
                        "contrast": "Checks color contrast in the design tokens JSON.",
                        "reviews": "Groups reviews from the reviews CSV; does not fetch or invent reviews.",
                        "listing": "Checks the launch listing JSON; the original brand is optional.",
                        "brand": "Scans project files for the original brand. Enter the brand name; no input file is needed.",
                        "image_diff": "Choose two project images. Layout compares structure; pixel also compares color."}
        self.checker_help.configure(text=descriptions[checker])

    def _sync_picker_buttons(self):
        for title, allowed in (("Input file (relative)", self.checker.get() != "brand"),
                               ("Second image (diff)", self.checker.get() == "image_diff")):
            self._browse_buttons[title].configure(state="normal" if allowed and not self.busy else "disabled")

    def generate(self):
        goal = self.goal.get("1.0", "end").strip()
        if not goal or len(goal) > 8000:
            self.status.configure(text="Enter a workflow goal between 1 and 8000 characters.")
            self.goal.focus_set()
            return
        self.run("replica_run", {**self.replica_args(), "goal": goal, "reference_url": self.reference.get().strip()}, "replica")

    def check(self):
        self.run("replica_check", {"checker": self.checker.get(), "workspace": self.workspace.get().strip(), "path": "" if self.checker.get() == "brand" else self.check_path.get().strip(),
                                 "other": self.other.get().strip(), "avoid": self.avoid.get().strip(), "image_mode": self.diff_mode.get()}, "replica")

    def run(self, tool, arguments, page):
        if self.busy:
            self.status.configure(text="The current action is still running.")
            return
        self._set_busy(True)
        self.status.configure(text="Working: " + tool)
        generation = self._screen_generation
        def worker():
            try:
                result = self.registry.execute(tool, arguments)
                preview = None
                if result.success and page == "people" and result.output.get("path") and "people" in result.output:
                    from PIL import Image, ImageOps
                    with Image.open(result.output["path"]) as image:
                        preview = ImageOps.exif_transpose(image).convert("RGB")
                        preview.thumbnail((1000, 350))
                self.results.put((page, result, preview, generation))
            except Exception as exc:
                self.results.put((page, str(exc), None, generation))
        threading.Thread(target=worker, daemon=True, name="hayat-workspace-tools").start()

    def tick(self):
        if self.closed:
            return
        while not self.results.empty():
            page, result, preview, generation = self.results.get_nowait()
            self._set_busy(False)
            if page == "screen" and generation != self._screen_generation:
                self.status.configure(text="Screen read stopped; result discarded.")
                continue
            success = not isinstance(result, str) and result.success
            value = result.output if success else None
            text = json.dumps(value, indent=2, ensure_ascii=False) if success else str(result if isinstance(result, str) else result.error)
            if page == 'people':
                self.show(self.people_raw, text)
                if success:
                    from app.perception.people_report import people_report
                    text = people_report(value)
            elif success:
                from app.plain_output import describe

                text = describe(value)
            if success and page == "screen" and "text" in value:
                text = value.get("window", "") + "\n\n" + value["text"]
            self.show(getattr(self, page + "_text"), text)
            self.status.configure(text="Action complete" if success else "Action failed; see details")
            if success and page == "replica" and value.get("passed") is False:
                self.status.configure(text="Checker needs attention; see details")
            if page == "people":
                self._people_preview, self._people_result = preview, value
                self._draw_people_preview()
        reader = getattr(self.registry, "screen_reader", None)
        if reader:
            state = reader.status()
            self.screen_state.configure(text=f"{'Reading' if state['running'] else 'Stopped'} - {state['samples']} reads - {state['changes']} changes" + (" - " + state["error"] if state["error"] else ""))
            latest = state.get("latest")
            if latest and latest["content_sha256"] != self._screen_digest and state["running"]:
                self._screen_digest = latest["content_sha256"]
                self.show(self.screen_text, latest.get("window", "") + "\n" + latest["captured_at"] + "\n\n" + latest["text"])
            elif not latest and (self._screen_digest is not None or "blocked" in state["error"]):
                self._screen_digest = None
                self.show(self.screen_text, state["error"] or "Stopped. Latest text cleared.")
        self.timer = self.after(200, self.tick)

    def _set_busy(self, busy):
        self.busy = busy
        for button in self._action_buttons:
            button.configure(state="disabled" if busy else "normal")
        self._sync_picker_buttons()
        if busy:
            self.progress.pack(side="right")
            self.progress.start(15)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def close(self):
        if self.closed:
            return
        self.stop_screen()
        self._cleanup()
        self.destroy()

    def _destroyed(self, event):
        if event.widget is self:
            self._cleanup()

    def _cleanup(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.progress.stop()
        except tk.TclError:
            pass  # A direct destroy has already destroyed the child widgets.
        reader = getattr(self.registry, "screen_reader", None)
        if reader:
            reader.stop(timeout=0)
        if getattr(self, "timer", None):
            self.after_cancel(self.timer)
