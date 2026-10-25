"""Shared visual tokens, monochrome navigation symbols and accessible ttk states."""
from __future__ import annotations

from app.gui_resources import BooleanVar, StringVar

import tkinter as tk
import weakref
from tkinter import font as tkfont, ttk


class Palette:
    BLACK = "#090C0A"
    PANEL = "#1A221F"
    PANEL_2 = "#26312B"
    PANEL_HOVER = "#354135"
    LINE = "#485643"
    WHITE = "#F7F8F2"
    MUTED = "#ADB6A5"
    GOLD = "#DDBB68"
    GOLD_SOFT = "#F0D899"
    ROYAL = "#284EC0"
    ROYAL_SOFT = "#B7C9FF"
    HOLO = "#AFC5EE"
    OLIVE = "#8C9B4E"
    OLIVE_SOFT = "#C2D18D"
    ALERT = "#F05D5E"
    BG, TEXT, ACCENT, SUCCESS, WARNING = BLACK, WHITE, GOLD, OLIVE_SOFT, GOLD


DARK = {key: value for key, value in vars(Palette).items() if key.isupper()}
MIDNIGHT = {**DARK, "BLACK": "#080F18", "PANEL": "#101C29", "PANEL_2": "#182737",
            "PANEL_HOVER": "#24384B", "LINE": "#33475A", "WHITE": "#F3F6FC",
            "MUTED": "#A9BACD", "GOLD": "#F1C765", "GOLD_SOFT": "#FFE1A0",
            "ROYAL": "#235ACA", "ROYAL_SOFT": "#8FC4FF", "HOLO": "#5BD3E5",
            "OLIVE": "#299D78", "OLIVE_SOFT": "#65DBA7"}
LIGHT = {**DARK, "BLACK": "#F6F7FA", "PANEL": "#FFFFFF", "PANEL_2": "#E8EDF5",
         "PANEL_HOVER": "#DCE5F3", "LINE": "#BEC9D8", "WHITE": "#152033", "MUTED": "#4C5D73",
         "GOLD": "#946400", "GOLD_SOFT": "#805600", "ROYAL": "#2257B2", "ROYAL_SOFT": "#1C4C99", "HOLO": "#19729C",
         "OLIVE": "#626F2E", "OLIVE_SOFT": "#4B5B22", "ALERT": "#B52635"}
CONTRAST = {**DARK, "BLACK": "#000000", "PANEL": "#080808", "PANEL_2": "#161616",
            "LINE": "#D5D5D5", "WHITE": "#FFFFFF", "MUTED": "#D8D8D8", "ROYAL_SOFT": "#AECBFF"}
# Glass: frosted panels over a deep cool backdrop (app/gui_glass.py).
from app.gui_glass import GLASS, install_title_bars, refresh_backdrops  # noqa: E402

THEMES = {"Midnight": MIDNIGHT, "Dark": DARK, "Light": LIGHT, "High contrast": CONTRAST, "Glass": {**DARK, **GLASS}}
TEXT_SCALES = {"Standard": 1.0, "Comfortable": 1.15, "Large": 1.3, "Extra large": 1.5}
ICONS = {"chat": "◇", "history": "≡", "tasks": "☑", "task_graph": "⋈", "gallery": "▧",
         "models": "◉", "background": "↻", "operator": "⌘", "research": "⌕", "tools": "⚒",
         "memory": "▤", "learning": "▥", "training": "△", "health": "+", "improve": "↑",
         "toolbox": "⊞", "files": "▱", "settings": "⚙"}


def tokens(name):
    values = dict(THEMES.get(name, DARK))
    values.update(BG=values["BLACK"], TEXT=values["WHITE"], ACCENT=values["GOLD"],
                  SUCCESS=values["OLIVE_SOFT"], WARNING=values["GOLD"])
    return values


