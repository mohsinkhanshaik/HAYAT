"""Select an existing reference voice without changing saved defaults."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

from app.gui_resources import BooleanVar, StringVar
from app.voice.cloning import LANGUAGES


def open_reference_voice(page):
    dialog = tk.Toplevel(page.page)
    dialog.title("HAYAT · Reference voice")
    dialog.transient(page.page.winfo_toplevel())
    dialog.configure(bg=page.palette.PANEL)
    body = tk.Frame(dialog, bg=page.palette.PANEL, padx=20, pady=16)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="Match a supplied voice", font=("Segoe UI Semibold", 14)).pack(anchor="w")
    ttk.Label(body, text="Use your own recording, or one the speaker has agreed to share.\n"
              "Needs a local XTTS checkpoint and the optional coqui-tts engine.\n"
              "Choose 3–60 seconds of clear speech in a 16-bit WAV file.", wraplength=490).pack(anchor="w", pady=(8, 12))
    name = StringVar(dialog, "My voice")
    model = StringVar(dialog, "data/voices/xtts")
    reference = StringVar(dialog, "")
    language = StringVar(dialog, "en")
    consent = BooleanVar(dialog, False)
    status = StringVar(dialog, "This selection applies to the current session. Recordings stay local.")
    for title, variable, chooser in (
            ("Voice name", name, None),
            ("XTTS model folder", model, lambda: filedialog.askdirectory(parent=dialog, title="Choose a local XTTS model folder")),
            ("Reference recording", reference, lambda: filedialog.askopenfilename(parent=dialog, title="Choose your agreed voice recording", filetypes=[("WAV audio", "*.wav")]))):
        ttk.Label(body, text=title).pack(anchor="w", pady=(5, 2))
        row = tk.Frame(body, bg=page.palette.PANEL); row.pack(fill="x")
        ttk.Entry(row, textvariable=variable, width=54).pack(side="left", fill="x", expand=True)
        if chooser:
            def choose(variable=variable, chooser=chooser):
                selected = chooser()
                if selected:
                    variable.set(selected)
            ttk.Button(row, text="Browse", command=choose).pack(side="right", padx=(6, 0))
    ttk.Label(body, text="Language").pack(anchor="w", pady=(7, 2))
    ttk.Combobox(body, textvariable=language, values=sorted(LANGUAGES), state="readonly", width=10).pack(anchor="w")
    ttk.Checkbutton(body, text="The recording is mine, or the speaker agreed to this use.", variable=consent).pack(anchor="w", pady=10)
    ttk.Label(body, textvariable=status, wraplength=490).pack(fill="x", pady=(2, 10))
    actions = tk.Frame(body, bg=page.palette.PANEL); actions.pack(fill="x")

    def select():
        try:
            result = page.voice.use_reference_voice({"checkpoint_dir": model.get(), "reference_audio": reference.get(),
                                                      "speaker_name": name.get(), "language": language.get(),
                                                      "speaker_consent": consent.get()})
            status.set(f"Selected {result['voice']}. Use a speech sample in Voice Studio to hear it; voice quality has not yet been verified.")
        except (RuntimeError, ValueError, OSError) as exc:
            status.set(str(exc))

    def restore():
        try:
            restored = page.voice.restore_normal_voice()
            status.set("Normal voice restored." if restored else "Normal voice is already selected.")
        except RuntimeError as exc:
            status.set(str(exc))

    ttk.Button(actions, text="Normal voice", command=restore).pack(side="left")
    ttk.Button(actions, text="Use for this session", command=select).pack(side="right")
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog._reference_fields = {"name": name, "model": model, "reference": reference, "language": language, "consent": consent, "status": status}
    dialog._select_reference = select
    from app.gui_windows import place_over
    place_over(dialog, page.page.winfo_toplevel())
    return dialog
