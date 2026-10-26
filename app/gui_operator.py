"""The Brain 5D operator view: live node health, evidence, the 3D brain, its timeline,
replay, heat, the "How it works" tour and brain-mesh checks.

Split out of app/gui.py. HayatGUI inherits these methods, so they still run as
``gui._select_brain_node(...)`` against the window's own state and widgets.
"""
from __future__ import annotations

import threading
import time
from app.gui_resources import StringVar

import tkinter as tk
from tkinter import ttk

from app.gui_theme import DarkScrolledText
from typing import Any


class BrainOperatorMixin:
    """Builds and drives the operator pane inside the main window."""

    def _update_brain_geometry_note(self):
        state = self.brain_graph.geometry_status()
        actual, requested = state['render_detail'], state['requested_detail']
        mode = actual if actual == requested else f'{requested} → {actual}'
        scope = ('Standing waves are an illustration, not measured brain activity.'
                 if self.brain_graph.surface == 'Standing waves'
                 else 'Arrows show software contracts; detail restores after the camera settles.')
        self.brain_mesh_note.set(f"{state['triangles']:,} triangles · {mode} · {state['problem'] or scope}")

    def _operator_pane(self) -> tk.Frame:
        """Build the live brain-node/operator surface once, on demand."""
        if self._operator_frame is not None:
            return self._operator_frame

        from app.brain_mesh import BrainMesh

        frame = tk.Frame(self._centre, bg=self.BLACK)
        header = tk.Frame(frame, bg=self.BLACK)
        header.pack(fill="x", pady=(0, 7))
        from app.gui_intelligence_parts import FlowRow
        filters = FlowRow(frame, self.BLACK, gap=6, height=36)
        filters.pack(fill="x", pady=(0, 6))
        self.brain_title = tk.Label(header, text="Brain 5D", bg=self.BLACK, fg=self.WHITE,
                                    font=("Segoe UI Semibold", 12))
        self.brain_title.pack(side="left")
        self.brain_subtitle = tk.Label(header, text="Activity · history · evidence",
                                       bg=self.BLACK, fg=self.MUTED, font=("Segoe UI", 9))
        self.brain_subtitle.pack(side="left", padx=10)

        self.operator_summary = StringVar(
            master=self.root, value="Loading live node health and contracts..."
        )
        # One short status line in the header instead of a three-line strip:
        # on a 768-pixel screen the strip took the height the brain needed.
        # The full summary (truth order, authority rule, timings) is on hover.
        from app.gui_help import Tooltip

        self.operator_status_line = StringVar(master=self.root, value="Checking HAYAT's parts…")
        status = tk.Label(header, textvariable=self.operator_status_line, bg=self.BLACK, fg=self.GOLD_SOFT,
                          font=("Segoe UI", 9), anchor="w")
        status.pack(side="left", padx=(6, 0))
        tip = Tooltip(status, self.operator_summary.get(), type(self))

        def _short(*_args: Any) -> None:
            full = self.operator_summary.get()
            first = full.split("\n", 1)[0].replace("  ·  ", " · ")
            self.operator_status_line.set(first if len(first) <= 70 else first[:67] + "…")
            tip.text = full
        self.operator_summary.trace_add("write", _short)
        _short()

        # The same nodes in 3D (health by colour, running work as pulses) or
        # as a list of cards with every result in full. One switch, one space:
        # both at once left neither enough room in a normal window.
        from app.gui_brain import BrainGraph, describe

        self._describe_brain_node = describe
        self.operator_layout = ttk.Button(filters, text="List view", style="Hayat.TButton",
                                          command=self._toggle_operator_layout)
        # A guided walk along one request's path, one contract at a time.
        self.brain_tour_button = ttk.Button(filters, text="How it works", width=0, style="HayatAccent.TButton",
                                            command=self._toggle_brain_tour)
        self._brain_tour_step: int | None = None
        self.brain_dimensions = {}
        for mode in ("3D", "5D"):
            self.brain_dimensions[mode] = filters.add(ttk.Button(filters, text=mode,
                style="HayatChosen.TButton" if mode == "5D" else "Hayat.TButton",
                command=lambda value=mode: self._set_brain_dimensions(value)))
        self.brain_layer = StringVar(self.root, "All layers")
        layer_picker = filters.add(ttk.Combobox(filters, textvariable=self.brain_layer, state="readonly", width=18,
            values=["All layers", *sorted({node.layer for node in BrainMesh.NODES})]))
        layer_picker.bind("<<ComboboxSelected>>", lambda _e: self.brain_graph.set_layer(self.brain_layer.get()))
        filters.add(tk.Label(filters, text="Dependency depth", bg=self.BLACK, fg=self.MUTED, font=("Segoe UI", 9)))
        self.brain_hops = StringVar(self.root, "1 hop")
        hops = filters.add(ttk.Combobox(filters, textvariable=self.brain_hops, state="readonly", width=8,
                                     values=["1 hop", "2 hops", "3 hops"]))
        hops.bind("<<ComboboxSelected>>", lambda _e: self._set_brain_hops())
        # A wrapping action row keeps the tour and List view reachable when
        # the inspector is narrow instead of packing them outside the header.
        filters.add(self.operator_layout)
        filters.add(self.brain_tour_button)
        for title, action in (("Refresh", self._refresh_operator), ("Self Check", self._run_self_check),
                              ("Benchmark", self._benchmark_brain_mesh)):
            filters.add(ttk.Button(filters, text=title, style="Hayat.TButton", command=action))
        self.brain_appearance_button = filters.add(ttk.Button(filters, text="Appearance",
            style="Hayat.TButton", command=self._toggle_brain_appearance))
        self.brain_inspector_button = filters.add(ttk.Button(filters, text="Hide details",
            style="Hayat.TButton", command=self._toggle_brain_inspector))
        self.brain_history_button = filters.add(ttk.Button(filters, text="History visible",
            style="Hayat.TButton", command=self._toggle_brain_history_view))
        self._brain_history_focus = False
        self._brain_history_layout = None
        appearance = tk.Frame(frame, bg=self.BLACK)
        self._brain_appearance = appearance
        mesh_controls = FlowRow(appearance, self.BLACK, gap=6, height=32)
        mesh_controls.pack(fill="x", pady=(0, 4))
        mesh_controls.add(tk.Label(mesh_controls, text="Model", bg=self.BLACK, fg=self.MUTED,
                                  font=("Segoe UI", 9)))
        self.brain_geometry = StringVar(self.root, "Sketchfab brain")
        self.brain_geometry_picker = mesh_controls.add(ttk.Combobox(
            mesh_controls, textvariable=self.brain_geometry, state="readonly", width=17,
            values=BrainGraph.GEOMETRIES))
        def update_geometry(_event=None):
            self.brain_graph.set_geometry(self.brain_geometry.get())
        self.brain_geometry_picker.bind("<<ComboboxSelected>>", update_geometry)
        def model_credits():
            from tkinter import messagebox
            from app.brain_asset import MODEL_CREDITS
            messagebox.showinfo("3D model credits", MODEL_CREDITS, parent=self.root)
        mesh_controls.add(ttk.Button(mesh_controls, text="Model credits", command=model_credits))
        mesh_controls.add(ttk.Button(mesh_controls, text="Reset 3D view", command=lambda: self.brain_graph.reset()))
        mesh_controls.add(tk.Label(mesh_controls, text="Surface", bg=self.BLACK, fg=self.MUTED,
                                  font=("Segoe UI", 9)))
        self.brain_surface = StringVar(self.root, "Folded")
        self.brain_mesh_detail = StringVar(self.root, "Normal")
        self.brain_wave_modes = StringVar(self.root, "2 / 3")
        self.brain_surface_picker = mesh_controls.add(ttk.Combobox(
            mesh_controls, textvariable=self.brain_surface, state="readonly", width=16,
            values=BrainGraph.SURFACES))
        mesh_controls.add(tk.Label(mesh_controls, text="Detail", bg=self.BLACK, fg=self.MUTED,
                                  font=("Segoe UI", 9)))
        self.brain_detail_picker = mesh_controls.add(ttk.Combobox(
            mesh_controls, textvariable=self.brain_mesh_detail, state="readonly", width=8,
            values=tuple(BrainGraph.DETAILS)))
        self.brain_wave_picker = mesh_controls.add(ttk.Combobox(
            mesh_controls, textvariable=self.brain_wave_modes, state="disabled", width=7,
            values=("2 / 3", "3 / 5", "4 / 7")))
        self.brain_mesh_note = StringVar(self.root, "Geometry guides the view; arrows show actual subsystem contracts.")
        note = tk.Label(appearance, textvariable=self.brain_mesh_note, bg=self.BLACK, fg=self.MUTED,
                        font=("Segoe UI", 8), anchor="w", justify="left", wraplength=600)
        note.pack(fill="x", pady=(0, 6))
        appearance.bind("<Configure>", lambda event: note.configure(wraplength=max(160, event.width - 12)))

        def update_mesh(_event=None):
            waves = self.brain_surface.get() == "Standing waves"
            self.brain_wave_picker.configure(state="readonly" if waves else "disabled")
            modes = tuple(int(value.strip()) for value in self.brain_wave_modes.get().split("/"))
            self.brain_graph.set_surface(self.brain_surface.get(), detail=self.brain_mesh_detail.get(), modes=modes)
            self.brain_mesh_note.set("Cymatics illustration; does not change model weights or measure neurons." if waves
                                     else "Geometry guides the view; arrows show actual subsystem contracts.")
        for control in (self.brain_surface_picker, self.brain_detail_picker, self.brain_wave_picker):
            control.bind("<<ComboboxSelected>>", update_mesh)
        brain_row = tk.Frame(frame, bg=self.BLACK)
        self._brain_row = brain_row
        brain_row.pack(fill="both", expand=True)
        self.brain_node_detail = StringVar(
            master=self.root,
            value="3D shows HAYAT's parts. The fourth dimension is time; the fifth is evidence.\n\n"
                  "Outer rings: solid green = checked; dotted gold = evidence gaps; a faint grey hairline = not checked yet.\n\n"
                  "Drag the brain to rotate. Click a part to inspect it; double-click to open it.\n\n"
                  "Drag the timeline to inspect past activity. Replay animates the recorded events. Live returns to now.")
        detail_side = tk.Frame(brain_row, bg=self.PANEL)
        self._brain_inspector = detail_side
        self._brain_inspector_open = True
        self._brain_compact = False
        self._brain_layout_signature = None
        detail_side.pack(side="right", fill="y", padx=(7, 0))
        self.brain_node_picker = ttk.Combobox(detail_side, state="readonly", width=28,
                                              values=[node.label for node in BrainMesh.NODES])
        self.brain_node_picker.set("Choose a part to inspect")
        self.brain_node_picker.pack(fill="x", padx=8, pady=8)
        def pick_node(event=None):
            index = self.brain_node_picker.current()
            if index >= 0:
                node = BrainMesh.NODES[index]
                if node.node_id not in dict(self.brain_graph.VIEWS):
                    self.brain_layer.set("All layers")
                    self.brain_graph.set_layer("All layers")
                self.brain_graph.select(node.node_id)
                self.brain_graph.face(node.node_id)
                self._select_brain_node(node.node_id)
        self.brain_node_picker.bind("<<ComboboxSelected>>", pick_node)
        focus_controls = tk.Frame(detail_side, bg=self.PANEL)
        focus_controls.pack(fill="x", padx=8, pady=(0, 6))
        self.brain_focus_button = ttk.Button(focus_controls, text="Focus links", width=0, style="Hayat.TButton",
                                             command=self._focus_brain_links)
        self.brain_focus_button.pack(side="left")
        ttk.Button(focus_controls, text="Reset view", width=0, style="Hayat.TButton",
                   command=lambda: self.brain_graph.reset()).pack(side="right")
        node_detail = DarkScrolledText(detail_side, bg=self.PANEL,
                               fg=self.WHITE, font=("Segoe UI", 9), wrap="word",
                               padx=12, pady=10, width=30, height=8, relief="flat")
        node_detail.pack(side="top", fill="both", expand=True)
        self.brain_detail_text = node_detail
        def update_node_detail(*_):
            node_detail.configure(state="normal")
            node_detail.delete("1.0", "end")
            node_detail.insert("end", self.brain_node_detail.get())
            node_detail.see("1.0")
            node_detail.configure(state="disabled")
        self.brain_node_detail.trace_add("write", update_node_detail)
        update_node_detail()
        self.brain_tour_controls = tk.Frame(detail_side, bg=self.PANEL, padx=10, pady=8)
        self.brain_tour_back = ttk.Button(self.brain_tour_controls, text="◀ Back", width=0, style="Hayat.TButton",
                                          command=lambda: self._brain_tour(-1))
        self.brain_tour_back.pack(side="left")
        self.brain_tour_next = ttk.Button(self.brain_tour_controls, text="Next ▶", width=0,
                                          style="HayatAccent.TButton", command=lambda: self._brain_tour(1))
        self.brain_tour_next.pack(side="right")
        brain_column = tk.Frame(brain_row, bg=self.BLACK)
        self._brain_column = brain_column
        brain_column.pack(side="left", fill="both", expand=True)
        brain_row.bind("<Configure>", lambda _event: self._layout_brain_workspace())

        # The fourth dimension: time. The event log already records every
        # turn, tool, research run and authority decision; the timeline turns
        # it back into which pathways were active, and the playhead shows the
        # 3D brain as it was at any moment in the window.
        from app.gui_brain import BrainTimeline

        controls = tk.Frame(brain_column, bg=self.BLACK)
        self._brain_time_controls = controls
        controls.pack(side="bottom", fill="x", pady=(5, 0))
        range_controls = FlowRow(controls, self.BLACK, gap=4, height=36)
        range_controls.pack(fill="x", pady=(0, 4))
        playback_controls = FlowRow(controls, self.BLACK, gap=4, height=36)
        playback_controls.pack(fill="x")
        self.brain_timeline = BrainTimeline(brain_column, type(self), self._brain_moment,
                                            on_replay=self._brain_replay_state)
        self.brain_timeline.pack(side="bottom", fill="x", pady=(6, 0))
        self._brain_window_seconds = 3600
        self._brain_history = None
        self._brain_history_serial = 0
        self._brain_history_loading_generation = None
        self._brain_history_loading = False
        self.brain_range_buttons: dict[int, ttk.Button] = {}
        for seconds, title in ((900, "15 min"), (3600, "1 hour"), (21600, "6 hours"), (86400, "24 hours")):
            button = ttk.Button(range_controls, text=title, width=0, style="Hayat.TButton",
                                command=lambda value=seconds: self._set_brain_window(value))
            button.pack(side="left", padx=(0, 4))
            self.brain_range_buttons[seconds] = button
        ttk.Button(playback_controls, text="Live", width=0, style="Hayat.TButton",
                   command=lambda: (self.brain_timeline.stop_replay(), self.brain_timeline.seek(None))
                   ).pack(side="right")
        self.brain_replay_button = ttk.Button(playback_controls, text="Replay", width=0, style="Hayat.TButton",
                                              command=self._replay_brain)
        self.brain_replay_button.pack(side="right", padx=(0, 4))
        self.brain_speed_button = ttk.Button(playback_controls, text="Normal", width=0, style="Hayat.TButton",
                                             command=lambda: self.brain_speed_button.configure(
                                                 text=self.brain_timeline.cycle_speed()))
        self.brain_speed_button.pack(side="right", padx=(0, 4))
        self._brain_heat_on = True
        self.brain_heat_button = ttk.Button(playback_controls, text="Heat on", width=0, style="Hayat.TButton",
                                            command=self._toggle_brain_heat)
        self.brain_heat_button.pack(side="left", padx=(0, 4))
        self.brain_evidence_button = ttk.Button(range_controls, text="Evidence on", width=0,
                                                style="Hayat.TButton", command=self._toggle_brain_evidence)
        self.brain_evidence_button.pack(side="right")
        self.brain_motion_button = ttk.Button(playback_controls, text="Pause 3D", width=0,
                                               style="Hayat.TButton",
                                               command=lambda: self.brain_graph.set_motion(not self.brain_graph.motion))
        self.brain_motion_button.pack(side="left", padx=(0, 4))
        ttk.Button(playback_controls, text="Fit", width=0, style="Hayat.TButton",
                   command=lambda: self.brain_timeline.fit()).pack(side="right", padx=(0, 4))
        for row in (range_controls, playback_controls):
            for widget in row.winfo_children():
                widget.pack_forget()
                row.add(widget)
        self.brain_graph = BrainGraph(brain_column, type(self), self._select_brain_node, height=200,
                                      on_drill=self._drill_brain_node, on_motion=self._brain_motion_state)
        self.brain_graph.set_motion(self.depth_nav.motion)
        # Press P on a subsystem to pin it, then P on another to light the contract path between them.
        self.brain_graph.on_path_status = lambda message: self.brain_node_detail.set(message)
        self.brain_graph.bind('<<GeometryChanged>>', lambda _event: self._update_brain_geometry_note())
        self._update_brain_geometry_note()
        # Esc ends a tour; with no tour running it keeps its usual meaning (stop).
        self.brain_graph.bind("<Escape>", lambda _event: (self._end_brain_tour(), "break")[1]
                              if self._brain_tour_step is not None else None)
        self.brain_graph.pack(side="top", fill="both", expand=True)
        from app.gui_3d_controls import camera_menu
        mesh_controls.add(camera_menu(mesh_controls, self.brain_graph))
        self._set_brain_window(3600)
        self._schedule(10_000, self._brain_history_tick)

        cards = tk.Frame(frame, bg=self.BLACK)
        self._operator_cards = cards
        canvas = tk.Canvas(cards, bg=self.BLACK, highlightthickness=0, bd=0)
        self._operator_canvas = canvas
        scrollbar = ttk.Scrollbar(cards, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        grid = tk.Frame(canvas, bg=self.BLACK)
        self._operator_grid = grid
        window = canvas.create_window((0, 0), window=grid, anchor="nw")
        grid.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: (
            canvas.itemconfigure(window, width=event.width), self._layout_operator_cards(event.width)))
        def scroll_cards(event: tk.Event) -> str:
            if self._operator_cards.winfo_ismapped():
                canvas.yview_scroll(int(-event.delta / 120), "units")
            return "break"
        canvas.bind("<MouseWheel>", scroll_cards)

        self._operator_card_columns = 0
        for index, node in enumerate(BrainMesh.NODES):
            card = tk.Frame(grid, bg=self.PANEL, padx=11, pady=9,
                            highlightthickness=1, highlightbackground=self.LINE,
                            relief="raised", bd=2)
            title = tk.Frame(card, bg=self.PANEL)
            title.pack(fill="x")
            dot = tk.Label(title, text="●", bg=self.PANEL, fg=self.MUTED,
                           font=("Segoe UI", 9))
            dot.pack(side="left", padx=(0, 6))
            tk.Label(title, text=node.label, bg=self.PANEL, fg=self.WHITE,
                     font=("Segoe UI Semibold", 9)).pack(side="left")
            tk.Label(title, text=node.layer.upper(), bg=self.PANEL, fg=self.ROYAL_SOFT,
                     font=("Segoe UI", 7)).pack(side="right")
            state = StringVar(master=self.root, value="waiting")
            detail = StringVar(master=self.root, value=node.responsibility)
            tk.Label(card, textvariable=state, bg=self.PANEL, fg=self.MUTED,
                     font=("Cascadia Mono", 8), anchor="w").pack(fill="x", pady=(5, 1))
            detail_label = tk.Label(card, textvariable=detail, bg=self.PANEL, fg=self.MUTED,
                                    font=("Segoe UI", 8), anchor="w", justify="left",
                                    wraplength=220)
            detail_label.pack(fill="x")
            inspect = ttk.Button(card, text="Inspect", width=0, style="Hayat.TButton",
                                 command=lambda key=node.node_id: self._inspect_operator_card(key))
            inspect.pack(anchor="e", pady=(5, 0))
            pending = [card]
            while pending:
                widget = pending.pop()
                widget.bind("<MouseWheel>", scroll_cards)
                pending.extend(widget.winfo_children())
            self.operator_nodes[node.node_id] = {
                "card": card, "title": title, "dot": dot, "state": state,
                "detail": detail, "detail_label": detail_label,
                "inspect": inspect, "critical": node.critical,
            }
        self._layout_operator_cards(900)

        self._operator_frame = frame
        return frame

    def _toggle_brain_appearance(self) -> None:
        opened = bool(self._brain_appearance.winfo_manager())
        if opened:
            self._brain_appearance.pack_forget()
        else:
            before = self._brain_row if self._brain_row.winfo_manager() else self._operator_cards
            self._brain_appearance.pack(fill="x", before=before)
        self.brain_appearance_button.configure(text="Hide appearance" if not opened else "Appearance")

    def _toggle_brain_inspector(self) -> None:
        self._brain_inspector_open = not self._brain_inspector_open
        self._layout_brain_workspace()

    def _layout_brain_workspace(self) -> None:
        """At narrow widths, give graph and inspector their own full-width view."""
        self._layout_brain_history()
        compact = self._brain_row.winfo_width() < 1000
        if compact != self._brain_compact:
            self._brain_compact = compact
            self._brain_inspector_open = not compact
        signature = (compact, self._brain_inspector_open)
        if signature == self._brain_layout_signature:
            return
        self._brain_layout_signature = signature
        self._brain_column.pack_forget()
        self._brain_inspector.pack_forget()
        if self._brain_inspector_open:
            self._brain_inspector.pack(side="right", fill="both" if compact else "y",
                                       expand=compact, padx=0 if compact else (7, 0))
        if not compact or not self._brain_inspector_open:
            self._brain_column.pack(side="left", fill="both", expand=True)
        self.brain_inspector_button.configure(text=("Show graph" if compact else "Hide details")
                                              if self._brain_inspector_open else "Show details")

    def _layout_operator_cards(self, width: int) -> None:
        """Keep cards readable as the operator pane gets narrower."""
        columns = 1 if width < 500 else 2 if width < 850 else 3
        grid = self._operator_grid
        if columns != self._operator_card_columns:
            for column in range(3):
                grid.grid_columnconfigure(column, weight=1 if column < columns else 0)
            for index, item in enumerate(self.operator_nodes.values()):
                item["card"].grid(row=index // columns, column=index % columns,
                                  sticky="nsew", padx=3, pady=3)
            self._operator_card_columns = columns
        wrap = max(160, width // columns - 48)
        for item in self.operator_nodes.values():
            item["detail_label"].configure(wraplength=wrap)

    def _inspect_operator_card(self, node_id: str) -> None:
        """Open a list card in the 3D inspector."""
        self._toggle_operator_layout()
        self.brain_graph.select(node_id)
        self._select_brain_node(node_id)

    def _brain_motion_state(self, enabled: bool) -> None:
        self.brain_motion_button.configure(text="Pause 3D" if enabled else "Move 3D")

    def _toggle_brain_history_view(self):
        self._brain_history_focus = not self._brain_history_focus
        if self._brain_history_focus and self._brain_row.winfo_height() < 260 and self._brain_appearance.winfo_manager():
            self._toggle_brain_appearance()
        self._layout_brain_history()

    def _layout_brain_history(self):
        if not hasattr(self, 'brain_graph'):
            return
        height = self._brain_row.winfo_height()
        short = 1 < height < 400  # Unmapped widgets have not received their real geometry yet.
        enabled = self.brain_graph.view_mode == '5D'
        history_only = enabled and short and self._brain_history_focus
        signature = (short, enabled, history_only)
        if signature == self._brain_history_layout:
            return
        self._brain_history_layout = signature
        self.brain_graph.pack_forget()
        self.brain_timeline.pack_forget()
        self._brain_time_controls.pack_forget()
        if enabled and (not short or history_only):
            self._brain_time_controls.pack(side='bottom', fill='x', pady=(5, 0))
            self.brain_timeline.pack(side='bottom', fill='x', pady=(6, 0))
        if not history_only:
            self.brain_graph.pack(side='top', fill='both', expand=True)
        self.brain_history_button.configure(text='Show 3D' if history_only else 'Show history' if short else 'History visible',
                                            state='normal' if enabled and short else 'disabled')

    def _set_brain_hops(self) -> None:
        self.brain_graph.connection_hops = int(self.brain_hops.get()[0])
        if not self.brain_graph.focus_connections:
            self._focus_brain_links()
        self.brain_graph.redraw()

    def _set_brain_dimensions(self, mode: str) -> None:
        if mode not in {"3D", "5D"}:
            raise ValueError("Choose 3D or 5D")
        graph = self.brain_graph
        graph.view_mode = mode
        self.brain_timeline.stop_replay()
        self.brain_timeline.seek(None)
        graph.evidence_on = mode == "5D"
        self.brain_title.configure(text=f"Brain {mode}")
        self.brain_subtitle.configure(text="Activity · history · evidence" if mode == "5D"
                                      else "Live architecture · full-size brain, no timeline")
        self.brain_evidence_button.configure(text="Evidence on" if graph.evidence_on else "Evidence off")
        if mode == "3D":
            self.brain_timeline.pack_forget()
            self._brain_time_controls.pack_forget()
            graph.set_heat(None)
        else:
            self._brain_time_controls.pack(side="bottom", fill="x", pady=(5, 0))
            self.brain_timeline.pack(side="bottom", fill="x", pady=(6, 0))
            self._paint_brain_heat()
        for value, button in self.brain_dimensions.items():
            button.configure(style="HayatChosen.TButton" if value == mode else "Hayat.TButton")
        self._layout_brain_history()
        graph.redraw()

    def _set_brain_window(self, seconds: int) -> None:
        self._brain_history_serial += 1
        self._brain_window_seconds = seconds
        for value, button in self.brain_range_buttons.items():
            button.configure(style="HayatChosen.TButton" if value == seconds else "Hayat.TButton")
        self.brain_timeline.stop_replay()
        self.brain_timeline.moment = None
        self.brain_timeline.view = None
        self.brain_graph.show_moment(None)
        self._load_brain_history()

    def _load_brain_history(self) -> None:
        """Rebuild the window from the event log, off the UI thread."""
        if self._brain_history_loading or self._closed:
            return
        events = getattr(self.runtime, "events", None)
        path = getattr(events, "path", None)
        if path is None:
            return
        self._brain_history_loading = True
        self._brain_history_serial += 1
        generation = self._brain_history_serial
        self._brain_history_loading_generation = generation
        seconds = self._brain_window_seconds

        def work() -> None:
            from app.brain_history import load

            try:
                now = time.time()
                history = load(path, now - seconds, now)
            except Exception:
                history = None
            self._post(self._show_brain_history, history, generation, seconds)

        threading.Thread(target=work, name="hayat-brain-history", daemon=True).start()

    def _show_brain_history(self, history: Any, generation=None, window=None) -> None:
        if generation is not None and generation != self._brain_history_serial:
            if self._brain_history_loading_generation == generation:
                self._brain_history_loading = False
                if window != self._brain_window_seconds:
                    self._load_brain_history()
            return
        if generation is None:
            self._brain_history_serial += 1
        self._brain_history_loading = False
        if history is None:
            return
        self._brain_history = history
        self._paint_brain_heat()
        # A moment being examined or replayed stays put; only "now" moves on.
        if self.brain_timeline.moment is None and self.brain_timeline._replay is None:
            self.brain_timeline.set_history(history)
        elif self.brain_timeline.history is None:
            self.brain_timeline.set_history(history)

    def _brain_history_tick(self) -> None:
        if self.current_view == "operator":
            self._load_brain_history()
        self._schedule(10_000, self._brain_history_tick)

    def _replay_brain(self) -> None:
        if not self.brain_graph.motion:
            # Reduced motion: no animated replay. Step with the arrow keys.
            self.brain_node_detail.set("Motion is paused, so replay is off. Drag the timeline or use "
                                       "the arrow keys on it to move through time.")
            return
        # Replay, pause, resume: one button, and Space on the timeline.
        self.brain_timeline.toggle_replay()

    def _brain_replay_state(self, playing: bool) -> None:
        paused = not playing and self.brain_timeline._replay_at is not None
        self.brain_replay_button.configure(text="Pause" if playing else "Resume" if paused else "Replay")

    def _brain_moment(self, moment: float | None) -> None:
        """Show the 3D brain, and the detail panel, as they were at a moment."""
        history = self._brain_history
        if moment is None or history is None:
            self.brain_graph.show_moment(None)
            selected = getattr(self, "_brain_selected", None)
            if selected:
                self._select_brain_node(selected, activate=False)
            return
        begin, finish = self.brain_timeline._view()
        span = max(1.0, finish - begin)
        marks = history.marks_near(moment, span / 120)
        # The trail: what was used in the moments just before this one.
        trail = max(5.0, span / 40)
        current = history.flows_at(moment)
        echoes = [flow for flow in history.flows_between(moment - trail, moment) if flow not in current]
        self.brain_graph.show_moment(moment, current, marks, echoes)
        lines = [time.strftime("At %H:%M:%S", time.localtime(moment))]
        doing = list(dict.fromkeys(history.labels_at(moment)))
        lines.append("Doing: " + "; ".join(doing[:4]) if doing else "Idle at this moment.")
        lines.extend(("✗ " if mark.kind == "failure" else "! ") + mark.label for mark in marks[:4])
        self.brain_node_detail.set("\n".join(lines))

    def _brain_tour(self, direction: int) -> None:
        """Step the "How it works" tour; stepping past either end finishes it."""
        from app.gui_brain import TOUR, tour_text

        step = -1 if self._brain_tour_step is None else self._brain_tour_step
        step += direction
        if not 0 <= step < len(TOUR):
            self._end_brain_tour()
            return
        self._brain_tour_step = step
        self._brain_inspector_open = True
        self._layout_brain_workspace()
        source, target = TOUR[step]
        self.brain_timeline.stop_replay()
        self.brain_graph.tour_edge = (source, target)
        self.brain_layer.set("All layers")
        self.brain_graph.set_layer("All layers")
        self.brain_graph.select(target)      # turns the brain to face where the request goes next
        self.brain_graph.redraw()
        self.brain_node_detail.set(tour_text(step))
        self.brain_tour_next.configure(text="Finish" if step == len(TOUR) - 1 else "Next ▶")
        self.brain_tour_back.state(["disabled"] if step == 0 else ["!disabled"])
        self.brain_tour_button.configure(text="End tour")
        if not self.brain_tour_controls.winfo_manager():
            self.brain_tour_controls.pack(side="bottom", fill="x")
        self.brain_graph.focus_set()

    def _toggle_brain_tour(self) -> None:
        if self._brain_tour_step is None:
            self._brain_tour(1)
        else:
            self._end_brain_tour()

    def _end_brain_tour(self) -> None:
        self._brain_tour_step = None
        self.brain_graph.tour_edge = None
        self.brain_graph.redraw()
        self.brain_tour_button.configure(text="How it works")
        self.brain_tour_controls.pack_forget()
        self.brain_node_detail.set("That is the path every request takes. Click any part to inspect it, "
                                   "double-click to open it, or drag the timeline to see when each part was busy.")

    def _toggle_operator_layout(self) -> None:
        listing = self._brain_row.winfo_manager() != ""
        if listing:
            self._brain_row.pack_forget()
            self._operator_cards.pack(fill="both", expand=True)
        else:
            self._operator_cards.pack_forget()
            self._brain_row.pack(fill="both", expand=True)
        self.operator_layout.configure(text="3D view" if listing else "List view")

    def _focus_brain_links(self) -> None:
        self.brain_graph.focus_connections = not self.brain_graph.focus_connections
        self.brain_focus_button.configure(text="Show all links" if self.brain_graph.focus_connections else "Focus links")
        if not self.brain_graph.active:
            key = self.brain_graph.VIEWS[0][0]
            self.brain_graph.select(key)
            self._select_brain_node(key)
        self.brain_graph.redraw()

    #: Double-clicking a brain part goes to where that part actually lives.
    _BRAIN_DESTINATIONS = {
        "interface": ("chat", "the conversation"),
        "intent": ("chat", "the conversation"),
        "context": ("chat", "the conversation"),
        "router": ("studio:models", "the model router"),
        "helpers": ("teams", "the team workspace: limits, teams and every worker"),
        "planner": ("view:task_graph", "the plan editor"),
        "tool_judgment": ("view:toolbox", "the toolbox"),
        "authority": ("security", "the security window"),
        "tools": ("view:toolbox", "the toolbox"),
        "verifier": ("selfcheck", "a live self-check"),
        "memory": ("memory", "memory"),
        "dna": ("memory", "memory"),
        "research": ("searches", "searches"),
        "learning": ("learning", "the learning view"),
        "network": ("remote", "secure access & memory"),
        "perception": ("camera", "the camera eye"),
        "observability": ("doctor", "the project doctor"),
        "cortex": ("view:invention", "executive goals and experiments"),
        "dual_brain": ("view:invention", "proposer and critic rounds"),
        "invention": ("view:invention", "the invention workbench"),
        "inquiry": ("view:invention", "executive goals, self-questioning and review"),
    }

    def _drill_brain_node(self, node_id: str) -> None:
        """Open the part a brain node stands for. Falls back to inspecting it."""
        destination, label = self._BRAIN_DESTINATIONS.get(node_id, ("", ""))
        try:
            if destination.startswith("view:"):
                self.show_view(destination.split(":", 1)[1])
            elif destination.startswith("studio:"):
                self._open_control_studio(destination.split(":", 1)[1])
            elif destination == "chat":
                self.show_view("chat")
            elif destination == "security":
                self._open_security()
            elif destination == "memory":
                self._show_memory()
            elif destination == "searches":
                self._open_searches()
            elif destination == "teams":
                self._open_team_workspace()
            elif destination == "learning":
                self._show_learning()
            elif destination == "remote":
                self._show_remote()
            elif destination == "camera":
                self.open_camera_view()
            elif destination == "doctor":
                self._show_doctor()
            elif destination == "selfcheck":
                self._run_self_check()
            else:
                self._select_brain_node(node_id)
                return
        except Exception as exc:
            self._record_failure(f"Open {node_id} from Brain 5D", exc)
            return
        self.console.write("operator", f"Opened {label} from the brain.")

    def _select_brain_node(self, node_id: str, *, activate: bool = True) -> None:
        if activate:
            self._brain_inspector_open = True
            self._layout_brain_workspace()
        if activate and getattr(self, "_brain_tour_step", None) is not None:
            self._end_brain_tour()          # a click on a part explains that part instead
        self._brain_selected = node_id
        from app.brain_mesh import BrainMesh
        self.brain_node_picker.set(next(node.label for node in BrainMesh.NODES if node.node_id == node_id))
        checks = getattr(self.brain_graph, "checks", {})
        text = self._describe_brain_node(node_id, checks.get(node_id))
        text += "\n\n" + self.brain_graph.evidence_detail(node_id)
        # Its own history: the timeline keeps this part's work bright. A
        # second click on the same part shows everything again.
        from app.gui_brain import FLOWS
        from app.brain_mesh import BrainMesh

        timeline = self.brain_timeline
        label = next(node.label for node in BrainMesh.NODES if node.node_id == node_id)
        flows = {flow for flow, members in FLOWS.items() if node_id in members}
        if activate:
            if timeline.focus_node == node_id:
                timeline.set_focus(None)
            else:
                timeline.set_focus(node_id, label, flows)
        if timeline.focus_node == node_id:
            history = self._brain_history
            if history is not None:
                seconds = history.node_seconds(history.start, history.end, FLOWS).get(node_id, 0.0)
                window = self._range_words(history.end - history.start)
                busy = (f"{seconds / 60:.1f} min" if seconds >= 90 else f"{seconds:.0f} s")
                text += (f"\n\nBusy for about {busy} in the last {window}. The timeline now shows "
                         f"{label}'s activity; click {label} again to show everything."
                         if flows or seconds else
                         f"\n\n{label} has no recorded activity lane; its work shows through the parts it connects to.")
        where = self._BRAIN_DESTINATIONS.get(node_id, ("", ""))[1]
        if where:
            text += f"\n\nDouble-click to open {where}."
        self.brain_node_detail.set(text)

    @staticmethod
    def _range_words(seconds: float) -> str:
        return f"{round(seconds / 3600)} hour{'s' if round(seconds / 3600) != 1 else ''}" if seconds >= 3600 \
            else f"{round(seconds / 60)} minutes"

    def _paint_brain_heat(self) -> None:
        """Glow each part by how busy it was over the loaded window."""
        history = self._brain_history
        if history is None or not getattr(self, "_brain_heat_on", True) or self.brain_graph.view_mode == "3D":
            self.brain_graph.set_heat(None)
            return
        from app.gui_brain import FLOWS

        self.brain_graph.set_heat(history.node_seconds(history.start, history.end, FLOWS))

    def _toggle_brain_heat(self) -> None:
        self._brain_heat_on = not getattr(self, "_brain_heat_on", True)
        self.brain_heat_button.configure(text="Heat on" if self._brain_heat_on else "Heat off")
        self._paint_brain_heat()

    def _toggle_brain_evidence(self) -> None:
        self.brain_graph.evidence_on = not self.brain_graph.evidence_on
        self.brain_evidence_button.configure(text="Evidence on" if self.brain_graph.evidence_on else "Evidence off")
        self.brain_graph.redraw()

    def _show_operator_pane(self, showing: bool) -> None:
        if not showing and self._operator_frame is None:
            return
        pane = self._operator_pane()
        if showing:
            self.chat.pack_forget()
            pane.pack(fill="both", expand=True, pady=(2, 8))
        else:
            pane.pack_forget()
            if not self.chat.winfo_ismapped() and self.current_view not in {"toolbox", "operator"}:
                self.chat.pack(fill="both", expand=True, pady=(2, 8))

    def _paint_operator(self, report: dict[str, Any], benchmark: dict[str, Any] | None = None) -> None:
        if self._closed or self._operator_frame is None:
            return
        nodes = report.get("independent", {}).get("nodes", {})
        self.brain_graph.set_checks(nodes)
        self.brain_graph.set_evidence(report.get("evidence_layer", {}))
        selected = getattr(self, "_brain_selected", None)
        if selected and getattr(self, "_brain_tour_step", None) is None:
            self._select_brain_node(selected, activate=False)
        for node_id, widgets in self.operator_nodes.items():
            item = nodes.get(node_id, {})
            passed = bool(item.get("passed"))
            colour = self.OLIVE if passed else self.ALERT if widgets["critical"] else self.GOLD
            widgets["dot"].configure(fg=colour)
            widgets["state"].set(
                f"{'READY' if passed else 'CHECK'}  {float(item.get('latency_ms', 0)):.2f} ms"
            )
            widgets["detail"].set(str(item.get("detail") or "No live result")[:150])
        team = report.get("team", {})
        first = (
            f"MESH {'READY' if report.get('passed') else 'ATTENTION'}  ·  "
            f"{report.get('nodes', len(nodes))} nodes  ·  {report.get('edges', 0)} contracts  ·  "
            f"team {'PASS' if team.get('passed') else 'FAIL'}  ·  {float(report.get('elapsed_ms', 0)):.2f} ms"
        )
        second = "Truth: live observation > sourced knowledge > durable memory > model inference"
        third = "Authority: deterministic kernel · Models cannot approve tools · Remote control requires an authorized connector"
        if benchmark:
            timings = benchmark.get("benchmarks", {})
            third = "Benchmark: " + " · ".join(
                f"{name.replace('_', ' ')} {row.get('mean_ms', 0):.2f} ms"
                for name, row in timings.items()
            )
        self.operator_summary.set("\n".join((first, second, third)))

    def _run_mesh_task(self, title: str, work, *, benchmark: bool = False) -> None:
        if self.current_view != "operator":
            self._operator_suppress_auto_refresh = True
            try:
                self.show_view("operator")
            finally:
                self._operator_suppress_auto_refresh = False
        self.operator_summary.set(f"{title} running...")

        def run() -> None:
            try:
                result = work()
                report = self.runtime.brain_mesh.self_check(deep=False) if benchmark else result
                self._post(self._paint_operator, report, result if benchmark else None)
                # _post passes positional arguments only; a keyword here once
                # turned every successful check into a recorded failure.
                self._post(lambda: self.console.write("operator", f"{title} completed", level="notable"))
            except Exception as exc:
                self._post(self.operator_summary.set,
                                f"{title} failed: {type(exc).__name__}: {exc}")
                self._record_failure(title, exc)

        threading.Thread(target=run, name="hayat-operator-check", daemon=True).start()

    def _refresh_operator(self) -> None:
        self._run_mesh_task("Operator refresh", lambda: self.runtime.brain_mesh.self_check(deep=False))

    def _run_self_check(self) -> None:
        self._run_mesh_task("Independent + team self-check",
                            lambda: self.runtime.brain_mesh.self_check(deep=True))

    def _benchmark_brain_mesh(self) -> None:
        self._run_mesh_task("Brain mesh benchmark",
                            lambda: self.runtime.brain_mesh.benchmark(iterations=20),
                            benchmark=True)

    def _show_operator(self) -> None:
        self.show_view("operator")