def style_controls(root, palette):
    style = ttk.Style(root)
    for name in ("TFrame", "TLabel", "TLabelframe", "TLabelframe.Label"):
        style.configure(name, background=palette.BLACK, foreground=palette.WHITE)
    style.configure("TLabel", font=("Segoe UI", 10))
    style.configure("TNotebook", background=palette.BLACK, borderwidth=0, tabmargins=(0, 0, 0, 6),
                    bordercolor=palette.LINE, lightcolor=palette.LINE, darkcolor=palette.LINE)
    style.configure("TNotebook.Tab", background=palette.PANEL_2, foreground=palette.WHITE,
                    padding=(16, 9), font=("Segoe UI Semibold", 10), borderwidth=0,
                    lightcolor=palette.PANEL_2, darkcolor=palette.PANEL_2, bordercolor=palette.LINE)
    # clam maps a selected tab to padding "6 4 6 2", which overrode the padding above only while selected:
    # the chosen tab shrank and sat lower than the others, so the unselected one looked active. Clearing the
    # map keeps every tab its own style's size (Side and Intelligence tabs too). clam's light top edge on
    # unselected tabs drew a pale outline; only the selected tab gets an edge, in gold.
    style.map("TNotebook.Tab", background=[("selected", palette.GOLD), ("active", palette.PANEL_HOVER)],
              foreground=[("selected", palette.BLACK)], padding=[],
              lightcolor=[("selected", palette.GOLD)])
    # Compact tabs for narrow side panels (the Trade Center's six tabs in about 350 px).
    style.configure("Side.TNotebook", background=palette.BLACK, borderwidth=0, tabmargins=(0, 0, 0, 4))
    style.configure("Side.TNotebook.Tab", padding=(6, 6), font=("Segoe UI Semibold", 8))
    # Segmented choices and chips. They were styled only once Global Intelligence had opened, so a Trade
    # Center opened first showed every segment as a plain light box.
    for name, padding in (("Chip.Toolbutton", (9, 3)), ("Segment.Toolbutton", (12, 5))):
        style.configure(name, background=palette.PANEL_2, foreground=palette.WHITE, padding=padding, relief="flat",
                        borderwidth=1, bordercolor=palette.LINE, font=("Segoe UI Semibold", 9))
        style.map(name, background=[("selected", palette.GOLD), ("active", palette.PANEL_HOVER)],
                  foreground=[("selected", palette.BLACK)])
    style.configure("Small.Segment.Toolbutton", padding=(8, 4))  # a long row of choices in a narrow pane
    for name in ("TButton", "TCheckbutton", "TRadiobutton"):
        style.configure(name, background=palette.PANEL_2, foreground=palette.WHITE,
                        padding=(12, 8), font=("Segoe UI", 10), borderwidth=1, focuscolor=palette.GOLD,
                        relief="flat", bordercolor=palette.LINE, lightcolor=palette.PANEL_2,
                        darkcolor=palette.PANEL_2)
        style.map(name, background=[("disabled", palette.PANEL), ("pressed", palette.ROYAL), ("active", palette.PANEL_HOVER)],
                  foreground=[("disabled", palette.MUTED), ("pressed", "#FFFFFF")])
    # The tick box itself: clam drew a white square with a dark cross on every dark panel.
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, indicatorbackground=palette.PANEL, indicatorforeground=palette.GOLD,
                        upperbordercolor=palette.LINE, lowerbordercolor=palette.LINE, indicatormargin=(0, 0, 8, 0))
        style.map(name, indicatorbackground=[("pressed", palette.PANEL_HOVER), ("selected", palette.PANEL)],
                  indicatorforeground=[("disabled", palette.MUTED)])
    # Spin boxes looked like a white form field with grey arrows; they now match the entries beside them.
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, fieldbackground=palette.PANEL_2, foreground=palette.WHITE,
                        insertcolor=palette.WHITE, padding=6, bordercolor=palette.LINE,
                        lightcolor=palette.PANEL_2, darkcolor=palette.PANEL_2, borderwidth=1)
    for name in ("TCombobox", "TSpinbox"):
        style.configure(name, background=palette.PANEL_2, arrowcolor=palette.WHITE)
        style.map(name, background=[("active", palette.PANEL_HOVER)], arrowcolor=[("disabled", palette.MUTED)])
        style.map(name, fieldbackground=[("readonly", palette.PANEL_2)], foreground=[("readonly", palette.WHITE)],
                  bordercolor=[("focus", palette.GOLD), ("!focus", palette.LINE)])
    for name in ("Treeview", "Workspace.Treeview"):
        style.configure(name, background=palette.PANEL_2, fieldbackground=palette.PANEL_2,
                        foreground=palette.WHITE, rowheight=32, font=("Segoe UI", 10), borderwidth=0)
        style.map(name, background=[("selected", palette.ROYAL)], foreground=[("selected", "#FFFFFF")])
        # Flat, dark column headers: the default bright bevel drew a white grid over every table.
        style.configure(name + ".Heading", background=palette.PANEL, foreground=palette.WHITE, padding=8,
                        relief="flat", bordercolor=palette.LINE, lightcolor=palette.PANEL, darkcolor=palette.PANEL)
        style.map(name + ".Heading", background=[("active", palette.PANEL_HOVER)])
        style.configure(name, bordercolor=palette.LINE, lightcolor=palette.PANEL_2, darkcolor=palette.PANEL_2)
    style.configure("Workspace.TFrame", background=palette.BLACK)
    style.configure("Workspace.TLabel", background=palette.BLACK, foreground=palette.WHITE)
    # Every scrollbar, not only the ones that asked for the Hayat style: an
    # unstyled one rendered as a bright white bar in the dark window. Each
    # needs its own state map too: without one, a horizontal bar with nothing
    # to scroll fell back to clam's "disabled" grey (#dcdad5) and drew a pale
    # stripe under the Security, Jobs, cameras and networking tables.
    for name in ("Hayat.Vertical.TScrollbar", "Vertical.TScrollbar", "HayatThin.Vertical.TScrollbar",
                 "Horizontal.TScrollbar"):
        style.configure(name, background=palette.PANEL_2, troughcolor=palette.BLACK, borderwidth=0,
                        arrowcolor=palette.MUTED, bordercolor=palette.BLACK, lightcolor=palette.PANEL_2,
                        darkcolor=palette.PANEL_2, gripcount=0)
        style.map(name, background=[("disabled", palette.PANEL), ("pressed", palette.ROYAL),
                                    ("active", palette.PANEL_HOVER)],
                  arrowcolor=[("disabled", palette.LINE), ("active", palette.WHITE)])
    # Split-pane dividers too: unstyled, the Knowledge Library's divider was a bright grey bar.
    style.configure("TPanedwindow", background=palette.BLACK)
    style.configure("Sash", background=palette.LINE, bordercolor=palette.LINE, lightcolor=palette.LINE,
                    darkcolor=palette.LINE, sashthickness=6, gripcount=0, handlesize=0)
    # The side rails keep the 9-pixel bar they were laid out around.
    style.configure("HayatThin.Vertical.TScrollbar", arrowsize=9, width=9)
    style.configure("Hayat.Horizontal.TProgressbar", background=palette.GOLD,
                    troughcolor=palette.PANEL_2, borderwidth=0, lightcolor=palette.GOLD,
                    darkcolor=palette.GOLD)
    root.option_add("*TCombobox*Listbox.background", palette.PANEL_2)
    root.option_add("*TCombobox*Listbox.foreground", palette.WHITE)


