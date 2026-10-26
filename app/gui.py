"""Portable desktop command surface for the HAYAT runtime."""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import tempfile
import threading
import time
from app.gui_resources import DoubleVar, StringVar

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any

from app.gui_theme import DarkScrolledText
from app.windows.com_apartment import configure_gui_sta
from app.logging import log_swallowed
from app.gui_fonts import DISPLAY

_log = logging.getLogger(__name__)

# Direct imports of app.gui (tests and embedded launchers) need the same
# ordering guarantee as main.py.
configure_gui_sta()

from app.activity import ACTIVITY
from app.gui_panels import (
    ButtonRow, Gauge, InfoCard, LiveConsole, ModelChips, ScrollableColumn, StatusPill,
    ToolTile, Workspace,
    human_bytes, human_duration, section,
)
from app.identity import IDENTITY
from app.gui_theme import (Palette, style_controls, style_hayat_buttons, apply_theme,
                           apply_text_scale, appearance_dialog, TEXT_SCALES)
from app.gui_operator import BrainOperatorMixin
from app.gui_remote import RemoteAccessMixin
from app.gui_depth import DepthNavigator, DepthSurface
from app.gui_hologram import NeuralWorkspace, ResourceCharts
from app.gui_icons import CameraEye, apply_icons
from app.output import get_output


#: A window claimed before the runtime was built, waiting to be adopted.
#: See reserve_window(): drag-and-drop only survives if the window is created
#: before pywinauto changes the COM apartment out from under it.
_RESERVED_ROOT: tuple[tk.Tk, bool] | None = None

#: Roots a HayatGUI already owns, by id. Only unclaimed windows are adopted.
_CLAIMED_ROOTS: set[int] = set()


def format_activity_countdown(
    summary: dict[str, Any], working_since: float | None,
    *, now: float | None = None, fallback_estimate: float = 30.0,
) -> str:
    """Human-readable live phase, elapsed time, and evidence-based ETA."""
    moment = time.monotonic() if now is None else float(now)
    running = list(summary.get("running") or [])
    if running:
        item = running[-1]
        kind = str(item.get("kind", "")).casefold()
        label = str(item.get("label", "")).casefold()
        if kind == "learning" or any(word in label for word in ("learn", "index knowledge")):
            phase = "Learning"
        elif kind == "research" or any(word in label for word in ("research", "web_search", "crawl", "read_page")):
            phase = "Researching"
        elif kind == "model" or "think" in label:
            phase = "Thinking"
        elif kind == "image" or "image" in label:
            phase = "Generating image"
        else:
            phase = "Working"
        elapsed = max(0.0, float(item.get("seconds", 0.0)))
        remaining = max(0.0, float(item.get("remaining_seconds", 0.0)))
        headline = str(item.get("label") or summary.get("headline") or phase).replace("_", " ")
        return f"{phase}: {headline} · ~{_countdown_duration(remaining)} left · {elapsed:.1f}s elapsed"
    if working_since is not None:
        elapsed = max(0.0, moment - working_since)
        remaining = max(0.0, float(fallback_estimate) - elapsed)
        return f"Thinking · ~{_countdown_duration(remaining)} left · {elapsed:.1f}s elapsed"
    return "Ready"


def _countdown_duration(seconds: float) -> str:
    bounded = max(0, int(round(seconds)))
    if bounded < 60:
        return f"{bounded}s"
    if bounded < 3600:
        minutes, remainder = divmod(bounded, 60)
        return f"{minutes}m {remainder:02d}s"
    return human_duration(bounded)


def activity_progress(
    summary: dict[str, Any], working_since: float | None,
    *, now: float | None = None, fallback_estimate: float = 30.0,
) -> float:
    """Return determinate progress without pretending an ETA is a deadline."""
    moment = time.monotonic() if now is None else float(now)
    running = list(summary.get("running") or [])
    if running:
        item = running[-1]
        elapsed = max(0.0, float(item.get("seconds", 0.0)))
        estimate = max(
            elapsed + max(0.0, float(item.get("remaining_seconds", 0.0))),
            float(item.get("estimated_seconds", 0.0)),
        )
    elif working_since is not None:
        elapsed = max(0.0, moment - working_since)
        estimate = max(0.0, float(fallback_estimate))
    else:
        return 0.0
    if estimate <= 0:
        return 5.0
    # An estimate may be exceeded. Keep a little unfilled until the operation
    # really finishes so the bar never falsely announces completion.
    return min(95.0, max(2.0, elapsed * 100.0 / estimate))


def minimum_size(screen: tuple[int, int]) -> tuple[int, int]:
    """Allow a compact 960x560 window, clamped for a smaller scaled desktop.

    HAYAT runs DPI-unaware, so Windows display scaling shrinks the screen it
    sees: a 1920x1080 panel is 1280x720 at 150 % and 960x540 at 200 %. A fixed
    1180x700 minimum then ran off the right edge and under the taskbar.
    """
    width, height = screen
    return min(960, max(760, width - 20)), min(560, max(460, height - 60))


def window_placement(saved: Any, screen: tuple[int, int],
                     minimum: tuple[int, int] | None = None) -> tuple[str, bool]:
    """Where the window opens: as it was left, or sized to this screen.

    A fixed 1540x920 left most of a large screen unused and forgot every
    resize. A saved placement is trusted only while it still fits the screen
    it is opening on -- a monitor can be unplugged, and a window restored off
    screen is a window that failed to open.
    """
    width, height = screen
    minimum = minimum or minimum_size(screen)
    if isinstance(saved, dict):
        match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", str(saved.get("geometry", "")))
        if match:
            w, h, x, y = (int(value) for value in match.groups())
            fits = (minimum[0] <= w <= width and minimum[1] <= h <= height
                    and 0 <= x <= width - 200 and 0 <= y <= height - 120)
            if fits:
                return f"{w}x{h}+{x}+{y}", bool(saved.get("zoomed"))
    # Start with the familiar roomy layout; the resize minimum can be smaller.
    w = max(minimum[0], min(1180, max(760, width - 20)), min(width - 40, round(width * .88)))
    h = max(minimum[1], min(700, max(460, height - 60)), min(height - 80, round(height * .88)))
    return f"{w}x{h}+{max(0, (width - w) // 2)}+{max(0, (height - h) // 2 - 20)}", False


def reserve_window() -> bool:
    """Claim the window now, so drag-and-drop still works later.

    Call before building the runtime. tkdnd initialises OLE2, which needs COM
    in a single-threaded apartment; importing pywinauto -- which the runtime
    does -- sets it the other way, and from then on TkinterDnD.Tk() fails with
    "unable to initialize OLE2" and files silently stop being droppable.

    Returns whether drag-and-drop is available, and is safe to call in a
    process that will never open a window: it fails quietly and the CLI is
    unaffected.
    """
    global _RESERVED_ROOT
    if _RESERVED_ROOT is not None:
        return _RESERVED_ROOT[1]
    before = tk._default_root
    try:
        from tkinterdnd2 import TkinterDnD

        root = TkinterDnD.Tk()
        root.withdraw()
        _RESERVED_ROOT = (root, True)
        return True
    except Exception:
        stranded = tk._default_root
        if stranded is not None and stranded is not before:
            try:
                stranded.destroy()
            except tk.TclError:
                pass
        return False


from app.gui_execution import ExecutionControlsMixin
from app.gui_operation_monitor import OperationPreviewMixin


