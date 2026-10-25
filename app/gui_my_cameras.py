"""Studios → My cameras: the owner's own cameras, added by exact address and played in HAYAT's player.

The login fields go straight to the Windows credential store (my_cameras.MyCameras.add); this
window never shows a stored login again, and nothing here searches the network for cameras.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from app.gui_resources import StringVar

HELP = ("Add a camera you own or are allowed to view, by its exact address, for example "
        "rtsp://192.168.1.20:554/stream1 or http://192.168.1.30/video.mjpg. HAYAT never scans your network. "
        "A login is kept in the Windows credential store and handed only to the local player.")


class MyCamerasWindow(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.p = gui, type(gui)
        self.cameras = gui.runtime.intelligence.my_cameras
        p = self.p
        self.title("HAYAT · My cameras")
        self.configure(bg=p.BLACK)
        from app.gui_windows import MARGIN, work_area
        area = work_area(gui.root)
        self.minsize(min(760, area[2] - area[0] - 2 * MARGIN), min(520, area[3] - area[1] - 2 * MARGIN - 40))
        self.bind("<Escape>", lambda _event: self.destroy())
        self.status = StringVar(self, HELP)
        tk.Label(self, text="My cameras", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 16), anchor="w").pack(
            fill="x", padx=16, pady=(14, 2))
        help_label = tk.Label(self, textvariable=self.status, bg=p.BLACK, fg=p.MUTED, wraplength=720, justify="left", anchor="w")
        help_label.pack(fill='x', padx=16)
        help_label.bind('<Configure>', lambda event: help_label.configure(wraplength=max(200, event.width - 4)))
        form = tk.Frame(self, bg=p.PANEL, padx=12, pady=10)
        form.pack(fill="x", padx=16, pady=10)
        self.fields: dict[str, StringVar] = {}
        for row, (key, label, show, width) in enumerate((("name", "Name", "", 24), ("url", "Address", "", 52),
                                                        ("username", "Login name", "", 24), ("password", "Password", "•", 24),
                                                        ("latitude", "Latitude (optional)", "", 12), ("longitude", "Longitude (optional)", "", 12))):
            self.fields[key] = StringVar(self, "")
            column = 0 if row < 2 or row % 2 == 0 else 2
            grid_row = row if row < 2 else 2 + (row - 2) // 2
            tk.Label(form, text=label, bg=p.PANEL, fg=p.MUTED).grid(row=grid_row, column=column, sticky="w", padx=(0, 6), pady=3)
            entry = ttk.Entry(form, textvariable=self.fields[key], width=width, show=show)
            entry.grid(row=grid_row, column=column + 1, columnspan=3 if row < 2 else 1, sticky="ew", pady=3)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        add = ttk.Button(form, text="Add camera", style="HayatAccent.TButton", command=self.add)
        add._hayat_icon = "check"
        add.grid(row=5, column=3, sticky="e", pady=(6, 0))
        search = tk.Frame(self, bg=p.BLACK)
        search.pack(fill='x', padx=16, pady=(0, 6))
        self.query = StringVar(self)
        ttk.Label(search, text='Filter cameras').pack(side='left', padx=(0, 8))
        ttk.Entry(search, textvariable=self.query).pack(side='left', fill='x', expand=True)
        ttk.Button(search, text='Refresh list', command=self.refresh, style='Hayat.TButton').pack(side='right', padx=(8, 0))
        self.query.trace_add('write', lambda *_: self.refresh())
        columns = ("kind", "host", "login")
        table = tk.Frame(self, bg=p.BLACK)
        table.pack(fill='both', expand=True, padx=16)
        self.tree = ttk.Treeview(table, columns=columns, height=8, selectmode='browse')
        for column, title, width in (("#0", "Camera", 200), ("kind", "Kind", 130), ("host", "Host", 220), ("login", "Login", 110)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, stretch=column in ("#0", "host"))
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        horizontal.pack(side='bottom', fill='x')
        vertical.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind("<Double-1>", lambda _event: self.play())
        actions = tk.Frame(self, bg=p.BLACK)
        actions.pack(side='bottom', fill='x', padx=16, pady=10, before=table)
        self.action_buttons = {}
        for label, command, icon in (("Play", self.play, "play"), ("Remove", self.remove, "stop")):
            button = ttk.Button(actions, text=label, style="Hayat.TButton", command=command)
            button._hayat_icon = icon
            button.pack(side="left", padx=(0, 6))
            self.action_buttons[label] = button
        self.refresh()
        from app.gui_icons import apply_icons
        from app.gui_windows import place_over
        apply_icons(self, p)
        place_over(self, gui.root, (820, 600))
        self.deiconify()

    def refresh(self) -> None:
        selected = self.tree.selection()
        position = self.tree.yview()[0], self.tree.xview()[0]
        query = self.query.get().casefold().strip()
        rows = [item for item in self.cameras.list()['cameras'] if not query
                or query in ' '.join(str(item[key]) for key in ('name', 'host', 'kind')).casefold()]
        wanted = {item['id'] for item in rows}
        for identifier in self.tree.get_children():
            if identifier not in wanted:
                self.tree.delete(identifier)
        for index, item in enumerate(rows):
            values = item['kind'], item['host'], 'stored safely' if item['login'] else 'none'
            if self.tree.exists(item['id']):
                self.tree.item(item['id'], text=item['name'], values=values)
                self.tree.move(item['id'], '', index)
            else:
                self.tree.insert('', index, iid=item['id'], text=item['name'], values=values)
        if selected and selected[0] in wanted:
            self.tree.selection_set(selected[0])
        self.tree.yview_moveto(position[0])
        self.tree.xview_moveto(position[1])
        from app.gui_empty import show_empty
        show_empty(self.tree, "No cameras match this filter." if query else "No cameras yet. Give one of yours a name and its address above (rtsp://… or http://…) and "
                              "press Add camera; double-click it here to watch.", self.p)

    def add(self) -> bool:
        values = {key: variable.get() if key in ('username', 'password') else variable.get().strip()
                  for key, variable in self.fields.items()}
        try:
            latitude = float(values["latitude"]) if values["latitude"] else None
            longitude = float(values["longitude"]) if values["longitude"] else None
            result = self.cameras.add(values["name"], values["url"], latitude, longitude, values["username"], values["password"])
        except ValueError as error:
            self.status.set(str(error))
            return False
        finally:
            self.fields["password"].set("")  # never left in the window
        for key in ("name", "url", "username", "latitude", "longitude"):
            self.fields[key].set("")
        self.status.set(f"Added {result['added']['name']} ({result['added']['kind']}). Double-click it to play.")
        self.refresh()
        return True

    def selected(self) -> str | None:
        selection = self.tree.selection()
        if not selection:
            self.status.set("Select a camera first.")
            return None
        return selection[0]

    def play(self) -> Any:
        camera_id = self.selected()
        if not camera_id:
            return None
        from app.gui_video_player import open_camera_window
        try:
            camera = self.cameras.play(camera_id)
        except ValueError as error:
            self.status.set(str(error))
            self.refresh()
            return None
        window = open_camera_window(self, self.gui, camera)
        self.status.set("Playing locally · nothing is recorded or sent anywhere.")
        return window

    def remove(self) -> bool:
        camera_id = self.selected()
        if not camera_id:
            return False
        result = self.cameras.remove(camera_id)
        self.status.set(f"Removed {result['name']} and its stored login.")
        self.refresh()
        return True