def style_hayat_buttons(root, palette):
    """Keep primary, secondary and keyboard-focus states legible in every theme."""
    style = ttk.Style(root)
    style.configure("Hayat.TButton", background=palette.PANEL_2, foreground=palette.WHITE,
                    bordercolor=palette.LINE, focuscolor=palette.GOLD, borderwidth=1,
                    lightcolor=palette.PANEL_2, darkcolor=palette.PANEL_2,
                    relief="flat", padding=(12, 7), font=("Segoe UI Semibold", 9))
    style.map("Hayat.TButton",
              background=[("disabled", palette.PANEL), ("pressed", palette.ROYAL),
                          ("active", palette.PANEL_HOVER)],
              foreground=[("disabled", palette.MUTED), ("pressed", "#FFFFFF"),
                          ("active", palette.ROYAL_SOFT)],
              bordercolor=[("focus", palette.GOLD), ("active", palette.LINE)],
              lightcolor=[("active", palette.PANEL_HOVER)], darkcolor=[("active", palette.PANEL_HOVER)],
              relief=[("pressed", "flat"), ("!pressed", "flat")])
    style.configure("HayatAccent.TButton", background=palette.GOLD, foreground=palette.BLACK,
                    bordercolor=palette.GOLD, focuscolor=palette.ROYAL_SOFT,
                    lightcolor=palette.GOLD, darkcolor=palette.GOLD,
                    borderwidth=1, relief="flat", padding=(16, 7),
                    font=("Segoe UI Semibold", 9))
    style.map("HayatAccent.TButton",
              background=[("disabled", palette.PANEL), ("pressed", palette.GOLD_SOFT),
                          ("active", palette.GOLD_SOFT)],
              foreground=[("disabled", palette.MUTED), ("pressed", palette.BLACK),
                          ("active", palette.BLACK)],
              bordercolor=[("focus", palette.ROYAL_SOFT)],
              lightcolor=[("active", palette.GOLD_SOFT)], darkcolor=[("active", palette.GOLD_SOFT)],
              relief=[("pressed", "flat"), ("!pressed", "flat")])
    style.configure("HayatRoyal.TButton", background=palette.ROYAL, foreground="#FFFFFF",
                    bordercolor=palette.ROYAL, lightcolor=palette.ROYAL, darkcolor=palette.ROYAL,
                    borderwidth=1, relief="flat", padding=(14, 7), font=("Segoe UI Semibold", 9))
    style.map("HayatRoyal.TButton", background=[("disabled", palette.PANEL), ("active", palette.ROYAL)],
              foreground=[("disabled", palette.MUTED)], bordercolor=[("focus", palette.GOLD)])