class HayatGUI(Palette, BrainOperatorMixin, RemoteAccessMixin, ExecutionControlsMixin, OperationPreviewMixin):
    """Chat, voice, sensors, status and trace around the one assistant.

    Mohsin's palette: olive green, gold, royal blue, black and white. They are
    assigned by meaning rather than sprinkled, so the window can be read at a
    glance instead of decoded.

    Gold is HAYAT itself -- the name, its replies, anything it is telling you
    about itself. Royal blue is Mohsin: what he typed, and the controls he
    presses. Olive green means live and healthy: a sensor that is on, a check
    that passed. White is ordinary text, black is everything behind it, and
    the one colour outside the palette is the red an error is written in,
    because a failure that blends into the theme is a failure that gets
    missed.
    """

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        # So the rest of the runtime knows its output is going to a window.
        runtime.gui = self
        # A drag-and-drop capable root when tkdnd is present, an ordinary one
        # when it is not. Dropping files is worth having and is not worth the
        # window failing to open over, so the capability is optional and the
        # rest of the code never has to know which kind it got.
        self.root, self.drag_and_drop = self._make_root()
        from app.gui_resources import manage_tk_collection
        manage_tk_collection(self.root)
        self.root.title("HAYAT · Private Local AI")
        # Glides and transitions follow Windows' own "Animation effects" setting.
        from app.gui_motion import set_motion, system_allows_motion
        set_motion(system_allows_motion())
        icon = Path(__file__).resolve().parent / "assets" / "hayat.ico"
        if os.name == "nt" and icon.is_file():
            try:
                self.root.iconbitmap(default=str(icon))   # default: pop-ups carry it too
            except tk.TclError:
                pass
        self.root.minsize(*minimum_size((self.root.winfo_screenwidth(), self.root.winfo_screenheight())))
        try:
            saved_window = json.loads((runtime.memory.path.parent / "appearance.json")
                                      .read_text(encoding="utf-8")).get("window")
        except (OSError, ValueError, AttributeError):
            saved_window = None
        self._saved_window = saved_window if isinstance(saved_window, dict) else None
        geometry, zoomed = window_placement(
            saved_window, (self.root.winfo_screenwidth(), self.root.winfo_screenheight()))
        self.root.geometry(geometry)
        if zoomed:
            try:
                self.root.state("zoomed")
            except tk.TclError:
                pass
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._closed = False
        from app.gui_activity import ActivityMonitor
        self.activity_monitor = ActivityMonitor(getattr(runtime, 'events', None))
        self._tool_event_unsubscribers = []
        self._transcript = tempfile.SpooledTemporaryFile(
            max_size=1_000_000, mode="w+t", encoding="utf-8", newline="\n")
        self._voice_action_busy = False
        self._voice_results = queue.Queue()
        # Every pending `after` handle, so closing can cancel them. There were
        # sixteen after() calls and no after_cancel anywhere, so a scheduled
        # callback outlived the interpreter it was scheduled in and Tk wrote
        # `invalid command name "..._tick" ("after" script)` to its error
        # channel on the way down. That text was later found inside
        # data/training/registry.db, over the header, which is a database this
        # process never meant to write to.
        self._after_handles: set[str] = set()
        self._ui_queue: queue.Queue = queue.Queue()
        self._ui_thread_id = threading.get_ident()
        self._media_serial = 0
        from concurrent.futures import ThreadPoolExecutor
        self._preview_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hayat-preview")
        # Tk drops a PhotoImage the instant nothing references it, and an image
        # embedded in the transcript then renders as an empty box. Holding the
        # references here keeps generated pictures on screen for the life of the
        # window.
        self._inline_images: list[Any] = []
        self._video_cards: dict[str, Any] = {}
        self._video_link_cards: dict[str, Any] = {}
        self._evidence_cards: list[Any] = []
        video_workflow = getattr(runtime.registry, 'video_workflow', None)
        if video_workflow is not None:
            video_workflow.player_dispatch = self._dispatch_video_control
        intelligence = getattr(runtime, 'intelligence', None)
        if intelligence is not None:
            intelligence.player_dispatch = self._dispatch_camera_control
        self._working_since: float | None = None
        self._generation_cancel = None
        self._resource_text = "Health check starting…"
        self._next_health_check = 0.0
        # Phone/browser access comes up with the window, not as a separate
        # process, when settings enable it and a passphrase is set. Started
        # after the loop is running so the announcement lands in a built chat.
        self._web_server: Any = None
        from app.resource_control import ResourceController
        from app.web.remote_access import RemoteAccessManager

        self._remote_access = RemoteAccessManager(self.runtime)
        self._resource_controller = ResourceController(self.runtime)
        from app.config import load_settings
        from app.policy.cost import background_autostart_allowed
        autostart = background_autostart_allowed(load_settings())
        if autostart:
            self._schedule(800, self._start_web_access)
        # The brain takes ~23 s to load from cold; start that while the window
        # settles, after the resource mode has set its window. Not under the
        # test suite, which must never load a real model.
        if autostart and not os.environ.get("PYTEST_CURRENT_TEST"):
            self._schedule(1500, self._preload_brain)
        self._health_check_running = False
        # Last sensor reading, painted by the tick and refreshed off-thread.
        self._sensor_reading: dict[str, tuple[str, bool]] = {}
        self._next_sensor_poll = 0.0
        self._sensor_poll_running = False
        self._camera_on = False
        #: The live view, when it is open. Owned here so closing the main
        #: window takes it with us rather than leaving an orphan on screen.
        self.camera_view: Any = None
        #: The preview inside the main window. Separate from camera_view: one
        #: is the panel on the right, the other is the bigger window.
        self.camera_stream: Any = None
        self._toolbox_frame: Any = None
        self._operator_frame: Any = None
        self._operator_suppress_auto_refresh = False
        self.operator_nodes: dict[str, dict[str, Any]] = {}
        self.workspace: Any = None
        self._pinned: list[str] = self._load_workspace()
        self.current_view = "chat"
        self._started_at = time.monotonic()
        self._next_slow_poll = 0.0
        self._slow_poll_running = False
        self._slow: dict[str, Any] = {}
        self._metrics: dict[str, Any] = {}
        self._mindstream_paused = False
        self._mindstream_unsubscribe: Any = None
        self._mindstream_state: tuple[Any, ...] | None = None
        self._mindstream_active_tab = "feed"
        self._mindstream_pulse = False
        self._focus_mode = False
        self._explorer_panels = {}
        self._build()
        from app.gui_workspace import DraftController, DraftStore
        self._command_palette = None
        self._draft = DraftController(
            self.input,
            getattr(runtime, "draft_store", None) or DraftStore(runtime.memory.path.parent / "workspace.db"),
            lambda: runtime.session.session_id,
            lambda: not runtime.authority.privacy_mode,
            self.draft_status.set,
        )
        self.show_view("chat")
        try:
            appearance = json.loads((runtime.memory.path.parent / "appearance.json").read_text(encoding="utf-8"))
            self._theme_name = appearance.get("theme", "Dark")
            self._set_workspace_motion(appearance.get("motion", True))
            self._text_scale = appearance.get("text_scale", 1.0)
        except (OSError, ValueError, AttributeError):
            self._theme_name = "Dark"
            self._text_scale = 1.0
        if self._text_scale not in TEXT_SCALES.values():
            self._text_scale = 1.0
        self.root._hayat_text_scale = self._text_scale
        apply_theme(self.root, type(self), self._theme_name)
        # Apply the saved text size to windows built after the main workspace.
        self.root.bind_all("<Map>", self._scale_new_window, add="+")
        get_output().add_listener(self._output_event)
        self._watch_tools()
        self._watch_mindstream()
        if self.runtime.voice is not None:
            self.runtime.voice.response_callback = self._voice_response
        self._install_approval()
        if (self.runtime.voice is not None and self.runtime.voice.config.get("enabled", True)
                and not getattr(self.runtime, "safe_mode", False)):
            self._schedule(350, self._offer_voice_startup)
        self._show_welcome()
        self.console.write("system", "3D workspace ready. Choose a capability or type a request.")
        if getattr(runtime, "camera", None) is not None:
            self._watch()      # so notes reach this window however watch mode is started
        self._schedule(40, self._drain_ui)
        self._tick()
        self.root.deiconify()

    #: Every control the window offers, as (label, method name, group).
    #: Declared as data rather than twenty near-identical pack() calls, so the
    #: set of things HAYAT can be asked to do from the window is one list to
    #: read -- and so the test that every button actually works can iterate it.
    CONTROLS = (
        ("Voice", "_toggle_voice", "voice"),
        ("Hands-free", "_continuous_voice", "voice"),
        ("Stop", "_stop", "voice"),
        ("+ Add file", "_add_files", "make"),
        ("Download file", "_download_file", "make"),
        ("Camera", "_toggle_camera", "sensors"),
        ("View", "open_camera_view", "sensors"),
        ("Look", "_look", "sensors"),
        ("Screen", "_read_screen", "sensors"),
        ("Clipboard", "_read_clipboard", "sensors"),
        ("Image", "_generate_image", "make"),
        ("Analyze video", "_analyze_video", "make"),
        ("Video clips", "_open_video_studio", "make"),
        ("Research", "_research", "make"),
        ("Web Clip", "_capture_web_page", "make"),
        ("Remote", "_show_remote", "guard"),
        ("Release RAM", "_release_hayat_memory", "guard"),
        ("Operator", "_show_operator", "know"),
        ("Self Check", "_run_self_check", "know"),
        ("Beast mode", "_toggle_god_mode", "know"),
        ("Team", "_open_team_workspace", "know"),
        ("Tools", "_show_tools", "know"),
        ("Memory", "_show_memory", "know"),
        ("Learning", "_show_learning", "know"),
        ("Learn Arabic", "_open_learn", "know"),
        ("Languages", "_open_languages", "know"),
        ("Searches", "_open_searches", "know"),
        ("Security", "_open_security", "guard"),
        ("Business", "_open_business", "make"),
        ("Revenue", "_open_revenue", "make"),
        ("Training", "_show_training", "know"),
        ("Health", "_show_doctor", "know"),
        ("Improve", "_show_improvements", "know"),
        ("Code monitor", "_show_code_monitor", "know"),
        ("Start at login", "_show_startup_status", "know"),
        ("Privacy", "_toggle_privacy", "guard"),
        ("Child mode", "_toggle_child", "guard"),
        # "Appearance" was clipped in the quick-button grid; the Commands
        # palette keeps the full name.
        ("Theme", "_show_appearance", "know"),
    )

    #: The preview box, in pixels. Sixteen-by-nine at the column's width, so
    #: a frame drops in without the box having to resize around it.
    PREVIEW_PIXELS = (344, 194)

    #: Must stay aligned with app.models.diffusion.PROMPT_STYLES. These are
    #: shown in the image dialog, so offering a label the engine silently
    #: converts back to ``auto`` is a broken control, not a harmless alias.
    IMAGE_STYLES = ("auto", "photo", "cinematic", "illustration", "concept")

    #: The sidebar, in order. Each key names a view the centre column can
    #: show; "chat" is the one you talk to and the others report on a part of
    #: HAYAT that previously had to be asked about in words.
    VIEWS = (
        ("chat", "\U0001F4AC", "Chat", "New conversation"),
        ("history", "\U0001F4DA", "Conversations", "Search saved messages"),
        ("tasks", "\U0001F4CB", "Tasks", "Progress & outcomes"),
        ("task_graph", "◇", "Plan editor", "Steps & dependencies"),
        ("gallery", "▧", "Gallery", "Created & added images"),
        ("models", "\U0001F9E0", "Models", "Installed capabilities"),
        ("background", "\U0001F504", "Background", "Learning queue & limits"),
        ("operator", "\U0001F5A7", "Operator", "Brain nodes & contracts"),
        ("invention", "◇", "Invention", "Hypotheses & experiments"),
        ("research", "\U0001F50D", "Research", "Web search & analysis"),
        ("tools", "\U0001F527", "Tools", "System tools & apps"),
        ("memory", "\U0001F5C4", "Memory", "Knowledge & history"),
        ("learning", "\U0001F4D6", "Learning", "Skills & improvement"),
        ("training", "\U0001F9EA", "Training", "Review examples"),
        ("health", "❤", "Health", "System health"),
        ("improve", "✨", "Improve", "Self-upgrade"),
        ("toolbox", "\U0001F9F0", "Toolbox", "Pin what you use"),
        ("files", "\U0001F4C1", "Files", "Project explorer"),
        ("settings", "⚙", "Settings", "Preferences"),
    )

    @staticmethod
    def _make_root() -> tuple[tk.Tk, bool]:
        """The window, with drag-and-drop if it could be arranged.

        Uses a root claimed earlier by `reserve_window()` when there is one.
        Order matters and is not obvious: tkdnd needs COM in a single-threaded
        apartment, pywinauto sets it the other way when the runtime imports
        it, and after that `TkinterDnD.Tk()` fails with "unable to initialize
        OLE2". Building the window before the runtime is the whole fix.
        """
        global _RESERVED_ROOT
        if _RESERVED_ROOT is not None:
            root, capable = _RESERVED_ROOT
            _RESERVED_ROOT = None
            try:
                if root.winfo_exists():
                    # Un-withdraw it. reserve_window() hides the root so an
                    # empty frame is not sitting on screen for the twenty
                    # seconds the runtime takes to build -- and nothing put it
                    # back, so the real window was never shown at all.
                    root.withdraw()
                    _CLAIMED_ROOTS.add(id(root))
                    return root, capable
            except tk.TclError:
                pass
        # An unclaimed root already standing is adopted rather than added to:
        # a second root is a second blank window and Tk gives no hint it is
        # there. Claimed ones are left alone -- adopting a window another
        # HayatGUI is already using means two of them share one root, and the
        # first to close destroys the other's window out from under it.
        existing = tk._default_root
        if existing is not None and id(existing) not in _CLAIMED_ROOTS:
            try:
                if existing.winfo_exists():
                    existing.withdraw()
                    _CLAIMED_ROOTS.add(id(existing))
                    return existing, hasattr(existing, "TkdndVersion")
            except tk.TclError:
                pass

        # Noted before the attempt, so cleanup afterwards can tell what the
        # attempt created from what was already there.
        before = tk._default_root
        try:
            from tkinterdnd2 import TkinterDnD

            made = TkinterDnD.Tk()
            made.withdraw()
            _CLAIMED_ROOTS.add(id(made))
            return made, True
        except Exception:
            # TkinterDnD.Tk builds the root and *then* loads tkdnd, so a
            # failure leaves a live root behind. Falling straight through to
            # tk.Tk() left that one on screen as an empty window, and a second
            # failure left two -- which is what Mohsin saw.
            #
            # Only what this call created is destroyed. Destroying whatever
            # happened to be the default root instead tore down a window
            # somebody else was already using.
            stranded = tk._default_root
            if stranded is not None and stranded is not before:
                try:
                    stranded.destroy()
                except Exception:
                    log_swallowed(_log, "destroying a stranded Tk root")
            plain = tk.Tk()
            plain.withdraw()
            _CLAIMED_ROOTS.add(id(plain))
            return plain, False

    def _accept_drops(self, *widgets: tk.Misc) -> None:
        """Let files be dropped onto these widgets.

        The whole window is a target, not only the transcript. Limiting it to
        the conversation was the tidier idea and it did not work in practice:
        the transcript is a disabled Text, the input box is small, and a file
        dropped an inch either side landed on a frame that accepted nothing
        and silently did nothing. A drop anywhere in the window means the same
        thing -- look at this -- so the window takes it.
        """
        if not self.drag_and_drop:
            return
        try:
            from tkinterdnd2 import DND_FILES
        except Exception:
            return
        for widget in widgets:
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                log_swallowed(_log, "registering a drop target")
                continue

    def _on_drop(self, event: Any) -> None:
        """Files were dropped onto the conversation."""
        paths = self._parse_dropped(getattr(event, "data", "") or "")
        if paths:
            self._ingest_files(paths)

    @staticmethod
    def _parse_dropped(data: str) -> list[str]:
        """Split what tkdnd hands over into real paths.

        Tk gives a single string, and any path containing a space arrives
        wrapped in braces -- so `C:/a b.txt C:/c.txt` is one path and two, and
        naive splitting on whitespace turns the first into two files that do
        not exist.
        """
        import re as _re

        found: list[str] = []
        for braced, bare in _re.findall(r"\{([^}]*)\}|(\S+)", str(data or "")):
            candidate = (braced or bare).strip()
            if candidate:
                found.append(candidate)
        return found

    def _build(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style_controls(self.root, type(self))
        style_hayat_buttons(self.root, type(self))

        self._build_titlebar()
        self._build_transparency_bar()
        # Packed before the body so the status strip keeps its height when the
        # window is dragged small, rather than being the first thing squeezed.
        self._build_statusbar()

        body = tk.Frame(self.root, bg=self.BLACK)
        body.pack(fill="both", expand=True)
        self._body = body
        # Order matters and cost an hour once: pack the expanding centre
        # before the right column and Tk hands it every remaining pixel, so
        # the entire right side -- camera, sensors, dials, console -- silently
        # never appears. Fixed edges first, the stretchy one last.
        self._build_sidebar(body)
        self._build_right(body)
        centre = tk.Frame(body, bg=self.BLACK, padx=12, pady=10)
        centre.pack(side="left", fill="both", expand=True)
        self._build_centre(centre)
        from app.gui_overview_layout import OverviewLayout
        self._overview_layout = OverviewLayout(self)
        # Both targets exist only once the centre is finished: the compose
        # bar is built before the transcript so the transcript can expand
        # into what is left.
        # The window itself first, so a drop cannot miss.
        self._accept_drops(self.root, self.chat, self.input, self.drop_zone, self.depth_nav)
        self._layout_state = None
        self.root.bind("<Configure>", self._on_window_resize, add="+")
        for row in (self._header_actions, self._conversation_actions, self._composer_actions):
            row.bind("<Configure>", lambda _event: self._fit_accessible_controls(), add="+")
        # In the Glass appearance a soft aurora shows in the gaps around the
        # frosted panels (app/gui_glass.py); other appearances are unchanged.
        from app.gui_glass import register_backdrops

        register_backdrops(self.root, [self.root, body, centre, self._sidebar, self.right_rail,
                                         self.right_rail.canvas, self.right_rail.body], type(self))

    # ------------------------------------------------------------- title bar

    def _build_titlebar(self) -> None:
        shell = tk.Frame(self.root, bg=self.GOLD, height=1)
        shell.pack(fill="x")
        header_surface = DepthSurface(self.root, type(self), padding=8)
        header_surface.pack(fill="x", padx=10, pady=(8, 0))
        from app.gui_intelligence_parts import FlowRow
        bar = FlowRow(header_surface.body, self.PANEL, gap=12, height=52)
        bar.pack(fill="x")
        self._header_actions = bar
        # Greeting first, because the window belongs to one person and saying
        # so is the cheapest way for it to feel like his rather than a tool's.
        greeting = bar.add(tk.Frame(bar, bg=self.PANEL))
        self.greeting_var = StringVar(master=self.root, value=f"Hello, {IDENTITY.owner_short}")
        tk.Label(greeting, textvariable=self.greeting_var, bg=self.PANEL, fg=self.WHITE,
                 font=("Segoe UI Semibold", 13), anchor="w").pack(fill="x")
        self.header_clock = StringVar(master=self.root, value="")
        self._header_clock_label = tk.Label(greeting, textvariable=self.header_clock, bg=self.PANEL, fg=self.MUTED,
                                            font=("Segoe UI", 9), anchor="w")
        self._header_clock_label.pack(fill="x")

        # Right side reads right-to-left as it is packed, so GPU is placed
        # first and ends up rightmost.
        centre = bar.add(tk.Frame(bar, bg=self.PANEL, pady=10))
        self.online_dot = tk.Label(centre, text="●", bg=self.PANEL, fg=self.OLIVE,
                                   font=("Segoe UI", 9))
        self.online_dot.pack(side="left", padx=(0, 6))
        self.online_var = StringVar(master=self.root, value="Starting…")
        tk.Label(centre, textvariable=self.online_var, bg=self.PANEL, fg=self.WHITE,
                 font=("Segoe UI", 10)).pack(side="left")
        metrics = bar.add(tk.Frame(bar, bg=self.PANEL, pady=8))
        actions = bar.add(tk.Frame(bar, bg=self.PANEL))
        self.metric_vars: dict[str, StringVar] = {}
        self.focus_mode_button = ttk.Button(
            actions, text="Focus Mode", style="Hayat.TButton", command=self._toggle_focus_mode,
        )
        self.focus_mode_button.pack(side="right", padx=(10, 0))
        self.overview_button = ttk.Button(actions, text="Hide overview", style="Hayat.TButton",
                                          command=self._toggle_overview)
        self.overview_button.pack(side="right", padx=(10, 0))
        # The way in for someone new: everything HAYAT can do, with examples to try.
        self.help_button = ttk.Button(actions, text="Help", style="Hayat.TButton", command=self._open_help)
        self.help_button.pack(side="right", padx=(10, 0))
        # Every download in one place: what, from where, saved where, and the history. Moves while one runs.
        from app.gui_downloads import DownloadsButton
        self.downloads_button = DownloadsButton(actions, self, self._open_downloads)
        self.downloads_button.pack(side="right", padx=(10, 0))
        self.header_more_button = ttk.Menubutton(actions, text="Layout ▾", style="Hayat.TButton")
        header_menu = tk.Menu(self.header_more_button, tearoff=0, bg=self.PANEL_2, fg=self.WHITE,
                              activebackground=self.PANEL_HOVER, activeforeground=self.WHITE)
        for label, command in (("Show / hide overview", self._toggle_overview),
                               ("Focus mode", self._toggle_focus_mode), ("Help", self._open_help),
                               ("Find a workspace", self._focus_workspace_search),
                               ("Commands", self._show_command_palette), ("Appearance", self._show_appearance)):
            header_menu.add_command(label=label, command=command)
        self.header_more_button.configure(menu=header_menu)
        for key, label in (("gpu", "GPU"), ("ram", "RAM"), ("cpu", "CPU")):
            variable = StringVar(master=self.root, value=f"{label} --")
            tk.Label(metrics, textvariable=variable, bg=self.PANEL, fg=self.GOLD_SOFT,
                     font=("Segoe UI Semibold", 9)).pack(side="right", padx=9)
            self.metric_vars[key] = variable
        # What Mohsin has earned and can withdraw, always in sight; a click
        # opens the Business window's books and wallet.
        self.wallet_var = StringVar(master=self.root, value="Wallet --")
        self.wallet_badge = tk.Label(metrics, textvariable=self.wallet_var, bg=self.PANEL_2, fg=self.OLIVE_SOFT,
                                     font=("Segoe UI Semibold", 9), padx=10, pady=4, cursor="hand2")
        self.wallet_badge.pack(side="right", padx=(9, 4))
        self.wallet_badge.bind("<Button-1>", lambda _event: self._open_business())
        self._wallet_checked = 0.0
        self._header_actions_default = list(bar.items)


    # --------------------------------------------------------------- sidebar

    def _build_transparency_bar(self) -> None:
        style = ttk.Style(self.root)
        for name in ('Compact.Hayat.TButton', 'Compact.HayatAccent.TButton'):
            style.configure(name, padding=(8, 3))
        bar = tk.Frame(self.root, bg=self.PANEL, padx=12, pady=6)
        self._transparency_bar = bar
        bar.pack(fill='x', padx=10, pady=(4, 0))
        self.features_button = ttk.Button(bar, text='All features', style='Compact.Hayat.TButton', command=self._open_features)
        self.features_button.pack(side='left', padx=(0, 6))
        self.activity_button = ttk.Button(bar, text='Activity', style='Compact.Hayat.TButton', command=self._open_activity)
        self.activity_button.pack(side='left', padx=(0, 10))
        # One click to each main workspace, like tabs across the top of the window.
        self.nav_buttons: dict[str, ttk.Button] = {}
        # Five buttons: a sixth wrapped the bar on narrower windows and took height from the brain view.
        # The Trade Center lives in Studios and answers in chat.
        for label, command in (('Global Intelligence', self._open_intelligence), ('Business', self._open_business),
                               ('Voice & Models', lambda: self._open_control_studio('voice')),
                               ('Research', lambda: self.show_view('research')), ('Automation', lambda: self.show_view('tasks'))):
            button = ttk.Button(bar, text=label, style='Compact.Hayat.TButton', command=command)
            button.pack(side='left', padx=(0, 4))
            self.nav_buttons[label] = button
        self.workspace_overflow = ttk.Menubutton(bar, text='Workspaces ▾', style='Compact.Hayat.TButton')
        self.workspace_overflow_menu = tk.Menu(self.workspace_overflow, tearoff=False, bg=self.PANEL, fg=self.WHITE,
                                              activebackground=self.GOLD, activeforeground=self.BLACK)
        for label, command in (('Global intelligence', self._open_intelligence), ('Searches', self._open_searches),
                               ('Trade Center', self._open_markets), ('Reverse engineering', self._open_reverse_engineer),
                               ('My cameras', self._open_my_cameras), ('Business', self._open_business),
                               ('Voice & models', lambda: self._open_control_studio('voice')),
                               ('Show / hide overview', self._toggle_overview)):
            self.workspace_overflow_menu.add_command(label=label, command=command)
        self.workspace_overflow.configure(menu=self.workspace_overflow_menu)
        style.configure('Emergency.TButton', background='#992c3a', foreground='white', padding=(10, 5))
        style.map('Emergency.TButton', background=[('active', '#b43646'), ('disabled', '#662936')], foreground=[('disabled', '#ffffff')])
        self.emergency_button = ttk.Button(bar, text='Emergency stop', command=self._emergency_stop, style='Emergency.TButton')
        self.emergency_button.pack(side='right', padx=(8, 0))
        self.transparency_stop = self.emergency_button
        self.resume_tools_button = ttk.Button(bar, text='Resume tools', command=self._resume_tools,
                                             style='Compact.Hayat.TButton', state='disabled')
        self.transparency_state = StringVar(self.root, 'Checking live state…')
        # Shown in the strip: the same state in short words, so it fits one line beside the tabs (the tooltip and
        # Activity keep the full sentence). The long form wrapped and left "off" alone on a second line at 1536 px.
        self.transparency_brief = StringVar(self.root, 'Checking live state…')
        self.transparency_last = StringVar(self.root, 'No tool actions recorded in this window session yet.')
        words = tk.Frame(bar, bg=self.PANEL)
        words.pack(side='left', fill='x', expand=True)
        self._transparency_words = words
        state_label = tk.Label(words, textvariable=self.transparency_brief, bg=self.PANEL, fg=self.WHITE,
                              font=('Segoe UI', 9), anchor='w', justify='left', wraplength=650)
        state_label.pack(fill='x')
        self._transparency_state_label = state_label
        from app.gui_help import Tooltip
        self._transparency_state_tip = Tooltip(state_label, self.transparency_state.get(), type(self))
        last_label = tk.Label(words, textvariable=self.transparency_last, bg=self.PANEL, fg=self.MUTED,
                             font=('Segoe UI', 8), anchor='w', justify='left', wraplength=650)
        last_label.pack(fill='x')
        self._transparency_last_label = last_label
        words.bind('<Configure>', lambda event: (state_label.configure(wraplength=max(1, event.width)),
                                                last_label.configure(wraplength=max(1, event.width))))

    def _paint_transparency(self) -> None:
        from app.gui_activity import live_state, live_state_brief, plain_activity
        rows, active = self.activity_monitor.snapshot()
        self.activity_button.configure(text=f'Activity ({active})' if active else 'Activity')
        self.transparency_state.set_if_changed(live_state(self))
        self.transparency_brief.set_if_changed(live_state_brief(self))
        self._transparency_state_tip.text = self.transparency_state.get()
        latest = (f'Last: {plain_activity(rows[0]["summary"])}' if rows else
                  'No recorded events yet.' if self.activity_monitor.available else 'Activity events unavailable.')
        running = f'{active} tool call{"s" if active != 1 else ""} running' if active else 'Nothing running'
        self.transparency_last.set_if_changed(f'{running} · ' + latest[:135])

    def _open_window_switch(self) -> None:
        """A quick switcher between HAYAT's open windows (app/gui_window_switch.py)."""
        from app.gui_window_switch import WindowSwitch, open_windows
        existing = getattr(self, "_window_switch", None)
        if existing is not None and existing.winfo_exists():
            existing.close()
        if not open_windows(self.root):
            self._toast("No other windows are open.") if hasattr(self, "_toast") else None
            return
        self._window_switch = WindowSwitch(self)

    def _open_features(self, query: str = '') -> None:
        from app.gui_features import FeaturesWindow
        window = getattr(self, '_features_window', None)
        if window is None or not window.winfo_exists():
            window = self._features_window = FeaturesWindow(self)
        window.deiconify()
        window.lift()
        if query:
            window.query.set(query)
        window.entry.focus_set()

    def _open_activity(self) -> None:
        from app.gui_activity import ActivityWindow
        window = getattr(self, '_activity_window', None)
        if window is None or not window.winfo_exists():
            window = self._activity_window = ActivityWindow(self)
        window.deiconify()
        window.lift()
        window.focus_set()

    def _build_sidebar(self, parent: tk.Misc) -> None:
        rail = tk.Frame(parent, bg=self.BLACK, width=218, padx=10, pady=10)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        self._sidebar = rail

        brand = tk.Frame(rail, bg=self.BLACK, padx=8, pady=6)
        brand.pack(fill="x", pady=(0, 8))
        tk.Label(brand, text="H", bg=self.GOLD, fg=self.BLACK, width=3,
                 font=(DISPLAY, 17), pady=4).pack(side="left", padx=(0, 10))
        brand_words = tk.Frame(brand, bg=self.BLACK)
        brand_words.pack(side="left", fill="x", expand=True)
        tk.Label(brand_words, text="HAYAT", bg=self.BLACK, fg=self.WHITE,
                 font=(DISPLAY, 16), anchor="w").pack(fill="x")
        tk.Label(brand_words, text="YOUR LOCAL AI", bg=self.BLACK,
                 fg=self.MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(2, 0))

        ttk.Button(rail, text='+ New chat', style='HayatAccent.TButton',
                   command=self.clear_chat).pack(fill='x', pady=(0, 8))
        workspace_button = ttk.Menubutton(rail, text='Open workspace ▾', style='Hayat.TButton')
        workspace_menu = tk.Menu(workspace_button, tearoff=0, bg=self.PANEL_2, fg=self.WHITE,
                                 activebackground=self.PANEL_HOVER, activeforeground=self.WHITE)
        for label, command in (('Home / Command centre', self._show_home),
                               ('Business & earning plans', self._open_revenue),
                               ('Voice studio', lambda: self._open_control_studio('voice')),
                               ('World observation', self._open_intelligence)):
            workspace_menu.add_command(label=label, command=command)
        workspace_button.configure(menu=workspace_menu)
        workspace_button.pack(fill='x', pady=(0, 4))

        from app.gui_navigation import WorkspaceNavigation
        self.workspace_navigation = WorkspaceNavigation(rail, type(self), self.VIEWS, self.show_view)
        self.workspace_navigation.pack(fill="both", expand=True)
        self.nav = self.workspace_navigation.items

        # The clock lives in the header; the rail footer is one compact row,
        # so the navigation keeps the height on a 768-pixel laptop screen.
        self.clock_var = StringVar(master=self.root, value="")
        profile = tk.Frame(rail, bg=self.BLACK, pady=8)
        profile.pack(side="bottom", fill="x")
        ttk.Button(profile, text="Settings", style="Hayat.TButton", width=0,
                   command=lambda: self.show_view("settings")).pack(side="bottom", fill='x', pady=(6, 0))
        tk.Frame(rail, bg=self.LINE, height=1).pack(fill="x", side="bottom", pady=(4, 0))
        tk.Label(profile, text=f"Local workspace · {IDENTITY.owner_short}", bg=self.BLACK, fg=self.MUTED,
                 font=("Segoe UI", 8), anchor="w").pack(fill="x")
        if IDENTITY.creator != IDENTITY.owner:
            # Every copy credits its creator (NOTICE, AGPL section 7(b)).
            tk.Label(profile, text=f"HAYAT by {IDENTITY.creator}", bg=self.BLACK, fg=self.MUTED,
                     font=("Segoe UI", 8), anchor="w").pack(fill="x")

    # ---------------------------------------------------------------- centre

    def _build_centre(self, parent: tk.Misc) -> None:
        # Held explicitly. ScrolledText wraps itself in its own frame, so
        # `self.chat.master` is inside the transcript widget rather than the
        # column it sits in -- anything packed there disappears into the
        # scrollbar's container and never draws.
        self._centre = parent
        header_surface = DepthSurface(parent, type(self), padding=7)
        self._conversation_header = header_surface
        header_surface.pack(fill="x")
        from app.gui_intelligence_parts import FlowRow
        header = FlowRow(header_surface.body, self.PANEL, gap=6, height=40)
        header.pack(fill="x")
        self._conversation_actions = header
        heading = header.add(tk.Frame(header, bg=self.PANEL, padx=2, pady=4))
        self.view_title = StringVar(master=self.root, value="Conversation")
        tk.Label(heading, textvariable=self.view_title, bg=self.PANEL, fg=self.WHITE,
                 font=("Segoe UI Semibold", 13)).pack(side="left")
        self.view_badge = tk.Label(heading, text="Active", bg=self.PANEL_2, fg=self.OLIVE_SOFT,
                                   font=("Segoe UI", 8), padx=8, pady=3)
        self.view_badge.pack(side="left", padx=10)
        self.chat_actions_button = ttk.Menubutton(header, text="Chat actions ▾", style="Hayat.TButton")
        self.chat_actions_menu = tk.Menu(self.chat_actions_button, tearoff=0,
                                        bg=self.PANEL_2, fg=self.WHITE, activebackground=self.PANEL_HOVER,
                                        activeforeground=self.WHITE, bd=0, font=("Segoe UI", 10))
        header.add(ttk.Button(header, text='Stop & correct', command=self._stop_and_correct, style='Hayat.TButton'))
        self.chat_actions_menu.add_command(label='Continue from earlier?', command=self._open_chat_rewind)
        self.chat_actions_menu.add_command(label="Save transcript", command=self.save_transcript)
        self.chat_actions_menu.add_command(label="Team workspace", command=self._open_team_workspace)
        self.chat_actions_menu.add_command(label="Revenue workspace", command=self._open_revenue)
        self.chat_actions_menu.add_command(label="Understand a customer", command=lambda: self.use_example('Customer NAME: Paste the actual inquiry here.\nAudience: \nBudget: \nDeadline: '))
        self.chat_actions_menu.add_command(label="Show customers", command=lambda: self.use_example('Show my customers'))
        self.chat_actions_menu.add_command(label="Decide next customer action", command=lambda: self.use_example('What should we do next for this customer?'))
        self.chat_actions_menu.add_command(label="Prepare next customer action", command=lambda: self.use_example('Decide and do the next step'))
        self.chat_actions_menu.add_command(label="Update selected customer", command=lambda: self.use_example('Customer update: Paste their latest feedback here.'))
        self.chat_actions_menu.add_command(label="Prepare customer deliverable", command=lambda: self.use_example('Prepare their deliverable'))
        self.chat_actions_menu.add_command(label="Prepare customer quote", command=lambda: self.use_example('Prepare a quote for this customer for AMOUNT CURRENCY'))
        self.chat_actions_menu.add_command(label="Invoice customer quote", command=lambda: self.use_example('Prepare an invoice for this customer from quote Q-YEAR-NUMBER'))
        self.chat_actions_menu.add_separator()
        self.chat_actions_menu.add_command(label="Clear chat", command=self.clear_chat)
        self.chat_actions_menu.add_separator()
        for label, command, keys in (("Copy chat", self.copy_chat, "Ctrl+Shift+C"),
                                     ("Find in chat", self._open_chat_find, "Ctrl+F"),
                                     ("Jump to latest", self._jump_to_latest, ""),
                                     ("Commands", self._show_command_palette, "Ctrl+Shift+P")):
            self.chat_actions_menu.add_command(label=label, command=command, accelerator=keys)
        self.chat_actions_button.configure(menu=self.chat_actions_menu)
        header.add(self.chat_actions_button)
        self.copy_button = ttk.Button(header, text="Copy chat", width=0, style="Hayat.TButton",
                                      command=self.copy_chat)
        header.add(self.copy_button)
        # Find and Commands live in "Chat actions" (with Ctrl+F / Ctrl+Shift+P): as buttons too they pushed the row
        # to two lines at 1536 px, and the transcript lost ~45 px of its ~330 (2026-10-24 screenshot review).
        self.find_button = ttk.Button(header, text="Find", width=0, style="Hayat.TButton", command=self._open_chat_find)
        self.latest_button = ttk.Button(header, text="Latest ↓", width=0, style="Hayat.TButton",
                                        command=self._jump_to_latest)
        header.add(self.latest_button)
        self.commands_button = ttk.Button(header, text="Commands", width=0, style="Hayat.TButton",
                                          command=self._show_command_palette)

        self.core_button = ttk.Button(header, text="Conversation", width=0, style="Hayat.TButton",
                                      command=self._toggle_core)
        header.add(self.core_button)
        self._conversation_actions_default = list(header.items)
        self.view_help = StringVar(self.root, "Ask a question, attach a file, or choose an action below.")
        self._chat_find_open = False
        self._chat_find_matches: list[tuple[str, str]] = []
        self._chat_find_index = -1
        self._chat_find_var = StringVar(self.root, "")
        self._chat_find_status = StringVar(self.root, "Type to search visible messages")
        find_bar = tk.Frame(parent, bg=self.PANEL, padx=8, pady=5)
        self._chat_find_bar = find_bar
        tk.Label(find_bar, text="Find in chat", bg=self.PANEL, fg=self.WHITE,
                 font=("Segoe UI Semibold", 9)).pack(side="left", padx=(0, 8))
        self._chat_find_entry = ttk.Entry(find_bar, textvariable=self._chat_find_var)
        self._chat_find_entry.pack(side="left", fill="x", expand=True)
        self._chat_find_entry.bind("<KeyRelease>", self._refresh_chat_find)
        self._chat_find_entry.bind("<Return>", lambda _event: self._step_chat_find(1))
        self._chat_find_entry.bind("<Shift-Return>", lambda _event: self._step_chat_find(-1))
        self._chat_find_entry.bind("<Escape>", lambda _event: self._close_chat_find())
        tk.Label(find_bar, textvariable=self._chat_find_status, bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9), width=22, anchor="e").pack(side="left", padx=6)
        ttk.Button(find_bar, text="Previous", style="Hayat.TButton",
                   command=lambda: self._step_chat_find(-1)).pack(side="left", padx=2)
        ttk.Button(find_bar, text="Next", style="Hayat.TButton",
                   command=lambda: self._step_chat_find(1)).pack(side="left", padx=2)
        ttk.Button(find_bar, text="Close", style="Hayat.TButton",
                   command=self._close_chat_find).pack(side="left", padx=2)
        tabs = tk.Frame(parent, bg=self.BLACK)
        self._chat_tabs = tabs
        tabs.pack(fill="x", pady=(4, 6))
        self.depth_shortcuts = {}
        primary_shortcuts = {"chat", "files", "web", "code", "voice"}
        more_shortcuts = ttk.Menubutton(tabs, text="More actions", style="Hayat.TButton")
        shortcut_menu = tk.Menu(more_shortcuts, tearoff=0, bg=self.PANEL_2, fg=self.WHITE,
                                activebackground=self.GOLD, activeforeground=self.BLACK, bd=0,
                                font=("Segoe UI", 10))
        more_shortcuts["menu"] = shortcut_menu
        self.shortcut_menu = shortcut_menu
        for key, title, command in (
            ("chat", "Chat", lambda: self.show_view("chat")),
            ("files", "Documents", lambda: self.show_view("files")),
            ("web", "Web search", self._research),
            ("code", "Code", lambda: self._compose_plan("Help me write or improve this code: ")),
            ("powershell", "PowerShell", lambda: self._compose_plan("Run PowerShell: ")),
            ("cmd", "CMD", lambda: self._compose_plan("Run CMD: ")),
            ("research", "Research", lambda: self.show_view("research")),
            ("vision", "Vision", self._open_vision_controls),
            ("voice", "Voice", lambda: self._open_control_studio("voice")),
        ):
            button = ttk.Button(tabs, text=title, width=0, style="Hayat.TButton", command=command)
            if key in primary_shortcuts:
                button.pack(side="left", fill="x", expand=True, padx=2)
            else:
                shortcut_menu.add_command(label=title, command=button.invoke)
            self.depth_shortcuts[key] = button
        more_shortcuts.pack(side="left", padx=2)
        self._build_brain_strip(parent)
        self._brain_outer.pack_forget()

        # Everything with a fixed height is packed to the bottom first, so the
        # transcript expands into what is left rather than pushing the input
        # box off the end of the window.
        self._build_compose(parent)

        self.chat = DarkScrolledText(
            parent, wrap="word", bg=self.PANEL, fg=self.WHITE, insertbackground=self.GOLD,
            relief="flat", bd=0, highlightthickness=1, highlightbackground=self.LINE,
            padx=24, pady=22, font=("Segoe UI", 12), state="disabled",
            selectbackground=self.ROYAL, selectforeground=self.WHITE, exportselection=False,
        )
        self.chat.pack(fill="both", expand=True, pady=(2, 8))
        self._unread_messages = 0
        self.chat.configure(yscrollcommand=self._chat_scrolled)
        self.chat.bind("<Control-a>", self._select_chat)
        self.chat.bind("<Control-c>", self.copy_selection)
        self.chat.bind("<Control-Shift-C>", self.copy_chat)
        self.chat.bind("<Button-3>", self._chat_menu)
        # Who is speaking is carried by colour, so a long transcript can be
        # skimmed without reading the names.
        self.chat.tag_configure("user", foreground=self.ROYAL_SOFT, background=self.PANEL_2,
                                lmargin1=20, lmargin2=20, rmargin=20, spacing1=12, spacing3=12)
        self.chat.tag_configure("assistant", foreground=self.WHITE,
                                lmargin1=12, lmargin2=12, rmargin=12, spacing1=10, spacing3=12)
        self.chat.tag_configure("system", foreground=self.MUTED, spacing1=8, spacing3=8)
        self.chat.tag_configure("report", foreground=self.GOLD_SOFT, spacing1=8, spacing3=8,
                                font=("Cascadia Mono", 10))
        self.chat.tag_configure("error", foreground=self.ALERT, spacing1=8, spacing3=8)
        self.chat.tag_configure("pending", foreground=self.MUTED, spacing1=8, spacing3=8,
                                font=("Segoe UI", 10, "italic"))
        self.chat.tag_configure("find_match", background=self.ROYAL, foreground=self.WHITE)
        self.chat.tag_configure("find_active", background=self.GOLD, foreground=self.BLACK)
        self.chat.tag_configure("stamp", foreground=self.MUTED, font=("Segoe UI", 9))
        # A listing, a file or a script's output lines up in a block.
        self.chat.tag_configure("block", font=("Cascadia Mono", 11), background=self.PANEL_2,
                                lmargin1=14, lmargin2=14, rmargin=14, spacing1=0, spacing3=0)
        # "How I got this": the steps behind a reply, opened on demand.
        self.chat.tag_configure("steps_toggle", foreground=self.ROYAL_SOFT, font=("Segoe UI", 9, "underline"),
                                spacing1=2, spacing3=6)
        self.chat.tag_configure("steps_detail", foreground=self.MUTED, font=("Cascadia Mono", 10),
                                lmargin1=18, lmargin2=18, spacing3=2)
        self.chat.tag_bind("steps_toggle", "<Enter>", lambda _event: self.chat.configure(cursor="hand2"))
        self.chat.tag_bind("steps_toggle", "<Leave>", lambda _event: self.chat.configure(cursor=""))
        self._steps: dict[str, list[str]] = {}
        # Formatted replies: headings, bullets, bold, code -- replies arrive as
        # Markdown, and raw "**" and "$\\times$" made them hard to read.
        self.chat.tag_configure("md_heading", font=("Segoe UI Semibold", 13), foreground=self.GOLD_SOFT,
                                spacing1=6, spacing3=2)
        self.chat.tag_configure("md_bold", font=("Segoe UI Semibold", 12))
        self.chat.tag_configure("md_italic", font=("Segoe UI", 12, "italic"))
        self.chat.tag_configure("md_code", font=("Cascadia Mono", 10), background=self.PANEL_2)
        self.chat.tag_configure("md_codeblock", font=("Cascadia Mono", 10), background=self.PANEL_2,
                                lmargin1=18, lmargin2=18)
        self.chat.tag_configure("md_bullet", lmargin1=12, lmargin2=26)
        self.chat.tag_configure("md_bullet2", lmargin1=30, lmargin2=44)
        self.chat.tag_configure("md_rule", foreground=self.LINE)
        # Lines inside a formatted reply sit close together; the speaker tag's
        # paragraph spacing on every line made a short reply look scattered.
        self.chat.tag_configure("md_body", spacing1=3, spacing3=3)
        self.chat.tag_configure("doc_card", background=self.PANEL_2, lmargin1=12, lmargin2=12,
                                rmargin=40, spacing1=2, spacing3=4, font=("Segoe UI", 10))
        self.chat.tag_configure("doc_title", font=("Segoe UI Semibold", 10), background=self.PANEL_2,
                                foreground=self.WHITE, lmargin1=12, lmargin2=12)
        self.chat.tag_configure("doc_line", font=("Cascadia Mono", 9), background=self.PANEL_2,
                                foreground=self.MUTED, lmargin1=24, lmargin2=24)
        # A gap above each speaker: "…$83,604." ran straight into the next "You 12:34 PM" (2026-10-24 review).
        self.chat.tag_configure("speaker_user", foreground=self.ROYAL_SOFT,
                                font=("Segoe UI Semibold", 10), spacing1=16, lmargin1=12)
        self.chat.tag_configure("speaker_hayat", foreground=self.GOLD,
                                font=("Segoe UI Semibold", 10), spacing1=16, lmargin1=12)
        self._welcome_pending = True
        self._welcome = tk.Frame(self.chat.frame, bg=self.PANEL)
        home_header = tk.Frame(self._welcome, bg=self.PANEL, padx=18, pady=6)
        home_header.pack(fill='x')
        tk.Label(home_header, text='What will you build today?', bg=self.PANEL, fg=self.WHITE,
                 font=(DISPLAY, 20), anchor='w').pack(fill='x', pady=(2, 2))
        tk.Label(home_header, text='Speak, research, create and turn a plan into finished work.',
                 bg=self.PANEL, fg=self.MUTED, font=('Segoe UI', 10), anchor='w').pack(fill='x')
        from app.gui_intelligence_parts import FlowRow
        home_actions = FlowRow(self._welcome, self.PANEL, gap=6, height=36)
        home_actions.pack(fill='x', padx=16, pady=(0, 8))
        # Short labels: six starting points on one row down to 1440 px (a seventh wrapped and squeezed the 3D scene).
        for label, command in (('Earning plan', self._open_earning_plan),
                               ('Attach', self._add_files),
                               ('Learning', lambda: self.show_view('background')),
                               ('Open source', lambda: self.use_example('open_source_search {"query": "local speech recognition"}')),
                               ('Find jobs', self._open_jobs), ('Markets', self._open_markets)):
            home_actions.add(ttk.Button(home_actions, text=label, width=0, style='Hayat.TButton', command=command))
        self._core_requested = False
        self.depth_view_title = StringVar(self.root, "Workspace")
        self.depth_view_detail = StringVar(self.root, "Drag to rotate · Scroll to zoom · Choose a capability")
        scene_controls = tk.Frame(self._welcome, bg=self.PANEL, padx=10, pady=4)
        scene_controls.pack(side="bottom", fill="x")
        self.depth_motion_button = ttk.Button(scene_controls, text="Pause motion", width=0,
                                              style="Hayat.TButton", command=self._toggle_depth_motion)
        self.depth_motion_button.pack(side="right")
        self.depth_nav = NeuralWorkspace(self._welcome, type(self), self.show_view, self._open_capability,
                                         on_preview=self._preview_depth_view,
                                         on_motion=lambda enabled: self.depth_motion_button.configure(
                                             text="Pause motion" if enabled else "Enable motion"))
        for title, command in (("−", lambda: self.depth_nav._zoom(-1)),
                               ("+", lambda: self.depth_nav._zoom(1))):
            ttk.Button(scene_controls, text=title, width=2 if len(title) == 1 else 0,
                       style="Hayat.TButton", command=command).pack(side="right", padx=2)
        from app.gui_3d_controls import camera_menu
        camera_menu(scene_controls, self.depth_nav).pack(side='right', padx=2)
        # Two primary views stay as direct buttons; the studios (which each open
        # their own window) live behind one "Studios" menu, so the row cannot
        # overflow and hide a button as more studios are added.
        ttk.Button(scene_controls, text="What can HAYAT do?", width=0, style="Hayat.TButton",
                   command=self._open_help).pack(side="left", padx=(0, 6))
        for title, command in (("Brain 5D", lambda: self.show_view("operator")),
                               ("Eye", self.open_camera_view)):
            ttk.Button(scene_controls, text=title, width=0, style="Hayat.TButton", command=command).pack(side="left", padx=2)
        studios = ttk.Menubutton(scene_controls, text="Studios ▾", style="Hayat.TButton")
        menu = tk.Menu(studios, tearoff=0, bg=self.PANEL_2, fg=self.WHITE, activebackground=self.GOLD,
                       activeforeground=self.BLACK, bd=0, font=("Segoe UI", 10))
        for label, command in (("What can HAYAT do?", self._open_help),
                               ("Switch window… (Ctrl+`)", self._open_window_switch),
                               ("Command Center (everything live)", self._open_command_center),
                               ("Voice", lambda: self._open_control_studio("voice")),
                               ("Models", lambda: self._open_control_studio("models")),
                               ("Learn a language", self._open_learn),
                               ("Learning atlas", self._open_learning_atlas),
                               ("Languages & persona", self._open_languages),
                               ("Searches", self._open_searches),
                               ("Reverse engineering (REA)", self._open_reverse_engineer),
                               ("Screen & computer", self._open_workspace_tools),
                               ('Operation view', self._open_operation_preview),
                               ('Private networking', self._open_networking),
                               ('Onion page navigator', self._open_onion_navigation),
                               ("Person detection", lambda: self._open_workspace_tools("people")),
                               ('Enrolled people (local)', self._open_enrolled_faces),
                               ('Local image tools', self._open_local_image_tools),
                               ("Replica app studio", lambda: self._open_workspace_tools("replica")),
                               ('Global intelligence', self._open_intelligence),
                               ('Explore & deep look', self._open_explore),
                               ('World atlas & video wall', self._open_atlas),
                               ('My cameras', self._open_my_cameras),
                               ('Wi-Fi sensing (CSI, movement, camera check)', self._open_sensing),
                               ('Router & home network', self._open_router),
                               ('Around me (hazard radar)', self._open_radar),
                               ('Location privacy (who can see where you are)', self._open_location_privacy),
                               ('Keys (unlock cameras and stock prices)', self._open_keys),
                               ('Trade Center (crypto, stocks, world events)', self._open_markets),
                               ('Web3 explorer & agentic web (Web 4.0)', self._open_web3),
                               ('Accounts & sign-in (Google, Microsoft, GitHub…)', self._open_accounts),
                               ('Jobs (search, match, apply)', self._open_jobs),
                               ('HAYAT Wallet (decisions, pots, bills)', self._open_money_desk),
                               ('Portfolio & net worth (USD)', self._open_portfolio),
                               ('Plate recognition (ANPR)', self._open_anpr),
                               ("Knowledge Library", self._open_knowledge),
                               ("Security", self._open_security),
                               ("Business", self._open_business),
                               ("OSINT research", lambda: self._open_control_studio("osint"))):
            menu.add_command(label=label, command=command)
        studios["menu"] = menu
        studios.pack(side="left", padx=2)
        self._studios_menu = menu
        self.depth_nav.pack(fill="both", expand=True)
        if self.runtime.voice is not None:
            self.depth_nav.voice_levels = self.runtime.voice.levels

    def _show_welcome(self) -> None:
        visible = (self._welcome_pending or self._core_requested) and self.current_view == "chat"
        if visible:
            self._welcome.place(x=0, y=0, relwidth=1, relheight=1)
            if getattr(self, "suggestion_buttons", None) is not None:
                self._refresh_suggestions()
        else:
            self._welcome.place_forget()
            self._unseen_notices = 0
        self._label_core_button(visible)
        self._fit_conversation()

    def _label_core_button(self, workspace_visible: bool) -> None:
        # "Conversation · update" said neither what nor how many. A count of
        # the notices waiting behind the workspace says both.
        unseen = getattr(self, "_unseen_notices", 0)
        if not workspace_visible:
            text = "3D workspace"
        elif unseen:
            text = f"Conversation ({unseen} new)"
        else:
            text = "Conversation"
        self.core_button.configure(text=text)

    def _toggle_core(self) -> None:
        visible = bool(self._welcome.winfo_manager())
        self._welcome_pending = False
        self._core_requested = not visible
        self.show_view("chat")

    def _show_home(self) -> None:
        self._core_requested = True
        self.show_view('chat')

    def _open_earning_plan(self) -> None:
        self._open_revenue()
        if getattr(self, '_revenue_workspace', None) is not None:
            self._revenue_workspace.open_intake('income_execution_plan')

    def _jump_to_latest(self) -> None:
        """Leave other views and bring the newest conversation message into view."""
        if self._chat_find_open:
            self._close_chat_find()
        self._welcome_pending = False
        self._core_requested = False
        self.show_view("chat")
        self._unread_messages = 0
        self.latest_button.configure(text="Latest ↓")
        self.chat._smooth_scroller.cancel()
        self.chat.see("end")

    def _chat_scrolled(self, first: str, last: str) -> None:
        self.chat.vbar.set(first, last)
        from app.gui_motion import near_bottom
        if getattr(self, "_unread_messages", 0) and near_bottom(self.chat):
            self._unread_messages = 0
            self.latest_button.configure(text="Latest ↓")

    def _chat_follows(self) -> bool:
        from app.gui_motion import near_bottom
        return not (self._chat_find_open and self._chat_find_var.get()) and near_bottom(self.chat)

    def _open_capability(self, key: str) -> None:
        actions = {
            "listening": lambda: self._open_control_studio("voice"),
            "vision": self._open_vision_controls,
            "research": self._research,
            "reasoning": lambda: self.show_view("task_graph"),
            "automations": lambda: self.show_view("tasks"),
            "execute": lambda: self.show_view("toolbox"),
        }
        action = actions.get(key)
        if action:
            action()

    def _open_control_studio(self, page: str = "voice") -> None:
        from app.gui_studio import ControlStudio
        if getattr(self, "_control_studio", None) is None or not self._control_studio.winfo_exists():
            self._control_studio = ControlStudio(self)
        self._control_studio.show(page)

    #: Vision's actions: (label, method name).
    VISION_ACTIONS = (
        ("Attach image or document", "_add_files"),
        ("Turn camera on / off", "_toggle_camera"),
        ("Open camera preview", "open_camera_view"),
        ("Describe camera image", "_look"),
        ("Read current screen", "_read_screen"),
        ("Live screen & computer", "_open_workspace_tools"),
        ("Person detection", "_open_people_workspace"),
        ("Analyze a video", "_analyze_video"),
    )

    def _open_vision_controls(self) -> None:
        from app.gui_vision import show_vision_controls
        show_vision_controls(self)

    def _close_vision_controls(self) -> None:
        window = getattr(self, "_vision_window", None)
        if window is not None and window.winfo_exists():
            from app.gui_windows import cancel_pending_reveal
            cancel_pending_reveal(window)
            window.withdraw()
            self.root.focus_force()

    def _run_vision_action(self, method: str) -> None:
        # Out of the way first: the result lands in the conversation, and a
        # file picker or camera window should not open behind this one.
        self._close_vision_controls()
        getattr(self, method)()

    def _build_brain_strip(self, parent: tk.Misc) -> None:
        """Compact live command deck: useful intelligence, not invented dials."""
        outer = tk.Frame(parent, bg=self.PANEL, padx=12, pady=8,
                         highlightbackground=self.LINE, highlightthickness=1)
        outer.pack(fill="x", pady=(6, 2))
        self._brain_outer = outer
        heading = tk.Frame(outer, bg=self.PANEL)
        heading.pack(fill="x", pady=(0, 6))
        self.brain_overview = StringVar(self.root, "Workspace status")
        tk.Label(heading, textvariable=self.brain_overview, bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        self._brain_expanded = False
        self.brain_toggle = ttk.Button(heading, text="Show details", style="Hayat.TButton",
                                       command=self._toggle_brain_details)
        self.brain_toggle.pack(side="right")
        grid = tk.Frame(outer, bg=self.PANEL)
        self._brain_grid = grid
        self.brain_card_vars: dict[str, tuple[StringVar, StringVar]] = {}
        definitions = (
            ("memory", "MEMORY", "Recall index"),
            ("knowledge", "KNOWLEDGE", "Verified claims"),
            ("context", "CONTEXT", "Adaptive window"),
            ("prediction", "PREDICTION", "Prepared follow-ups"),
            ("skills", "SKILLS", "Review candidates"),
            ("operators", "OPERATORS", "Nodes / tools"),
            ("voice", "VOICE V2", "Local speech"),
            ("speed", "SPEED", "Answer reuse"),
        )
        for index, (key, title, subtitle) in enumerate(definitions):
            card = tk.Frame(grid, bg=self.PANEL_2, padx=9, pady=6,
                            highlightbackground=self.LINE, highlightthickness=1)
            card.grid(row=index // 4, column=index % 4, sticky="nsew", padx=2, pady=2)
            tk.Frame(card, bg=self.GOLD if index == 0 else self.ROYAL,
                     height=2).pack(fill="x", pady=(0, 5))
            tk.Label(card, text=title, bg=self.PANEL_2, fg=self.MUTED,
                     font=("Segoe UI Semibold", 7), anchor="w").pack(fill="x")
            primary = StringVar(master=self.root, value="--")
            detail = StringVar(master=self.root, value=subtitle)
            tk.Label(card, textvariable=primary, bg=self.PANEL_2, fg=self.WHITE,
                     font=("Segoe UI Semibold", 11), anchor="w").pack(fill="x")
            tk.Label(card, textvariable=detail, bg=self.PANEL_2, fg=self.MUTED,
                     font=("Segoe UI", 7), anchor="w").pack(fill="x")
            self.brain_card_vars[key] = (primary, detail)
            grid.grid_columnconfigure(index % 4, weight=1, uniform="brain")

    def _toggle_brain_details(self) -> None:
        self._brain_expanded = not self._brain_expanded
        if self._brain_expanded:
            self._brain_outer.pack(fill="x", pady=(6, 2), before=self._compose_hint)
            self._brain_grid.pack(fill="x", pady=(8, 0))
            if getattr(self, "_tools_expanded", False):
                self._toggle_tools_drawer()
        else:
            self._brain_grid.pack_forget()
            self._brain_outer.pack_forget()
        self.brain_toggle.configure(text="Hide details" if self._brain_expanded else "Show details")

    #: Suggestion chips above the message box: (label, request put in the box).
    SUGGESTIONS = (("World now", "What is happening around the world?"),
                   ("AI news", "Research the latest AI news"),
                   ("Business brief", "Show my business brief for today"),
                   ("Write a report", "Write a short report about "),
                   ("Plan my day", "Plan my day"),
                   ("Public cameras", "Find public cameras in London"))

    def _build_compose(self, parent: tk.Misc) -> None:
        """Controls, input box and hint line, packed bottom-up.

        Anchored to the bottom edge and built before the transcript, so the
        transcript expands into what is left instead of pushing the box you
        type into off the end of the window.
        """
        hint = tk.Frame(parent, bg=self.BLACK, pady=4)
        hint.pack(side="bottom", fill="x")
        self._compose_hint = hint
        self.activity_var = StringVar(master=self.root, value="Ready")
        tk.Label(hint, textvariable=self.activity_var, bg=self.BLACK, fg=self.GOLD_SOFT,
                 font=("Segoe UI", 9)).pack(side="left")
        self.reasoning_button = ttk.Button(hint, text='Beast mode', style='Hayat.TButton',
                                          command=self._toggle_god_mode)
        self.reasoning_button.pack(side='right', padx=(8, 0))
        self._paint_reasoning_mode()
        self.activity_progress_var = DoubleVar(master=self.root, value=0.0)
        progress = ttk.Progressbar(
            hint, variable=self.activity_progress_var, maximum=100,
            length=96, mode="determinate", style="Hayat.Horizontal.TProgressbar",
        )
        self._activity_progress = progress

        def show_progress(*_args: Any) -> None:
            # An empty bar beside "Ready" looked like something broken.
            # It appears only while there is progress to show.
            try:
                busy = 0 < float(self.activity_progress_var.get()) < 100
            except (tk.TclError, ValueError):
                busy = False
            if busy and not progress.winfo_manager():
                progress.pack(side="left", padx=(10, 0))
            elif not busy and progress.winfo_manager():
                progress.pack_forget()

        self.activity_progress_var.trace_add("write", show_progress)
        self.voice_hint = StringVar(self.root, "Mic off · Voice settings")
        voice_link = tk.Label(hint, textvariable=self.voice_hint, bg=self.BLACK,
                             fg=self.ROYAL_SOFT, font=("Segoe UI", 9), cursor="hand2")
        voice_link.pack(side="right", padx=(0, 8))
        voice_link.bind("<Button-1>", lambda _event: self._open_control_studio("voice"))

        compose_surface = DepthSurface(parent, type(self), padding=9)
        self._compose_surface = compose_surface
        compose_surface.pack(side="bottom", fill="x")
        # Starting points above the box: each fills the box for you to send or change.
        from app.gui_intelligence_parts import FlowRow
        # One row: HAYAT's own starting points (where we left off) come first,
        # and the fixed examples fill what is left.
        chips = FlowRow(parent, self.BLACK, gap=6, height=32, max_items=6)
        chips.pack(side="bottom", fill="x", pady=(0, 6))
        self._suggestion_chips = chips
        self.suggestion_buttons: dict[str, ttk.Button] = {}
        for label, request in self.SUGGESTIONS:
            button = chips.add(ttk.Button(chips, text=label, style="Compact.Hayat.TButton",
                                          command=lambda text=request: self.use_example(text)))
            self.suggestion_buttons[label] = button
        compose = tk.Frame(compose_surface.body, bg=self.PANEL)
        compose.pack(fill='both', expand=True)
        self.send_button = ttk.Button(compose, text="Send", style="HayatAccent.TButton",
                                      command=self.send)
        self.send_button.pack(side="right", padx=(9, 0), anchor="n")
        self.reply_stop_button = ttk.Button(compose, text="Stop reply", style="Hayat.TButton",
                                            command=self._cancel_reply, state="disabled")
        self.reply_stop_button.pack(side="right", padx=(9, 0), anchor="n")
        # Right where a file would be attached in any other chat.
        self.input = tk.Text(
            compose, height=2, wrap="word", bg=self.PANEL, fg=self.WHITE,
            insertbackground=self.GOLD, relief="flat", bd=0, padx=12, pady=8,
            font=("Segoe UI", 12), selectbackground=self.ROYAL, selectforeground=self.WHITE,
        )
        self.input.pack(side="left", fill="both", expand=True)
        self.input.bind("<Return>", self._enter)
        self.input.bind("<Shift-Return>", lambda event: None)
        from app.gui_composer import ComposerSizing
        self._composer_sizing = ComposerSizing(self.input)
        self._input_placeholder = tk.Label(self.input, text='Ask HAYAT · attach files or paste a link…   Enter sends · Shift+Enter: new line',
                                          bg=self.PANEL,
                                          fg=self.MUTED, font=('Segoe UI', 11), cursor='xterm', justify='left')
        self._input_placeholder.bind('<Button-1>', lambda _event: self.input.focus_set())
        def refresh_placeholder(_event=None):
            self._input_placeholder.configure(wraplength=max(120, self.input.winfo_width() - 28))
            if self.input.get('1.0', 'end-1c').strip() or self.input.focus_get() is self.input:
                self._input_placeholder.place_forget()
            else:
                self._input_placeholder.place(x=12, y=9)
        for sequence in ('<FocusIn>', '<FocusOut>', '<KeyRelease>', '<Configure>', '<<Modified>>'):
            self.input.bind(sequence, refresh_placeholder, add='+')
        refresh_placeholder()
        media_tools = FlowRow(compose_surface.body, self.PANEL, gap=5, height=32)
        self._composer_actions = media_tools
        media_tools.pack(fill='x', pady=(7, 0))
        # A standing instruction ("for the next two answers, reply with one
        # word") is visible while it lasts, and one click ends it.
        self.standing_chip = tk.Frame(compose_surface.body, bg=self.PANEL_2, padx=8, pady=3)
        self.standing_text = tk.Label(self.standing_chip, text="", bg=self.PANEL_2, fg=self.GOLD,
                                      font=("Segoe UI", 9), anchor="w")
        self.standing_text.pack(side="left")
        close = tk.Label(self.standing_chip, text="  ✕", bg=self.PANEL_2, fg=self.MUTED,
                         font=("Segoe UI Semibold", 9), cursor="hand2")
        close.pack(side="left")
        close.bind("<Button-1>", lambda _event: self._end_standing_instruction())
        self._refresh_standing_chip()
        self.composer_buttons = {}
        for label, command in (
                ('Attach', self._add_files), ('Image', lambda: self._add_files('image')),
                ('Video', self._open_video_studio), ('Mic', self._toggle_voice),
                ('Web search', self._research), ('Tools', self._toggle_tools_drawer)):
            button = media_tools.add(ttk.Button(media_tools, text=label, command=command,
                                                style='Compact.Hayat.TButton'))
            self.composer_buttons[label] = button
        self._composer_more = ttk.Menubutton(media_tools, text="Actions ▾", style="Compact.Hayat.TButton")
        composer_menu = tk.Menu(self._composer_more, tearoff=0, bg=self.PANEL_2, fg=self.WHITE,
                                activebackground=self.PANEL_HOVER, activeforeground=self.WHITE)
        for label, button in self.composer_buttons.items():
            composer_menu.add_command(label=label, command=button.invoke)
        composer_menu.add_command(label='Research an image…', command=self._research_image)
        self._composer_more.configure(menu=composer_menu)
        media_tools.add(self._composer_more)
        self._composer_actions_default = list(media_tools.items)
        media_tools.max_items = len(media_tools.items) - 1
        self.draft_status = StringVar(self.root, value="")
        draft_label = ttk.Label(parent, textvariable=self.draft_status, font=("Segoe UI", 8))
        self._draft_label = draft_label
        def show_draft_status(*_args: Any) -> None:
            if self.draft_status.get() and not getattr(self, "_inspection_active", False):
                draft_label.pack(side="bottom", anchor="w", before=compose_surface)
            else:
                draft_label.pack_forget()
        self.draft_status.trace_add("write", show_draft_status)
        from app.gui_workspace import StreamingPreview
        self._stream_preview = StreamingPreview(parent, type(self))
        from app.gui_rich import WorkingView
        self._working_view = WorkingView(parent, type(self))

        # A visible target makes the capability discoverable. The whole
        # window still accepts a drop; this strip confirms what will happen
        # and reports whether the files were actually retained.
        self.drop_zone = tk.Frame(parent, bg=self.PANEL_2, padx=10, pady=5,
                                  cursor="hand2", highlightthickness=1, highlightbackground=self.LINE)
        # Attach file and window-wide drop handling remain available in the composer.
        self.drop_icon = tk.Label(
            self.drop_zone, text="⇩", bg=self.PANEL_2, fg=self.GOLD,
            font=("Segoe UI Semibold", 11), cursor="hand2",
        )
        self.drop_icon.pack(side="left", padx=(0, 7))
        self.attachment_status = StringVar(
            master=self.root,
            value="Drop files or images anywhere · HAYAT will read and remember them",
        )
        self.drop_status_label = tk.Label(
            self.drop_zone, textvariable=self.attachment_status,
            bg=self.PANEL_2, fg=self.MUTED, font=("Segoe UI", 9),
            anchor="w", cursor="hand2",
        )
        self.drop_status_label.pack(side="left", fill="x", expand=True)
        self.drop_capability = tk.Label(
            self.drop_zone,
            text="DROP READY" if self.drag_and_drop else "USE ＋",
            bg=self.PANEL_2, fg=self.OLIVE_SOFT if self.drag_and_drop else self.MUTED,
            font=("Segoe UI Semibold", 8), cursor="hand2",
        )
        self.drop_capability.pack(side="right")
        for widget in (self.drop_zone, self.drop_icon, self.drop_status_label,
                       self.drop_capability):
            widget.bind("<Button-1>", lambda _event: self._add_files())

        controls = tk.Frame(parent, bg=self.BLACK)
        controls.pack(side="bottom", fill="x", pady=(4, 6))
        self._compose_controls = controls
        self._tools_expanded = False
        self._tools_window = tk.Toplevel(self.root)
        self._tools_window.withdraw()
        self._tools_window.title("HAYAT · Tools & controls")
        self._tools_window.transient(self.root)
        self._tools_window.geometry("620x420")
        self._tools_window.minsize(520, 300)
        self._tools_window.configure(bg=self.BLACK)
        self._tools_window.protocol("WM_DELETE_WINDOW", self._toggle_tools_drawer)
        self._tools_window.bind("<Escape>", lambda _event: (self._toggle_tools_drawer(), "break")[1])
        tools_heading = tk.Frame(self._tools_window, bg=self.BLACK)
        tools_heading.pack(fill="x", padx=18, pady=(16, 4))
        tk.Label(tools_heading, text="Tools & controls", bg=self.BLACK, fg=self.WHITE,
                 font=(DISPLAY, 16), anchor="w").pack(side="left")
        # A close button of its own: the title bar can be under a docked bar.
        ttk.Button(tools_heading, text="✕  Close", width=0, style="Hayat.TButton",
                   command=lambda: self._tools_expanded and self._toggle_tools_drawer()).pack(side="right")
        tk.Label(self._tools_window, text="Choose an action. Your conversation stays open.",
                 bg=self.BLACK, fg=self.MUTED, font=("Segoe UI", 10), anchor="w").pack(fill="x", padx=18, pady=(0, 12))
        self._tools_drawer = ScrollableColumn(self._tools_window, type(self), width=560)
        self._tools_drawer.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        quick = tk.Frame(controls, bg=self.BLACK)
        quick.pack(fill="x")
        self._quick_actions = quick
        self.buttons: dict[str, ttk.Button] = {}
        quick_labels = {"+ Add file": "Attach file", "Voice": "Voice", "Image": "Create image",
                        "Research": "Research", "Stop": "Stop"}
        tool_labels = {"Hands-free": "Hands-free voice", "View": "Camera window", "Look": "Describe camera",
                       "Screen": "Read screen", "Clipboard": "Read clipboard", "Remote": "Phone access",
                       "Release RAM": "Free memory", "Operator": "System map", "Self Check": "Check system",
                       "Tools": "Available tools", "Memory": "Saved memory", "Training": "Training review",
                       "Improve": "Improvements", "Theme": "Appearance"}
        quick_index = other_index = 0
        for label, method, _group in self.CONTROLS:
            is_quick = label in quick_labels
            holder = quick if is_quick else self._tools_drawer.body
            button = ttk.Button(holder, text=quick_labels.get(label, tool_labels.get(label, label)), width=0,
                                style="Hayat.TButton", command=getattr(self, method))
            index = quick_index if is_quick else other_index
            button.grid(row=0 if is_quick else index // 3,
                        column=index if is_quick else index % 3, sticky="ew", padx=3, pady=3)
            self.buttons[label] = button
            if is_quick:
                quick_index += 1
            else:
                other_index += 1
        ttk.Button(self._tools_drawer.body, text="Workspace status", style="Hayat.TButton",
                   command=self._toggle_brain_details).grid(row=(other_index + 2) // 3, column=0,
                                                            sticky="ew", padx=3, pady=3)
        self.more_tools_button = ttk.Button(quick, text="More tools", width=0, style="Hayat.TButton",
                                            command=self._toggle_tools_drawer)
        self.more_tools_button.grid(row=0, column=quick_index, sticky="ew", padx=3, pady=3)
        for column in range(quick_index + 1):
            quick.grid_columnconfigure(column, weight=1, uniform="quick")
        for column in range(3):
            self._tools_drawer.body.grid_columnconfigure(column, weight=1, uniform="tools")
        self._tools_drawer.adopt(self._tools_drawer.body)
        self.voice_button = self.buttons["Voice"]
        self.privacy_button = self.buttons["Privacy"]
        self.camera_button = self.buttons["Camera"]
        # The quick-action row folds away in a running conversation; every
        # action in it stays one click away here.
        self.shortcut_menu.add_separator()
        for label, key in (("Attach file", "+ Add file"), ("Create image", "Image"), ("Start voice", "Voice")):
            if key in self.buttons:
                self.shortcut_menu.add_command(label=label, command=self.buttons[key].invoke)
        self.shortcut_menu.add_command(label="All tools & controls", command=self._toggle_tools_drawer)

        self.input.focus_set()
        self._bind_shortcuts()
        # Every main button explains itself on hover, once the window is laid out.
        self._schedule(400, self._attach_tooltips)
        # A prepared outward action (publish, register, submit) opens its
        # confirmation window at once, so the owner sees it before anything is sent.
        gate = getattr(self.runtime, "confirmations", None)
        if gate is not None:
            gate.listeners.append(lambda action: self._post(self._show_confirmation, action.id))
        # Today's brief, shown once a day when something needs attention. Reads only.
        self._schedule(20_000, self._business_brief_check)

    def _toggle_tools_drawer(self) -> None:
        self._tools_expanded = not self._tools_expanded
        if self._tools_expanded:
            from app.gui_windows import place_over

            place_over(self._tools_window, self.root, (620, 420))
            self._tools_window.deiconify()
            self._tools_window.lift()
        else:
            self._tools_window.withdraw()
        self.more_tools_button.configure(text="Close tools" if self._tools_expanded else "More tools")

    #: Keys for the things reached most often. Bound on the window rather than
    #: the text box so they work wherever the focus happens to be -- a
    #: shortcut that only fires when the cursor is in the right widget is a
    #: shortcut you stop trusting.
    SHORTCUTS = (
        ("<Control-f>", "_open_chat_find"),
        ("<Control-Shift-A>", "_open_features"),
        ("<Control-Shift-L>", "_open_activity"),
        ("<Control-Shift-P>", "_show_command_palette"),
        ("<Control-Shift-K>", "_focus_workspace_search"),
        ("<Control-grave>", "_open_window_switch"),
        ("<Control-k>", "_toggle_camera"),
        ("<Control-l>", "_look"),
        ("<Control-d>", "_read_screen"),
        ("<Control-t>", "_show_tools"),
        ("<Control-p>", "_toggle_privacy"),
        ("<Control-h>", "_show_doctor"),
        ("<Control-m>", "_show_memory"),
        ("<Control-e>", "save_transcript"),
        ("<Control-Shift-F>", "_toggle_focus_mode"),
        ("<F11>", "_toggle_focus_mode"),
        ("<F1>", "_open_help"),
        ("<Control-slash>", "_show_shortcuts"),
        ("<Escape>", "_stop"),
    )

    #: Plain names for the shortcut sheet, so a binding is discoverable and not
    #: only a key someone has to already know. Keyed by the bound method.
    SHORTCUT_LABELS = {
        "_open_chat_find": "Find in conversation",
        "_open_features": "Open the features panel",
        "_open_activity": "Show the activity log",
        "_show_command_palette": "Command palette",
        "_focus_workspace_search": "Search the workspace",
        "_open_window_switch": "Switch between open windows",
        "_toggle_camera": "Camera on / off",
        "_look": "Look through the camera",
        "_read_screen": "Read the screen",
        "_show_tools": "Show tools",
        "_toggle_privacy": "Privacy mode on / off",
        "_show_doctor": "Diagnostics",
        "_show_memory": "Show memory",
        "save_transcript": "Export conversation",
        "_toggle_focus_mode": "Focus mode",
        "_open_help": "Help — what can HAYAT do?",
        "_show_shortcuts": "This shortcut list",
        "_stop": "Stop the current work",
    }

    @staticmethod
    def _pretty_key(sequence: str) -> str:
        """"<Control-Shift-P>" -> "Ctrl+Shift+P"; readable, not Tk's event syntax."""
        names = {"Control": "Ctrl", "Shift": "Shift", "Alt": "Alt",
                 "slash": "/", "Escape": "Esc", "space": "Space"}
        return "+".join(names.get(part, part.upper() if len(part) == 1 else part)
                        for part in sequence.strip("<>").split("-"))

    def _bind_shortcuts(self) -> None:
        for sequence, method in self.SHORTCUTS:
            handler = getattr(self, method)
            self.root.bind_all(sequence, lambda event, run=handler: (run(), "break")[1])

    def _focus_workspace_search(self) -> None:
        if self._focus_mode:
            self._focus_mode = False
            self.focus_mode_button.configure(text="Focus Mode")
            self._arrange_rails()
        self.workspace_navigation.entry.focus_set()
        self.workspace_navigation.entry.selection_range(0, "end")

    def _show_command_palette(self) -> None:
        from app.gui_workspace import CommandPalette, WorkspaceCommand
        if self._command_palette is not None and self._command_palette.winfo_exists():
            self._command_palette.lift()
            self._command_palette.entry.focus_set()
            return
        commands = [WorkspaceCommand(label, getattr(self, method), keywords)
                    for label, method, keywords in (
                        ("Clear conversation", "clear_chat", "new chat"),
                        ("Attach files", "_add_files", "open image document video"),
                        ("Show memory", "_show_memory", "knowledge recall"),
                        ("Refresh models", "refresh_models", "provider health"),
                        ("Read screen", "_read_screen", "screenshot vision"),
                        ("Toggle voice", "_toggle_voice", "microphone speech"),
                        ("Show tools", "_show_tools", "capabilities"),
                        ("Show diagnostics", "_show_doctor", "health errors"),
                        ("Open settings", "_show_settings", "preferences"),
                        ("Focus workspace", "_toggle_focus_mode", "panels fullscreen"),
                        ("Export conversation", "save_transcript", "save history"),
                        ("Stop current work", "_stop", "cancel"),
                    )]
        commands.append(WorkspaceCommand("Recover previous unsent draft", self._draft.recover_previous, "restore restart"))
        commands.append(WorkspaceCommand("Appearance", self._show_appearance, "theme light dark contrast"))
        commands.append(WorkspaceCommand("Run PowerShell", lambda: self._compose_plan("Run PowerShell: "),
                                         "terminal command script shell pwsh"))
        commands.append(WorkspaceCommand("Run CMD", lambda: self._compose_plan("Run CMD: "),
                                         "terminal command prompt shell"))
        commands.append(WorkspaceCommand("Analyze video scenes and audio", self._analyze_video, "video transcript timeline"))
        commands.append(WorkspaceCommand("Download videos and make clips", self._open_video_studio,
                                         "video download youtube reels shorts viral clipping quality"))
        commands.append(WorkspaceCommand("Start at login", self._show_startup_status, "startup autostart restart"))
        commands.append(WorkspaceCommand("Learn Arabic", self._open_learn, "language lesson course alphabet teach study"))
        commands.append(WorkspaceCommand("Searches", self._open_searches, "research search results running find"))
        commands.append(WorkspaceCommand("Reverse engineering", self._open_reverse_engineer,
                                         "rea binary decompile javascript electron artifact assembly inspect"))
        commands.append(WorkspaceCommand("Knowledge Library", self._open_knowledge, "knowledge facts sources evidence learn freshness"))
        commands.append(WorkspaceCommand("Security", self._open_security, "security intrusion ban lockout scan firewall attack"))
        commands.append(WorkspaceCommand("Around me (hazard radar)", self._open_radar,
                                         "radar around me near me hazards danger safe earthquake storm fire flood nearby"))
        commands.append(WorkspaceCommand("Location privacy", self._open_location_privacy,
                                         "location privacy track tracking gps photo who can see where am i hide nomap vpn"))
        commands.append(WorkspaceCommand("Router & home network", self._open_router,
                                         "router gateway modem 5g signal internet wifi network devices channel latency"))
        commands.append(WorkspaceCommand("Wi-Fi sensing", self._open_sensing,
                                         "wifi csi channel state information esp32 motion movement presence sensing radar"))
        commands.append(WorkspaceCommand("Pending confirmations", lambda: self._show_confirmation(),
                                         "confirm confirmation pending approve publish register submit form"))
        commands.append(WorkspaceCommand("What can HAYAT do? (Help)", self._open_help,
                                         "help guide beginner start capabilities abilities how to use what can"))
        commands.append(WorkspaceCommand("Keyboard shortcuts", self._show_shortcuts,
                                         "keyboard shortcuts keys hotkeys bindings cheat sheet accelerators"))
        commands.append(WorkspaceCommand("Business", self._open_business,
                                         "business money invoice client income expense profit lead earn tax"))
        commands.append(WorkspaceCommand("Languages & persona", self._open_languages,
                                         "language install voice persona human reply software winget"))
        for label, opener, words in (
                ("Command Center", self._open_command_center, "dashboard overview live everything mission control"),
                ("Downloads & web activity", self._open_downloads, "downloads download history transfers network web requests sites"),
                ("Trade Center", self._open_markets, "markets crypto stocks bitcoin chart scenarios odds prediction heatmap"),
                ("Web3 explorer & agentic web", self._open_web3, "blockchain address transaction ens defi web4 llms.txt crawlers"),
                ("Accounts & sign-in", self._open_accounts, "gmail outlook github linkedin slack notion connect login sign in"),
                ("Jobs", self._open_jobs, "jobs job search cv resume apply application career vacancy hiring match"),
                ('Operation view', self._open_operation_preview, 'desktop computer window live preview actions monitor'),
                ('Private networking', self._open_networking, 'contacts networking collaboration outreach friendship follow up'),
                ('Onion page navigator', self._open_onion_navigation, 'tor onion darknet navigate scrape links public text'),
                ("HAYAT Wallet", self._open_money_desk, "wallet pots bills budget money decisions approve afford set aside savings"),
                ("Portfolio & net worth", self._open_portfolio, "portfolio net worth holdings stocks crypto cash usd assets"),
                ("Global intelligence", self._open_intelligence, "world events earthquakes cameras map news"),
                ("World atlas (browser)", self._open_atlas, "map browser trade chokepoints video wall"),
                ("Keys", self._open_keys, "api keys 511 wsdot finnhub")):
            commands.append(WorkspaceCommand(label, opener, words))
        commands.extend(WorkspaceCommand("Open " + key, lambda name=key: self.show_view(name), "workspace search")
                        for key in ("memory", "history", "tasks", "task_graph", "gallery", "models", "background"))
        from app.gui_features import inventory_commands
        commands.extend(inventory_commands(self))
        commands = list({command.label.casefold(): command for command in commands}.values())
        self._command_palette = CommandPalette(self.root, commands)

    def _toggle_focus_mode(self) -> None:
        """Give chat/workspace the full window without losing rail state."""
        self._focus_mode = not self._focus_mode
        self.focus_mode_button.configure(text="Restore Rails" if self._focus_mode else "Focus Mode")
        self._arrange_rails()

    def _on_window_resize(self, event: Any) -> None:
        if event.widget is not self.root or self._closed or event.width < 100:
            return
        self._fit_accessible_controls()
        self._fit_very_short(self.root.winfo_height())
        # Configure events can be queued for an earlier size. Rail arranging
        # also pumps idle geometry, which may deliver a newer event recursively.
        # Apply the current size, and let a newer layout supersede this one.
        width, height = self.root.winfo_width(), self.root.winfo_height()
        self._overview_layout.reflow()
        state = (width < 1380, height < 800)
        previous = self._layout_state
        tall_before = getattr(self, "_window_height", 0) >= self.TALL_WINDOW
        self._window_height = height
        if state == previous:
            if tall_before != (height >= self.TALL_WINDOW):
                self._fit_conversation()
            return
        self._layout_state = state
        self._compact_layout = state[0]
        if previous is None or previous[0] != state[0]:
            self._overview_open = self._overview_layout.preferred_open(state[0])
            self._arrange_rails(settle=False)
            if self._layout_state != state:
                return
            # The workspace tabs need width; a narrower window keeps the live-state
            # line readable instead (the sidebar and Studios reach the same places).
            for button in self.nav_buttons.values():
                if state[0]:
                    button.pack_forget()
                elif not button.winfo_manager():
                    button.pack(side='left', padx=(0, 4), before=self._transparency_words)
            if state[0]:
                self.workspace_overflow.pack(side='left', padx=(0, 6), before=self._transparency_words)
            else:
                self.workspace_overflow.pack_forget()
        if state[1]:
            # Narrow intermediate allocations can wrap the live-state label
            # into many lines and leave no room for a running conversation.
            # Activity and the tooltip retain the full sensor/privacy state.
            self._transparency_state_label.configure(height=1 if getattr(self, '_very_short', False) else 2)
            self._brain_outer.pack_forget()
            self.drop_zone.pack_forget()
            self._transparency_last_label.pack_forget()
            self._transparency_bar.configure(pady=2)
            self._centre.configure(pady=6)
        else:
            self._transparency_state_label.configure(height=0)
            self._transparency_last_label.pack(fill='x')
            self._transparency_bar.configure(pady=6)
            self._centre.configure(pady=10)
            if self._brain_expanded:
                self._brain_outer.pack(fill="x", pady=(6, 2), before=self._compose_hint)
        # Suggestion chips are a convenience; a short window keeps the chat instead.
        self._fit_conversation()

    #: From this height the quick-action row stays even in a running conversation.
    TALL_WINDOW = 1000
    #: Below this height (200 % display scaling on a 1080p panel is 540 logical
    #: pixels) the fixed rows left the conversation about 50 px tall.
    VERY_SHORT_WINDOW = 600

    def _fit_very_short(self, height: int) -> None:
        very_short = height < self.VERY_SHORT_WINDOW
        if self._closed or getattr(self, "_very_short", None) == very_short or not hasattr(self, "input"):
            return
        self._very_short = very_short
        # The date is also in the status bar's clock; the live-state line keeps
        # one row and its tooltip; Beast mode keeps its place at a compact size.
        if very_short:
            self._header_clock_label.pack_forget()
        else:
            self._header_clock_label.pack(fill="x")
        self._transparency_state_label.configure(height=1 if very_short else 2)
        self.reasoning_button.configure(style="Compact.Hayat.TButton" if very_short else "Hayat.TButton")
        self._compose_hint.configure(pady=1 if very_short else 4)
        # The composer sizes the message box (one line here). The status bar
        # returns on a taller window; the live-state line, Activity and the
        # voice line carry the same state meanwhile.
        bar = getattr(self, "_status_bar", None)
        if bar is None:
            return
        if very_short and bar.winfo_manager():
            slaves = self.root.pack_slaves()
            index = slaves.index(bar)
            self._status_bar_before = slaves[index + 1] if index + 1 < len(slaves) else None
            bar.pack_forget()
        elif not very_short and not bar.winfo_manager():
            before = getattr(self, "_status_bar_before", None)
            options = {"before": before} if before is not None and before.winfo_manager() else {}
            bar.pack(side="bottom", fill="x", **options)

    def _fit_accessible_controls(self) -> None:
        if self._closed or not hasattr(self, "_composer_actions_default"):
            return
        # Short windows need the same primary controls at normal text size:
        # Windows DPI scaling can make every toolbar consume several rows.
        compact = self.root.winfo_height() < 800
        if getattr(self, "_accessible_compact_controls", None) == compact:
            return
        self._accessible_compact_controls = compact
        self._conversation_header.body.configure(pady=3 if compact else 7)
        self._compose_surface.body.configure(pady=3 if compact else 9)
        self.input.configure(pady=5 if compact else 8)
        for key, pill in self.pills.items():
            pill.pack_forget()
            if not compact or key in {"voice", "models"}:
                pill.pack(side="left")
        if compact:
            self.status_more_button.pack(side="left", padx=4)
        else:
            self.status_more_button.pack_forget()
        if compact:
            for button in (self.help_button, self.overview_button, self.focus_mode_button):
                button.pack_forget()
            self.header_more_button.pack(side="right")
            primary = [self._header_actions_default[0], self._header_actions_default[-1]]
            self._header_actions.items = primary + [item for item in self._header_actions_default if item not in primary]
            self._header_actions.max_items = len(primary)
            primary = [self._conversation_actions_default[0], self.chat_actions_button, self.core_button]
            self._conversation_actions.items = primary + [item for item in self._conversation_actions_default if item not in primary]
            self._conversation_actions.max_items = len(primary)
            primary = [self.composer_buttons["Attach"], self.composer_buttons["Mic"], self._composer_more]
            self._composer_actions.items = primary + [item for item in self._composer_actions_default if item not in primary]
            self._composer_actions.max_items = len(primary)
        else:
            self.header_more_button.pack_forget()
            self._header_actions.items = list(self._header_actions_default)
            self._header_actions.max_items = None
            for button in (self.focus_mode_button, self.overview_button, self.help_button):
                button.pack(side="right", padx=(10, 0))
            self._conversation_actions.items = list(self._conversation_actions_default)
            self._conversation_actions.max_items = None
            self._composer_actions.items = list(self._composer_actions_default)
            self._composer_actions.max_items = len(self._composer_actions_default) - 1
        self._header_actions.arrange()
        self._conversation_actions.arrange()
        self._composer_actions.arrange()
        self._fit_conversation()

    def _fit_conversation(self) -> None:
        """Give a running conversation the room the starting points were using.

        Measured on the owner's screen (1920x1200 at 125%, maximized): the
        transcript got about 300 of 900 pixels -- eight lines -- under five
        rows of buttons. The quick actions and suggestion chips are starting
        points: they stay on the empty-chat screen, and fold away once a
        conversation is running, unless the window is tall enough for both.
        Nothing is lost -- each quick action is also in "More actions", and
        the starting points come back with the workspace.
        """
        if self._closed or getattr(self, "_inspection_active", False) or not hasattr(self, "_compose_controls"):
            return
        started = not (self._welcome_pending or self._core_requested)
        short = bool(self._layout_state and self._layout_state[1])
        tall = getattr(self, "_window_height", 0) >= self.TALL_WINDOW
        show_chips = not short and not started
        show_quick = not started or tall
        home = not started and self.current_view == 'chat'
        details = getattr(self, 'chat_details', None)
        if details is not None:
            if self.current_view == 'chat' and started:
                siblings = [item for item in details.master.pack_slaves() if item is not details]
                details.pack(fill='x', pady=(0, 8), **({'before': siblings[0]} if siblings else {}))
            else:
                details.pack_forget()
        if home or (short and getattr(self, "_accessible_compact_controls", False)):
            # Home already has capability cards and task shortcuts. Repeated
            # toolbar rows used to squeeze the 3D scene into a 100px strip.
            show_chips = show_quick = False
            self._chat_tabs.pack_forget()
            if home:
                self._conversation_header.pack_forget()
        elif not self._chat_tabs.winfo_manager():
            options = {'before': self.chat.frame} if self.chat.frame.winfo_manager() == 'pack' else {}
            self._chat_tabs.pack(fill='x', pady=(4, 6), **options)
        if not home and not self._conversation_header.winfo_manager():
            siblings = self._centre.pack_slaves()
            self._conversation_header.pack(fill='x', **({'before': siblings[0]} if siblings else {}))
        chips, controls = self._suggestion_chips, self._compose_controls
        if bool(chips.winfo_manager()) == show_chips and bool(controls.winfo_manager()) == show_quick:
            return
        for widget in (chips, controls):
            widget.pack_forget()
        previous = self._compose_surface
        if show_chips:
            chips.pack(side="bottom", fill="x", pady=(0, 6), after=previous)
            previous = chips
        if show_quick:
            controls.pack(side="bottom", fill="x", pady=(4, 6), after=previous)

    def _toggle_overview(self) -> None:
        if self._focus_mode:
            self._focus_mode = False
            self.focus_mode_button.configure(text="Focus Mode")
            self._overview_open = True
        else:
            self._overview_open = not getattr(self, "_overview_open", True)
        self._overview_layout.remember_open(self._overview_open)
        self._arrange_rails()

    def _arrange_rails(self, *, settle: bool = True) -> None:
        self._centre.pack_forget()
        self._sidebar.pack_forget()
        self.right_rail.pack_forget()
        self.right_rail.place_forget()
        self._overview_layout.hide_grip()
        overview = getattr(self, "_overview_open", True) and not self._focus_mode
        compact = getattr(self, "_compact_layout", False)
        if not self._focus_mode:
            self._sidebar.pack(side="left", fill="y")
        if overview and not compact:
            self.right_rail.pack(side="right", fill="y")
            self._overview_layout.dock_grip()
        self._centre.pack(side="left", fill="both", expand=True)
        if overview and compact:
            self.right_rail.place(relx=1, y=0, anchor="ne", width=self._overview_layout.width(), relheight=1)
            self.right_rail.lift()
        self._overview_layout.reflow()
        self.overview_button.configure(text="Hide overview" if overview else "Show overview")
        if settle:
            self.root.update_idletasks()
        if overview:
            self._restore_right_rail_canvas()

    def _restore_right_rail_canvas(self) -> None:
        if self._closed or self._focus_mode:
            return
        self.right_rail.canvas.itemconfigure(self.right_rail._window, state="normal")

    # ----------------------------------------------------------- right column

    def _build_panel_bulk_controls(self, column: tk.Misc) -> None:
        """One line above the fourteen panels: collapse or open them all at once.

        Folding is per-panel and remembered, which is right day to day, but after
        opening several to look something up there was no way back to the compact
        column except folding each one. These two do it in a keystroke, and are
        reachable by Tab then Enter/Space like the panel titles themselves.
        """
        from app.gui_help import Tooltip

        bg = column.cget("bg")
        bar = tk.Frame(column, bg=bg)
        bar.pack(fill="x", pady=(0, 4))

        def control(text: str, tip: str, command: Any) -> tk.Label:
            label = tk.Label(bar, text=text, bg=bg, fg=self.MUTED, font=("Segoe UI", 9),
                             cursor="hand2", takefocus=1, padx=6)
            label.pack(side="right")
            for sequence in ("<Button-1>", "<Return>", "<space>"):
                label.bind(sequence, lambda _event, run=command: run())
            label.bind("<FocusIn>", lambda _event, w=label: w.configure(fg=self.WHITE))
            label.bind("<FocusOut>", lambda _event, w=label: w.configure(fg=self.MUTED))
            label._bulk_tip = Tooltip(label, tip, type(self))
            return label

        self._expand_all_button = control("▼ Expand all", "Open every panel in this column.", self._expand_all_panels)
        self._collapse_all_button = control("▶ Collapse all", "Fold every panel to its one-line summary.", self._collapse_all_panels)

    def _collapse_all_panels(self) -> None:
        self._folds.fold_all()

    def _expand_all_panels(self) -> None:
        self._folds.unfold_all()

    def _build_right(self, parent: tk.Misc) -> None:
        rail = ScrollableColumn(parent, type(self), width=404)
        rail.pack(side="right", fill="y")
        column = rail.body
        self.right_rail = rail
        self._build_panel_bulk_controls(column)
        from app.gui_chat_details import ChatDetails
        self.chat_details = ChatDetails(column, self)

        resources = section(column, type(self), fold=self._folds, title="System • live resources",
                            action=("Details", lambda: self.show_view("health")))
        resources.pack(fill="x", pady=(0, 8))
        self.resource_charts = ResourceCharts(resources.body, type(self))
        self.resource_charts.pack(fill="x")

        # Global intelligence lives beside the workspace, with a mini world map
        # of the newest located events; the 3D workspace keeps the centre.
        if getattr(self.runtime, 'intelligence', None) is not None:
            from app.gui_intelligence_home import IntelligenceHomeCard
            self._intelligence_home = IntelligenceHomeCard(column, self, pack={'fill': 'x', 'pady': (0, 8)},
                                                         show_map=True, movable=True)

        # -- everything running, foreground and background. The owner asked
        # that nothing run out of sight: every tool, model call, search and
        # learning cycle is listed here while it runs, with how long so far.
        running = section(column, type(self), fold=self._folds, title="Running now · nothing hidden",
                          action=("Report", self._report_running))
        running.pack(fill="x", pady=(0, 8))
        self.running_text = tk.Text(running.body, height=2, wrap="word", state="disabled", bg=self.PANEL,
                                    fg=self.WHITE, relief="flat", font=("Segoe UI", 9), highlightthickness=0)
        self.running_text.tag_configure("idle", foreground=self.MUTED)
        self.running_text.tag_configure("background", foreground=self.GOLD_SOFT)
        self.running_text.tag_configure("teams", foreground=self.GOLD, underline=False)
        self.running_text.tag_bind("teams", "<Button-1>", lambda _event: self._open_team_workspace())
        self.running_text.tag_bind("teams", "<Enter>", lambda _event: self.running_text.configure(cursor="hand2"))
        self.running_text.tag_bind("teams", "<Leave>", lambda _event: self.running_text.configure(cursor=""))
        self.running_text.pack(fill="x")
        self._running_painted = ""

        # -- camera preview
        camera = section(column, type(self), fold=self._folds, title="HAYAT's eye · Camera",
                         action=("Open window", self.open_camera_view))
        camera.pack(fill="x")
        head = tk.Frame(camera.body, bg=self.PANEL)
        head.pack(fill="x")
        self.camera_eye = CameraEye(head, type(self))
        self.camera_eye.pack(side="left", padx=(0, 8))
        self.preview_dot = tk.Label(head, text="●", bg=self.PANEL, fg=self.MUTED,
                                    font=("Segoe UI", 9))
        self.preview_dot.pack(side="left")
        self.preview_detail = StringVar(master=self.root, value="camera off")
        tk.Label(head, textvariable=self.preview_detail, bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 8)).pack(side="left", padx=6)
        # Watch mode must never be invisible: while HAYAT is watching on its
        # own, this says so in gold beside the camera.
        self.watch_badge = tk.Label(head, text="", bg=self.PANEL, fg=self.GOLD, font=("Segoe UI Semibold", 8))
        self.watch_badge.pack(side="left", padx=(2, 0))
        self.watch_button = ttk.Button(head, text="Watch mode", width=0, style="Hayat.TButton",
                                       command=self._toggle_watch_mode)
        self.watch_button.pack(side="right")
        # A fixed box, and the picture sits inside it.
        #
        # This flickered badly, and the reason is a genuine trap: a Label's
        # `height` counts text lines when it holds text and *pixels* when it
        # holds an image. `height=7` therefore meant about a hundred pixels
        # between frames and seven pixels during them, so the panel snapped
        # between two sizes fifteen times a second and dragged the whole
        # scrolling column through a relayout each time.
        #
        # The box is sized once, in pixels, and never resizes. Whatever is
        # inside it -- a frame, a message, nothing -- cannot move anything
        # else on screen.
        self.preview_box = tk.Frame(camera.body, bg=self.BLACK,
                                    width=self.PREVIEW_PIXELS[0],
                                    height=self.PREVIEW_PIXELS[1])
        self.preview_box.pack(pady=(6, 0))
        self.preview_box.pack_propagate(False)
        self.preview = tk.Label(self.preview_box, bg=self.BLACK, fg=self.MUTED,
                                text="Camera is off.\nPress Camera to switch it on.",
                                font=("Segoe UI", 9), bd=0, highlightthickness=0)
        self.preview.place(relx=0.5, rely=0.5, anchor="center")

        # -- sensors. Kept beside the camera because this is the same
        # question asked more broadly: what can HAYAT perceive right now.
        sensors = section(column, type(self), fold=self._folds, title="Sensors")
        sensors.pack(fill="x", pady=(10, 0))
        self.sensor_vars: dict[str, StringVar] = {}
        self.sensor_labels: dict[str, tk.Label] = {}
        grid = tk.Frame(sensors.body, bg=self.PANEL)
        grid.pack(fill="x")
        for index, (key, name) in enumerate(
                (("camera", "Camera"), ("microphone", "Mic"), ("screen", "Screen"),
                 ("clipboard", "Clipboard"), ("vision", "Vision"), ("browser", "Browser"), ("wifi", "Wi-Fi"))):
            variable = StringVar(master=self.root, value=f"{name} …")
            label = tk.Label(grid, textvariable=variable, bg=self.PANEL_2, fg=self.MUTED,
                             font=("Segoe UI", 9), padx=9, pady=5, anchor="w")
            label.grid(row=index // 3, column=index % 3, sticky="nsew", padx=2, pady=2)
            self.sensor_vars[key] = variable
            self.sensor_labels[key] = label
        for column_index in range(3):
            grid.grid_columnconfigure(column_index, weight=1)

        # -- Wi-Fi sensing: the movement CSI sees, live, beside what the camera sees.
        wifi = section(column, type(self), fold=self._folds, title="Wi-Fi sensing", action=("Open window", self._open_sensing))
        wifi.pack(fill="x", pady=(10, 0))
        wifi_head = tk.Frame(wifi.body, bg=self.PANEL)
        wifi_head.pack(fill="x")
        self.wifi_dot = tk.Label(wifi_head, text="●", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9))
        self.wifi_dot.pack(side="left")
        self.wifi_status_var = StringVar(master=self.root, value="Not connected")
        tk.Label(wifi_head, textvariable=self.wifi_status_var, bg=self.PANEL, fg=self.MUTED, anchor="w",
                 font=("Segoe UI", 8)).pack(side="left", padx=6, fill="x", expand=True)
        self.wifi_level_var = StringVar(master=self.root, value="")
        self.wifi_level_label = tk.Label(wifi_head, textvariable=self.wifi_level_var, bg=self.PANEL, fg=self.MUTED,
                                         font=("Segoe UI Semibold", 9))
        self.wifi_level_label.pack(side="right")
        self.wifi_spark = tk.Canvas(wifi.body, width=self.PREVIEW_PIXELS[0], height=34, bg=self.BLACK,
                                    highlightthickness=0)
        self.wifi_spark.pack(pady=(5, 0))
        self._wifi_painted_at = 0.0

        # -- Around me: the hazard radar, beside what HAYAT senses locally.
        from app.gui_radar import build_panel as build_radar_panel
        build_radar_panel(self, column, section)

        media = section(column, type(self), fold=self._folds, title="Voice & Vision V2")
        media.pack(fill="x", pady=(10, 0))
        self.media_rows: dict[str, StringVar] = {}
        for key, label in (("speech", "Speech"), ("capture", "Capture"),
                           ("vision", "Vision"), ("operator", "Operator")):
            row = tk.Frame(media.body, bg=self.PANEL)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, bg=self.PANEL, fg=self.MUTED, width=10,
                     anchor="w", font=("Segoe UI", 9)).pack(side="left")
            variable = StringVar(master=self.root, value="--")
            tk.Label(row, textvariable=variable, bg=self.PANEL, fg=self.WHITE,
                     anchor="w", font=("Segoe UI", 9)).pack(side="left", fill="x", expand=True)
            self.media_rows[key] = variable
        media_controls = ButtonRow(media.body, type(self))
        media_controls.pack(fill="x", pady=(7, 0))
        for label, command in (("Test Mic", self._probe_microphone),
                               ("Test Voice", self._test_speaker),
                               ("Analyze 5 Frames", self._multi_frame_look)):
            media_controls.add(label, command)

        # Safe, auditable operational narration. This is intentionally built
        # from lifecycle events and verified state rather than exposing model
        # scratch work or pretending that generated prose is consciousness.
        mindstream = section(column, type(self), fold=self._folds, title="Live Mindstream",
                             action=("Details", self._show_mindstream_details))
        mindstream.pack(fill="x", pady=(10, 0))
        tk.Label(
            mindstream.body,
            text="Operational summaries only · no private chain-of-thought",
            bg=self.PANEL, fg=self.GOLD_SOFT, anchor="w", font=("Segoe UI", 8),
        ).pack(fill="x", pady=(0, 5))
        live_band = tk.Frame(mindstream.body, bg=self.PANEL_2, padx=8, pady=6)
        live_band.pack(fill="x", pady=(0, 5))
        self.mindstream_live_dot = tk.Label(
            live_band, text="●", bg=self.PANEL_2, fg=self.OLIVE,
            font=("Segoe UI", 9),
        )
        self.mindstream_live_dot.pack(side="left")
        self.mindstream_status_var = StringVar(master=self.root, value="LIVE · READY")
        tk.Label(live_band, textvariable=self.mindstream_status_var, bg=self.PANEL_2,
                 fg=self.WHITE, anchor="w", font=("Segoe UI Semibold", 9)).pack(
                     side="left", fill="x", expand=True, padx=(6, 0))
        self.mindstream_activity_var = StringVar(master=self.root, value="0 active")
        tk.Label(live_band, textvariable=self.mindstream_activity_var, bg=self.PANEL_2,
                 fg=self.MUTED, anchor="e", font=("Segoe UI", 8)).pack(side="right")
        self.mindstream_progress_var = DoubleVar(master=self.root, value=0.0)
        self.mindstream_progress = ttk.Progressbar(
            mindstream.body, variable=self.mindstream_progress_var, maximum=100,
            style="Hayat.Horizontal.TProgressbar",
        )
        self.mindstream_progress.pack(fill="x", pady=(0, 6))
        self.mindstream_rows: dict[str, StringVar] = {}
        for key, label in (("focus", "Focus"), ("learning", "Learning"),
                           ("prediction", "Prediction"), ("verification", "Verify")):
            row = tk.Frame(mindstream.body, bg=self.PANEL)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, bg=self.PANEL, fg=self.MUTED, width=10,
                     anchor="w", font=("Segoe UI", 9)).pack(side="left")
            variable = StringVar(master=self.root, value="--")
            tk.Label(row, textvariable=variable, bg=self.PANEL, fg=self.WHITE,
                     anchor="w", font=("Segoe UI", 9)).pack(side="left", fill="x", expand=True)
            self.mindstream_rows[key] = variable

        mindstream_tabs = tk.Frame(mindstream.body, bg=self.PANEL)
        mindstream_tabs.pack(fill="x", pady=(6, 0))
        self.mindstream_tab_buttons: dict[str, tk.Button] = {}
        for key, label in (("feed", "LIVE FEED"), ("learning", "LEARNING"),
                           ("prediction", "PREDICTIONS")):
            button = tk.Button(
                mindstream_tabs, text=label, command=lambda value=key: self._show_mindstream_tab(value),
                bg=self.PANEL_2, fg=self.MUTED, activebackground=self.PANEL_HOVER,
                activeforeground=self.WHITE, relief="flat", bd=0, padx=6, pady=4,
                font=("Segoe UI Semibold", 8), cursor="hand2",
            )
            button.pack(side="left", expand=True, fill="x", padx=1)
            self.mindstream_tab_buttons[key] = button

        self.mindstream_page_host = tk.Frame(mindstream.body, bg=self.BLACK, height=104)
        self.mindstream_page_host.pack(fill="x")
        self.mindstream_page_host.pack_propagate(False)
        self.mindstream_pages: dict[str, tk.Widget] = {}
        self.mindstream_feed = LiveConsole(self.mindstream_page_host, type(self), height=5)
        self.mindstream_pages["feed"] = self.mindstream_feed
        self.mindstream_learning_detail = StringVar(
            master=self.root, value="Learning state is being measured…")
        learning_page = tk.Label(
            self.mindstream_page_host, textvariable=self.mindstream_learning_detail,
            bg=self.BLACK, fg=self.WHITE, justify="left", anchor="nw", padx=10, pady=8,
            wraplength=375, font=("Segoe UI", 9),
        )
        self.mindstream_pages["learning"] = learning_page
        self.mindstream_prediction_detail = StringVar(
            master=self.root, value="Prediction evidence is being measured…")
        prediction_page = tk.Label(
            self.mindstream_page_host, textvariable=self.mindstream_prediction_detail,
            bg=self.BLACK, fg=self.WHITE, justify="left", anchor="nw", padx=10, pady=8,
            wraplength=375, font=("Segoe UI", 9),
        )
        self.mindstream_pages["prediction"] = prediction_page
        self._show_mindstream_tab("feed")
        # Side by side, "Learning" was cut to "Learr"; the row wraps instead.
        mindstream_controls = ButtonRow(mindstream.body, type(self))
        mindstream_controls.pack(fill="x", pady=(6, 0))
        self.mindstream_pause_button = mindstream_controls.add("Pause Feed", self._toggle_mindstream,
                                                               style="Compact.Hayat.TButton")
        mindstream_controls.add("Clear", self._clear_mindstream, style="Compact.Hayat.TButton")
        mindstream_controls.add("Learning", lambda: self.show_view("learning"), style="Compact.HayatAccent.TButton")

        # Durable intelligence activity, refreshed with the other slow probes.
        # This makes overnight learning and research observable without
        # repeatedly querying SQLite from the 250 ms paint loop.
        intelligence = section(column, type(self), fold=self._folds, title="Learning & Research",
                               action=("Details", lambda: self.show_view("learning")))
        intelligence.pack(fill="x", pady=(10, 0))
        self.intelligence_rows: dict[str, StringVar] = {}
        for key, label in (
                ("research", "Knowledge"), ("evidence", "Evidence"),
                ("learning", "Learner"), ("training", "Training"),
                ("control", "Self-control"),
                ("authority", "Permissions")):
            row = tk.Frame(intelligence.body, bg=self.PANEL)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, bg=self.PANEL, fg=self.MUTED, width=11,
                     anchor="w", font=("Segoe UI", 9)).pack(side="left")
            variable = StringVar(master=self.root, value="--")
            tk.Label(row, textvariable=variable, bg=self.PANEL, fg=self.WHITE,
                     anchor="w", font=("Segoe UI", 9)).pack(side="left", fill="x", expand=True)
            self.intelligence_rows[key] = variable
        # Four buttons wrap to two rows: side by side, "Adapt" was pushed out of the column entirely.
        learning_controls = ButtonRow(intelligence.body, type(self))
        learning_controls.pack(fill="x", pady=(7, 0))
        self.learning_toggle_button = learning_controls.add("Start Learning", self._toggle_background_learning)
        learning_controls.add("Learn Next", self._learn_next_now)
        learning_controls.add("Skills", self._show_skill_learning)
        learning_controls.add("Adapt", self._adapt_learning, style="HayatAccent.TButton")

        # -- system status
        status = section(column, type(self), fold=self._folds, title="System Status")
        status.pack(fill="x", pady=(10, 0))
        dials = tk.Frame(status.body, bg=self.PANEL)
        dials.pack(fill="x")
        self.gauges: dict[str, Gauge] = {}
        for key, label in (("cpu", "CPU"), ("ram", "RAM"), ("gpu", "GPU"), ("disk", "Disk")):
            gauge = Gauge(dials, type(self), label, size=66)
            gauge.pack(side="left", expand=True)
            self.gauges[key] = gauge
        cards = tk.Frame(status.body, bg=self.PANEL)
        cards.pack(fill="x", pady=(10, 0))
        self.cards: dict[str, InfoCard] = {}
        for index, (key, title) in enumerate(
                (("gpu", "GPU"), ("ram", "MEMORY"), ("storage", "STORAGE"), ("temp", "TEMP"))):
            card = InfoCard(cards, type(self), title)
            card.grid(row=index // 2, column=index % 2, sticky="nsew", padx=3, pady=3)
            self.cards[key] = card
        cards.grid_columnconfigure(0, weight=1)
        cards.grid_columnconfigure(1, weight=1)

        # -- remote access and memory pressure. These are explicit owner
        # controls; neither is callable by web content or the model.
        remote = section(column, type(self), fold=self._folds, title="Secure Access & Memory",
                         action=("Details", self._show_remote))
        remote.pack(fill="x", pady=(10, 0))
        self.remote_access_var = StringVar(master=self.root, value="Remote access: checking...")
        self.resource_mode_var = StringVar(master=self.root, value="Memory mode: Balanced")
        tk.Label(remote.body, textvariable=self.remote_access_var, bg=self.PANEL,
                 fg=self.WHITE, anchor="w", justify="left", font=("Segoe UI", 9),
                 wraplength=390).pack(fill="x")
        access_buttons = ButtonRow(remote.body, type(self))
        access_buttons.pack(fill="x", pady=(6, 2))
        for label, mode in (("Off", "off"), ("Same Wi-Fi", "same_wifi"),
                            ("Anywhere mTLS", "anywhere")):
            access_buttons.add(label, lambda value=mode: self._set_remote_mode(value))
        device_buttons = ButtonRow(remote.body, type(self))
        device_buttons.pack(fill="x", pady=2)
        device_buttons.add("Pair Device", self._pair_remote_device)
        device_buttons.add("Revoke", self._revoke_remote_device)
        device_buttons.add("Disconnect All", self._disconnect_remote_devices)
        ttk.Button(remote.body, text="Copy Phone Login (no app needed)",
                   style="HayatAccent.TButton", command=self._copy_remote_login).pack(
                       fill="x", pady=(4, 2))
        ttk.Button(remote.body, text="Different-Network Readiness Guide",
                   style="Hayat.TButton", command=self._show_different_network).pack(
                       fill="x", pady=(2, 2))

        tk.Frame(remote.body, bg=self.LINE, height=1).pack(fill="x", pady=6)
        tk.Label(remote.body, textvariable=self.resource_mode_var, bg=self.PANEL,
                 fg=self.GOLD_SOFT, anchor="w", font=("Segoe UI", 9)).pack(fill="x")
        resource_buttons = ButtonRow(remote.body, type(self))
        resource_buttons.pack(fill="x", pady=(6, 2))
        for label, mode in (("Eco", "eco"), ("Balanced", "balanced"),
                            ("Performance", "performance"), ("Adaptive Max", "adaptive")):
            resource_buttons.add(label, lambda value=mode: self._set_resource_mode(value))
        ttk.Button(remote.body, text="Release HAYAT Memory + Unload Models",
                   style="Hayat.TButton", command=self._release_hayat_memory).pack(
                       fill="x", pady=(3, 0))
        ttk.Button(remote.body, text="Deep Recall Search (paged)",
                   style="Hayat.TButton", command=self._deep_recall_search).pack(
                       fill="x", pady=(3, 0))
        # Put the controls above the dials even though the dials are built
        # first. At 768p this keeps every access toggle in the first viewport.
        remote.pack_configure(before=status, pady=(10, 0))
        self._paint_remote_controls()

        # -- what this machine actually is. Named parts rather than
        # percentages: the dials say how busy it is, this says what it is.
        info = section(column, type(self), fold=self._folds, title="System Info")
        info.pack(fill="x", pady=(10, 0))
        self.info_rows: dict[str, StringVar] = {}
        for key, label in (("cpu", "CPU"), ("gpu", "GPU"), ("ram", "RAM"),
                           ("disk", "Storage"), ("temp", "Temp"), ("uptime", "Uptime")):
            row = tk.Frame(info.body, bg=self.PANEL)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, bg=self.PANEL, fg=self.MUTED, width=8,
                     anchor="w", font=("Segoe UI", 9)).pack(side="left")
            variable = StringVar(master=self.root, value="--")
            tk.Label(row, textvariable=variable, bg=self.PANEL, fg=self.WHITE,
                     anchor="w", font=("Segoe UI", 9)).pack(side="left", fill="x", expand=True)
            self.info_rows[key] = variable

        # -- live console
        console_section = section(column, type(self), fold=self._folds, title="Live Console",
                                  action=("Clear", lambda: self.console.clear()))
        console_section.pack(fill="x", pady=(10, 0))
        self.console = LiveConsole(console_section.body, type(self), height=5)
        self.console.pack(fill="both", expand=True)
        # The old trace pane became this console. Keeping the name means
        # everything that already writes operational lines keeps working, and
        # they land somewhere better looking rather than nowhere.
        self.trace = self.console.text

        # A command line under the console. Slash commands already exist and
        # were reachable only from the terminal, so the window could show what
        # HAYAT was doing but not be told to do any of it.
        command_row = tk.Frame(console_section.body, bg=self.PANEL)
        command_row.pack(fill="x", pady=(6, 0))
        self.command_var = StringVar(master=self.root)
        entry = tk.Entry(command_row, textvariable=self.command_var, bg=self.PANEL_2,
                         fg=self.WHITE, insertbackground=self.GOLD, relief="flat",
                         font=("Cascadia Mono", 9))
        entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))
        entry.bind("<Return>", lambda _event: self.run_command())
        ttk.Button(command_row, text="Run", style="Hayat.TButton", width=5,
                   command=self.run_command).pack(side="right")

        # -- models
        models = section(column, type(self), fold=self._folds, title="Models Available",
                         action=("Refresh", self.refresh_models))
        models.pack(fill="x", pady=(10, 12))
        self.model_chips = ModelChips(models.body, type(self), rows=True, limit=3)
        self.model_chips.pack(fill="x")
        ttk.Button(models.body, text="Manage models", style="Hayat.TButton",
                   command=lambda: self._open_control_studio("models")).pack(fill="x", pady=(6, 0))
        models.pack_configure(before=camera, pady=(0, 8))
        console_section.pack_configure(before=camera, pady=(0, 8))

        # The wheel scrolls the column wherever the pointer is inside it,
        # rather than only over the few pixels of bare canvas between panels.
        rail.adopt(column)

    def _watch(self):
        """The runtime's watch mode, with its notes routed into this window."""
        from app.perception.camera.watch import watch_mode

        mode = watch_mode(self.runtime)
        mode.on_note = lambda note: self._post(self._on_watch_note, note)
        return mode

    def _toggle_watch_mode(self) -> None:
        if getattr(self.runtime, "camera", None) is None:
            self._append("system", "There's no camera set up, so I can't watch.")
            return
        mode = self._watch()
        if mode.active:
            self._append("system", mode.stop())
            self._paint_watch_state()
            return
        self.watch_button.state(["disabled"])
        # Starting opens the camera and waits for exposure: off the Tk thread.
        def start() -> None:
            message = mode.start()
            self._post(self._append, "system", message)
            self._post(self._paint_watch_state)
        threading.Thread(target=start, name="hayat-watch-start", daemon=True).start()

    def _on_watch_note(self, note: str) -> None:
        self._append("assistant", f"HAYAT\n{note}")
        self.console.write("eye", note, level="notable")
        self._paint_watch_state()

    def _paint_watch_state(self) -> None:
        mode = getattr(self.runtime, "watch", None)
        watching = bool(mode is not None and mode.active)
        if getattr(self, "watch_badge", None) is None:
            return
        self.watch_badge.configure(text="● WATCHING" if watching else "")
        self.watch_button.state(["!disabled"])
        self.watch_button.configure(text="Stop watching" if watching else "Watch mode")

    def _preview_depth_view(self, key: str) -> None:
        titles = {row[0]: row[2] for row in self.VIEWS}
        descriptions = {row[0]: row[3] for row in self.VIEWS}
        self.depth_view_title.set(titles.get(key, "Workspace"))
        self.depth_view_detail.set(DepthNavigator.DESCRIPTIONS.get(key, descriptions.get(key, "Choose a view to begin.")))

    def _toggle_depth_motion(self) -> None:
        self._set_workspace_motion(not self.depth_nav.motion)
        if not self._save_appearance(theme=self._theme_name, motion=self.depth_nav.motion):
            self.depth_view_detail.set("Motion changed for this session; preference could not be saved.")

    def _set_workspace_motion(self, enabled: bool) -> None:
        self.depth_nav.set_motion(enabled)
        graph = getattr(self, "brain_graph", None)
        if graph is not None:
            graph.set_motion(enabled)

    def _set_text_scale(self, scale: float) -> None:
        self._text_scale = scale if scale in TEXT_SCALES.values() else 1.0
        apply_text_scale(self.root, self._text_scale)

    def _scale_new_window(self, event: Any) -> None:
        if isinstance(event.widget, tk.Toplevel) and not self._closed:
            window = event.widget
            window.after_idle(lambda: window.winfo_exists() and
                              apply_text_scale(window, self._text_scale))

    def _save_appearance(self, **changes: Any) -> bool:
        """Merge settings into appearance.json atomically; False if it could not be written."""
        path = self.runtime.memory.path.parent / "appearance.json"
        try:
            try:
                settings = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(settings, dict):
                    settings = {}
            except (OSError, ValueError):
                settings = {}
            settings.update(changes)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(settings), encoding="utf-8")
            temporary.replace(path)
            return True
        except (OSError, AttributeError, TypeError):
            return False

    def _remember_window(self) -> None:
        """Keep where and how big the window was, for next time.

        Only a window that is actually on screen is remembered: the test
        harness parks hidden windows at -4000,-4000, and restoring that would
        open HAYAT somewhere nobody can see it.
        """
        try:
            if not self.root.winfo_viewable():
                return
            zoomed = self.root.state() == "zoomed"
            if zoomed:
                self._save_appearance(window={**(self._saved_window or {}), "zoomed": True})
                return
            geometry = self.root.winfo_geometry()
            placement, _ = window_placement({"geometry": geometry},
                                            (self.root.winfo_screenwidth(), self.root.winfo_screenheight()))
            if placement == geometry:
                self._save_appearance(window={"geometry": geometry, "zoomed": False})
        except (tk.TclError, AttributeError):
            pass


    # ------------------------------------------------------------ status bar

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self.root, bg=self.PANEL)
        bar.pack(side="bottom", fill="x")
        self._status_bar = bar
        # One line: the health summary is packed first on the right, so the
        # pills share its row instead of pushing it onto a second line.
        self.health_var = StringVar(master=self.root, value="Starting…")
        tk.Label(bar, textvariable=self.health_var, bg=self.PANEL, fg=self.OLIVE_SOFT,
                 font=("Segoe UI", 9)).pack(side="right", padx=14)
        inner = tk.Frame(bar, bg=self.PANEL, pady=3)
        inner.pack(side="left", expand=True)
        self.pills: dict[str, StatusPill] = {}
        for key, name in (("voice", "Voice"), ("internet", "Internet"), ("models", "Models"),
                          ("phone", "Phone"), ("context", "Context"), ("answer", "Answer"),
                          ("tasks", "Tasks"), ("uptime", "Uptime")):
            pill = StatusPill(inner, type(self), name)
            pill.pack(side="left")
            self.pills[key] = pill
        self.status_more_button = ttk.Menubutton(inner, text="Status ▾", style="Compact.Hayat.TButton")
        self.status_more_menu = tk.Menu(self.status_more_button, tearoff=0, bg=self.PANEL_2, fg=self.WHITE,
                                       activebackground=self.PANEL_HOVER, activeforeground=self.WHITE,
                                       postcommand=self._populate_status_menu)
        self.status_more_button.configure(menu=self.status_more_menu)

    def _populate_status_menu(self) -> None:
        self.status_more_menu.delete(0, "end")
        for pill in self.pills.values():
            self.status_more_menu.add_command(label=pill.value.get())
        self.status_more_menu.add_separator()
        self.status_more_menu.add_command(label=self.health_var.get())

    # ---------------------------------------------------------------- views

    def _inspection_layout(self, inspect: bool) -> None:
        """Give dedicated workspaces the available height; preserve the chat draft."""
        if getattr(self, "_inspection_active", False) == inspect:
            return
        self._inspection_active = inspect
        for widget in (self._compose_hint, self._compose_surface, self._draft_label, self._compose_controls, self._suggestion_chips):
            widget.pack_forget()
        if inspect:
            self._inspection_saved_overview = getattr(self, "_overview_open", True)
            self._overview_open = False
            self._chat_tabs.pack_forget()
            self._arrange_rails()
        if not inspect:
            self._overview_open = getattr(self, "_inspection_saved_overview", getattr(self, "_overview_open", True))
            self._arrange_rails()
            self._chat_tabs.pack(fill="x", pady=(4, 6))
            self._compose_hint.pack(side="bottom", fill="x")
            self._compose_surface.pack(side="bottom", fill="x")
            if self.draft_status.get():
                self._draft_label.pack(side="bottom", anchor="w", before=self._compose_surface)
            self._compose_controls.pack(side="bottom", fill="x", pady=(4, 6))
            self._fit_conversation()

    def show_view(self, key: str) -> None:
        """Open one of the sidebar views.

        Chat is the live conversation. The rest are reports, and rather than
        building nine half-panels they render into the same transcript -- the
        place answers already appear, which means a report can be read
        alongside the exchange that prompted it instead of in a pane that
        loses it on the next click.
        """
        if self._closed:
            return
        self.current_view = key
        if key == "chat" and self._chat_find_open:
            self._pack_chat_find_bar()
        else:
            self._chat_find_bar.pack_forget()
        self._inspection_layout(key in {"operator", "background", "training", "models",
                                       "memory", "history", "tasks", "task_graph", "gallery", "invention"})
        self._schedule(0, lambda: apply_icons(self.root, type(self)))
        self._show_welcome()
        self.depth_nav.select(key)
        self._preview_depth_view(key)
        for name, button in self.depth_shortcuts.items():
            button.configure(style="HayatChosen.TButton" if name == key else "Hayat.TButton")
        for name, item in self.nav.items():
            item.select(name == key)
        titles = {name: title for name, _icon, title, _sub in self.VIEWS}
        self.view_title.set("Conversation" if key == "chat" else titles.get(key, key.title()))
        descriptions = {name: subtitle for name, _icon, _title, subtitle in self.VIEWS}
        self.view_help.set("Ask a question, attach a file, or choose an action below." if key == "chat"
                           else descriptions.get(key, "Choose an item to inspect it."))
        self.view_badge.configure(text="Active" if key == "chat" else "Report")
        for pane in self._explorer_panels.values():
            pane.pack_forget()
        # Views report. They never prompt.
        #
        # "research" used to open a modal dialog asking what to look into, so
        # clicking a sidebar item stopped the entire window until someone
        # answered it -- and in a test with nobody to answer, forever. A nav
        # item is a place to look, not a question; asking for a search term
        # belongs to the Research button, which is a thing you press to start
        # something.
        # The toolbox replaces the transcript rather than reporting into it:
        # it is a place to press things, not an answer.
        if key == "toolbox":
            self._show_operator_pane(False)
            self._show_toolbox(True)
            return
        if key == "operator":
            self._show_toolbox(False)
            self._show_operator_pane(True)
            if not self._operator_suppress_auto_refresh:
                self._refresh_operator()
            return
        self._show_toolbox(False)
        self._show_operator_pane(False)

        if key in {"task_graph", "gallery", "training", "invention"}:
            if key not in self._explorer_panels:
                from app.gui_task_graph import TaskGraphEditor
                from app.gui_gallery import ImageGallery
                from app.gui_training import TrainingReview
                from app.gui_invention import InventionPanel
                panel = {"task_graph": TaskGraphEditor, "gallery": ImageGallery, "training": TrainingReview,
                         "invention": InventionPanel}[key]
                self._explorer_panels[key] = panel(self._centre, type(self), self.runtime, self._compose_plan)
            pane = self._explorer_panels[key]
            pane.configure(relief="raised", bd=2)
            self.chat.pack_forget()
            pane.pack(fill="both", expand=True, pady=(2, 8))
            self.view_badge.configure(text="Workspace")
            pane.refresh()
            return

        if key in {"memory", "history", "tasks", "models", "background"}:
            from app.gui_explorer import WorkspaceExplorer
            if key not in self._explorer_panels:
                actions = (("Pause / resume", self._toggle_background_learning),
                           ("Recover failed research", self._recover_learning)) if key == "background" else ()
                self._explorer_panels[key] = WorkspaceExplorer(self._centre, type(self), self.runtime, key, actions)
            pane = self._explorer_panels[key]
            pane.configure(relief="raised", bd=2)
            self.chat.pack_forget()
            pane.pack(fill="both", expand=True, pady=(2, 8))
            self.view_badge.configure(text="Workspace")
            pane.refresh()
            return

        action = {
            "research": self._show_research,
            "tools": self._show_tools,
            "memory": self._show_memory,
            "learning": self._show_learning,
            "training": self._show_training,
            "health": self._show_doctor,
            "improve": self._show_improvements,
            "files": self._show_files,
            "settings": self._show_settings,
        }.get(key)
        if action is not None:
            action()

    def _compose_plan(self, text):
        self.show_view("chat")
        existing = self.input.get("1.0", "end-1c")
        self.input.insert("end", ("\n\n" if existing.strip() else "") + text)
        self.input.focus_set()

    def run(self) -> int:
        self.root.mainloop()
        return 0

    def send(self) -> None:
        if self.runtime.registry.interlock.halted:
            self.draft_status.set('Emergency stop is active. Resume tools before sending.')
            return
        text = self.input.get("1.0", "end").strip()
        if not text:
            return
        if self._working_since is not None:
            self.draft_status.set("A reply is running; Stop it before sending another")
            return
        self.input.delete("1.0", "end")
        self._draft.flush()
        if self.current_view in {"memory", "history", "tasks", "models", "background"}:
            self.show_view("chat")
        self._append("user", f"You\n{text}")
        self._last_sent_text = text
        self._working_since = time.monotonic()
        self._working_view.begin()
        self._generation_cancel = threading.Event()
        self._paint_reply_controls()
        threading.Thread(target=self._work, args=(text,),
                         name="hayat-gui-turn", daemon=True).start()

    def _enter(self, event) -> str | None:
        if event.state & 0x0001:  # Shift+Enter keeps a newline.
            return None
        self.send()
        return "break"

    def _work(self, text: str, cancel=None) -> None:
        from app.models.streaming import answer_stream, GenerationCancelled, check_cancelled
        cancel = cancel if cancel is not None else self._generation_cancel or threading.Event()
        try:
            with answer_stream(self._stream_preview.publish, cancel):
                reply = self.runtime.assistant.handle(text, source="gui")
                check_cancelled()
            if reply.display_response:
                self._post(self._append, "assistant", f"HAYAT\n{reply.display_response}")
                from app.observed_reply import steps_trace

                steps = steps_trace(getattr(reply, "intent", None), getattr(reply, "outcome", None))
                if steps:
                    self._post(self._append_steps, steps)
                if getattr(reply, 'outcome', None) is not None:
                    self._post(self._embed_camera_outcome, reply.outcome)
                    from app.gui_evidence import outcome_evidence
                    self._post(self._append_evidence, outcome_evidence(reply.outcome))
        except GenerationCancelled:
            self._post(self._append, "system", "Response cancelled. Incomplete streamed text was not saved.")
        except Exception as exc:
            # The window used to print the raw exception, which is how Mohsin
            # met "error: Cannot switch to a different thread" followed by two
            # lines of greenlet object addresses. The CLI already knew how to
            # explain that in a sentence; the window was simply never given
            # the same courtesy.
            from app.cli import describe_turn_failure

            self._post(self._append, "error", f"Error\n{describe_turn_failure(exc)}")
            self._record_failure(text, exc)
        finally:
            self._stream_preview.publish("clear")
            self._post(self._finish_reply)

    def _finish_reply(self) -> None:
        self._working_since = None
        self._generation_cancel = None
        self._working_view.end()
        self._paint_reply_controls()
        self._paint_reasoning_mode()

    def _paint_reasoning_mode(self) -> None:
        manager = getattr(self.runtime, 'modes', None)
        if manager is None or not hasattr(self, 'reasoning_button'):
            return
        current = manager.snapshot()['configured']
        god = 'DEEP' in current and 'ADVERSARIAL' in current
        self.reasoning_button.configure(text='Beast mode: on' if god else 'Beast mode',
                                        style='HayatChosen.TButton' if god else 'Hayat.TButton')

    def _paint_reply_controls(self) -> None:
        busy = self._working_since is not None
        self.send_button.configure(state="disabled" if busy else "normal",
                                   text="Replying…" if busy else "Send")
        cancelling = busy and self._generation_cancel is not None and self._generation_cancel.is_set()
        self.reply_stop_button.configure(state="normal" if busy and not cancelling else "disabled",
                                         text="Stopping…" if cancelling else "Stop reply")
        if busy and not self.reply_stop_button.winfo_manager():
            self.reply_stop_button.pack(side='right', before=self.input, padx=(9, 0), fill='y')
        elif not busy:
            self.reply_stop_button.pack_forget()

    def _cancel_reply(self) -> None:
        """Cancel this reply without also stopping voice or unrelated jobs."""
        if self._working_since is None:
            return
        if self._generation_cancel is not None:
            self._generation_cancel.set()
        self.runtime.agent.cancel_current()
        self._paint_reply_controls()

    def _preload_brain(self) -> None:
        # Admission scans model inventory and hardware before starting the
        # load. Those checks must also run off the interface thread.
        threading.Thread(target=self._resource_controller.preload_brain,
                         name="hayat-gui-preload", daemon=True).start()

    def _record_failure(self, text: str, exc: Exception, *, stage: str = "gui_turn") -> None:
        """Keep the technical detail where it belongs: in the failure log.

        Explaining a failure plainly must not mean losing it. The classified
        record is what /doctor and the repair specialist read, and what lets a
        recurring problem be counted rather than remembered.
        """
        failures = getattr(self.runtime, "failures", None)
        if failures is None:
            return
        try:
            recorded = getattr(failures, 'last', None)
            if recorded is not None and getattr(exc, 'hayat_failure_id', None) == recorded.failure_id:
                return  # The shared turn boundary already retained the actual failed request.
            request = getattr(exc, 'hayat_failed_request', text)
            failures.from_exception(exc, stage=stage, user_request=str(request),
                                    session_id=getattr(getattr(self.runtime, 'session', None), 'session_id', None))
        except Exception as log_error:
            logging.getLogger(__name__).warning("Could not record GUI failure (%s): %s",
                                                type(exc).__name__, type(log_error).__name__)

    def _record_background_failure(self, component: str, exc: Exception) -> None:
        """Keep polling failures visible without writing the same error every tick."""
        reported = getattr(self, "_background_failures", None)
        if reported is None:
            self._background_failures = reported = {}
        now = time.monotonic()
        if now - reported.get(component, float("-inf")) >= 60:
            reported[component] = now
            self._record_failure(component, exc, stage="gui_status")

    def _report(self, title: str, body: str) -> None:
        """A block of HAYAT's own output: gold, monospaced, kept readable."""
        self._append("report", f"{title}\n{body}")

    #: Lines kept in each panel. A Tk text widget holds everything ever put
    #: into it, so a window left open for a day grows until it is slow to
    #: scroll and then slow to draw. Trimming the top costs nothing: the
    #: conversation itself lives in memory, and this is only the view of it.
    MAX_TRANSCRIPT_LINES = 4000
    MAX_TRACE_LINES = 1500

    def _trim(self, widget: Any, maximum: int) -> None:
        lines = int(widget.index("end-1c").split(".")[0])
        if lines > maximum:
            widget.delete("1.0", f"{lines - maximum + 1}.0")
        if widget is getattr(self, "chat", None):
            visible = set(widget.image_names())
            self._inline_images[:] = [photo for photo in self._inline_images if str(photo) in visible]
            visible_windows = set(widget.window_names())
            for card in list(self._evidence_cards):
                if str(card) not in visible_windows:
                    card.destroy()
                    self._evidence_cards.remove(card)
            for key, card in list(self._video_cards.items()):
                if str(card) not in visible_windows:
                    card.close()
                    card.destroy()
                    del self._video_cards[key]
            for key, card in list(self._video_link_cards.items()):
                if str(card) not in visible_windows:
                    card.close()
                    del self._video_link_cards[key]
            studio = getattr(self, '_video_studio', None)
            if studio is not None and studio.winfo_exists() and str(studio) not in visible_windows:
                studio.close()

    #: Tags whose first line names a speaker and therefore gets a clock time.
    #: A transcript scrolled back through days is much easier to place when
    #: each turn says when it happened.
    _STAMPED = frozenset({"user", "assistant", "report"})

    def _append(self, tag: str, text: str) -> None:
        if self._closed:
            return
        if tag != "system":
            self._welcome_pending = False
            self._core_requested = False
            self._show_welcome()
        elif self._welcome.winfo_manager():
            self._unseen_notices = getattr(self, "_unseen_notices", 0) + 1
            self._label_core_button(True)
        # U+FE0F asks for a colour emoji, which Tk 8.6 cannot draw; it showed as a blank gap after "☀️".
        body = text.rstrip().replace("️", "")
        # Emoji themselves are shown in colour as small pictures (app/gui_emoji.py).
        from app.gui_emoji import insert as emoji_insert
        from app.gui_motion import near_bottom
        follow = tag == "user" or near_bottom(self.chat)
        if self._chat_find_open and self._chat_find_var.get():
            follow = False
        if tag == "assistant" and not follow:
            self._unread_messages = getattr(self, "_unread_messages", 0) + 1
            self.latest_button.configure(text=f"Latest ↓ ({self._unread_messages} new)")
        from app.gui_motion import reading_position
        with reading_position(self.chat, follow=follow):
            self.chat.configure(state="normal")
            if tag in self._STAMPED and "\n" in body:
                speaker, _, rest = body.partition("\n")
                style = "speaker_user" if tag == "user" else "speaker_hayat"
                self.chat.insert("end", speaker.strip(), style)
                stamp = f"   {time.strftime('%I:%M %p').lstrip('0')}\n"
                self.chat.insert("end", stamp, "stamp")
                self._transcript.write(speaker.strip() + stamp + rest.rstrip() + "\n")
                lead, newline, block = rest.rstrip().partition("\n")
                if tag == "assistant" and newline and lead.rstrip().endswith(":"):
                    # "app/voice has 17 items:" then the items: the lines below the
                    # heading are data, shown aligned rather than as prose.
                    emoji_insert(self.chat, "end", lead + "\n", tag)
                    emoji_insert(self.chat, "end", block + "\n", (tag, "block"))
                elif tag == "assistant":
                    self._insert_formatted(rest.rstrip(), tag)
                else:
                    emoji_insert(self.chat, "end", rest.rstrip() + "\n", tag)
            else:
                emoji_insert(self.chat, "end", body + "\n", tag)
                self._transcript.write(body + "\n")
            # Show, don't just name. A generated picture whose path is in the reply
            # is embedded straight into the transcript, because "Saved to <path>"
            # made Mohsin go hunting for a file and, when he could not find it,
            # conclude nothing had been made.
            if tag in {"assistant", "user"}:
                self._embed_generated_image(body)
                self._embed_document_link(body)
                self._embed_video_link(body)
                self._embed_video_request(body)
            self._trim(self.chat, self.MAX_TRANSCRIPT_LINES)
            if hasattr(self, 'chat_details'):
                self.chat_details.observe(tag, body)
            if tag == "assistant":
                self._refresh_standing_chip()
            self.chat.configure(state="disabled")
            if self._chat_find_open and self._chat_find_var.get():
                self._refresh_chat_find()

    def _append_steps(self, steps: list[str]) -> None:
        """A collapsed "How I got this" line under the reply it explains."""
        if self._closed or not steps:
            return
        key = f"steps-{len(self._steps) + 1}"
        self._steps[key] = steps
        self._transcript.write("How I got this:\n" + "\n".join(steps) + "\n")
        follow = self._chat_follows()
        count = len(steps) - 2
        self.chat.configure(state="normal")
        self.chat.insert("end", f"▸ How I got this ({count} step{'s' if count != 1 else ''})\n",
                         ("steps_toggle", key))
        self.chat.tag_bind(key, "<Button-1>", lambda _event, name=key: self._toggle_steps(name))
        self._trim(self.chat, self.MAX_TRANSCRIPT_LINES)
        self.chat.configure(state="disabled")
        if follow:
            self.chat.see("end")

    def _append_evidence(self, evidence: dict) -> None:
        if self._closed:
            return
        from app.gui_motion import reading_position
        with reading_position(self.chat, follow=self._chat_follows()):
            from app.gui_evidence import EvidenceCard
            card = EvidenceCard(self.chat, self, evidence)
            self._evidence_cards.append(card)
            self.chat.configure(state='normal')
            self.chat.window_create('end', window=card, padx=8, pady=8)
            self.chat.insert('end', '\n')
            self._transcript.write(evidence['label'] + '\n' + evidence['detail'] + '\n')
            while len(self._evidence_cards) > 12:
                self._evidence_cards.pop(0).destroy()
            self._trim(self.chat, self.MAX_TRANSCRIPT_LINES)
            self.chat.configure(state='disabled')

    def _open_team_workspace(self) -> None:
        existing = getattr(self, '_team_workspace', None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            return
        from app.gui_team import TeamWorkspace
        self._team_workspace = TeamWorkspace(self.root, type(self), getattr(self.runtime, 'teams', None),
                                             self.runtime.manager.roles())

    def _toggle_god_mode(self) -> None:
        if self._working_since is not None:
            self.draft_status.set('Finish or stop the current reply before switching reasoning mode.')
            return
        def change():
            manager = self.runtime.modes
            current = manager.snapshot()['configured']
            enabled = 'DEEP' in current and 'ADVERSARIAL' in current
            # Preserve all fixed specialist and privacy/local preferences.
            retained = [mode for mode in current if mode not in {'AUTO', 'FAST', 'BALANCED', 'DEEP', 'ADVERSARIAL'}]
            manager.set((retained + ['BALANCED'] if retained else ['AUTO']) if enabled else retained + ['BEAST'])
            self._post(self._paint_reasoning_mode)
            return ('Automatic reasoning restored.' if enabled and not retained else 'Balanced reasoning restored.' if enabled
                    else 'Beast mode enabled: deeper reasoning, alternatives and conclusion checks. Replies may take longer; accuracy still needs evidence.')
        self._background('Reasoning mode', change)

    def _toggle_steps(self, key: str) -> str:
        """Open or close one reply's steps in place, leaving the rest alone."""
        ranges = self.chat.tag_ranges(key)
        if not ranges:
            return "break"
        detail = f"{key}-detail"
        self.chat.configure(state="normal")
        opened = self.chat.tag_ranges(detail)
        start, end = ranges[0], ranges[1]
        label = self.chat.get(start, end)
        if opened:
            self.chat.delete(opened[0], opened[1])
            flipped = label.replace("▾", "▸", 1)
        else:
            body = "\n".join(self._steps.get(key, [])) + "\n"
            self.chat.insert(end, body, ("steps_detail", detail))
            flipped = label.replace("▸", "▾", 1)
        self.chat.delete(start, end)
        self.chat.insert(start, flipped, ("steps_toggle", key))
        self.chat.configure(state="disabled")
        return "break"

    #: A local image file named in a reply -- the thing to show inline.
    _IMAGE_PATH = re.compile(r"(?i)([A-Za-z]:\\[^\r\n]+?\.(?:png|jpe?g|webp)"
                             r"|(?:/[^\s\r\n]+?)\.(?:png|jpe?g|webp))")

    def _embed_generated_image(self, text: str) -> None:
        """Render a generated image inline, at a readable size, or do nothing.

        Best-effort by design: a missing file, an unreadable image, or PIL not
        being importable must never break the transcript. The picture is a
        bonus on top of the path, not a replacement for it.

        The thumbnail is a fit-to-window preview; the real file is often 1080
        and worth seeing full size, so the picture and the line under it both
        open it in the system viewer on a click.
        """
        from app.gui_rich import find_images

        for path in find_images(text):
            self._embed_image_file(path)

    def _insert_formatted(self, body: str, tag: str) -> None:
        """A reply with its Markdown and maths shown as formatting, not symbols."""
        from app.gui_emoji import insert as emoji_insert
        from app.gui_rich import markdown_segments

        body = re.sub(r"\n{3,}", "\n\n", body)
        for segment in markdown_segments(body):
            tags = (tag, "md_body", *segment.tags)
            if segment.href:
                self._media_serial += 1
                link_tag = f"source_{self._media_serial}"
                self.chat.tag_config(link_tag, foreground=self.ROYAL_SOFT, underline=True)
                self.chat.tag_bind(link_tag, "<Button-1>", lambda _e, url=segment.href: self._open_source(url))
                tags += (link_tag,)
            emoji_insert(self.chat, "end", segment.text, tags)
        self.chat.insert("end", "\n", tag)

    @staticmethod
    def _open_source(url: str) -> str:
        from urllib.parse import urlsplit
        import webbrowser
        parsed = urlsplit(url)
        if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.username and not parsed.password:
            webbrowser.open(url)
        return "break"

    def _embed_camera_outcome(self, outcome) -> None:
        """Only verified tool results can create a network player, never model text."""
        from app.gui_video_player import NativeVideoPreview
        if self._closed:
            return
        from app.gui_motion import reading_position
        with reading_position(self.chat, follow=self._chat_follows()):
            for event in getattr(outcome, 'history', []) or []:
                if (event.get('tool') not in ('intelligence_camera_play', 'my_camera_play')
                        or not (event.get('result') or {}).get('success')
                        or not (event.get('verification') or {}).get('passed')):
                    continue
                camera = event['result']['output']
                key = 'camera:' + camera['event_id']
                for other in self._video_cards.values():
                    if hasattr(other, 'camera') and other.camera and not other._closed:
                        other.stop()
                    elif hasattr(other, 'pause'):
                        other.pause()
                old = self._video_cards.pop(key, None)
                if old is not None:
                    old.close()
                    old.destroy()
                self.chat.configure(state='normal')
                card = NativeVideoPreview(self.chat, self, camera=camera)
                self._video_cards[key] = card
                self.chat.window_create('end', window=card, padx=8, pady=8)
                self.chat.insert('end', '\n')
                while len(self._video_cards) > 4:
                    self._video_cards.pop(next(iter(self._video_cards))).destroy()
                self.chat.configure(state='disabled')

    def _dispatch_camera_control(self, action):
        ready, result = threading.Event(), {}
        def apply():
            try:
                card = next((card for card in reversed(list(self._video_cards.values()))
                             if getattr(card, 'camera', None) and not card._closed), None)
                if card is None:
                    raise ValueError('Play a public camera in chat first.')
                result.update(accepted=True, **card.control(action))
            except Exception as exc:
                result['error'] = str(exc)
            finally:
                ready.set()
        self._post(apply)
        if not ready.wait(5):
            raise ValueError('Camera player did not respond.')
        if 'error' in result:
            raise ValueError(result['error'])
        return result

    def _embed_video_link(self, text: str) -> None:
        from app.gui_rich import find_videos
        from app.gui_video_player import create_video_preview
        autoplay = text.startswith(('HAYAT\nVideo ready in chat:', 'HAYAT\nPlaying video in chat:'))
        for path in find_videos(text):
            key = str(path.resolve())
            if key in self._video_cards:
                if autoplay and hasattr(self._video_cards[key], 'request_play'):
                    for other_key, other in self._video_cards.items():
                        if other_key != key and hasattr(other, 'pause'):
                            other.pause()
                    self._video_cards[key].request_play()
                continue
            card = create_video_preview(self.chat, self, path)
            self._video_cards[key] = card
            self.chat.window_create('end', window=card, padx=8, pady=8)
            self.chat.insert('end', '\n')
            if autoplay and hasattr(card, 'request_play'):
                for other in self._video_cards.values():
                    if other is not card and hasattr(other, 'pause'):
                        other.pause()
                card.request_play()
            while len(self._video_cards) > 4:
                old_key = next(iter(self._video_cards))
                old = self._video_cards.pop(old_key)
                old.close()
                old.destroy()

    def _open_video_studio(self, source: str = '') -> None:
        from app.gui_video import VideoStudio, VideoEditorCard
        self.show_view('chat')
        self._welcome_pending = False
        self._core_requested = False
        self._show_welcome()
        if getattr(self, '_tools_expanded', False):
            self._toggle_tools_drawer()
        existing = getattr(self, '_video_studio', None)
        if isinstance(existing, VideoEditorCard) and existing.winfo_exists():
            existing.close()
            existing = None
        if existing is not None and existing.winfo_exists():
            if source:
                existing.source.set(source)
            self.chat.see(existing)
            return
        self.chat.configure(state='normal')
        self.chat.insert('end', '\n')
        self._video_studio = VideoStudio(self, parent=self.chat)
        if source:
            self._video_studio.source.set(source)
        self.chat.window_create('end', window=self._video_studio, padx=8, pady=8)
        self.chat.insert('end', '\n')
        self.chat.configure(state='disabled')
        self.chat.see('end')
        self._schedule(40, self.chat.see, 'end')

    def _embed_video_request(self, text: str) -> None:
        if text.startswith('HAYAT\nHayat intelligence\nOpening public camera in desktop chat:'):
            return  # Its verified outcome supplies the native camera card.
        from app.gui_rich import find_video_links
        from app.gui_video import VideoLinkCard
        for url in find_video_links(text):
            if url in self._video_link_cards:
                continue
            card = VideoLinkCard(self.chat, self, url)
            self._video_link_cards[url] = card
            self.chat.window_create('end', window=card, padx=8, pady=8)
            self.chat.insert('end', '\n')
            while len(self._video_link_cards) > 4:
                key = next(iter(self._video_link_cards))
                self._video_link_cards.pop(key).close()

    def _open_video_editor(self, path: str, position: float = 0, duration: float = 0) -> None:
        from app.gui_video import VideoEditorCard
        self.show_view('chat')
        self._welcome_pending = False
        self._core_requested = False
        self._show_welcome()
        existing = getattr(self, '_video_studio', None)
        if existing is not None and existing.winfo_exists():
            existing.close()
        self.chat.configure(state='normal')
        self.chat.insert('end', '\n')
        self._video_studio = VideoEditorCard(self.chat, self, path, position, duration)
        self.chat.window_create('end', window=self._video_studio, padx=8, pady=8)
        self.chat.insert('end', '\n')
        self.chat.configure(state='disabled')
        self.chat.see('end')
        self._schedule(40, self.chat.see, 'end')

    def _dispatch_video_control(self, path: str, action: str, value: float) -> dict:
        """Tool workers ask the UI loop to control its card and wait for acceptance."""
        from app.models.streaming import check_cancelled
        ready = threading.Event()
        result = {}
        def apply():
            try:
                card = self._video_cards.get(str(Path(path).resolve()))
                if card is None or not getattr(card, '_ready', False):
                    raise ValueError('Play this video in chat first; its player is not ready.')
                if action in {'play', 'pause'}:
                    card._send(action)
                elif action == 'seek':
                    card.seek(value)
                elif action in {'mute', 'unmute'}:
                    card._send('mute', action == 'mute')
                else:
                    card._send(action, value)
                result['accepted'] = True
            except Exception as exc:
                result['error'] = str(exc)
            finally:
                ready.set()
        self._post(apply)
        started = time.monotonic()
        while not ready.wait(.1):
            check_cancelled()
            if self._closed or time.monotonic() - started > 5:
                raise ValueError('The chat player did not respond; reopen the video preview.')
        if 'error' in result:
            raise ValueError(result['error'])
        return result

    def _embed_image_file(self, path: Any) -> None:
        """One image shown inline at a readable size, opening full size on a click."""
        try:
            from PIL import Image, ImageTk

            with Image.open(path) as source:
                source.load()
                full_width, full_height = source.width, source.height
                image = source.copy()
                image.thumbnail((380, 260), Image.LANCZOS)
                photo = ImageTk.PhotoImage(image, master=self.chat)
        except Exception:
            return
        self._inline_images.append(photo)
        # A tag unique to this picture, bound to open exactly it -- the loop
        # variable trap (every link opening the last image) is why the path is
        # bound as a default argument.
        self._media_serial += 1
        tag = f"openimg_{self._media_serial}"
        start = self.chat.index("end-1c")
        self.chat.image_create("end", image=photo, padx=6, pady=6)
        self.chat.tag_add(tag, start, "end-1c")
        kb = path.stat().st_size // 1024
        self.chat.insert("end", "\n")
        self.chat.insert("end", f"  Open full size  ({full_width}x{full_height}, {kb} KB) ↗\n", tag)
        self.chat.tag_config(tag, foreground=self.ROYAL_SOFT, underline=True)
        self.chat.tag_bind(tag, "<Enter>", lambda _e: self.chat.config(cursor="hand2"))
        self.chat.tag_bind(tag, "<Leave>", lambda _e: self.chat.config(cursor=""))
        self.chat.tag_bind(tag, "<Button-1>", lambda _e, p=path: self._open_file(p))

    @staticmethod
    def _open_file(path: Any) -> None:
        """Open a file in the system's default viewer, tolerating any failure."""
        from app.platform_support import open_path

        open_path(path)

    #: A document file named in a reply -- something to offer to open in-app.
    _DOC_PATH = re.compile(r"(?i)([A-Za-z]:\\[^\r\n]+?\.(?:pdf|docx)"
                           r"|(?:/[^\s\r\n]+?)\.(?:pdf|docx))")

    def _embed_document_link(self, text: str) -> None:
        """A card for each document named in a message: what it is, a preview, and ways to open it.

        A PDF opens in HAYAT's own scrollable viewer and shows its first page;
        text, Word, Excel and PowerPoint files show their first lines or slide
        titles. Every card can open the file and show it in its folder.
        Best-effort: an unreadable file still gets a card with its name.
        """
        from app.gui_rich import document_preview, find_documents

        for path in find_documents(text):
            try:
                self.runtime.registry.permissions.validate_project_path(path)
            except PermissionError:
                continue
            self._media_serial += 1
            key = f"preview_{self._media_serial}"
            self._embed_document_card(path, {"name": path.name, "kind": path.suffix[1:].upper(),
                                            "size": "", "_pending": key})
            def load(target=path, pending=key):
                try:
                    with ACTIVITY.track("service", "Document preview", str(target.name)):
                        result = document_preview(target)
                except Exception as exc:
                    result = {"lines": [f"Preview unavailable ({type(exc).__name__}). Open the file to inspect it."]}
                self._post(self._finish_document_preview, pending, result)
            self._preview_workers.submit(load)

    def _embed_document_card(self, path: Any, preview: dict[str, Any]) -> None:
        is_pdf = path.suffix.lower() == ".pdf"
        pages = preview.get("pages")
        counted = (f" · {pages} page{'s' if pages != 1 else ''}" if is_pdf and pages else
                   f" · {pages} sheet{'s' if pages != 1 else ''}" if path.suffix.lower() == ".xlsx" and pages else
                   f" · {pages} slide{'s' if pages != 1 else ''}" if path.suffix.lower() == ".pptx" and pages else "")
        self.chat.insert("end", "\n")
        self.chat.insert("end", f"\U0001f4c4  {preview['name']}   {preview['kind']} · {preview['size']}{counted}\n",
                         "doc_title")
        if preview.get("_pending"):
            self.chat.insert("end", "Loading preview…\n", (preview["_pending"], "doc_line"))
        thumbnail = preview.get("thumbnail")
        if thumbnail is not None:
            try:
                from PIL import ImageTk

                photo = ImageTk.PhotoImage(thumbnail, master=self.chat)
                self._inline_images.append(photo)
                self.chat.image_create("end", image=photo, padx=12, pady=4)
                self.chat.insert("end", "\n", "doc_card")
            except Exception:
                logging.getLogger(__name__).debug("PDF thumbnail could not be shown", exc_info=True)
        for line in preview.get("lines") or []:
            self.chat.insert("end", line + "\n", "doc_line")
        self._media_serial += 1
        key = f"doc_{self._media_serial}"
        open_tag, folder_tag = f"open{key}", f"folder{key}"
        label = ("Open PDF in viewer" if is_pdf else
                 "Open Word document" if path.suffix.lower() == ".docx" else "Open")
        self.chat.insert("end", f"  {label} ↗", (open_tag, "doc_card"))
        self.chat.insert("end", "     ", "doc_card")
        self.chat.insert("end", "Show in folder ↗\n", (folder_tag, "doc_card"))
        edit_tag = f"edit{key}"
        self.chat.insert("end", "  Edit / revise…\n", (edit_tag, "doc_card"))
        action = self._open_pdf_viewer if is_pdf else self._open_file
        for tag_name, callback in ((open_tag, lambda _e, p=path: action(p)),
                                   (folder_tag, lambda _e, p=path: self._open_file(p.parent)),
                                   (edit_tag, lambda _e, p=path: self._edit_file(p))):
            self.chat.tag_config(tag_name, foreground=self.ROYAL_SOFT, underline=True)
            self.chat.tag_bind(tag_name, "<Enter>", lambda _e: self.chat.config(cursor="hand2"))
            self.chat.tag_bind(tag_name, "<Leave>", lambda _e: self.chat.config(cursor=""))
            self.chat.tag_bind(tag_name, "<Button-1>", callback)

    def _finish_document_preview(self, key: str, preview: dict[str, Any]) -> None:
        ranges = self.chat.tag_ranges(key)
        if not ranges:
            return  # cleared/trimmed messages must not resurrect themselves
        follow = self._chat_follows()
        anchor = key + "_viewport"
        if not follow:
            self.chat.mark_set(anchor, self.chat.index("@0,0"))
            self.chat.mark_gravity(anchor, "left")
        self.chat.configure(state="normal")
        self.chat.mark_set(key, ranges[0])
        self.chat.mark_gravity(key, "right")
        self.chat.delete(ranges[0], ranges[1])
        try:
            thumbnail = preview.get("thumbnail")
            if thumbnail is not None:
                from PIL import ImageTk
                thumbnail.thumbnail((340, 200))
                photo = ImageTk.PhotoImage(thumbnail, master=self.chat)
                self._inline_images.append(photo)
                self.chat.image_create(key, image=photo, padx=12, pady=4)
                self.chat.insert(key, "\n")
            info = " · ".join(str(v) for v in (preview.get("size"), f"{preview['pages']} pages / sheets / slides" if preview.get("pages") else None) if v)
            if info:
                self.chat.insert(key, info + "\n", "doc_line")
            for line in preview.get("lines", []):
                self.chat.insert(key, line + "\n", "doc_line")
        finally:
            self.chat.mark_unset(key)
            self.chat.tag_delete(key)
            self.chat.configure(state="disabled")
        if follow:
            self.chat.see("end")
        else:
            self.chat.yview(anchor)
            self.chat.mark_unset(anchor)

    def _edit_file(self, path: Any) -> None:
        from app.gui_files import open_editor
        editor = open_editor(self, Path(path))
        editor.lift()

    def _open_pdf_viewer(self, path: Any) -> None:
        from app.gui_files import PdfViewer

        path = Path(path)
        self.runtime.registry.permissions.validate_project_path(path)
        windows = getattr(self, "_pdf_windows", {})
        existing = windows.get(str(path))
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            return
        windows[str(path)] = PdfViewer(self, path)
        self._pdf_windows = windows

    def _append_trace(self, text: str) -> None:
        if self._closed:
            return
        self.trace.configure(state="normal")
        self.trace.insert("end", text.rstrip() + "\n")
        self._trim(self.trace, self.MAX_TRACE_LINES)
        self.trace.configure(state="disabled")
        self.trace.see("end")

    def _watch_mindstream(self) -> None:
        """Connect the compact feed to sanitised lifecycle narration."""
        thoughts = getattr(self.runtime, "thoughts", None)
        if thoughts is None or not hasattr(thoughts, "add_listener"):
            self._mindstream_add("system", "Operational event stream unavailable")
            return
        try:
            self._mindstream_unsubscribe = thoughts.add_listener(self._mindstream_event)
            for line in thoughts.recent(8):
                self._mindstream_add("history", line)
        except Exception:
            self._mindstream_unsubscribe = None

    def _show_mindstream_tab(self, name: str) -> None:
        """Switch the compact evidence view without starting any new work."""
        if name not in getattr(self, "mindstream_pages", {}):
            return
        self._mindstream_active_tab = name
        for key, page in self.mindstream_pages.items():
            page.pack_forget()
            button = self.mindstream_tab_buttons[key]
            selected = key == name
            button.configure(
                bg=self.ROYAL if selected else self.PANEL_2,
                fg=self.WHITE if selected else self.MUTED,
            )
        self.mindstream_pages[name].pack(fill="both", expand=True)

    def _mindstream_event(self, thought: Any) -> None:
        """Receive one already-sanitised narration line from any thread."""
        event = str(getattr(thought, "event", "event"))
        text = str(getattr(thought, "text", thought))
        if event.startswith("RESEARCH"):
            source = "research"
        elif event.startswith("TOOL"):
            source = "tools"
        elif event.startswith("MODEL"):
            source = "model"
        elif event.startswith("TASK"):
            source = "plan"
        else:
            source = "focus"
        level = "error" if event.endswith("FAILED") else "notable" if event.endswith("STARTED") else ""
        self._post(self._mindstream_add, source, text, level)

    def _mindstream_add(self, source: str, text: str, level: str = "") -> None:
        if self._closed or self._mindstream_paused or not hasattr(self, "mindstream_feed"):
            return
        self.mindstream_feed.write(source, text, level=level)

    def _toggle_mindstream(self) -> None:
        self._mindstream_paused = not self._mindstream_paused
        self.mindstream_pause_button.configure(
            text="Resume Feed" if self._mindstream_paused else "Pause Feed")
        if self._mindstream_paused:
            self.mindstream_status_var.set("VIEW PAUSED · WORK CONTINUES")
            self.mindstream_live_dot.configure(fg=self.MUTED)
        if not self._mindstream_paused:
            self._mindstream_add("system", "Live operational view resumed", "notable")

    def _clear_mindstream(self) -> None:
        self.mindstream_feed.clear()

    def _show_mindstream_details(self) -> None:
        """Open an evidence-backed snapshot without blocking the Tk loop."""
        def gather() -> str:
            activity = ACTIVITY.summary()
            learner = getattr(self.runtime, "learner", None)
            learning = learner.status() if learner is not None else {}
            queue = learning.get("queue") or {}
            predictions = queue.get("predictions") or {}
            thoughts = getattr(self.runtime, "thoughts", None)
            recent = thoughts.recent(20) if thoughts is not None else []
            assessment = getattr(self.runtime.conversation, "last_assessment", None)
            lines = [
                "LIVE MINDSTREAM · OPERATIONAL EVIDENCE",
                "This report contains lifecycle summaries, not private chain-of-thought.",
                "",
                f"current focus       {activity.get('headline') or 'ready'}",
                f"active operations   {activity.get('count', 0)}",
                f"learner             {'running' if learning.get('running') else 'idle'}"
                f" · {'paused' if learning.get('paused') else learning.get('reason', 'ready')}",
                f"learning queue      {(queue.get('counts') or {}).get('pending', 0)} pending",
                f"predictions         {predictions.get('prepared', 0)} prepared"
                f" · {float(predictions.get('precision', 0) or 0) * 100:.0f}% verified precision",
                (f"last answer         {assessment.depth.label} · "
                 f"{'complete' if assessment.complete else 'incomplete'} · "
                 f"{assessment.word_count} words") if assessment is not None else "last answer         not assessed",
                "",
                "Recent operational events:",
            ]
            lines.extend(f"  · {line}" for line in recent)
            return "\n".join(lines)

        self._background("Live Mindstream", gather)

    def _watch_tools(self) -> None:
        """Put every tool run on screen, now that none of them stop to ask.

        The approval prompt was doing two jobs. One was authorising the action.
        The other, which nobody designed and everybody relied on, was telling
        Mohsin the action was happening at all. Turning the default policy to
        ALLOW removes the first and would silently remove the second, leaving
        a system that acts on its own and never mentions it.

        The events were already published and nothing was listening to them.
        """
        events = getattr(self.runtime, "events", None)
        if events is None or not hasattr(events, "subscribe"):
            return
        self._tool_event_unsubscribers.append(events.subscribe('PRIVACY_MODE_CHANGED', self._preview_privacy_event))
        for event_type in ("TOOL_STARTED", "TOOL_COMPLETED", "TOOL_FAILED"):
            try:
                self._tool_event_unsubscribers.append(events.subscribe(event_type, self._tool_event))
            except Exception:
                log_swallowed(_log, "subscribing to tool events")
                continue

    def _tool_event(self, event: dict[str, Any]) -> None:
        """One line per tool run, from a background thread."""
        self._post(self._operation_event, event)
        payload = event.get("payload") or {}
        tool = str(payload.get("tool") or "tool")
        kind = str(event.get("event_type") or "")
        if kind == "TOOL_STARTED":
            self._post(self.console.write, "tools", f"{tool} running")
        elif kind == "TOOL_COMPLETED":
            self._post(self.console.write, "tools", f"{tool} done")
        elif kind == "TOOL_FAILED":
            # A failure it chose to attempt on its own is the single most
            # useful line here, so it is the one that gets the operator's eye.
            self._post(self.console.write, "tools",
                       f"{tool} failed: {str(payload.get('error') or '')[:160]}", level="error")

    def _output_event(self, source: str, text: str) -> None:
        if self._closed:
            return
        if source == "voice_transcript":
            self._post(self._append, "user", "You\n" + text.removeprefix("You: "))
        elif source == "voice":
            self._post(self._append, "system", text)
        elif source in {"thinking", "progress"}:
            self._post(self.console.write, source, text)
        elif source == "assistant_notice":
            # A search finished after its turn ended: it reports back on its own.
            self._post(self._append, "assistant", f"HAYAT\n{text}")

    def _refresh_standing_chip(self) -> None:
        """Show the standing instruction above the composer while it lasts."""
        chip = getattr(self, "standing_chip", None)
        if chip is None:
            return
        session = getattr(self.runtime, "session", None)
        lasting = dict(getattr(session, "standing_instruction", None) or {})
        if not lasting.get("rule"):
            chip.pack_forget()
            return
        left = lasting.get("remaining")
        self.standing_text.configure(
            text=f"Standing instruction: {lasting['rule']}"
                 + (f" · {left} answer{'s' if left != 1 else ''} left" if left else " · until you end it"))
        if not chip.winfo_manager():
            chip.pack(fill="x", pady=(6, 0), before=self._composer_actions)

    def _end_standing_instruction(self) -> None:
        session = getattr(self.runtime, "session", None)
        if session is not None:
            session.standing_instruction = {}
        self._refresh_standing_chip()

    def _teams_line(self) -> str:
        """Agent teams in one line: what is running and what this machine allows. Click opens the workspace."""
        teams = getattr(self.runtime, "teams", None)
        if teams is None or not callable(getattr(teams, "capacity", None)):
            return ""
        try:
            running = teams.status()["running"]
            capacity = teams.capacity()
        except Exception:
            log_swallowed(_log, "reading agent team status for the home card")
            return ""
        busy = (f"{len(running)} running ("
                + ", ".join(f"{row.get('current_worker') or 'starting'} {row.get('done', 0)}/{row.get('total', '?')}"
                            for row in running[:2]) + ")") if running else "none running"
        return (f"👥 Agent teams: {busy} · up to {capacity.max_workers} workers each · "
                f"{capacity.concurrent_teams} at once · open team workspace")

    def _paint_running(self, activity: dict[str, Any]) -> None:
        """List every running operation, and the background learner's state, in plain words."""
        if not hasattr(self, "running_text"):
            return
        from app.gui_rich import activity_line

        rows = [(activity_line(item), "background" if item.get("kind") in {"learning", "service"} else "")
                for item in activity.get("running") or []]
        learner = getattr(self.runtime, "learner", None)
        if learner is not None and not any(item.get("kind") == "learning" for item in activity.get("running") or []):
            rows.append(("📚 Background learner: " + ("on · waiting for its next topic"
                                                     if getattr(learner, "running", False) else "off"), "idle"))
        if not any(tag != "idle" for _line, tag in rows):
            rows.insert(0, ("Nothing running in the foreground · HAYAT is ready", "idle"))
        teams_line = self._teams_line()
        if teams_line:
            rows.append((teams_line, "teams"))
        painted = "\n".join(line for line, _tag in rows)
        if painted == self._running_painted:
            return
        self._running_painted = painted
        self.running_text.configure(state="normal", height=4)
        self.running_text.delete("1.0", "end")
        for index, (line, tag) in enumerate(rows):
            self.running_text.insert("end", line + ("\n" if index < len(rows) - 1 else ""), tag)
        self.running_text.configure(state="disabled")

    def _report_running(self) -> None:
        """Everything running, and what the background learner and research jobs are doing, in the chat."""
        def gather() -> str:
            lines = [ACTIVITY.report()]
            learner = getattr(self.runtime, "learner", None)
            if learner is not None:
                try:
                    status = learner.status()
                    counts = (status.get("queue") or {}).get("counts") or {}
                    lines.append(f"Background learner: {'running' if status.get('running') else 'stopped'}"
                                 f" · may run: {status.get('may_run')} ({status.get('reason') or 'no limit hit'})"
                                 f" · queued topics: {counts.get('pending', 0)} · learned: {counts.get('learned', 0)}")
                except Exception as exc:
                    lines.append(f"Background learner status unavailable: {type(exc).__name__}")
            jobs = getattr(self.runtime, "research_jobs", None)
            if jobs is not None and hasattr(jobs, "status_text"):
                lines.append(jobs.status_text())
            return "\n".join(lines)

        self._background("Running now", gather)

    def _screenshot_after_action(self) -> None:
        """What the screen looks like after HAYAT acted on it, shown in the working view."""
        def grab() -> None:
            try:
                from PIL import ImageGrab

                image = ImageGrab.grab()
            except Exception:
                return
            # One file, overwritten each time, so a click can open it full size.
            from app.config import PROJECT_ROOT

            target = PROJECT_ROOT / "data" / "artifacts" / "working" / "after-last-action.png"
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                image.save(target)
            except OSError:
                target = None
            self._post(self._working_view.show_screenshot, image, target)

        threading.Thread(target=grab, name="hayat-action-screenshot", daemon=True).start()

    def _voice_response(self, response) -> None:
        if response.display_response:
            self._post(self._append, "assistant", f"HAYAT\n{response.display_response}")

    def _tick(self) -> None:
        if self._closed:
            return
        # Keep polling even when a single widget/status source fails.
        self._schedule(250, self._tick)
        self._drain_voice_results()
        if getattr(self, "_stream_preview", None) is not None:
            self._stream_preview.drain()
        for pane in self._explorer_panels.values():
            try:
                pane.drain()
            except Exception as exc:
                self._record_background_failure("Workspace refresh", exc)
        activity = ACTIVITY.summary()
        self._paint_running(activity)
        if self._working_since is not None:
            finished = self._working_view.update_from(activity.get("running") or [])
            if finished:
                self._screenshot_after_action()
        voice = self.runtime.voice.status() if self.runtime.voice else {"state": "off", "mode": "off"}
        self.activity_var.set_if_changed(format_activity_countdown(
            activity, self._working_since,
            fallback_estimate=ACTIVITY.estimate("model", "thinking"),
        ))
        self.activity_progress_var.set_if_changed(activity_progress(
            activity, self._working_since,
            fallback_estimate=ACTIVITY.estimate("model", "thinking"),
        ))
        privacy = " · privacy" if self.runtime.authority.privacy_mode else ""
        child = " · child-safe" if self.runtime.learning.guardian.active else ""
        self.health_var.set_if_changed(f"{self._resource_text}{privacy}{child}")
        self.voice_button.configure(text="Stop voice" if voice.get("enabled") else "Start voice")
        from app.gui_voice import voice_summary
        self.voice_hint.set_if_changed("Updating voice…" if self._voice_action_busy
                            else voice_summary(voice) + " · Voice settings")
        self.privacy_button.configure(text="Privacy on" if self.runtime.authority.privacy_mode else "Privacy off")
        self.camera_button.configure(text="Camera off" if self._camera_on else "Camera on")
        # The eye workspace clears stale frames itself and keeps Start camera accessible.
        self._follow_camera()
        # Paints the last reading. Taking a fresh one here would put 800ms of
        # device probing on a 250ms tick -- see _refresh_sensors.
        self._paint_sensors()
        self._paint_wifi_sensing()
        from app.gui_radar import paint_panel as paint_radar_panel
        paint_radar_panel(self)
        from app.gui_network import guard_tick
        guard_tick(self)
        self._paint_metrics()
        self.depth_nav.set_status(activity, voice, self._camera_on)
        self.camera_eye.set_active(self._camera_on)
        self._paint_watch_state()
        studio = getattr(self, "_control_studio", None)
        if studio is not None and studio.winfo_exists():
            studio.update_live(activity, voice, self._camera_on)
        if self._operator_frame is not None:
            self.brain_graph.set_activity(activity, voice, self._camera_on)
        self._paint_mindstream(activity)
        self._paint_statusbar(voice)
        self._paint_transparency()
        self.clock_var.set_if_changed(time.strftime("%I:%M %p\n%b %d, %Y"))
        hour = time.localtime().tm_hour
        part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        self.greeting_var.set_if_changed(f"Good {part}, {IDENTITY.owner_short} 👋")
        self.header_clock.set_if_changed(time.strftime("%I:%M %p  ·  %b %d, %Y").lstrip("0"))
        self._paint_info()
        if time.monotonic() >= self._next_health_check and not self._health_check_running:
            self._health_check_running = True
            self._next_health_check = time.monotonic() + 5
            threading.Thread(target=self._refresh_health, name="hayat-gui-health", daemon=True).start()
        if time.monotonic() >= self._next_sensor_poll and not self._sensor_poll_running:
            self._sensor_poll_running = True
            self._next_sensor_poll = time.monotonic() + 2
            threading.Thread(target=self._refresh_sensors, name="hayat-gui-sensors", daemon=True).start()
        if time.monotonic() >= self._next_slow_poll and not self._slow_poll_running:
            self._slow_poll_running = True
            self._next_slow_poll = time.monotonic() + 20
            threading.Thread(target=self._refresh_slow, name="hayat-gui-slow", daemon=True).start()

    def _refresh_health(self) -> None:
        """Take the measurements. Off-thread, because psutil and nvidia-smi
        both take their time and neither is worth a stalled window."""
        try:
            system = self.runtime.observability.system()
            cpu = system.get("cpu_percent")
            ram = system.get("ram", {}).get("used_percent")
            gpu_rows = system.get("gpu", [])
            gpu = next((row for row in gpu_rows if "utilization_percent" in row), {})
            gpu_use = gpu.get("utilization_percent")
            disk = system.get("disk", {})
            total = float(disk.get("total_bytes") or 0)
            free = float(disk.get("free_bytes") or 0)
            self._metrics = {
                "cpu": cpu, "ram": ram,
                "gpu": float(gpu_use) if isinstance(gpu_use, (int, float)) else None,
                "disk": ((total - free) / total * 100.0) if total else None,
                "gpu_name": gpu.get("name"), "vram_total": gpu.get("vram_total_mb"),
                "vram_used": gpu.get("vram_used_mb"), "temp": gpu.get("temperature_c"),
                "ram_total": system.get("ram", {}).get("total_bytes"),
                "ram_installed": system.get("ram", {}).get("installed_bytes"),
                "disk_total": total, "disk_free": free,
            }
            readings = [value for value in (cpu, ram, gpu_use) if isinstance(value, (int, float))]
            prefix = "High load" if readings and max(readings) >= 90 else "Healthy" if readings else "Health unavailable"
            gpu_text = f" · GPU {gpu_use:.0f}%" if isinstance(gpu_use, (int, float)) else ""
            cpu_text = f"{cpu:.0f}%" if isinstance(cpu, (int, float)) else "--"
            ram_text = f"{ram:.0f}%" if isinstance(ram, (int, float)) else "--"
            self._resource_text = f"{prefix} · CPU {cpu_text} · RAM {ram_text}{gpu_text}"
        except Exception as exc:
            self._record_background_failure("Health polling", exc)
            self._metrics = {}
            self._resource_text = "Health unavailable"
        finally:
            self._health_check_running = False

    def _refresh_slow(self) -> None:
        """Things worth knowing but not worth asking often.

        Listing models probes every configured provider, and counting tasks
        opens the database. Twenty seconds apart is far more often than either
        actually changes.
        """
        found: dict[str, Any] = {}
        try:
            found["models"] = self.runtime.router.available()
        except Exception as exc:
            self._record_background_failure("models status", exc)
            found["models"] = {}
        # Publish the inventory before slower memory/learning diagnostics finish.
        self._slow = {**self._slow, "models": found["models"]}
        try:
            with self.runtime.memory.connect() as db:
                found["tasks"] = int(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        except Exception as exc:
            self._record_background_failure("tasks status", exc)
            found["tasks"] = None
        try:
            found["internet"] = bool(
                self.runtime.state._internet(live=False).get("available"))
        except Exception as exc:
            self._record_background_failure("internet status", exc)
            found["internet"] = None
        try:
            found["knowledge"] = self.runtime.research.knowledge.stats()
        except Exception as exc:
            self._record_background_failure("knowledge status", exc)
            found["knowledge"] = {}
        try:
            found["learning"] = self.runtime.learner.status() if self.runtime.learner else {}
        except Exception as exc:
            self._record_background_failure("learning status", exc)
            found["learning"] = {}
        try:
            found["training"] = self.runtime.training.status()
        except Exception as exc:
            self._record_background_failure("training status", exc)
            found["training"] = {}
        try:
            found["authority"] = self.runtime.authority.status()
        except Exception as exc:
            self._record_background_failure("authority status", exc)
            found["authority"] = {}
        try:
            found["memory"] = self.runtime.memory.stats()
        except Exception as exc:
            self._record_background_failure("memory status", exc)
            found["memory"] = {}
        try:
            skill_learner = getattr(self.runtime.research, "skill_learner", None)
            found["skills"] = skill_learner.status() if skill_learner else {}
            found["skills"]["trusted"] = len(getattr(self.runtime.skills, "skills", {}) or {})
        except Exception as exc:
            self._record_background_failure("skills status", exc)
            found["skills"] = {}
        try:
            found["routing"] = self.runtime.intelligent_router.status()
        except Exception as exc:
            self._record_background_failure("routing status", exc)
            found["routing"] = {}
        try:
            found["metacognition"] = self.runtime.metacognition.status()
        except Exception as exc:
            self._record_background_failure("metacognition status", exc)
            found["metacognition"] = {}
        try:
            budget = getattr(self.runtime, "context_budget", None)
            found["context_budget"] = budget.status() if budget is not None else {}
        except Exception as exc:
            self._record_background_failure("context budget status", exc)
            found["context_budget"] = {}
        # Read like the others: an exception here used to skip the reset below, so the slow poll stopped for good.
        conversation = getattr(self.runtime, "conversation", None)
        found["answer_cache"] = {
            "exact": int(getattr(conversation, "answer_cache_hits", 0) or 0),
            "paraphrase": int(getattr(conversation, "semantic_answer_cache_hits", 0) or 0),
        }
        self._slow = found
        self._slow_poll_running = False

    def _paint_metrics(self) -> None:
        """Dials and cards from the last measurement, or `--` when there is none.

        A dial resting at 0% and a dial that could not be read look identical,
        and only one of them is good news -- so an absent reading shows as
        absent rather than as zero.
        """
        metrics = getattr(self, "_metrics", {})
        self.resource_charts.set_metrics(metrics)
        for key, gauge in self.gauges.items():
            gauge.set(metrics.get(key))
        self._folds.summarise("System Status", " · ".join(
            f"{label} {metrics[key]:.0f}%" for key, label in (("cpu", "CPU"), ("ram", "RAM"), ("gpu", "GPU"), ("disk", "Disk"))
            if isinstance(metrics.get(key), (int, float))))
        if self.console.last_line:
            self._folds.summarise("Live Console", self.console.last_line)
        for key, variable in self.metric_vars.items():
            value = metrics.get(key)
            variable.set_if_changed(f"{key.upper()} {value:.0f}%" if isinstance(value, (int, float))
                         else f"{key.upper()} --")
        if time.monotonic() - getattr(self, "_wallet_checked", 0.0) > 30:
            self.refresh_wallet_badge()

        name = metrics.get("gpu_name")
        vram_total, vram_used = metrics.get("vram_total"), metrics.get("vram_used")
        self.cards["gpu"].set(
            str(name).replace("NVIDIA ", "").replace("Laptop GPU", "").strip()[:24]
            if name else "No GPU reported",
            f"{(vram_used or 0) / 1024:.1f} / {vram_total / 1024:.0f} GB VRAM"
            if vram_total else "")
        # Installed first, because that is the number Mohsin knows he bought.
        # Windows only offers 39.7 of his 48 GiB -- the rest is reserved for
        # hardware before it ever sees it -- and showing only the smaller
        # figure made HAYAT look like it could not read its own machine.
        installed, usable = metrics.get("ram_installed"), metrics.get("ram_total")
        if installed and usable and installed - usable > 512 * 1024**2:
            self.cards["ram"].set(
                f"{human_bytes(installed)} installed",
                f"{human_bytes(usable)} usable · {metrics.get('ram', 0):.0f}% in use")
        else:
            self.cards["ram"].set(human_bytes(installed or usable) if (installed or usable) else "--",
                                  f"{metrics['ram']:.0f}% in use" if metrics.get("ram") else "")
        self.cards["storage"].set(
            human_bytes(metrics.get("disk_total")) if metrics.get("disk_total") else "--",
            f"{human_bytes(metrics.get('disk_free'))} free" if metrics.get("disk_free") else "")
        temperature = metrics.get("temp")
        self.cards["temp"].set(
            f"{temperature:.0f} °C" if isinstance(temperature, (int, float)) else "--",
            ("Hot" if isinstance(temperature, (int, float)) and temperature >= 85
             else "Normal" if isinstance(temperature, (int, float)) else "not reported"))

        models = self._slow.get("models") or {}
        if "models" in self._slow:
            active = getattr(self.runtime.model, "model", "") or ""
            self.model_chips.set_models(models, active=active)
            count = len(models) if hasattr(models, "__len__") else 0
            self._folds.summarise("Models Available", f"{count} reachable" + (f" · using {active}" if active else ""))
        intelligence = getattr(self, "intelligence_rows", {})
        knowledge = self._slow.get("knowledge") or {}
        learning = self._slow.get("learning") or {}
        training = self._slow.get("training") or {}
        authority = self._slow.get("authority") or {}
        control = self._slow.get("metacognition") or {}
        queue_counts = (learning.get("queue") or {}).get("counts") or {}
        if intelligence:
            intelligence["research"].set_if_changed(
                f"{knowledge.get('knowledge', 0)} summaries / {knowledge.get('claims', 0)} claims")
            intelligence["evidence"].set_if_changed(
                f"{knowledge.get('stale', 0)} stale / {knowledge.get('disputed_claims', 0)} disputed")
            intelligence["learning"].set_if_changed(
                f"{'running' if learning.get('running') else 'idle'} / {queue_counts.get('pending', 0)} queued")
            intelligence["training"].set_if_changed(
                f"{training.get('eligible', 0)} eligible / {training.get('private_local_records', 0)} private")
            intelligence["control"].set_if_changed(
                f"{int(control.get('curriculum_candidates', 0) or 0)} gaps / verified delegation")
            intelligence["authority"].set_if_changed(
                f"{len(authority.get('auto_categories') or [])} local auto / "
                f"{len(authority.get('always_protected') or [])} protected")
        brain = getattr(self, "brain_card_vars", {})
        if brain:
            memory = self._slow.get("memory") or {}
            memory_total = sum(int(value or 0) for value in (memory.get("memories") or {}).values())
            predictions = (learning.get("queue") or {}).get("predictions") or {}
            skills = self._slow.get("skills") or {}
            cache = self._slow.get("answer_cache") or {}
            brain["memory"][0].set_if_changed(f"{memory_total:,}")
            brain["memory"][1].set_if_changed(f"{int(memory.get('associations', 0) or 0):,} neural links")
            brain["knowledge"][0].set_if_changed(f"{int(knowledge.get('claims', 0) or 0):,}")
            brain["knowledge"][1].set_if_changed(f"{int(knowledge.get('knowledge', 0) or 0):,} summaries")
            budget_status = self._slow.get("context_budget") or {}
            ceiling = int(budget_status.get("machine_ceiling") or 0)
            brain["context"][0].set_if_changed(f"{ceiling // 1024}k" if ceiling else "--")
            brain["context"][1].set_if_changed("paged recall beyond window")
            brain["prediction"][0].set_if_changed(f"{int(predictions.get('prepared', 0) or 0):,}")
            brain["prediction"][1].set_if_changed(
                f"{float(predictions.get('precision', 0) or 0) * 100:.0f}% verified matches")
            brain["skills"][0].set_if_changed(
                f"{int(skills.get('trusted', 0) or 0)} / {int(skills.get('candidates', 0) or 0)}")
            brain["skills"][1].set_if_changed("trusted / review")
            try:
                from app.brain_mesh import BrainMesh

                node_count = len(BrainMesh.NODES)
            except Exception:
                node_count = 0
            tool_count = len(self.runtime.registry.names())
            self.brain_overview.set_if_changed(f"{memory_total:,} memories  ·  {tool_count} tools available")
            brain["operators"][0].set_if_changed(f"{node_count} / {tool_count}")
            brain["operators"][1].set_if_changed("adaptive control / live tools")
            voice_status = self.runtime.voice.status() if self.runtime.voice else {}
            brain["voice"][0].set_if_changed(str(voice_status.get("state", "off")).title())
            brain["voice"][1].set_if_changed(
                f"{voice_status.get('stt_provider', '-')} / {voice_status.get('tts_provider', '-')}")
            total_hits = int(cache.get("exact", 0) or 0) + int(cache.get("paraphrase", 0) or 0)
            brain["speed"][0].set_if_changed(f"{total_hits:,}")
            brain["speed"][1].set_if_changed(f"{int(cache.get('paraphrase', 0) or 0)} strict paraphrases")
        media = getattr(self, "media_rows", {})
        if media:
            voice_status = self.runtime.voice.status() if self.runtime.voice else {}
            media["speech"].set_if_changed(
                f"{voice_status.get('mode', 'off')} · {voice_status.get('stt_provider', '-')} → {voice_status.get('tts_provider', '-')}")
            capture = voice_status.get("last_capture") or {}
            media["capture"].set_if_changed(
                f"{capture.get('duration_seconds', capture.get('seconds', 0)) or 0}s · "
                f"{capture.get('reason', 'waiting')}")
            observation = getattr(self.runtime.camera, "last_observation", None) or {}
            media["vision"].set_if_changed(
                f"{int(observation.get('frames_analyzed', 0) or 0)} analyzed / "
                f"{int(observation.get('frames_captured', 0) or 0)} captured")
            media["operator"].set_if_changed(f"{len(self.runtime.registry.names())} tools · local authority")
        if hasattr(self, "learning_toggle_button"):
            if learning.get("running") and learning.get("paused"):
                label = "Resume Learning"
            elif learning.get("running"):
                label = "Pause Learning"
            else:
                label = "Start Learning"
            self.learning_toggle_button.configure(text=label)

    def _paint_mindstream(self, activity: dict[str, Any]) -> None:
        """Paint cached operational state; never probe storage on the UI loop."""
        rows = getattr(self, "mindstream_rows", {})
        if not rows:
            return
        learning = self._slow.get("learning") or {}
        queue = learning.get("queue") or {}
        counts = queue.get("counts") or {}
        topics = queue.get("topics") or []
        predictions = queue.get("predictions") or {}

        headline = str(activity.get("headline") or "Ready")
        if activity.get("count", 0) > 1:
            headline += f" +{int(activity.get('count', 0)) - 1}"
        rows["focus"].set_if_changed(headline[:62])
        active_count = int(activity.get("count", 0) or 0)
        self.mindstream_activity_var.set_if_changed(
            f"{active_count} active" if active_count else "standing by")
        self.mindstream_progress_var.set_if_changed(activity_progress(
            activity, self._working_since,
            fallback_estimate=ACTIVITY.estimate("model", "thinking"),
        ))

        active_topic = next((item for item in topics if item.get("status") == "learning"), None)
        learner_state = "paused" if learning.get("paused") else (
            "running" if learning.get("running") else "idle")
        topic_text = str((active_topic or {}).get("topic") or "")
        rows["learning"].set_if_changed(
            f"{learner_state} · {counts.get('pending', 0)} queued" +
            (f" · {topic_text[:34]}" if topic_text else ""))
        next_learning = active_topic or next(
            (item for item in topics if item.get("status") == "pending"), None)
        next_topic = str((next_learning or {}).get("topic") or "No verified gap is waiting")
        self.mindstream_learning_detail.set_if_changed(
            f"STATE   {learner_state.upper()}\n"
            f"QUEUE   {counts.get('pending', 0)} pending · {counts.get('learned', 0)} learned\n"
            f"DEPTH   {str(learning.get('research_level', 'off')).upper()}\n"
            f"NEXT    {next_topic[:150]}")

        anticipated = next(
            (item for item in topics if item.get("source") == "anticipated_follow_up"
             and item.get("status") in {"pending", "learning"}),
            None,
        )
        prediction_text = str((anticipated or {}).get("topic") or "")
        prepared = int(predictions.get("prepared", 0) or 0)
        precision = float(predictions.get("precision", 0) or 0) * 100
        rows["prediction"].set_if_changed(
            f"{prepared} prepared · {precision:.0f}% verified" +
            (f" · {prediction_text[:26]}" if prediction_text else ""))
        self.mindstream_prediction_detail.set_if_changed(
            f"PREPARED   {prepared}\n"
            f"VERIFIED   {int(predictions.get('matched', 0) or 0)} matched · "
            f"{int(predictions.get('different_question', 0) or 0)} different\n"
            f"PRECISION  {precision:.0f}% from real follow-up outcomes\n"
            f"EXPECTED   {prediction_text[:140] if prediction_text else 'No pending prediction'}")

        assessment = getattr(self.runtime.conversation, "last_assessment", None)
        if assessment is None:
            verification = "Waiting for an assessed answer"
        elif assessment.complete:
            verification = f"passed · {assessment.depth.label} · {assessment.word_count} words"
        else:
            missing = len(getattr(assessment, "missing", ()) or ())
            verification = f"needs follow-up · {missing} missing"
        rows["verification"].set_if_changed(verification)

        if not self._mindstream_paused:
            self._mindstream_pulse = not self._mindstream_pulse
            if active_count:
                running = activity.get("running") or []
                kind = str((running[-1] if running else {}).get("kind") or "working")
                self.mindstream_status_var.set_if_changed(f"LIVE · {kind.upper()}")
                self.mindstream_live_dot.configure(
                    fg=self.GOLD if self._mindstream_pulse else self.GOLD_SOFT)
            elif learner_state == "running":
                self.mindstream_status_var.set_if_changed("LIVE · LEARNING WATCH")
                self.mindstream_live_dot.configure(
                    fg=self.OLIVE if self._mindstream_pulse else self.OLIVE_SOFT)
            else:
                self.mindstream_status_var.set_if_changed("LIVE · READY")
                self.mindstream_live_dot.configure(fg=self.OLIVE)

        # Add state transitions to the event feed, but only when the slow
        # snapshot changes. This makes background learning and prediction
        # preparation visible without writing four duplicate lines a second.
        state = (learner_state, counts.get("pending", 0), prepared, round(precision),
                 prediction_text, verification)
        if state != self._mindstream_state:
            self._mindstream_state = state
            self._mindstream_add(
                "learning", f"{learner_state}; {counts.get('pending', 0)} queued; "
                f"{prepared} predictions prepared")

    def _paint_info(self) -> None:
        """The named parts of this machine, from what was measured.

        Deliberately not the same information as the dials. They say how busy
        it is; this says what it is -- which is the question anyone actually
        asks about a machine they are not sitting in front of.
        """
        metrics = getattr(self, "_metrics", {})
        if not metrics:
            return
        gpu = str(metrics.get("gpu_name") or "").replace("NVIDIA ", "").strip()
        vram = metrics.get("vram_total")
        self.info_rows["gpu"].set(
            f"{gpu[:30]}" + (f"  ·  {vram / 1024:.0f} GB VRAM" if vram else "")
            if gpu else "not reported")

        installed, usable = metrics.get("ram_installed"), metrics.get("ram_total")
        self.info_rows["ram"].set(
            f"{human_bytes(installed)} installed  ·  {human_bytes(usable)} usable"
            if installed and usable else human_bytes(usable or installed) or "--")

        total, free = metrics.get("disk_total"), metrics.get("disk_free")
        self.info_rows["disk"].set(
            f"{human_bytes(total)} total  ·  {human_bytes(free)} free"
            if total else "--")

        temperature = metrics.get("temp")
        self.info_rows["temp"].set(
            f"{temperature:.0f} °C  ·  {'hot' if temperature >= 85 else 'normal'}"
            if isinstance(temperature, (int, float)) else "not reported")
        self.info_rows["uptime"].set(human_duration(time.monotonic() - self._started_at))
        self.info_rows["cpu"].set(self._cpu_name())
        self._folds.summarise("System Info", " · ".join(part for part in (
            gpu.replace("Laptop GPU", "").strip()[:22], human_bytes(installed or usable) + " RAM" if installed or usable else "",
            "up " + human_duration(time.monotonic() - self._started_at)) if part))

    _cpu_cache: str = ""

    @classmethod
    def _cpu_name(cls) -> str:
        """The processor's own name. Asked once; it does not change."""
        if cls._cpu_cache:
            return cls._cpu_cache
        import platform

        name = ""
        try:
            # platform.processor() on Windows is the CPUID family string ("AMD64 Family 26 Model 68 Stepping 0,
            # AuthenticAMD"); the marketing name the owner knows is in the registry.
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                name = " ".join(str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).split())
        except (ImportError, OSError):
            name = ""
        cls._cpu_cache = (name or platform.processor() or platform.machine() or "unknown")[:44]
        return cls._cpu_cache

    def _paint_statusbar(self, voice: dict[str, Any]) -> None:
        # Same wording as the voice shell, so the two never disagree.
        words = NeuralWorkspace.VOICE_FORMS[NeuralWorkspace.voice_form(voice)][2].removeprefix("Voice ")
        self.pills["voice"].set(words[:1].upper() + words[1:], bool(voice.get("enabled")))
        internet = self._slow.get("internet")
        self.pills["internet"].set(
            {True: "Connected", False: "Offline", None: "checking…"}[internet], internet)
        loaded = sum(len(names) for names in (self._slow.get("models") or {}).values())
        self.pills["models"].set(f"{loaded} available", loaded > 0)
        # What the last turn was actually given, so a shrunk window is visible
        # rather than mistaken for the model losing the thread.
        budget = getattr(self.runtime, "context_budget", None)
        last = budget.last if budget is not None else None
        if last is None:
            self.pills["context"].set("idle", False)
        else:
            self.pills["context"].set(
                f"{last.context_tokens // 1024}k win · {last.answer_tokens // 1024}k out",
                None if last.constrained_by else True)

        # Whether the last answer passed the completeness check, and how long
        # it was. A silent quality gate is one nobody can tell is working.
        check = getattr(self.runtime.conversation, "last_assessment", None)
        if check is None:
            self.pills["answer"].set("none yet", False)
        elif check.complete:
            self.pills["answer"].set(f"{check.depth.label} · {check.word_count}w", True)
        else:
            self.pills["answer"].set(
                f"{check.depth.label} · incomplete" if not check.missing
                else f"missing {len(check.missing)}", None)

        # Phone/browser access: off when not serving, otherwise how many devices
        # are connected right now -- highlighted the moment one attaches.
        if self._web_server is None:
            self.pills["phone"].set("off", False)
        else:
            try:
                connected = self._web_server.active_users()
            except Exception:
                connected = 0
            self.pills["phone"].set(f"{connected} on" if connected else "ready", connected > 0)

        tasks = self._slow.get("tasks")
        self.pills["tasks"].set("checking…" if tasks is None else str(tasks), tasks is not None)
        self.pills["uptime"].set(human_duration(time.monotonic() - self._started_at), True)

        online = not self._closed and bool(self.runtime.model or self.runtime.registry.names())
        self.online_var.set("System Online" if online else "Limited")
        self.online_dot.configure(fg=self.OLIVE if online else self.GOLD)

    def run_command(self) -> None:
        """Send a slash command from the window.

        The same handler the terminal uses, so `/doctor` here and `/doctor`
        there cannot mean different things. Anything that is not a command is
        treated as something said, because typing a sentence into a box and
        having nothing happen is the worse surprise.
        """
        text = self.command_var.get().strip()
        if not text:
            return
        self.command_var.set("")
        self.console.write("you", text, level="notable")
        if not text.startswith("/"):
            self.input.insert("1.0", text)
            self.send()
            return

        def run() -> str:
            from app.cli import handle_extended_command

            captured: list[str] = []
            from app.output import get_output

            listener = lambda source, line: captured.append(str(line))
            get_output().add_listener(listener)
            try:
                handled = handle_extended_command(self.runtime, text)
            finally:
                try:
                    get_output().remove_listener(listener)
                except Exception:
                    log_swallowed(_log, "removing a command output listener")
            if not handled:
                return f"{text} is not a command I know here."
            return "\n".join(captured) or f"{text} ran."

        self._background(text, run)

    def refresh_models(self) -> None:
        """Ask the providers again, now, rather than waiting for the poll."""
        self._next_slow_poll = 0.0
        self.console.write("models", "re-checking which models are reachable")

    def _toggle_voice(self) -> None:
        dialog = getattr(self, "_voice_startup_dialog", None)
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()
        self._request_voice("toggle")

    def _request_voice(self, action: str, complete=None) -> None:
        if self._closed:
            return
        if self.runtime.registry.interlock.halted and action not in {'quiet', 'push_to_talk', 'continuous', 'wake_word'}:
            message = 'Emergency stop active. Resume tools before starting voice.'
            self.voice_hint.set(message)
            if complete is not None:
                complete(message)
            return
        if self._voice_action_busy:
            if complete is not None:
                complete("A voice change is already in progress. The live status will update when it finishes.")
            return
        self._voice_action_busy = True
        self.voice_hint.set("Updating voice…")

        def work():
            from app.gui_voice import change_voice
            try:
                result, error = change_voice(self.runtime.voice, action), False
            except Exception as exc:
                result, error = str(exc), True
            self._voice_results.put((result, error, complete))

        threading.Thread(target=work, name="hayat-voice-control", daemon=True).start()

    def _drain_voice_results(self) -> None:
        while not self._voice_results.empty():
            result, error, complete = self._voice_results.get_nowait()
            self._voice_action_busy = False
            self._append("error" if error else "system", result)
            if complete is not None:
                complete(result)
            if not error:
                self._install_approval()

    def _offer_voice_startup(self) -> None:
        """Ask for microphone access each launch before starting any listener."""
        voice = self.runtime.voice
        if (voice is None or not voice.config.get("enabled", True)
                or getattr(self.runtime, "safe_mode", False) or voice.status().get("enabled")):
            return
        dialog = getattr(self, "_voice_startup_dialog", None)
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            return
        from app.gui_windows import place_over

        dialog = tk.Toplevel(self.root)
        self._voice_startup_dialog = dialog
        dialog.title("Voice permission")
        dialog.configure(bg=self.PANEL)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        body = tk.Frame(dialog, bg=self.PANEL, padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Start voice for this session?", bg=self.PANEL, fg=self.WHITE,
                 font=("Segoe UI Semibold", 12), anchor="w").pack(fill="x")
        tk.Label(body, text="The microphone is off. Allow voice to use local speech recognition now, "
                 "or start it later with the Mic button.", bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9), justify="left", wraplength=380).pack(fill="x", pady=(9, 18))
        actions = tk.Frame(body, bg=self.PANEL)
        actions.pack(fill="x")

        def close() -> None:
            dialog.destroy()

        def allow() -> None:
            close()
            if not voice.status().get("enabled"):
                self._request_voice("start")

        dialog.dismiss_button = ttk.Button(actions, text="Not now", style="Hayat.TButton", command=close)
        dialog.dismiss_button.pack(side="right")
        dialog.allow_button = ttk.Button(actions, text="Allow voice", style="HayatAccent.TButton",
                                         command=allow)
        dialog.allow_button.pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.bind("<Escape>", lambda _event: (close(), "break")[1])
        place_over(dialog, self.root, (440, 165))
        dialog.lift()
        dialog.dismiss_button.focus_set()

    def _probe_microphone(self) -> None:
        if not self.runtime.voice:
            self._append("error", "Voice is unavailable in this runtime.")
            return
        self._background("Microphone diagnostic", lambda: self.runtime.voice.probe_microphone(1.25))

    def _test_speaker(self) -> None:
        if not self.runtime.voice:
            self._append("error", "Voice is unavailable in this runtime.")
            return

        def test() -> str:
            self.runtime.voice.test_speaker(wait=True)
            return "The local speech output test completed."

        self._background("Voice output", test)

    def _multi_frame_look(self) -> None:
        self._append("system", "Comparing five camera frames for a clearer observation…")
        self._background("Multi-frame vision", lambda: self._tool(
            "camera_observe", {
                "prompt": "Describe stable visible details, meaningful changes, and readable text. Separate observation from inference.",
                "shots": 5, "interval_ms": 250, "analyze_frames": 3,
            }))

    def _continuous_voice(self) -> None:
        self._request_voice("start_continuous")

    def _stop(self) -> None:
        generation = getattr(self, "_generation_cancel", None)
        if generation is not None:
            generation.set()
        if self.runtime.voice:
            self.runtime.voice.tts.stop()
            self.runtime.voice.approvals.cancel()
        cancelled = self.runtime.agent.cancel_current()
        self._append("system", "Current task cancellation requested." if cancelled else "Speech stopped. No agent task was running.")

    def _toggle_privacy(self) -> None:
        active = not self.runtime.authority.privacy_mode
        self.runtime.authority.set_privacy_mode(active)
        if self.runtime.voice:
            self.runtime.voice.pause_for_privacy() if active else self.runtime.voice.resume_after_privacy()
        self._append("system", "Privacy mode is on." if active else "Privacy mode is off.")

    def _toggle_child(self) -> None:
        guardian = self.runtime.learning.guardian
        if guardian.active:
            pin = simpledialog.askstring(
                "Parent control", "Enter the parent PIN to leave child mode.", show="*",
                parent=self.root,
            )
            if pin is None:
                return
            try:
                guardian.disable(pin)
                self._append("system", "Child-safe teaching mode is off.")
            except PermissionError as exc:
                messagebox.showerror("Parent control", str(exc), parent=self.root)
        else:
            guardian.enable("child")
            self._append("system", "Child-safe teaching mode is active. Computer and private-resource actions are locked.")

    def _toggle_background_learning(self) -> None:
        learner = getattr(self.runtime, "learner", None)
        if learner is None:
            self._append("error", "The bounded background learner is unavailable.")
            return
        if learner.running:
            if learner.paused:
                learner.resume()
                self._append("system", "Background learning resumed within its local budget.")
            else:
                learner.pause("paused from the owner dashboard")
                self._append("system", "Background learning paused. Its queue and verified knowledge were kept.")
        else:
            learner.clear_stop_file()
            learner.resume()
            from app.config import load_settings

            settings = load_settings().get("learning", {}).get("continuous", {})
            interval = max(10.0, float(settings.get("interval_seconds", 900)))
            learner.start(topics=learner.next_topic, interval=interval)
            self._append("system", "Background learning started: local, budgeted, deduplicated, and reviewable.")
        self._mindstream_add(
            "learning", "Background learning resumed" if learner.running and not learner.paused
            else "Background learning paused", "notable")
        self._next_slow_poll = 0.0

    def _learn_next_now(self) -> None:
        learner = getattr(self.runtime, "learner", None)
        if learner is None:
            self._append("error", "The bounded background learner is unavailable.")
            return
        allowed, reason = learner.may_run()
        if not allowed:
            self._append("system", f"Learning did not start: {reason}.")
            return

        def work() -> Any:
            topic = learner.next_topic()
            if not topic:
                return {"action": "idle", "detail": "No verified knowledge gap is waiting."}
            return learner.run_cycle(topic).to_dict()

        self._owner_control("Learn next", work, self._learning_cycle_finished)

    def _adapt_learning(self) -> None:
        control = getattr(self.runtime, "metacognition", None)
        if control is None:
            self._append("error", "The adaptive control plane is unavailable.")
            return
        self._owner_control(
            "Adaptive curriculum", lambda: control.adapt(3),
            self._adaptive_curriculum_finished,
        )

    def _adaptive_curriculum_finished(self, result: dict[str, Any]) -> None:
        self._next_slow_poll = 0.0
        learning = result.get("learning") or result
        upgrades = result.get("upgrades") or {}
        topics = learning.get("topics") or []
        message = f"Adaptive control queued {learning.get('queued', 0)} verified learning gap(s)"
        message += f" and applied {len(upgrades.get('applied', []))} small reversible improvement(s)."
        if upgrades.get("needs_owner_review"):
            message += f" {len(upgrades['needs_owner_review'])} larger proposal(s) await review."
        if topics:
            message += " Next: " + str(topics[0])[:180]
        self._append("system", message)
        self._mindstream_add("learning", message, "notable")

    def _learning_cycle_finished(self, result: dict[str, Any]) -> None:
        self._next_slow_poll = 0.0
        action = result.get("action", "learning")
        detail = result.get("detail", "completed")
        self._append("system", f"Learning cycle {action}: {detail}")
        self._mindstream_add("learning", f"Cycle {action}: {detail}",
                             "notable" if result.get("ok", True) else "error")

    def _recover_learning(self) -> None:
        if self.runtime.learner is not None:
            self._background('Recover failed research', self.runtime.learner.retry_evidence_failures,
                             then=lambda _result: self._explorer_panels['background'].refresh())

    def _show_skill_learning(self) -> None:
        def gather() -> str:
            learner = getattr(self.runtime.research, "skill_learner", None)
            status = learner.status() if learner is not None else {}
            trusted = getattr(self.runtime.skills, "skills", {}) or {}
            lines = [
                f"trusted skills       {len(trusted)}",
                f"review candidates    {status.get('candidates', 0)}",
                f"candidate directory  {status.get('directory', 'unavailable')}",
                "promotion            explicit owner review",
                "automatic execution  disabled",
                "",
                "A candidate is created only after three similar successful research runs. "
                "It remains declarative and untrusted until you explicitly promote it.",
            ]
            latest = status.get("latest", []) or []
            if latest:
                lines.extend(["", "latest candidates:"])
                lines.extend(f"  · {name}" for name in latest)
            return "\n".join(lines)

        self._background("Skill learning", gather)

    def _show_learning(self) -> None:
        """What HAYAT taught itself while nobody was watching.

        Mohsin leaves this running for hours and had no way to see what it
        read in that time -- which is a strange thing to be unable to ask an
        assistant that learns. Background learning and things he asked for are
        listed apart, because "what did you go and read on your own" and "what
        did I ask you to look up" are different questions.
        """
        def gather() -> str:
            learner = self.runtime.learner
            queue = learner.queue_status(12) if learner else {}
            learner_status = learner.status() if learner else {}
            knowledge = self.runtime.research.knowledge.stats()
            compaction = getattr(self.runtime, "compaction", None)
            held = compaction.status() if compaction is not None else {}
            lines = [
                f"{knowledge.get('knowledge', 0)} summaries · "
                f"{knowledge.get('claims', 0)} verified claims · "
                f"queue {queue.get('counts', {})}",
                f"background learner: {'running' if learner and learner.running else 'stopped'}",
                f"may run now: {learner_status.get('may_run', False)}"
                f" ({learner_status.get('reason', 'unavailable')})",
                f"research depth: {learner_status.get('research_level', 'off')}"
                f" · idle reflection: {learner_status.get('idle_reflection', False)}"
                f" · candidates: {learner_status.get('reflection_candidates', 0)}",
                f"anticipated follow-ups: {learner_status.get('anticipate_followups', False)}"
                f" · per request: {learner_status.get('anticipated_per_request', 0)}",
                f"budget used: {learner_status.get('spent', {})}",
                (f"memory: {held.get('memories', 0)} entries, "
                 f"{held.get('bytes', 0) / 1024**2:.1f} MB "
                 f"({held.get('compressible', 0)} still compressible, "
                 f"{held.get('already_compacted', 0)} digested)") if held else "",
                "",
            ]
            runs = self._recent_research(10)
            if runs:
                lines.append("Read on its own (most recent first):")
                for row in runs:
                    if row["asked"]:
                        continue
                    lines.append(f"  {row['when']}  {row['confidence']}  {row['query'][:58]}")
                asked = [row for row in runs if row["asked"]]
                if asked:
                    lines.append("")
                    lines.append("Looked up because you asked:")
                    lines.extend(
                        f"  {row['when']}  {row['confidence']}  {row['query'][:58]}"
                        for row in asked)
            else:
                lines.append("Nothing researched yet.")
            pending = queue.get("topics", [])
            if pending:
                lines.extend(["", "Waiting to read:"])
                lines.extend(f"  · {str(item.get('topic'))[:58]}" for item in pending[:8])
            tutor = getattr(self.runtime.learning, "language", None)
            if tutor is not None and tutor.progress("arabic")["introduced"]:
                lines.extend(["", "Your courses (open Learn on the toolbar to practise):",
                              *("  " + line for line in tutor.progress_text("arabic").splitlines())])
            return "\n".join(lines)

        self._background("What I have been learning", gather)

    def _show_training(self) -> None:
        """Verified standard and private-local corpus readiness."""
        def gather() -> str:
            status = self.runtime.training.status()
            private = status.get("private_data", {})
            gate = status.get("training_gate", {})
            validation = status.get("validation", {})
            states = status.get("states", {})
            return "\n".join([
                "LOCAL TRAINING DATA",
                f"  total records        {status.get('total', 0)}",
                f"  eligible records     {status.get('eligible', 0)}",
                f"  private-local        {status.get('private_local_records', 0)}",
                f"  states               {states}",
                "",
                "PRIVATE DATA BOUNDARY",
                f"  enabled              {private.get('enabled', False)}",
                f"  local only           {private.get('local_only', True)}",
                f"  explicit consent     {private.get('explicit_consent_required', True)}",
                f"  credential redaction {private.get('credentials_redacted', True)}",
                f"  cloud export         {private.get('cloud_export_allowed', False)}",
                f"  automatic collection {private.get('automatic_collection', False)}",
                "",
                "QUALITY GATE",
                f"  dataset valid        {validation.get('passed', False)}",
                f"  ready                {gate.get('ready', False)}",
                f"  minimum              {gate.get('minimum_validated_samples', 0)} verified records",
                f"  automatic training   {gate.get('automatic_training', False)}",
                "",
                "Private examples enter only through an explicit owner-consented, verified action. "
                "Files learned for recall are not silently copied into model-training data.",
            ])

        self._background("Training", gather)

    def _recent_research(self, limit: int = 10) -> list[dict[str, Any]]:
        """Recent research runs, and whether Mohsin asked for each one.

        Read straight from the knowledge store rather than from an in-memory
        log, so it survives restarts -- twelve hours of overnight learning is
        exactly the history that would otherwise be lost on the next launch.
        """
        import sqlite3

        path = getattr(self.runtime.research.knowledge, "path", None)
        if path is None:
            return []
        rows: list[dict[str, Any]] = []
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            try:
                found = db.execute(
                    "SELECT query, started_at, confidence FROM research_runs "
                    "ORDER BY started_at DESC LIMIT ?", (max(1, limit),)).fetchall()
            finally:
                db.close()
        except Exception:
            return []
        for row in found:
            query = str(row["query"] or "")
            rows.append({
                "query": query,
                "when": str(row["started_at"] or "")[5:16].replace("T", " "),
                "confidence": f"{float(row['confidence'] or 0):.2f}",
                # A topic the learner queued reads like a topic; a thing Mohsin
                # typed reads like a sentence addressed to someone.
                "asked": bool(re.search(r"(?i)\b(?:me|my|i|you|your|explain|tell|show)\b", query)),
            })
        return rows

    # ------------------------------------------------------ sensors and doing
    #
    # Every one of these was already possible by typing a sentence and hoping
    # the right tool was chosen. A button is not a new capability, it is the
    # difference between a capability existing and being reachable.

    def _background(self, title: str, work, then=None, *, show_in_chat: bool = False) -> None:
        """Run something off the Tk loop and report it back onto the loop.

        Tk is single-threaded, and a camera opening or a model describing an
        image takes seconds. Doing that on the loop freezes the window, which
        is how a working assistant comes to look like a hung one.
        """
        # A report view used to sit blank until its report arrived, which on
        # a slow check looked like a broken page. Say what is coming at once.
        pending = (self._show_pending(title)
                   if not show_in_chat and threading.current_thread() is threading.main_thread() else None)

        def run() -> None:
            try:
                result = str(work())
                if pending:
                    self._post(self._clear_pending, pending)
                if show_in_chat:
                    self._post(self._append, "assistant", f"HAYAT\n{result}")
                else:
                    self._post(self._report, title, result)
                if then is not None:
                    self._post(then)
            except Exception as exc:
                from app.cli import describe_turn_failure

                if pending:
                    self._post(self._clear_pending, pending)
                self._post(self._append, "error", f"{title}\n{describe_turn_failure(exc)}")
                self._record_failure(title, exc)

        threading.Thread(target=run, name="hayat-gui-action", daemon=True).start()

    def _show_pending(self, title: str) -> str | None:
        """A muted "Gathering …" line, removed when the real report lands. Never saved."""
        if self._closed:
            return None
        self._pending_count = getattr(self, "_pending_count", 0) + 1
        token = f"pending-{self._pending_count}"
        try:
            from app.gui_motion import reading_position
            with reading_position(self.chat, follow=self._chat_follows()):
                self.chat.configure(state="normal")
                self.chat.insert("end", f"Gathering {title}…\n", ("pending", token))
                self.chat.configure(state="disabled")
        except tk.TclError:
            return None
        return token

    def _clear_pending(self, token: str) -> None:
        try:
            ranges = self.chat.tag_ranges(token)
            if ranges:
                self.chat.configure(state="normal")
                self.chat.delete(ranges[0], ranges[-1])
                self.chat.configure(state="disabled")
        except tk.TclError:
            log_swallowed(_log, "removing a pending report line")

    def _tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Call a tool through the same registry, permissions and authority
        path the model uses. The window is another caller, not a back door."""
        if name not in self.runtime.registry.names():
            # Some tools register only when what they need exists -- the
            # screen describer appears only when a vision model is reachable.
            # "KeyError: 'describe_screen'" tells Mohsin nothing; which part
            # is missing tells him what to switch on.
            return (f"I don't have {name} available right now. "
                    + self._why_missing(name))
        result = self.runtime.registry.execute(name, arguments or {})
        if not result.success:
            return f"That didn't work: {result.error}"
        output = result.output
        if name in {"generate_image", "generate_video"}:
            from app.assistant import AssistantService

            return AssistantService._format_generated_media(name, output)
        if isinstance(output, dict):
            answer = output.get("answer") or output.get("interpretation") or output.get("text")
            if answer:
                return str(answer)
        from app.plain_output import describe

        return describe(output)[:4000]

    #: Everything the toolbox offers, as (key, icon, name, what it does). The
    #: handler is looked up in CONTROLS by name, so a tile and its button
    #: cannot drift apart -- there is one implementation behind both.
    TOOLBOX = (
        ("Image", "\U0001F5BC", "Image", "Generate & analyse"),
        ("Research", "\U0001F50D", "Research", "Search and verify"),
        ("Web Clip", "\U0001F310", "Web Clip", "Capture and remember"),
        ("Operator", "\U0001F5A7", "Operator", "Brain nodes & contracts"),
        ("Self Check", "\U00002713", "Self Check", "Independent + team test"),
        ("Tools", "\U0001F527", "Tools", "What I can do"),
        ("Memory", "\U0001F9E0", "Memory", "Knowledge & history"),
        ("Learning", "\U0001F4DA", "Learning", "What I have read"),
        ("Training", "\U0001F9EA", "Training", "Private local datasets"),
        ("Health", "❤", "Health", "System health"),
        ("Camera", "\U0001F4F7", "Camera", "Switch it on"),
        ("Look", "\U0001F441", "Look", "See and describe"),
        ("Screen", "\U0001F5A5", "Screen", "Read the screen"),
        ("Clipboard", "\U0001F4CB", "Clipboard", "What is copied"),
        ("+ Add file", "\U0001F4CE", "Add file", "Read and remember"),
        ("Improve", "✨", "Improve", "Self-upgrade"),
    )

    def _toolbox_pane(self) -> tk.Frame:
        """Build the toolbox and workspace, once, on first use."""
        if getattr(self, "_toolbox_frame", None) is not None:
            return self._toolbox_frame

        frame = tk.Frame(self._centre, bg=self.BLACK)
        header = tk.Frame(frame, bg=self.BLACK)
        header.pack(fill="x", pady=(0, 6))
        tk.Label(header, text="Toolbox", bg=self.BLACK, fg=self.WHITE,
                 font=("Segoe UI Semibold", 11)).pack(side="left")
        tk.Label(header, text="press a tool to run it · ＋ to keep it in your workspace",
                 bg=self.BLACK, fg=self.MUTED, font=("Segoe UI", 9)).pack(side="left", padx=10)

        grid = tk.Frame(frame, bg=self.BLACK)
        grid.pack(fill="x")
        for index, (key, icon, title, subtitle) in enumerate(self.TOOLBOX):
            tile = ToolTile(grid, type(self), key, icon, title, subtitle,
                            on_run=self.run_tool, on_pin=self.pin_tool)
            tile.grid(row=index // 3, column=index % 3, sticky="ew", padx=3, pady=3)
        for column in range(3):
            grid.grid_columnconfigure(column, weight=1, uniform="tiles")

        pinned_header = tk.Frame(frame, bg=self.BLACK)
        pinned_header.pack(fill="x", pady=(14, 4))
        tk.Label(pinned_header, text="Workspace", bg=self.BLACK, fg=self.WHITE,
                 font=("Segoe UI Semibold", 11)).pack(side="left")
        ttk.Button(pinned_header, text="Clear", style="Hayat.TButton",
                   command=self.clear_workspace).pack(side="right")

        self.workspace = Workspace(frame, type(self), on_run=self.run_tool,
                                   on_remove=self.unpin_tool)
        self.workspace.pack(fill="both", expand=True)
        self._toolbox_frame = frame
        self._paint_workspace()
        return frame

    def _show_toolbox(self, showing: bool) -> None:
        pane = self._toolbox_pane()
        if showing:
            self.chat.pack_forget()
            pane.pack(fill="both", expand=True, pady=(2, 8))
        else:
            pane.pack_forget()
            if not self.chat.winfo_ismapped():
                self.chat.pack(fill="both", expand=True, pady=(2, 8))

    def run_tool(self, key: str) -> None:
        """Run a toolbox tile through the same handler its button uses."""
        for label, method, _group in self.CONTROLS:
            if label == key:
                getattr(self, method)()
                return
        self._append("error", f"I have no tool called {key}.")

    def pin_tool(self, key: str) -> None:
        if key not in self._pinned:
            self._pinned.append(key)
            self._save_workspace()
            self._paint_workspace()

    def unpin_tool(self, key: str) -> None:
        if key in self._pinned:
            self._pinned.remove(key)
            self._save_workspace()
            self._paint_workspace()

    def clear_workspace(self) -> None:
        self._pinned.clear()
        self._save_workspace()
        self._paint_workspace()

    def _paint_workspace(self) -> None:
        if getattr(self, "workspace", None) is None:
            return
        by_key = {key: (icon, title) for key, icon, title, _sub in self.TOOLBOX}
        self.workspace.set_pinned(
            [(key, *by_key[key]) for key in self._pinned if key in by_key])

    #: Panels folded until the owner opens them: diagnostics he rarely watches, so the radar and sensing come into view.
    FOLDED_BY_DEFAULT = ("System Status", "Secure Access & Memory", "System Info", "Live Console", "Models Available")

    @property
    def _folds(self):
        folds = getattr(self, "_fold_memory", None)
        if folds is None:
            from app.gui_panels import FoldMemory

            folds = self._fold_memory = FoldMemory(self._panels_file, self.FOLDED_BY_DEFAULT)
        return folds

    @property
    def _panels_file(self) -> Path:
        from app.config import PROJECT_ROOT

        return PROJECT_ROOT / "data" / "gui-panels.json"

    @property
    def _workspace_file(self) -> Path:
        from app.config import PROJECT_ROOT

        return PROJECT_ROOT / "data" / "workspace.json"

    def _load_workspace(self) -> list[str]:
        """What was pinned last time. A workspace that forgets is a toolbar."""
        try:
            found = json.loads(self._workspace_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [str(item) for item in found] if isinstance(found, list) else []

    def _save_workspace(self) -> None:
        try:
            self._workspace_file.parent.mkdir(parents=True, exist_ok=True)
            self._workspace_file.write_text(json.dumps(self._pinned), encoding="utf-8")
        except OSError:
            pass

    def _show_research(self) -> None:
        """What HAYAT has read, without asking it to read anything new."""
        def summarise() -> str:
            knowledge = self.runtime.research.knowledge.stats()
            queue = self.runtime.learner.queue_status(6) if self.runtime.learner else {}
            lines = [
                "EVIDENCE STORE",
                f"  summaries       {knowledge.get('knowledge', 0)}",
                f"  claims          {knowledge.get('claims', 0)}",
                f"  evidence links  {knowledge.get('evidence', 0)}",
                f"  sources         {knowledge.get('sources', 0)}",
                f"  stale           {knowledge.get('stale', 0)}",
                f"  disputed        {knowledge.get('disputed_claims', 0)}",
                f"  conflicts open  {knowledge.get('unresolved_conflicts', 0)}",
                f"  cached queries  {knowledge.get('query_cache', 0)}",
                "",
                "RESEARCH QUEUE",
                f"  pending         {queue.get('counts', {}).get('pending', 0)}",
                f"  learning        {queue.get('counts', {}).get('learning', 0)}",
                f"  learned         {queue.get('counts', {}).get('learned', 0)}",
                f"  failed          {queue.get('counts', {}).get('failed', 0)}",
                "",
            ]
            topics = queue.get("topics", [])
            lines.extend(f"  · {str(item.get('topic'))[:64]}" for item in topics[:6])
            if not topics:
                lines.append("  nothing queued.")
            recent = self._recent_research(5)
            if recent:
                lines.append("\nRECENT RUNS")
                lines.extend(
                    f"  {item['when']}  confidence {item['confidence']}  {item['query'][:62]}"
                    for item in recent)
            lines.append("\nPress Research to look something up or Web Clip to capture a full page.")
            return "\n".join(lines)

        self._background("Research", summarise)

    def _show_files(self) -> None:
        """What is in the project, biggest directories first."""
        def walk() -> str:
            from app.config import PROJECT_ROOT

            rows = []
            for entry in sorted(PROJECT_ROOT.iterdir()):
                if entry.name.startswith(".") and entry.name != ".venv":
                    continue
                try:
                    if entry.is_dir():
                        size = sum(item.stat().st_size for item in entry.rglob("*")
                                   if item.is_file())
                        rows.append((size, f"{entry.name}/", size))
                    else:
                        rows.append((entry.stat().st_size, entry.name, entry.stat().st_size))
                except OSError:
                    continue
            rows.sort(reverse=True)
            lines = [f"{PROJECT_ROOT}", ""]
            lines.extend(f"  {name:<28} {human_bytes(size)}" for _, name, size in rows[:18])
            return "\n".join(lines)

        self._background("Project files", walk)

    def _show_appearance(self) -> None:
        appearance_dialog(self)

    def _open_security(self) -> None:
        """Locked-out intruders and what is watching this machine."""
        from app.gui_security import SecurityStudio

        studio = getattr(self, "_security_studio", None)
        if studio is not None and studio.winfo_exists():
            studio.deiconify()
            studio.lift()
            studio.refresh_bans()
            return
        self._security_studio = SecurityStudio(self)

    def _refresh_suggestions(self) -> None:
        """Put starting points from HAYAT's own record first: where we left off, what usually comes next.

        The fixed chips ("World now", "AI news") are the same for everyone; these come
        from the situation model (app/situation.py), so the empty chat offers to
        pick up the owner's own thread. Reads a local database; never a model call.
        """
        chips = getattr(self, "_suggestion_chips", None)
        situation = getattr(self.runtime, "situation", None)
        if chips is None or situation is None or self._closed:
            return
        for button in getattr(self, "_situation_buttons", []):
            chips.remove(button)
        self._situation_buttons: list[ttk.Button] = []
        try:
            picks = situation.suggestions()
        except Exception:
            log_swallowed(_log, "reading situation suggestions for the chat chips")
            return
        for index, (label, request) in enumerate(picks):
            style = "Compact.Hayat.TButton"
            button = chips.add(ttk.Button(chips, text=label, style=style,
                                          command=lambda text=request: self.use_example(text)), index)
            self._situation_buttons.append(button)
            self.suggestion_buttons[label] = button
        chips.arrange()

    def _business_brief_check(self) -> None:
        """Post today's business brief in the chat when it is due, then check again later."""
        from app.config import load_settings

        from app.policy.cost import background_autostart_allowed
        configuration = load_settings()
        if not background_autostart_allowed(configuration):
            return
        settings = configuration.get("business", {})
        if not settings.get("daily_brief", True) or os.environ.get("PYTEST_CURRENT_TEST"):
            return
        brief = getattr(self.runtime, "brief", None)
        if brief is not None:
            def work() -> str | None:
                if not brief.due_today():
                    return None
                text = brief.text(brief.brief())
                brief.mark_shown()
                return text

            def show(text: str | None) -> None:
                if text:
                    self._append("system", text + "\n(Read from your books. Nothing was sent or changed.)")

            def run() -> None:
                try:
                    result = work()
                except Exception as exc:
                    self._record_background_failure("business brief", exc)
                    return
                self._post(show, result)

            threading.Thread(target=run, name="hayat-business-brief", daemon=True).start()
        hours = float(settings.get("brief_check_hours", 4) or 4)
        self._schedule(int(max(1.0, hours) * 3_600_000), self._business_brief_check)

    def _show_confirmation(self, action_id: int | None = None) -> None:
        """Open the confirmation window for a prepared action, or the oldest one waiting."""
        from app.gui_confirm import ConfirmDialog

        gate = getattr(self.runtime, "confirmations", None)
        if gate is None:
            return
        if action_id is None:
            waiting = gate.pending()
            if not waiting:
                self._append("system", "Nothing is waiting for your confirmation.")
                return
            action_id = waiting[0]["action_id"]
        windows = getattr(self, "_confirm_windows", {})
        existing = windows.get(action_id)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        windows[action_id] = ConfirmDialog(self, action_id)
        self._confirm_windows = windows

    def _attach_tooltips(self) -> None:
        from app.gui_help import attach_tooltips

        attach_tooltips(self.root, type(self))

    def _open_help(self) -> None:
        """The "What can HAYAT do?" guide: every area, its status, and examples to try."""
        from app.gui_help import CapabilityGuide

        guide = getattr(self, "_help_guide", None)
        if guide is not None and guide.winfo_exists():
            guide.deiconify()
            guide.lift()
            guide.refresh()
            return
        self._help_guide = CapabilityGuide(self)

    def use_example(self, text: str) -> None:
        """Put an example request in the chat box, ready to send or change."""
        self.show_view("chat")
        self.input.delete("1.0", "end")
        self.input.insert("1.0", text)
        self.input.focus_set()
        self.draft_status.set("Press Send to try it, or change it first.")

    def refresh_wallet_badge(self) -> None:
        """Show the money available to withdraw in the header, per currency."""
        self._wallet_checked = time.monotonic()
        ledger = getattr(self.runtime, "business", None)
        if ledger is None or not hasattr(self, "wallet_var"):
            return
        try:
            balances = ledger.wallet(activity=0)["balances"]
        except Exception:
            log_swallowed(_log, "reading the wallet for the header badge")
            self.wallet_var.set("Wallet --")
            return
        shown = [b["available"] for b in balances if b["available_cents"]]
        self.wallet_var.set("Wallet " + (" · ".join(shown[:2]) if shown else "0.00"))

    def _open_revenue(self) -> None:
        if getattr(self.runtime, 'revenue', None) is None:
            self._append('error', 'The revenue workspace is not available in this runtime.')
            return
        existing = getattr(self, '_revenue_workspace', None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.refresh()
            return
        from app.gui_revenue import RevenueWindow
        self._revenue_workspace = RevenueWindow(self)

    def _open_business(self) -> None:
        """Income, expenses, invoices, clients and work leads from the business books."""
        from app.gui_business import BusinessStudio

        if getattr(self.runtime, "business", None) is None:
            self._append("error", "The business books aren't available in this runtime.")
            return
        studio = getattr(self, "_business_studio", None)
        if studio is not None and studio.winfo_exists():
            studio.deiconify()
            studio.lift()
            studio.refresh()
            return
        self._business_studio = BusinessStudio(self)

    def _open_intelligence(self) -> None:
        from app.gui_intelligence import IntelligenceWindow
        if getattr(self.runtime, 'intelligence', None) is None:
            self._append('error', 'The intelligence workspace is unavailable in this runtime.')
            return
        window = getattr(self, '_intelligence_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._intelligence_window = IntelligenceWindow(self)

    def _open_explore(self, deep_look: bool = False) -> None:
        """Global intelligence on its Explore tab; `deep_look` also starts a deep look at this computer's location."""
        self._open_intelligence()
        window = getattr(self, '_intelligence_window', None)
        if window is None or not window.winfo_exists() or 'explore' not in getattr(window, 'tabs', {}):
            return
        window.notebook.select(window.tabs['explore'])
        if deep_look:
            window.explore_place.set('here')
            window.explore_run('Deep look')

    def _open_atlas(self) -> None:
        """The browser atlas: places, cameras, live layers, the video wall and deep looks."""
        self._open_intelligence()
        window = getattr(self, '_intelligence_window', None)
        if window is not None and window.winfo_exists():
            window.world_atlas('')

    def _open_keys(self) -> None:
        """Free developer keys: more camera networks and exact stock prices, stored in the local .env."""
        from app.gui_keys import KeysWindow
        window = getattr(self, '_keys_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._keys_window = KeysWindow(self)

    def _open_my_cameras(self) -> None:
        """The owner's own cameras, added by exact address; logins stay in the credential store."""
        from app.gui_my_cameras import MyCamerasWindow
        if getattr(getattr(self.runtime, 'intelligence', None), 'my_cameras', None) is None:
            self._append('error', 'My cameras is unavailable in this runtime.')
            return
        window = getattr(self, '_my_cameras_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._my_cameras_window = MyCamerasWindow(self)

    def _open_anpr(self) -> None:
        """Plate recognition for the owner's own vehicles, authorized cameras and test footage."""
        from app.gui_anpr import AnprDashboard
        window = getattr(self, '_anpr_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._anpr_window = AnprDashboard(self)

    def _open_markets(self) -> None:
        """The read-only Trade Center: crypto markets, charts, order books and blockchain state."""
        from app.gui_markets import TradeCenter
        if getattr(self.runtime, 'markets', None) is None:
            self._append('error', 'The Trade Center is unavailable in this runtime.')
            return
        window = getattr(self, '_markets_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._markets_window = TradeCenter(self)

    def _open_downloads(self) -> None:
        """Downloads & web activity: active downloads, the history of files, models and videos, and every web request."""
        from app.gui_downloads import DownloadsWindow
        window = getattr(self, '_downloads_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._downloads_window = DownloadsWindow(self)

    def _open_command_center(self) -> None:
        """Everything live on one screen: markets, world pulse, trade routes, odds, chains and the owner's own items."""
        from app.gui_command_center import CommandCenter
        window = getattr(self, '_command_center_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._command_center_window = CommandCenter(self)

    def _open_accounts(self) -> None:
        """Connect the owner's own accounts (signed in on each provider's page) and work with them."""
        from app.gui_accounts import AccountsCenter
        if getattr(self.runtime, 'accounts', None) is None:
            self._append('error', 'Accounts are unavailable in this runtime.')
            return
        window = getattr(self, '_accounts_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._accounts_window = AccountsCenter(self)

    def _open_jobs(self) -> None:
        """Find postings, see why each fits, prepare and send applications (after confirmation), and track them."""
        from app.gui_jobs import JobsWindow
        if getattr(self.runtime, 'jobs', None) is None:
            self._append('error', 'Jobs are unavailable in this runtime.')
            return
        window = getattr(self, '_jobs_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._jobs_window = JobsWindow(self)

    def _open_networking(self):
        from app.gui_networking import NetworkingWindow
        existing = getattr(self, '_networking_window', None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return existing
        self._networking_window = NetworkingWindow(self)
        return self._networking_window

    def _open_onion_navigation(self):
        from app.gui_onion_navigation import OnionNavigationWindow
        existing = getattr(self, '_onion_navigation_window', None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return existing
        self._onion_navigation_window = OnionNavigationWindow(self)
        return self._onion_navigation_window

    def _open_money_desk(self) -> None:
        """HAYAT's money decisions for the owner to approve, with pots, bills and rules; it never pays outside the wallet."""
        from app.gui_money_desk import MoneyDeskWindow
        if getattr(self.runtime, 'money_desk', None) is None:
            self._append('error', 'The HAYAT Wallet is unavailable in this runtime.')
            return
        window = getattr(self, '_money_desk_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._money_desk_window = MoneyDeskWindow(self)

    def _open_portfolio(self) -> None:
        """What the owner holds at their own bank, broker and exchange, valued live in USD; nothing is held or traded."""
        from app.gui_portfolio import PortfolioWindow
        if getattr(self.runtime, 'portfolio', None) is None:
            self._append('error', 'The portfolio is unavailable in this runtime.')
            return
        window = getattr(self, '_portfolio_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._portfolio_window = PortfolioWindow(self)

    def _open_web3(self) -> None:
        """Read-only Web3: networks and live blocks, an explorer, DeFi, and the agent-web (Web 4.0) check."""
        from app.gui_web3 import Web3Center
        if getattr(self.runtime, 'web3', None) is None:
            self._append('error', 'The Web3 explorer is unavailable in this runtime.')
            return
        window = getattr(self, '_web3_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._web3_window = Web3Center(self)

    def _open_reverse_engineer(self) -> None:
        from app.gui_reverse_engineering import ReverseEngineeringStudio

        window = getattr(self, '_reverse_engineering_studio', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._reverse_engineering_studio = ReverseEngineeringStudio(self)

    def _open_workspace_tools(self, page="screen") -> None:
        from app.gui_workspace_tools import WorkspaceTools
        window = getattr(self, "_workspace_tools", None)
        if window is not None and window.winfo_exists():
            window.tabs.select({"screen": 0, "people": 1, "replica": 2}.get(page, 0))
            window.deiconify()
            window.lift()
            return
        self._workspace_tools = WorkspaceTools(self, page)

    def _open_replica_workspace(self) -> None:
        self._open_workspace_tools("replica")

    def _open_enrolled_faces(self) -> None:
        from app.gui_enrolled_faces import EnrolledFacesWindow
        window = getattr(self, '_enrolled_faces_window', None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._enrolled_faces_window = EnrolledFacesWindow(self)

    def _open_research_workspace(self, kind, *, image_path='', name='', context=''):
        """Reuse a related workspace and prefill explicit inputs without running tools."""
        if kind == 'images':
            self._open_local_image_tools(path=image_path)
            return self._local_image_tools_window
        if kind == 'faces':
            self._open_enrolled_faces()
            panel = self._enrolled_faces_window
            if panel.busy or (panel._worker is not None and panel._worker.is_alive()):
                panel.status.set('Finish the current local operation before changing the selected image.')
                return panel
            if image_path:
                panel.image_path.set(image_path)
            return panel
        if kind == 'contacts':
            panel = self._open_networking()
            if panel.find_professional_contacts(name=name, organization=context):
                return panel._contact_discovery
            self._append('error', panel.status.get())
            return None
        if kind not in {'sources', 'professional'}:
            raise ValueError('Choose a supported research workspace.')
        self._open_searches()
        studio = self._research_studio
        if kind == 'sources':
            panel = studio.open_image_research()
            if image_path:
                panel.prepare_image(image_path)
            return panel
        studio.open_person_studio()
        panel = studio._person_studio
        if panel.busy or (panel._worker and panel._worker.is_alive()) or (panel.job and panel.job.status == 'running'):
            panel.status.set('Finish or cancel the current operation before changing its research inputs.')
            return panel
        if name:
            panel.input_mode.set('Name / professional profile')
            panel.query.delete(0, 'end')
            panel.query.insert(0, name)
        if context:
            panel.context.set(context)
        panel._buttons()
        return panel

    def _open_local_image_tools(self, path='', other='') -> None:
        from app.gui_image_tools import LocalImageToolsWindow
        window = getattr(self, '_local_image_tools_window', None)
        if window is not None and window.winfo_exists():
            if window.busy or (window._worker is not None and window._worker.is_alive()):
                window.status.set('Finish the current local operation before changing its selected images.')
                window.deiconify()
                window.lift()
                return
            if path:
                window.path.set(path)
            if other:
                window.other.set(other)
            window.deiconify()
            window.lift()
            return
        self._local_image_tools_window = LocalImageToolsWindow(self, path, other)

    def _open_searches(self) -> None:
        """Running and finished searches, with their answers, trust verdicts and a Cancel button."""
        from app.gui_research import ResearchStudio

        if getattr(self.runtime, "research_jobs", None) is None:
            self._append("error", "Background searches aren't available in this runtime.")
            return
        studio = getattr(self, "_research_studio", None)
        if studio is not None and studio.winfo_exists():
            studio.deiconify()
            studio.lift()
            studio.refresh()
            return
        self._research_studio = ResearchStudio(self)

    def _open_knowledge(self) -> None:
        from app.gui_knowledge import KnowledgeStudio

        if getattr(getattr(self.runtime, "research", None), "knowledge", None) is None:
            self._append("error", "The knowledge store is unavailable in this runtime.")
            return
        studio = getattr(self, "_knowledge_studio", None)
        if studio is not None and studio.winfo_exists():
            studio.deiconify()
            studio.lift()
            studio.refresh()
        else:
            self._knowledge_studio = KnowledgeStudio(self)

    def _open_languages(self) -> None:
        """Persona switch, reply language, language installs and the software installer."""
        from app.gui_languages import LanguagesStudio

        studio = getattr(self, "_languages_studio", None)
        if studio is not None and studio.winfo_exists():
            studio.deiconify()
            studio.lift()
            studio.refresh()
            return
        self._languages_studio = LanguagesStudio(self)

    def _open_learning_atlas(self) -> None:
        from app.gui_learning_atlas import LearningAtlas
        existing = getattr(self, '_learning_atlas', None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.refresh()
            return
        self._learning_atlas = LearningAtlas(self)

    def _open_learn(self, course: str = "arabic") -> None:
        """The Learn window: the Arabic course with listening, speaking and an on-screen keyboard."""
        from app.gui_learn import LearnStudio

        if getattr(self.runtime.learning, "language", None) is None:
            self._append("error", "The language tutor isn't available in this runtime.")
            return
        studio = getattr(self, "_learn_studio", None)
        if studio is not None and studio.winfo_exists():
            studio.deiconify()
            studio.lift()
            return
        self._learn_studio = LearnStudio(self, course)

    def _show_shortcuts(self) -> None:
        """A themed cheat-sheet of every keyboard shortcut, read from SHORTCUTS."""
        from app.gui_windows import place_over

        existing = getattr(self, "_shortcuts_window", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        dialog = tk.Toplevel(self.root)
        self._shortcuts_window = dialog
        dialog.title("HAYAT · Keyboard shortcuts")
        dialog.configure(bg=self.PANEL)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        body = tk.Frame(dialog, bg=self.PANEL, padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Keyboard shortcuts", bg=self.PANEL, fg=self.WHITE,
                 font=("Segoe UI Semibold", 12), anchor="w").pack(fill="x", pady=(0, 12))
        grid = tk.Frame(body, bg=self.PANEL)
        grid.pack(fill="both", expand=True)
        rows: list[tuple[str, str]] = []
        for index, (sequence, method) in enumerate(self.SHORTCUTS):
            key = self._pretty_key(sequence)
            label = self.SHORTCUT_LABELS.get(method, method.lstrip("_").replace("_", " ").capitalize())
            rows.append((key, label))
            tk.Label(grid, text=key, bg=self.PANEL, fg=self.OLIVE_SOFT, font=("Consolas", 10),
                     anchor="e", width=16).grid(row=index, column=0, sticky="e", padx=(0, 14), pady=2)
            tk.Label(grid, text=label, bg=self.PANEL, fg=self.WHITE, font=("Segoe UI", 10),
                     anchor="w").grid(row=index, column=1, sticky="w", pady=2)
        dialog._rows = rows  # read by the test
        tk.Label(body, text="Also: Tab to a panel title and press Enter or Space to fold it.",
                 bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=360).pack(fill="x", pady=(14, 0))

        def close() -> None:
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.bind("<Escape>", lambda _event: (close(), "break")[1])
        place_over(dialog, self.root, (420, 60 + 24 * len(rows)))
        dialog.lift()
        dialog.focus_set()

    def _show_startup_status(self) -> None:
        from app.gui_startup import StartupDialog
        dialog = getattr(self, "_startup_dialog", None)
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            return
        self._startup_dialog = StartupDialog(self.root, type(self))

    def _analyze_video(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, title="Analyze video scenes and audio",
            filetypes=[("Video files", "*.mp4 *.mkv *.avi *.mov *.webm *.m4v")])
        if path:
            self._background("Video", lambda: self._tool("video_analyze", {
                "path": path, "scenes": True, "transcribe_audio": True}))

    def _show_settings(self) -> None:
        """What HAYAT is currently set to do, read from the live runtime."""
        def describe() -> str:
            voice = self.runtime.voice.status() if self.runtime.voice else {}
            learner = self.runtime.learner
            lines = [
                f"identity        {IDENTITY.name}, for {IDENTITY.owner}",
                f"modes           {'+'.join(item.value for item in self.runtime.modes.effective)}",
                f"model           {getattr(self.runtime.model, 'model', None) or 'none active'}"
                f"  via {getattr(self.runtime.model, 'name', None) or '-'}",
                f"vision          {getattr(getattr(self.runtime.vision, 'provider', None), 'model', None) or 'off'}",
                f"voice           {voice.get('mode', 'off')} · {voice.get('tts_provider', '-')}"
                f" / {voice.get('stt_provider', '-')}",
                f"privacy mode    {'on' if self.runtime.authority.privacy_mode else 'off'}",
                f"child-safe      {'on' if self.runtime.learning.guardian.active else 'off'}",
                f"owner mode      {'on' if self.runtime.authority.owner_mode else 'off'}",
                f"local auto permissions  {len(self.runtime.authority.status().get('auto_categories', []))}",
                f"always protected        {len(self.runtime.authority.status().get('always_protected', []))}",
                f"cost policy     {self.runtime.cost_policy.mode}",
                "paid API fallback  blocked; external services with unverified billing are manual",
                "optional startup   manual (chat and explicitly requested tools remain available)",
                f"tools           {len(self.runtime.registry.names())} registered",
                f"context budget  {self._budget_line()}",
                f"background learner  {'running' if learner and learner.running else 'stopped'}",
                f"private training     {'enabled, local only' if self.runtime.training.private_status().get('enabled') else 'disabled'}",
                "",
                "Shortcuts: " + "  ".join(
                    f"{sequence.strip('<>').replace('Control-', 'Ctrl+')}"
                    for sequence, _method in self.SHORTCUTS),
            ]
            return "\n".join(lines)

        self._background("Settings", describe)

    def _budget_line(self) -> str:
        """What the last turn was budgeted, and what limited it.

        Worth surfacing because a budget that quietly shrinks is
        indistinguishable from a model that has started forgetting -- and the
        first thing anyone would want to know is which of the two happened.
        """
        budget = getattr(self.runtime, "context_budget", None)
        if budget is None:
            return "not available"
        last = budget.last
        if last is None:
            ceiling = budget.status().get("machine_ceiling")
            return f"nothing decided yet (this machine allows up to {ceiling})"
        return last.describe()

    def clear_chat(self) -> None:
        """Clear the chat now, and summarize the thread in the background.

        The view clears immediately -- the owner should not wait on a model
        call to empty a window. The summary and session reset then happen off
        the UI thread, keeping the gist of what was said for next time without
        holding up the clear.
        """
        if hasattr(self, 'chat_details'):
            self.chat_details.reset()
        for card in self._evidence_cards:
            card.destroy()
        self._evidence_cards.clear()
        self._transcript.seek(0)
        self._transcript.truncate()
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self._inline_images.clear()
        for card in self._video_cards.values():
            card.close()
            card.destroy()
        self._video_cards.clear()
        for card in self._video_link_cards.values():
            card.close()
        self._video_link_cards.clear()
        studio = getattr(self, '_video_studio', None)
        if studio is not None and studio.winfo_exists():
            studio.close()
        session = getattr(self.runtime, 'session', None)
        if session is not None:
            session.last_video = {}
        self.chat.configure(state="disabled")
        if self._chat_find_open:
            self._refresh_chat_find()
        conversation = getattr(self.runtime, "conversation", None)
        if conversation is None or not hasattr(conversation, "summarize_and_reset"):
            self._append("system", "Cleared the chat.")
            return
        self._append("system", "Cleared the chat. Summarizing what we covered so I can pick "
                               "it up next time…")

        def work() -> None:
            try:
                info = conversation.summarize_and_reset()
            except Exception:
                info = {"summarized": False}
            self._post(self._after_clear_chat, info)

        threading.Thread(target=work, name="hayat-clear-chat", daemon=True).start()

    def _after_clear_chat(self, info: dict) -> None:
        if info.get("summarized"):
            self._append("system", "Stored a summary of that chat, so its context is ready for next time.")

    def _deep_recall_search(self) -> None:
        query = simpledialog.askstring(
            "Deep recall", "What should HAYAT search across durable memory?",
            parent=self.root,
        )
        if not query or not query.strip():
            return

        def recall() -> str:
            page = self.runtime.memory.retrieve_page(
                query.strip(), page=1, page_size=20,
                session_id=self.runtime.session.session_id,
            )
            lines = [
                f"DEEP RECALL · page {page['page']} · {page['returned']} matches",
                "",
            ]
            for index, item in enumerate(page["items"], 1):
                content = " ".join(str(item.get("content", "")).split())
                lines.append(f"{index:>2}. {float(item.get('relevance', 0)):.3f} · {content[:260]}")
            if page["has_more"]:
                lines += ["", f"More matches exist. Ask HAYAT to use recall_memory_page with page={page['next_page']} to continue."]
            elif not page["items"]:
                lines.append("No integrity-valid memories matched this query.")
            return "\n".join(lines)

        self._background("Deep recall", recall)

    def _transcript_text(self) -> str:
        self._transcript.seek(0)
        text = self._transcript.read()
        self._transcript.seek(0, 2)
        return text

    def _pack_chat_find_bar(self) -> None:
        # Sit above the chat tabs when they are shown; a compact/folded layout may hide them, and pack(before=...)
        # a widget that is not itself packed raises "isn't packed", so only use before= when the tabs are managed.
        options = {"before": self._chat_tabs} if self._chat_tabs.winfo_manager() else {}
        self._chat_find_bar.pack(fill="x", pady=(4, 2), **options)

    def _open_chat_find(self) -> None:
        if self.current_view != "chat":
            self.show_view("chat")
        self._chat_find_open = True
        self._pack_chat_find_bar()
        self._chat_find_entry.focus_set()
        self._chat_find_entry.selection_range(0, "end")
        self._refresh_chat_find()

    def _close_chat_find(self) -> str:
        self._chat_find_open = False
        self._chat_find_bar.pack_forget()
        for tag in ("find_match", "find_active"):
            self.chat.tag_remove(tag, "1.0", "end")
        self._chat_find_matches = []
        self._chat_find_index = -1
        self.chat.focus_set()
        return "break"

    def _refresh_chat_find(self, event=None) -> None:
        if event is not None and getattr(event, "keysym", "") in {"Return", "Escape"}:
            return
        for tag in ("find_match", "find_active"):
            self.chat.tag_remove(tag, "1.0", "end")
        self._chat_find_matches = []
        self._chat_find_index = -1
        term = self._chat_find_var.get().strip()
        if not term:
            self._chat_find_status.set("Type to search visible messages")
            return
        position = "1.0"
        while len(self._chat_find_matches) < 500:
            start = self.chat.search(term, position, stopindex="end", nocase=True)
            if not start:
                break
            end = self.chat.index(f"{start}+{len(term)}c")
            self._chat_find_matches.append((start, end))
            self.chat.tag_add("find_match", start, end)
            position = end
        if self._chat_find_matches:
            self._chat_find_index = 0
            self._paint_chat_find_active()
        else:
            self._chat_find_status.set("No visible matches")

    def _paint_chat_find_active(self) -> None:
        self.chat.tag_remove("find_active", "1.0", "end")
        start, end = self._chat_find_matches[self._chat_find_index]
        self.chat.tag_add("find_active", start, end)
        self.chat.tag_raise("find_active")
        self.chat.see(start)
        count = len(self._chat_find_matches)
        suffix = "+" if count == 500 else ""
        self._chat_find_status.set(f"{self._chat_find_index + 1} of {count}{suffix} visible")

    def _step_chat_find(self, direction: int) -> str:
        if self._chat_find_matches:
            self._chat_find_index = (self._chat_find_index + direction) % len(self._chat_find_matches)
            self._paint_chat_find_active()
        return "break"

    def _select_chat(self, _event=None) -> str:
        self.chat.tag_add("sel", "1.0", "end-1c")
        self.chat.focus_set()
        return "break"

    def _copy_text(self, text: str) -> str:
        if text:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(text)
                self.copy_button.configure(text="Copied")
                self._schedule(1800, lambda: self.copy_button.configure(text="Copy chat"))
            except tk.TclError as exc:
                self._append("error", f"Could not copy text: {exc}. Use Save to export the conversation.")
        return "break"

    def copy_chat(self, _event=None) -> str:
        """Copy all messages since Clear, including messages trimmed from view."""
        return self._copy_text(self._transcript_text())

    def copy_selection(self, _event=None) -> str:
        try:
            text = self.chat.get("sel.first", "sel.last")
        except tk.TclError:
            return "break"
        return self._copy_text(text)

    def _chat_menu(self, event) -> str:
        menu = tk.Menu(self.chat, tearoff=False)
        menu.add_command(label="Copy selection  Ctrl+C", command=self.copy_selection,
                         state="normal" if self.chat.tag_ranges("sel") else "disabled")
        menu.add_command(label="Copy entire chat  Ctrl+Shift+C", command=self.copy_chat)
        menu.add_command(label="Select visible chat  Ctrl+A", command=self._select_chat)
        menu.add_separator()
        menu.add_command(label="Continue from earlier?", command=self._open_chat_rewind)
        menu.add_command(label="Save entire chat…", command=self.save_transcript)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            menu.destroy()
        return "break"

    def save_transcript(self) -> None:
        """Export all messages since Clear, including earlier trimmed messages."""
        from tkinter import filedialog

        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".txt",
            initialfile=time.strftime("hayat-%Y%m%d-%H%M.txt"),
            filetypes=[("Text", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self._transcript_text())
            self._append("system", f"Saved this conversation to {path}")
        except OSError as exc:
            self._append("error", f"I could not save that: {exc}")

    def _why_missing(self, name: str) -> str:
        """Which piece is absent, in terms of what to do about it."""
        if name in {"describe_screen", "camera_observe", "clipboard_analyze_image"}:
            if self.runtime.vision is None:
                return ("Seeing needs a vision model, and none is reachable, so I can "
                        "still capture the screen or a frame but not tell you what is in "
                        "it. Start the local model service and I will have it back.")
            return "The vision model is configured but did not register its tools."
        if name.startswith("camera"):
            return "The camera subsystem is not available in this session."
        if name == "generate_image":
            return "Image generation is not available; the diffusion model is not present."
        return "It was not registered in this session."

    def _add_files(self, kind: str = '') -> bool:
        """Hand HAYAT files or images: read, understood, and kept.

        The understanding is stored, not the file. A long PDF becomes a few
        hundred searchable characters plus a path back to the original, which
        is what makes "what did that invoice say" answerable next week without
        handing the invoice over again.
        """
        from tkinter import filedialog

        image_types = [('Images', '*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff')]
        chosen = filedialog.askopenfilenames(
            parent=self.root, title="Give HAYAT files, images or videos",
            filetypes=image_types if kind == 'image' else [("Everything I can read", "*.txt *.md *.pdf *.docx *.xlsx *.csv *.json *.log *.py "
                                                 "*.png *.jpg *.jpeg *.webp *.bmp *.mp4 *.mkv *.avi *.mov *.webm"),
                       ("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff"),
                       ("Documents", "*.pdf *.docx *.xlsx *.txt *.md *.csv *.json *.log"),
                       ("Videos", "*.mp4 *.mkv *.avi *.mov *.webm *.m4v"),
                       ("All files", "*.*")])
        if chosen:
            self._ingest_files(list(chosen))
            return True
        return False

    def _ingest_files(self, paths: list[str]) -> None:
        """Read, understand and remember these files. Shared by ＋ and by drop."""
        if not paths:
            return
        names = ", ".join(Path(item).name for item in paths[:3])
        more = f" and {len(paths) - 3} more" if len(paths) > 3 else ""
        self.show_view('chat')
        self._append('user', f'{IDENTITY.owner_short}\nAttached {names}{more}')
        from app.gui_attachments import AttachmentBatchCard
        card = AttachmentBatchCard(self.chat, self, paths)
        self._evidence_cards.append(card)
        self.chat.configure(state='normal')
        self.chat.window_create('end', window=card, padx=8, pady=8)
        self.chat.insert('end', '\n')
        self.chat.configure(state='disabled')
        self.chat.see('end')
        self._append("system", f"Reading {names}{more}…")
        self._set_attachment_status(f"Reading {names}{more}…", "working")

        def ingest() -> str:
            reader = getattr(self.runtime, "attachments", None)
            if reader is None:
                self._post(
                    self._set_attachment_status,
                    "File reading is unavailable in this runtime.", "error",
                )
                return "File reading is not available in this runtime."
            lines = []
            readable = 0
            remembered = 0
            for index, path in enumerate(paths):
                self._post(card.update_file, index, 'Reading…')
                try:
                    from app.video import VIDEO_KINDS
                    if Path(path).suffix.lower() in VIDEO_KINDS:
                        preview = self.runtime.registry.execute('video_preview', {'path': path})
                        if preview.success:
                            self._post(self._append, 'assistant', 'HAYAT\n' + preview.output['text'])
                            self._post(card.update_file, index, 'Preview ready · analyzing')
                    attachment = reader.ingest(path, session_id=self.runtime.session.session_id)
                except Exception as exc:
                    self._post(card.update_file, index, 'Could not read')
                    lines.append(f'{Path(path).name}: could not read — {str(exc)[:250]}')
                    continue
                self._post(card.update_file, index, 'Remembered' if attachment.memory_id else
                           ('Read · not stored' if attachment.ok else 'Could not read'))
                readable += int(attachment.ok)
                remembered += int(attachment.memory_id is not None)
                lines.append(attachment.describe())
                lines.append("  " + ("remembered — ask me about it any time"
                                     if attachment.memory_id else "not stored"))
                lines.append("")
            if remembered == len(paths):
                status = f"Remembered {remembered} file{'s' if remembered != 1 else ''} · ready for questions"
                state = "success"
            elif readable:
                status = f"Read {readable} of {len(paths)} · remembered {remembered}"
                state = "working"
            else:
                status = f"Could not read the {len(paths)} selected file{'s' if len(paths) != 1 else ''}"
                state = "error"
            self._post(self._set_attachment_status, status, state)
            return "\n".join(lines).rstrip()

        self._background("What I read", ingest)

    def _set_attachment_status(self, text: str, state: str = "idle") -> None:
        if self._closed:
            return
        self.attachment_status.set(text)
        colour = {
            "success": self.OLIVE_SOFT,
            "working": self.GOLD_SOFT,
            "error": self.ALERT,
        }.get(state, self.MUTED)
        self.drop_status_label.configure(fg=colour)

    def _toggle_camera(self) -> None:
        if self._camera_on:
            self._background("Camera", lambda: self._tool("camera_off"))
        else:
            # The view opens with the device, because "the camera is on" is a
            # claim and the picture is the evidence.
            self._background("Camera", lambda: self._tool("camera_on"),
                             then=self.open_camera_view)

    def _follow_camera(self) -> None:
        """Keep the preview panel matched to whether the camera is on.

        The panel is not something to switch on separately -- if the camera is
        running, its picture belongs on screen, and if it is not, a panel
        holding the last frame it saw would read as still watching.
        """
        if self._camera_on and (self.camera_stream is None or not self.camera_stream.alive):
            from app.camera_view import CameraStream

            try:
                self.camera_stream = CameraStream(
                    self.runtime, self.preview, palette=type(self),
                    detail=self.preview_detail, dot=self.preview_dot, width=386)
            except Exception:
                self.camera_stream = None
        elif not self._camera_on and self.camera_stream is not None:
            self.camera_stream.stop()
            self.camera_stream = None
            self.preview.configure(image="", text="Camera is off.\nPress Camera to switch it on.")
            self.preview_detail.set("camera off")
            self.preview_dot.configure(fg=self.MUTED)

    def open_camera_view(self) -> None:
        """Show what the camera is seeing. Safe to call when it already is."""
        if self._closed:
            return
        if self.camera_view is not None and self.camera_view.alive:
            try:
                self.camera_view.window.lift()
            except Exception:
                log_swallowed(_log, "raising the camera window")
            return
        from app.camera_view import CameraView

        try:
            self.camera_view = CameraView(self.root, self.runtime, type(self))
        except Exception as exc:
            self.camera_view = None
            self._append("error", f"I could not open the camera view: {type(exc).__name__}: {exc}")

    def _close_camera_view(self) -> None:
        if self.camera_view is not None:
            self.camera_view.close()
            self.camera_view = None

    def _look(self) -> None:
        """Capture a frame and say what is in it, in one press."""
        self._append("system", "Looking…")
        self._background("What I can see", lambda: self._tool(
            "camera_observe", {"prompt": "Describe what you see, briefly and plainly."}))

    def _read_screen(self) -> None:
        self._append("system", "Reading the screen…")
        self._background("What is on screen", lambda: self._tool(
            "screen_read", {}))

    def _open_people_workspace(self):
        self._open_workspace_tools("people")

    def _read_clipboard(self) -> None:
        self._background("Clipboard", lambda: self._tool("clipboard_status"))

    def _generate_image(self) -> None:
        """Ask for a picture, with the settings that change how it comes out.

        A single prompt box was all this offered, so quality, style and size
        -- which the generator has supported all along -- were unreachable
        from the window. Quality defaults to auto, which reads free VRAM and
        asks for what the card can actually deliver.
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("Generate an image")
        dialog.configure(bg=self.BLACK)
        dialog.transient(self.root)
        dialog.resizable(False, False)

        tk.Label(dialog, text="What should I draw?", bg=self.BLACK, fg=self.WHITE,
                 font=("Segoe UI Semibold", 10)).grid(row=0, column=0, columnspan=4,
                                                      sticky="w", padx=16, pady=(14, 4))
        prompt = tk.Text(dialog, height=3, width=56, bg=self.PANEL_2, fg=self.WHITE,
                         insertbackground=self.GOLD, relief="flat", padx=10, pady=8,
                         font=("Segoe UI", 10), wrap="word")
        prompt.grid(row=1, column=0, columnspan=4, padx=16, sticky="ew")
        prompt.focus_set()

        choices: dict[str, StringVar] = {}
        options = (("Quality", "quality", ("auto", "fast", "balanced", "quality")),
                   ("Style", "style", self.IMAGE_STYLES),
                   ("Size", "size", ("512x512", "512x768", "768x512", "768x768")))
        for index, (label, key, values) in enumerate(options):
            tk.Label(dialog, text=label, bg=self.BLACK, fg=self.MUTED,
                     font=("Segoe UI", 9)).grid(row=2, column=index, sticky="w",
                                                padx=(16 if not index else 6, 0), pady=(12, 2))
            variable = StringVar(master=self.root, value=values[0])
            ttk.Combobox(dialog, textvariable=variable, values=list(values), width=12,
                         state="readonly").grid(row=3, column=index, sticky="w",
                                                padx=(16 if not index else 6, 0))
            choices[key] = variable

        chosen: dict[str, Any] = {}

        def start() -> None:
            text = prompt.get("1.0", "end").strip()
            if text:
                width, _, height = choices["size"].get().partition("x")
                chosen.update(prompt=text, quality=choices["quality"].get(),
                              style=choices["style"].get(),
                              width=int(width), height=int(height))
            dialog.destroy()

        buttons = tk.Frame(dialog, bg=self.BLACK)
        buttons.grid(row=4, column=0, columnspan=4, sticky="e", padx=16, pady=14)
        ttk.Button(buttons, text="Cancel", style="Hayat.TButton",
                   command=dialog.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Draw it", style="Hayat.TButton",
                   command=start).pack(side="right")
        prompt.bind("<Control-Return>", lambda _event: start())
        # Modal, so it must open where it can be seen: over HAYAT, inside the
        # usable screen area, never with its title bar under a docked bar.
        from app.gui_windows import place_over

        place_over(dialog, self.root)
        dialog.grab_set()
        self.root.wait_window(dialog)

        if not chosen:
            return
        self._append("system", f"Generating ({chosen['quality']}, {chosen['style']}, "
                               f"{chosen['width']}x{chosen['height']}): {chosen['prompt']}")
        self._background("Image", lambda: self._tool("generate_image", chosen),
                         show_in_chat=True)

    def _research(self) -> None:
        """Open the search workspace so depth, progress and results stay together."""
        self._open_searches()
        studio = getattr(self, "_research_studio", None)
        if studio is not None and studio.winfo_exists():
            studio.query.focus_set()

    def _research_image(self) -> None:
        self._open_searches()
        studio = getattr(self, '_research_studio', None)
        if studio is not None and studio.winfo_exists():
            studio.open_image_research()

    def _capture_web_page(self) -> None:
        """Render one URL, save a visual snippet, and retain what it says."""
        url = simpledialog.askstring(
            "Web Clip", "Public webpage URL to capture and learn:", parent=self.root,
        )
        if not url:
            return
        topic = simpledialog.askstring(
            "Web Clip", "What should I learn from this page? (optional)",
            parent=self.root,
        )
        arguments = {
            "url": url.strip(), "topic": (topic or "").strip(), "remember": True,
            "screenshot": True, "full_page": True, "scroll_page": True,
        }
        self._append("system", f"Capturing and learning: {url.strip()}")
        self._background(
            "Web Clip", lambda: self._tool("capture_web_page", arguments),
        )

    def _show_tools(self) -> None:
        self._background("My tools", self._tools_overview)

    def _tools_overview(self) -> str:
        """Every registered tool, grouped by what it is for in plain words, with its track record.

        Read from the registry and the capability catalog, so the list is the
        real inventory, and from the recorded outcomes, so "reliable" means
        observed, not claimed.
        """
        from app import self_knowledge

        grouped = self_knowledge.tools_answer(self.runtime)
        record = self_knowledge.reliability_answer(self.runtime)
        return (grouped + "\n\nReliability: " + record
                + "\n\nWhat I won't do: press Help (F1), or ask \"what can't you do?\".")

    def _show_memory(self) -> None:
        def summarise() -> str:
            memory = self.runtime.memory
            with memory.connect() as db:
                memories = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                tasks = db.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall()
            lines = [f"{memories} memories stored", f"database: {memory.path}"]
            lines.extend(f"  {status}: {count}" for status, count in tasks)
            if self.runtime.recovery is not None:
                unfinished = self.runtime.recovery.unfinished_tasks()
                lines.append(f"unfinished from an interrupted run: {len(unfinished)}")
            return "\n".join(lines)

        self._background("Memory", summarise)

    def _show_doctor(self) -> None:
        def check() -> str:
            system = self.runtime.observability.system()
            storage = self.runtime.storage.status()
            recent = self.runtime.failures.recent(5)
            lines = [
                f"CPU {system.get('cpu_percent', 0):.0f}%  "
                f"RAM {system.get('ram', {}).get('used_percent', 0):.0f}%",
                f"storage {self.runtime.storage.human(storage['used_bytes'])} of "
                f"{self.runtime.storage.human(storage['maximum_total_bytes'])}",
                f"tools registered: {len(self.runtime.registry.names())}",
                f"model: {getattr(self.runtime.model, 'model', None) or 'none active'}",
            ]
            if self.runtime.recovery is not None:
                clean = self.runtime.recovery.status()["last_shutdown_clean"]
                lines.append(f"last shutdown: {'clean' if clean else 'interrupted'}")
            lines.append(f"recent failures: {len(recent)}")
            lines.extend(f"  {item.get('failure_class')} · {str(item.get('stage'))}"
                         for item in recent[:3])
            return "\n".join(lines)

        self._background("Health", check)

    def _show_improvements(self) -> None:
        def review() -> str:
            improvement = self.runtime.improvement
            if improvement is None:
                return "Self-improvement is not available in this runtime."
            return improvement.report()

        self._append("system", "Looking at my own record…")
        self._background("What would make me better", review)

    def _show_code_monitor(self) -> None:
        def scan() -> str:
            monitor = getattr(self.runtime, "code_monitor", None)
            if monitor is None:
                return "Code monitor is unavailable."
            monitor.scan_all()
            return monitor.format_status(monitor.status())

        self._background("Code monitor", scan)

    def _download_file(self) -> None:
        url = simpledialog.askstring("Download file", "HTTPS file URL:", parent=self.root)
        if url:
            self._background("Download file", lambda: self._tool(
                "download_public_file", {"url": url.strip()}), show_in_chat=True)

    # -------------------------------------------------------- sensor lights

    def _sensor_state(self) -> dict[str, tuple[str, bool]]:
        """One line per sensor: what it says, and whether it is live.

        Every probe is wrapped. A status call that raises must dim a light,
        never take the window down -- the same rule the state snapshot learned
        the hard way.
        """
        state: dict[str, tuple[str, bool]] = {}

        def probe(key: str, label: str, read) -> None:
            try:
                text, live = read()
                state[key] = (f"{label} {text}", live)
            except Exception:
                state[key] = (f"{label} —", False)

        def camera():
            status = self.runtime.camera.status()
            on = str(status.get("state", "")).upper() == "ON" or bool(status.get("active"))
            if on:
                return "on", True
            detected = int(status.get("devices_detected", 0) or 0)
            return ("ready" if detected else "none"), False

        def microphone():
            voice = self.runtime.voice.status() if self.runtime.voice else {}
            if voice.get("enabled"):
                return voice.get("mode", "on").replace("_", "-"), True
            return "off", False

        def screen():
            count = int(self.runtime.displays.status().get("count", 0) or 0)
            return (f"{count}" if count else "none"), bool(count)

        def clipboard():
            status = self.runtime.clipboard.status()
            characters = int(status.get("characters", 0) or 0)
            return (f"{characters}c" if characters else "empty"), characters > 0

        def vision():
            model = getattr(getattr(self.runtime.vision, "provider", None), "model", None)
            return (str(model).split(":")[0] if model else "off"), self.runtime.vision is not None

        def browser():
            page = getattr(self.runtime.browser, "page", None)
            return ("open" if page is not None else "closed"), page is not None

        probe("camera", "Camera", camera)
        probe("microphone", "Mic", microphone)
        probe("screen", "Screen", screen)
        probe("clipboard", "Clipboard", clipboard)
        probe("vision", "Vision", vision)
        def wifi():
            status = self.runtime.registry.csi.status()
            receiving = [item for item in status["connections"] if item["receiving"]]
            if not receiving:
                return ("open" if status["connected"] else "off"), False
            item = receiving[-1]
            return (f"CSI {item['frames_per_second']:.0f}/s" if item["is_csi"] else "signal"), True

        probe("browser", "Browser", browser)
        probe("wifi", "Wi-Fi", wifi)
        return state

    def _refresh_sensors(self) -> None:
        """Read the devices. Never on the UI thread.

        Measured on this machine: camera.status() takes 610ms because it
        enumerates capture devices, and clipboard.status() 207ms. Together
        that is 817ms of work, and the tick that wanted it runs every 250ms --
        so the window could never finish one refresh before owing the next.
        It did not stutter, it stopped.

        Sensor lights do not need to be fresh to the frame. They need to be
        honest within a second or two, which a slow background poll gives at
        no cost to the window.
        """
        try:
            reading = self._sensor_state()
        except Exception as exc:
            self._record_background_failure("Sensor polling", exc)
            reading = {}
        finally:
            self._sensor_poll_running = False
        if not reading:
            return
        self._sensor_reading = reading
        self._camera_on = reading.get("camera", ("", False))[1]

    def _paint_sensors(self) -> None:
        privacy = self.runtime.authority.privacy_mode
        for key, (text, live) in self._sensor_reading.items():
            variable = self.sensor_vars.get(key)
            label = self.sensor_labels.get(key)
            if variable is None or label is None:
                continue
            variable.set_if_changed(text)
            # Privacy mode greys the sensing ones out, because a light that
            # still reads "live" while privacy is on is a lie.
            if privacy and key in {"camera", "microphone", "screen", "clipboard", "wifi"}:
                label.configure(fg=self.GOLD, bg=self.PANEL)
            else:
                label.configure(fg=self.OLIVE_SOFT if live else self.MUTED, bg=self.PANEL)

    def _paint_wifi_sensing(self) -> None:
        """The Wi-Fi sensing strip: connection, movement level and the last minute as bars. Once a second."""
        if time.monotonic() - self._wifi_painted_at < 1.0:
            return
        self._wifi_painted_at = time.monotonic()
        monitor = getattr(self.runtime.registry, "sensing", None)
        if monitor is None:
            self.wifi_status_var.set_if_changed("Unavailable in this runtime")
            return
        from app.gui_sensing import level_colour, reading_height

        snapshot = monitor.snapshot(history=60)
        status = snapshot["status"]
        receiving = [item for item in status["connections"] if item["receiving"]]
        if self.runtime.authority.privacy_mode:
            text, colour = "Paused: privacy mode", self.GOLD
        elif receiving:
            item = receiving[-1]
            what = f"real CSI · {item['subcarriers']} subcarriers" if item["is_csi"] else "signal strength (not CSI)"
            text, colour = f"{item['name']} · {what} · {item['frames_per_second']}/s", self.OLIVE_SOFT
        elif status["connected"]:
            text, colour = "Connected, no frames arriving", self.GOLD_SOFT
        else:
            text, colour = "Not connected · Open window to connect", self.MUTED
        self.wifi_status_var.set_if_changed(text)
        self.wifi_dot.configure(fg=colour)
        level = snapshot["level"] if receiving else "unknown"
        self.wifi_level_var.set_if_changed(level.capitalize() if level != "unknown" else "")
        self.wifi_level_label.configure(fg=level_colour(type(self), level))
        canvas = self.wifi_spark
        canvas.delete("all")
        width, height = self.PREVIEW_PIXELS[0], 34
        history = snapshot["history"] if receiving else []
        if not history:
            canvas.create_text(width // 2, height // 2, text="Movement shows here while a source is connected",
                               fill=self.MUTED, font=("Segoe UI", 8))
            return
        bar = width / 60
        for index, reading in enumerate(history):
            x = width - (len(history) - index) * bar
            canvas.create_rectangle(x, height - 2 - reading_height(reading) * (height - 6), x + bar - 1, height - 1,
                                    outline="", fill=level_colour(type(self), reading["level"]))

    def _open_sensing(self, tab: str = "sensing") -> None:
        """Wi-Fi sensing and the router: connect a CSI source, watch it live, cross-check with the camera, see the router."""
        from app.gui_sensing import SensingStudio

        if getattr(self.runtime.registry, "sensing", None) is None:
            self._append("error", "Wi-Fi sensing is unavailable in this runtime.")
            return
        studio = getattr(self, "_sensing_studio", None)
        if studio is None or not studio.winfo_exists():
            studio = self._sensing_studio = SensingStudio(self)
        studio.deiconify()
        studio.lift()
        studio.show_tab(tab)

    def _open_radar(self) -> None:
        """Around me: every hazard near you on a radar, with the list beside it."""
        from app.gui_radar import RadarWindow

        window = getattr(self, "_radar_window", None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._radar_window = RadarWindow(self)

    def _open_location_privacy(self) -> None:
        """Location privacy: who can learn where you are, and HAYAT's own guard (app/location_privacy.py)."""
        from app.gui_location_privacy import LocationPrivacyWindow

        window = getattr(self, "_location_privacy_window", None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._location_privacy_window = LocationPrivacyWindow(self)

    def _open_router(self) -> None:
        """The router & network tab of the Wi-Fi window."""
        self._open_sensing("router")

    def _approval(self, tool: str, reason: str, arguments: dict[str, Any]) -> bool:
        result = threading.Event()
        answer = {"value": False}

        def ask() -> None:
            try:
                answer["value"] = messagebox.askyesno("Permission required", f"Allow {tool}?\n\n{reason}",
                                                      parent=self.root, default="no", icon="warning")
            finally:
                result.set()

        self._post(ask)
        result.wait(timeout=60)
        return bool(answer["value"])

    def _install_approval(self) -> None:
        if not self._closed:
            self.runtime.registry.approval_callback = self._approval

    def _schedule(self, delay: int, callback: Any, *args: Any) -> str | None:
        """`after`, with the handle kept so it can be cancelled on the way out."""
        if self._closed:
            return None
        def run() -> None:
            self._after_handles.discard(handle)
            self._run_scheduled(callback, *args)
        handle = self.root.after(delay, run)
        self._after_handles.add(handle)
        return handle

    def _run_scheduled(self, callback: Any, *args: Any) -> None:
        """Run a scheduled callback, unless the window has since closed."""
        if self._closed:
            return
        callback(*args)

    def _post(self, callback: Any, *args: Any) -> None:
        """Hand work to the UI thread, dropping it if the window is gone.

        Workers only write a queue; the main loop drains it. No worker calls
        Tk, including during shutdown. UI callers can run immediately.
        """
        if self._closed:
            return
        if threading.get_ident() == self._ui_thread_id:
            self._run_scheduled(callback, *args)
            return
        self._ui_queue.put((callback, args))

    def _drain_ui(self) -> None:
        if self._closed:
            return
        self._schedule(40, self._drain_ui)
        # Bound each batch so chat/results cannot starve scrolling and input.
        deadline = time.monotonic() + .008
        for _ in range(50):
            if time.monotonic() >= deadline:
                break
            try:
                callback, args = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._run_scheduled(callback, *args)
            except Exception as exc:
                self._record_background_failure("Display update", exc)

    def _cancel_scheduled(self) -> None:
        for handle in list(self._after_handles):
            try:
                self.root.after_cancel(handle)
            except Exception:
                log_swallowed(_log, "cancelling a scheduled callback")
        self._after_handles.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._remember_window()
        # Watch mode never outlives the window: it is not remembered, and the
        # camera it opened is released.
        mode = getattr(self.runtime, "watch", None)
        if mode is not None and mode.active:
            mode.stop("window closed")
        if getattr(self, "_draft", None) is not None:
            self._draft.close()
        if self._generation_cancel is not None:
            self._generation_cancel.set()
        self._closed = True
        intelligence_home = getattr(self, '_intelligence_home', None)
        if intelligence_home is not None:
            intelligence_home.close()
        self.activity_monitor.close()
        for unsubscribe in self._tool_event_unsubscribers:
            unsubscribe()
        self._tool_event_unsubscribers.clear()
        self._close_operation_monitor()
        video_workflow = getattr(self.runtime.registry, 'video_workflow', None)
        if video_workflow is not None:
            video_workflow.player_dispatch = None
        intelligence = getattr(self.runtime, 'intelligence', None)
        if intelligence is not None:
            intelligence.player_dispatch = None
        for card in self._video_cards.values():
            card.close()
        for card in self._video_link_cards.values():
            card.close()
        studio = getattr(self, '_video_studio', None)
        if studio is not None and studio.winfo_exists():
            studio.close()
        self._preview_workers.shutdown(wait=False, cancel_futures=True)
        self._transcript.close()
        # Before anything is destroyed. A callback that fires against a
        # half-torn-down interpreter is the thing being prevented here.
        self._cancel_scheduled()
        if self._mindstream_unsubscribe is not None:
            try:
                self._mindstream_unsubscribe()
            except Exception:
                log_swallowed(_log, "unsubscribing the Mindstream")
            self._mindstream_unsubscribe = None
        _CLAIMED_ROOTS.discard(id(self.root))
        # Stop serving the phone before the runtime it talks to is torn down.
        try:
            self._remote_access.stop()
        except Exception:
            # Compatibility for a partially constructed window or an older
            # injected test server that does not have the manager.
            if self._web_server is not None:
                try:
                    self._web_server.stop()
                except Exception:
                    log_swallowed(_log, "stopping the web server on close")
        self._web_server = None
        # Before the runtime goes: a preview left running would keep reading a
        # device that is being torn down underneath it.
        try:
            self._close_camera_view()
            if self.camera_stream is not None:
                self.camera_stream.stop()
                self.camera_stream = None
        except Exception:
            log_swallowed(_log, "stopping the camera on close", level=logging.WARNING)
        try:
            self.runtime.close()
        finally:
            self._inline_images.clear()
            getattr(self.root, "_hayat_icons", {}).clear()
            self.root.destroy()


def run_gui(runtime: Any) -> int:
    return HayatGUI(runtime).run()
