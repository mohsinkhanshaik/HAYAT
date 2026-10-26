"""Camera action chooser, separate from the main desktop window."""
import tkinter as tk
from tkinter import ttk

from app.gui_depth import DepthSurface
from app.gui_icons import apply_icons
from app.gui_fonts import DISPLAY


def show_vision_controls(gui) -> None:
    """A small chooser over the window, closed by its own button, Escape or a choice.

    It used to open in the screen's top-left corner, where a docked bar
    (Voice Access) hid its title bar -- the only close button -- and it
    stayed over half the main window. It now opens centred over HAYAT
    inside the usable screen area, has a Close button of its own, and
    closes itself once an action is chosen so the answer is not covered.
    """
    from app.gui_windows import place_over

    existing = getattr(gui, "_vision_window", None)
    if existing is not None and existing.winfo_exists():
        place_over(existing, gui.root, (560, getattr(gui, "_vision_height", 300)))
        existing.deiconify()
        existing.lift()
        existing.focus_force()
        return
    window = gui._vision_window = tk.Toplevel(gui.root)
    window.withdraw()
    window.title("HAYAT · Camera eye")
    window.transient(gui.root)
    window.configure(bg=gui.BLACK)
    window.protocol("WM_DELETE_WINDOW", gui._close_vision_controls)
    window.bind("<Escape>", lambda _event: (gui._close_vision_controls(), "break")[1])
    surface = DepthSurface(window, type(gui), padding=14)
    surface.pack(fill="x", padx=12, pady=12)
    heading = tk.Frame(surface.body, bg=gui.PANEL)
    heading.pack(fill="x")
    tk.Label(heading, text="Camera eye", bg=gui.PANEL, fg=gui.WHITE,
             font=(DISPLAY, 16), anchor="w").pack(side="left")
    gui.vision_close_button = ttk.Button(heading, text="✕  Close", width=0, style="Hayat.TButton",
                                          command=gui._close_vision_controls)
    gui.vision_close_button.pack(side="right")
    tk.Label(surface.body, text="Choose what HAYAT should look at. Esc closes this.", bg=gui.PANEL,
             fg=gui.MUTED, font=("Segoe UI", 10), anchor="w").pack(fill="x", pady=(4, 8))
    tk.Label(surface.body, textvariable=gui.preview_detail, bg=gui.PANEL, fg=gui.HOLO,
             font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(0, 8))
    actions = tk.Frame(surface.body, bg=gui.PANEL)
    actions.pack(fill="x")
    gui.vision_buttons: dict[str, ttk.Button] = {}
    for i, (title, method) in enumerate(gui.VISION_ACTIONS):
        button = ttk.Button(actions, text=title, style="Hayat.TButton",
                            command=lambda name=method: gui._run_vision_action(name))
        button.grid(row=i // 2, column=i % 2, sticky="ew", padx=3, pady=5)
        gui.vision_buttons[method] = button
    for column in range(2):
        actions.grid_columnconfigure(column, weight=1, uniform="vision")
    window.update_idletasks()
    # The rounded surface reports its real height only once shown, so the
    # window is sized from what it holds (plus the surface's own margins).
    gui._vision_height = surface.content.winfo_reqheight() + 18 + 24
    place_over(window, gui.root, (560, gui._vision_height))
    window.deiconify()
    window.lift()
    window.focus_force()
    apply_icons(window, type(gui))