def _scaled_size(base: int, scale: float) -> int:
    """Keep small UI copy legible while preserving pixel-sized font signs."""
    sign = -1 if base < 0 else 1
    magnitude = abs(base)
    minimum = 9 if magnitude <= 13 else magnitude
    return sign * max(minimum, round(magnitude * scale))


def owned_font(owner: tk.Misc, **options) -> tkfont.Font:
    """Release Tcl fonts with their widget, never from worker-thread GC.

    Widget callbacks can form cycles collected by any Python thread. Tk's
    default Font finalizer calls Tcl and can deadlock a worker against the UI.
    The Destroy event runs on the Tk thread and owns that cleanup instead.
    """
    font = tkfont.Font(root=owner, **options)
    font.delete_font = False
    fonts = getattr(owner, "_hayat_owned_fonts", None)
    if fonts is None:
        fonts = owner._hayat_owned_fonts = []
        owner_ref = weakref.ref(owner)

        def release(event):
            widget = owner_ref()
            if widget is None or event.widget is not widget:
                return
            for item in widget._hayat_owned_fonts:
                try:
                    widget.tk.call("font", "delete", item.name)
                except tk.TclError:
                    pass
            widget._hayat_owned_fonts.clear()
            getattr(widget, "_hayat_fonts", {}).clear()

        owner.bind("<Destroy>", release, add="+")
    fonts.append(font)
    return font


def apply_text_scale(root: tk.Misc, scale: float) -> None:
    """Resize existing Tk text without compounding repeated live previews."""
    if scale not in TEXT_SCALES.values():
        scale = 1.0
    root._hayat_text_scale = scale

    def resize_font(owner, value, cache, key, setter):
        if not value:
            return
        entry = cache.get(key)
        if entry is None:
            try:
                base = int(owner.tk.call("font", "actual", value, "-size"))
            except (tk.TclError, ValueError, TypeError):
                return
            if not base or abs(base) > 13:
                return  # Large headings are intentionally fixed.
            font = owned_font(owner, font=value)
            entry = (font, base)
            cache[key] = entry
            setter(font)
        font, base = entry
        font.configure(size=_scaled_size(base, scale))

    def walk(widget):
        if getattr(widget, "_hayat_theme_preview", False):
            return
        cache = getattr(widget, "_hayat_fonts", None)
        if cache is None:
            cache = widget._hayat_fonts = {}
        try:
            resize_font(widget, widget.cget("font"), cache, "widget",
                        lambda font: widget.configure(font=font))
        except tk.TclError:
            pass
        if isinstance(widget, tk.Text):
            for tag in widget.tag_names():
                value = widget.tag_cget(tag, "font")
                resize_font(widget, value, cache, ("tag", tag),
                            lambda font, tag=tag: widget.tag_configure(tag, font=font))
        for child in widget.winfo_children():
            walk(child)

    walk(root)
    style_text_scale(root, scale)


