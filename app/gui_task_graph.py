"""Keyboard-accessible dependency editor with a visual graph and saved drafts."""
import json
from app.gui_resources import StringVar

import tkinter as tk
from tkinter import ttk

from app.task_graph import GraphStore, ordered_nodes, plan_text


class TaskGraphEditor(tk.Frame):
    def __init__(self, parent, palette, runtime, compose):
        super().__init__(parent, bg=palette.BLACK)
        self.runtime, self.palette, self.compose = runtime, palette, compose
        self.store = GraphStore(runtime.memory.path.parent / "workspace.db")
        self.nodes = []
        self.status = StringVar(self, "Add steps, then choose which steps each one depends on.")
        self.name = StringVar(self, "My plan")
        self.dependencies = StringVar(self)
        top = ttk.Frame(self)
        top.pack(fill="x", pady=8)
        self.saved = ttk.Combobox(top, textvariable=self.name, width=22)
        self.saved.pack(side="left")
        for label, action in (("New", self.new), ("Load", self.load), ("Save", self.save), ("Delete draft", self.delete)):
            ttk.Button(top, text=label, command=lambda fn=action: self.attempt(fn)).pack(side="left", padx=2)
        self.tree = ttk.Treeview(self, columns=("goal", "after"), show="tree headings", height=5)
        self.tree.heading("#0", text="Step")
        self.tree.column("#0", width=45, stretch=False)
        self.tree.heading("goal", text="Description")
        self.tree.heading("after", text="Depends on")
        self.tree.column("after", width=90, stretch=False)
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.goal = tk.Text(self, height=3, wrap="word", bg=palette.PANEL_2, fg=palette.WHITE,
                            insertbackground=palette.WHITE, font=("Segoe UI", 11))
        self.goal.pack(fill="x", pady=5)
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="After step IDs (comma separated)").pack(side="left")
        ttk.Entry(row, textvariable=self.dependencies, width=14).pack(side="left", padx=5)
        for label, action in (("Add", self.add), ("Update", self.update_node), ("Remove", self.remove)):
            ttk.Button(row, text=label, command=lambda fn=action: self.attempt(fn)).pack(side="left", padx=2)
        self.canvas = tk.Canvas(self, bg=palette.BLACK, highlightthickness=0, height=170)
        self.canvas.pack(fill="both", expand=True, pady=5)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        scroll.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=scroll.set)
        footer = ttk.Frame(self)
        footer.pack(fill="x")
        ttk.Button(footer, text="Review in chat", command=lambda: self.attempt(self.review)).pack(side="right")
        ttk.Button(footer, text="Copy latest task as draft", command=lambda: self.attempt(self.import_task)).pack(side="left")
        ttk.Label(self, textvariable=self.status, wraplength=650).pack(fill="x", pady=5)

    def attempt(self, action):
        try:
            if self.runtime.authority.privacy_mode:
                raise ValueError("Leave privacy mode to access saved task plans")
            action()
        except Exception as exc:
            self.status.set(str(exc))

    def refresh(self):
        if self.runtime.authority.privacy_mode:
            self.new()
            self.saved.configure(values=())
            self.status.set("Saved plans are hidden in privacy mode")
            return
        self.attempt(lambda: self.saved.configure(values=self.store.names()))

    def drain(self):
        if self.runtime.authority.privacy_mode and self.nodes:
            self.new()
            self.saved.configure(values=())
            self.status.set("Saved plans are hidden in privacy mode")

    def new(self):
        self.nodes = []
        self.name.set("My plan")
        self.goal.delete("1.0", "end")
        self.dependencies.set("")
        self.paint()

    def load(self):
        self.nodes = self.store.load(self.name.get())
        self.paint()
        self.status.set("Saved draft loaded")

    def save(self):
        self.store.save(self.name.get(), self.nodes)
        self.refresh()
        self.status.set("Plan saved. Review in chat when you are ready to run it.")

    def delete(self):
        self.store.delete(self.name.get())
        self.new()
        self.refresh()

    def select(self, event=None):
        selected = self.tree.selection()
        if selected:
            node = next(node for node in self.nodes if node["id"] == selected[0])
            self.goal.delete("1.0", "end")
            self.goal.insert("1.0", node["goal"])
            self.dependencies.set(", ".join(node["depends_on"]))

    def candidate(self, key):
        return {"id": key, "goal": self.goal.get("1.0", "end-1c"),
                "depends_on": [part.strip() for part in self.dependencies.get().split(",") if part.strip()]}

    def add(self):
        key = str(max((int(node["id"]) for node in self.nodes), default=0) + 1)
        self.nodes = ordered_nodes([*self.nodes, self.candidate(key)])
        self.paint()

    def update_node(self):
        selected = self.tree.selection()
        if not selected:
            raise ValueError("Select a step to update")
        self.nodes = ordered_nodes([self.candidate(node["id"]) if node["id"] == selected[0] else node for node in self.nodes])
        self.paint()

    def remove(self):
        selected = set(self.tree.selection())
        self.nodes = [{**node, "depends_on": [key for key in node["depends_on"] if key not in selected]}
                      for node in self.nodes if node["id"] not in selected]
        self.paint()

    def review(self):
        if not self.nodes:
            raise ValueError("Add at least one step first")
        self.compose(plan_text(self.nodes))

    def import_task(self):
        with self.runtime.memory.connect() as db:
            row = db.execute("SELECT goal,plan_json FROM tasks ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row is None:
            raise ValueError("No recorded task to copy")
        steps = json.loads(row["plan_json"])
        self.nodes = ordered_nodes([{"id": str(i + 1), "goal": step.get("description") or step.get("tool", "Step"),
                                   "depends_on": [str(i)] if i else []} for i, step in enumerate(steps)])
        self.name.set(row["goal"][:100])
        self.paint()
        self.status.set("Task copied as a draft. The recorded task is unchanged.")

    def paint(self):
        self.tree.delete(*self.tree.get_children())
        self.canvas.delete("all")
        points = {}
        for i, node in enumerate(ordered_nodes(self.nodes)):
            self.tree.insert("", "end", iid=node["id"], text=node["id"], values=(node["goal"], ", ".join(node["depends_on"])))
            x, y = 90 + (i % 3) * 190, 30 + (i // 3) * 90
            points[node["id"]] = (x, y)
        for node in self.nodes:
            x, y = points[node["id"]]
            for key in node["depends_on"]:
                px, py = points[key]
                self.canvas.create_line(px, py + 20, x, y - 20, arrow="last", fill=self.palette.MUTED, width=2)
        for node in self.nodes:
            x, y = points[node["id"]]
            self.canvas.create_rectangle(x - 80, y - 20, x + 80, y + 25, fill=self.palette.PANEL_2, outline=self.palette.ROYAL)
            item = self.canvas.create_text(x, y + 2, text=f"{node['id']}. {node['goal'][:40]}", width=145, fill=self.palette.WHITE)
            self.canvas.tag_bind(item, "<Button-1>", lambda event, key=node["id"]: self.tree.selection_set(key))
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