def style_text_scale(root: tk.Misc, scale: float) -> None:
    """The named ttk styles' text sizes. They belong to the whole Tk interpreter, not to one window."""
    style = ttk.Style(root)
    style.configure("TLabel", font=("Segoe UI", _scaled_size(10, scale)))
    for name in ("TButton", "TCheckbutton", "TRadiobutton"):
        style.configure(name, font=("Segoe UI", _scaled_size(10, scale)))
    for name in ("Hayat.TButton", "HayatAccent.TButton", "HayatRoyal.TButton"):
        style.configure(name, font=("Segoe UI Semibold", _scaled_size(9, scale)))
    for name in ("Treeview", "Workspace.Treeview"):
        style.configure(name, font=("Segoe UI", _scaled_size(10, scale)),
                        rowheight=max(32, round(32 * scale)))
        style.configure(name + ".Heading", font=("Segoe UI Semibold", _scaled_size(10, scale)))
    for name in ("TEntry", "TCombobox"):
        style.configure(name, font=("Segoe UI", _scaled_size(10, scale)))


def apply_theme(root, palette, name):
    old = {key: getattr(palette, key) for key in DARK}
    new = tokens(name)
    mapping = {old[key].lower(): new[key] for key in old}
    for key, value in new.items():
        setattr(palette, key, value)
    def recolor(widget):
        if getattr(widget, "_hayat_theme_preview", False):
            return
        for option in ("background", "foreground", "activebackground", "activeforeground", "insertbackground",
                       "selectbackground", "selectforeground", "highlightbackground", "highlightcolor",
                       "disabledforeground", "troughcolor", "readonlybackground"):
            try:
                value = str(widget.cget(option)).lower()
                if value in mapping:
                    widget.configure(**{option: mapping[value]})
            except tk.TclError:
                pass
        if isinstance(widget, tk.Text):
            for tag in widget.tag_names():
                for option in ("foreground", "background"):
                    value = str(widget.tag_cget(tag, option)).lower()
                    if value in mapping:
                        widget.tag_configure(tag, **{option: mapping[value]})
        if isinstance(widget, tk.Canvas):
            for item in widget.find_all():
                for option in ("fill", "outline"):
                    try:
                        value = str(widget.itemcget(item, option)).lower()
                        if value in mapping:
                            widget.itemconfigure(item, **{option: mapping[value]})
                    except tk.TclError:
                        pass
        for child in widget.winfo_children():
            recolor(child)
        refresh = getattr(widget, "refresh_theme", None)
        if callable(refresh):
            refresh()
    recolor(root)
    style_controls(root, palette)
    style_hayat_buttons(root, palette)
    apply_text_scale(root, getattr(root, "_hayat_text_scale", 1.0))
    from app.gui_icons import apply_icons
    apply_icons(root, palette)
    # Windows draws the title bar; give it this appearance's colours too.
    install_title_bars(root, palette)
    # The aurora behind the glass panels shows only in the Glass appearance.
    refresh_backdrops(root)


def appearance_dialog(gui):
    existing = getattr(gui, "_appearance_window", None)
    if existing is not None and existing.winfo_exists():
        existing.deiconify()
        existing.lift()
        return existing
    dialog = tk.Toplevel(gui.root)
    gui._appearance_window = dialog
    dialog.withdraw()
    dialog.title("Appearance")
    dialog.transient(gui.root)
    dialog.configure(bg=gui.BLACK)
    dialog.resizable(True, True)
    committed_theme = getattr(gui, "_theme_name", "Dark")
    if committed_theme not in THEMES:
        committed_theme = "Dark"
    committed_motion = bool(gui.depth_nav.motion)
    committed_scale = getattr(gui, "_text_scale", 1.0)
    if committed_scale not in TEXT_SCALES.values():
        committed_scale = 1.0
    name = StringVar(dialog, committed_theme)
    reduce_motion = BooleanVar(dialog, not committed_motion)
    size_name = StringVar(dialog, next(label for label, value in TEXT_SCALES.items()
                                          if value == committed_scale))
    status = StringVar(dialog, "Choose a theme to preview it. Save to keep it.")
    dialog._appearance_choice = name
    dialog._appearance_motion = reduce_motion
    dialog._appearance_text_size = size_name

    body = tk.Frame(dialog, bg=gui.BLACK, padx=22, pady=18)
    body.pack(fill="both", expand=True)
    tk.Label(body, text="Make HAYAT comfortable to use", bg=gui.BLACK, fg=gui.WHITE,
             font=("Segoe UI Semibold", 15), anchor="w").pack(fill="x")
    tk.Label(body, text="Colors change as you choose. Escape restores the last applied look.",
             bg=gui.BLACK, fg=gui.MUTED, font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(3, 14))

    cards = {}
    descriptions = {"Midnight": "Deep navy, warm gold and clear cyan accents",
                    "Dark": "Black, gold, olive and royal blue", "Light": "Bright and clear in daylight",
                    "High contrast": "Sharper edges and brighter text",
                    "Glass": "Frosted navy panels over a soft aurora glow"}
    for title in THEMES:
        palette = tokens(title)
        card = tk.Frame(body, bg=gui.PANEL, padx=12, pady=9, highlightthickness=2,
                        highlightbackground=gui.LINE)
        card.pack(fill="x", pady=(0, 8))
        cards[title] = card
        label = tk.Frame(card, bg=gui.PANEL)
        label.pack(side="left", fill="x", expand=True)
        ttk.Radiobutton(label, text=title, variable=name, value=title).pack(anchor="w")
        tk.Label(label, text=descriptions[title], bg=gui.PANEL, fg=gui.MUTED,
                 font=("Segoe UI", 8), anchor="w").pack(fill="x", padx=(22, 0))
        sample = tk.Frame(card, bg=palette["PANEL"], width=124, height=36,
                          padx=7, pady=6, highlightthickness=1,
                          highlightbackground=palette["LINE"])
        sample._hayat_theme_preview = True
        sample.pack(side="right", padx=(8, 0))
        sample.pack_propagate(False)
        for colour in ("GOLD", "ROYAL", "OLIVE"):
            tk.Label(sample, bg=palette[colour], width=2).pack(side="left", fill="y", padx=(0, 5))
        tk.Label(sample, text="Aa", bg=palette["PANEL"], fg=palette["WHITE"],
                 font=("Segoe UI Semibold", 10)).pack(side="left")

    def preview(*_args):
        apply_theme(gui.root, type(gui), name.get())
        for title, card in cards.items():
            card.configure(highlightbackground=gui.GOLD if title == name.get() else gui.LINE)
        status.set("Previewing. Save to keep this appearance.")

    name.trace_add("write", preview)
    cards[committed_theme].configure(highlightbackground=gui.GOLD)
    ttk.Label(body, text="Text size").pack(anchor="w", pady=(10, 3))
    sizes = tk.Frame(body, bg=gui.BLACK)
    sizes.pack(fill="x")
    for label in TEXT_SCALES:
        ttk.Radiobutton(sizes, text=label, variable=size_name, value=label).pack(
            side="left", padx=(0, 8))
    tk.Label(body, text="HAYAT  ·  Clear chat, code and controls", bg=gui.PANEL,
             fg=gui.WHITE, padx=12, pady=9, anchor="w",
             font=("Segoe UI", 11)).pack(fill="x", pady=(8, 4))
    def preview_size(*_args):
        gui._set_text_scale(TEXT_SCALES[size_name.get()])
        status.set("Previewing. Save to keep this appearance.")

    size_name.trace_add("write", preview_size)
    reduce_motion.trace_add("write", lambda *_args: gui._set_workspace_motion(not reduce_motion.get()))
    ttk.Checkbutton(body, text="Reduce motion in the 3D workspace",
                    variable=reduce_motion).pack(anchor="w", pady=(7, 2))
    # How see-through the Glass window is, previewed live while dragging.
    from app.gui_glass import MIN_GLASS_OPACITY, glass_opacity, is_glass

    committed_opacity = glass_opacity()
    transparency = tk.IntVar(dialog, round((1 - committed_opacity) * 100))
    transparency_text = StringVar(dialog, "")
    ttk.Label(body, text="Glass transparency").pack(anchor="w", pady=(10, 3))
    transparency_row = tk.Frame(body, bg=gui.BLACK)
    transparency_row.pack(fill="x")

    def preview_transparency(*_args):
        value = max(0, min(round((1 - MIN_GLASS_OPACITY) * 100), int(float(transparency.get()))))
        transparency_text.set(f"{value}% see-through" + ("" if is_glass(gui) else " · applies in the Glass appearance"))
        if is_glass(gui):
            gui.root.attributes("-alpha", 1 - value / 100)

    ttk.Scale(transparency_row, from_=0, to=round((1 - MIN_GLASS_OPACITY) * 100), variable=transparency,
              command=preview_transparency).pack(side="left", fill="x", expand=True)
    ttk.Label(transparency_row, textvariable=transparency_text, width=34).pack(side="left", padx=(10, 0))
    preview_transparency()
    dialog._appearance_transparency = transparency
    ttk.Label(body, textvariable=status, wraplength=420).pack(anchor="w", pady=(9, 8))
    actions = tk.Frame(body, bg=gui.BLACK)
    actions.pack(fill="x", pady=(4, 0))

    def save():
        nonlocal committed_theme, committed_motion, committed_scale, committed_opacity
        gui._theme_name = name.get()
        committed_theme = name.get()
        committed_motion = not reduce_motion.get()
        committed_scale = TEXT_SCALES[size_name.get()]
        committed_opacity = round(1 - int(float(transparency.get())) / 100, 2)
        saved = gui._save_appearance(theme=committed_theme, motion=committed_motion,
                                     text_scale=committed_scale, glass_opacity=committed_opacity)
        status.set("Saved. This look will be used next time." if saved else
                   "Applied for this session; the preference could not be saved.")

    def cancel():
        if name.get() != committed_theme:
            apply_theme(gui.root, type(gui), committed_theme)
        if gui.depth_nav.motion != committed_motion:
            gui._set_workspace_motion(committed_motion)
        if gui._text_scale != committed_scale:
            gui._set_text_scale(committed_scale)
        if is_glass(gui):
            gui.root.attributes("-alpha", committed_opacity)
        dialog.destroy()

    dialog._appearance_save = save
    dialog._appearance_cancel = cancel
    ttk.Button(actions, text="Cancel", style="Hayat.TButton", command=cancel).pack(side="right")
    ttk.Button(actions, text="Save appearance", style="HayatAccent.TButton",
               command=save).pack(side="right", padx=(0, 8))
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    dialog.bind("<Escape>", lambda _event: (cancel(), "break")[1])
    from app.gui_windows import place_over

    apply_text_scale(dialog, committed_scale)
    place_over(dialog, gui.root)
    dialog.deiconify()
    dialog.lift()
    actions.winfo_children()[-1].focus_set()
    return dialog


class DarkScrolledText(tk.Text):
    """``scrolledtext.ScrolledText`` with a themed ttk scrollbar.

    On Windows the classic Tk scrollbar ignores its colours and draws as a
    bright white bar, the one light thing in every dark panel. Same layout and
    attributes as ScrolledText: ``.frame`` holds the text, ``.vbar`` the bar.
    """

    def __init__(self, master: tk.Misc | None = None, **kw) -> None:
        self.frame = tk.Frame(master, bg=kw.get("bg", kw.get("background", "")) or None)
        self.vbar = ttk.Scrollbar(self.frame, orient="vertical", style="Hayat.Vertical.TScrollbar")
        self.vbar.pack(side="right", fill="y")
        kw.update({"yscrollcommand": self.vbar.set})
        super().__init__(self.frame, **kw)
        self.pack(side="left", fill="both", expand=True)
        self.vbar["command"] = self.yview
        # The wheel glides instead of jumping a few lines per notch.
        from app.gui_motion import attach
        attach(self)
        # Geometry calls on the text go to its frame, as with ScrolledText.
        text_meths = vars(tk.Text).keys()
        methods = vars(tk.Pack).keys() | vars(tk.Grid).keys() | vars(tk.Place).keys()
        for name in methods.difference(text_meths):
            if name[0] != "_" and name not in ("config", "configure"):
                setattr(self, name, getattr(self.frame, name))

    def __str__(self) -> str:
        return str(self.frame)
