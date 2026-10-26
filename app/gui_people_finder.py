"""Private networking → Find people to contact: who to reach for a goal, from public professional sources.

Describe who you want to reach ("CTOs of fintech companies in UAE"), or fill in the role, field, place and
organisations. Results show each person with the source that names them and professional ways in; save the
ones you want to the encrypted contact book, find their published contact routes, or open their profile.
Every search is kept (data/networking/people-finds) and can be reopened from "Earlier searches".
"""
from __future__ import annotations

import csv
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

from app.gui_fonts import DISPLAY
from app.gui_intelligence_parts import FlowRow
from app.gui_resources import BooleanVar, StringVar
from app.gui_windows import place_over

EXAMPLES = ("CTOs of fintech companies in UAE", "Founders of fintech startups in India", "CEOs of banks in Germany",
            "Top machine learning researchers in India", "Python developers in Hyderabad", "Executives at Infosys")
PURPOSES = ("Networking", "A job", "Business / clients", "Investment", "Research collaboration", "Mentoring")
SOURCE_LABELS = (("wikidata", "Leaders (Wikidata)"), ("openalex", "Researchers (OpenAlex)"), ("github", "Developers (GitHub)"),
                 ("teams", "Organisations' own pages"))


class PeopleFinderWindow(tk.Toplevel):
    def __init__(self, networking: Any) -> None:
        super().__init__(networking)
        self.withdraw()
        self.networking, self.gui = networking, networking.gui
        self.p = p = networking.palette
        self.make = getattr(self.gui.runtime, "people_finder", None)
        self._results: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._closed = False
        self.busy = False
        self.report: dict[str, Any] = {}
        self.people: dict[str, dict[str, Any]] = {}
        self.goal = StringVar(self, "")
        self.role, self.field, self.place, self.organizations = (StringVar(self, "") for _ in range(4))
        self.purpose = StringVar(self, PURPOSES[0])
        self.auto = BooleanVar(self, True)
        self.deep = BooleanVar(self, False)
        self.source_vars = {key: BooleanVar(self, False) for key, _label in SOURCE_LABELS}
        self.status = StringVar(self, "Describe who you want to reach, then press Find people.")
        self.title("HAYAT · Find people to contact")
        self.configure(bg=p.BLACK)
        self.minsize(980, 640)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: self.close())
        self._build()
        place_over(self, self.gui.root, (1240, 820))
        self.deiconify()
        self.after(100, self._poll)
        self.after_idle(self.goal_entry.focus_set)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        p = self.p
        head = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        head.pack(fill="x")
        tk.Label(head, text="Find people to contact", bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 18), anchor="w").pack(side="left")
        ttk.Button(head, text="Close", command=self.close).pack(side="right")
        tk.Label(self, text="Real people from public professional sources: leaders of organisations (Wikidata), researchers "
                            "(OpenAlex), developers (GitHub) and organisations' own team pages. Each comes with where it was "
                            "found and professional ways in. No guessed e-mail, no broker, nobody contacted.",
                 bg=p.BLACK, fg=p.MUTED, anchor="w", justify="left", wraplength=1150, font=("Segoe UI", 9)).pack(fill="x", padx=16)

        form = tk.Frame(self, bg=p.PANEL, padx=12, pady=10)
        form.pack(fill="x", padx=16, pady=(8, 4))
        tk.Label(form, text="Who do you want to reach?", bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 11),
                 anchor="w").pack(fill="x")
        line = tk.Frame(form, bg=p.PANEL)
        line.pack(fill="x", pady=(4, 6))
        self.goal_entry = ttk.Entry(line, textvariable=self.goal, font=("Segoe UI", 12))
        self.goal_entry.pack(side="left", fill="x", expand=True)
        self.goal_entry.bind("<Return>", lambda _event: self.find())
        self.find_button = ttk.Button(line, text="Find people", style="HayatAccent.TButton", command=self.find)
        self.find_button.pack(side="left", padx=(8, 0))
        self.cancel_button = ttk.Button(line, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(6, 0))
        chips = FlowRow(form, p.PANEL, gap=4, height=30)
        chips.pack(fill="x")
        for example in EXAMPLES:
            chips.add(ttk.Button(chips, text=example, style="Compact.Hayat.TButton",
                                 command=lambda text=example: (self.goal.set(text), self.find())))
        # Or the parts one by one: they override what the sentence says.
        parts = tk.Frame(form, bg=p.PANEL)
        parts.pack(fill="x", pady=(8, 0))
        for column, (label, variable, width) in enumerate((("Role", self.role, 16), ("Field / industry", self.field, 18),
                                                           ("Place", self.place, 14), ("Organisations (comma-separated)",
                                                                                       self.organizations, 26))):
            box = tk.Frame(parts, bg=p.PANEL)
            box.grid(row=0, column=column, sticky="ew", padx=(0, 8))
            parts.columnconfigure(column, weight=width)
            tk.Label(box, text=label, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w").pack(fill="x")
            entry = ttk.Entry(box, textvariable=variable, width=width)
            entry.pack(fill="x")
            entry.bind("<Return>", lambda _event: self.find())
        box = tk.Frame(parts, bg=p.PANEL)
        box.grid(row=0, column=4, sticky="ew")
        tk.Label(box, text="Why", bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w").pack(fill="x")
        ttk.Combobox(box, textvariable=self.purpose, values=PURPOSES, state="readonly", width=20).pack(fill="x")
        sources = FlowRow(form, p.PANEL, gap=4, height=30)
        sources.pack(fill="x", pady=(8, 0))
        sources.add(ttk.Checkbutton(sources, text="Choose sources for me", variable=self.auto, command=self._paint_sources))
        self.source_checks = []
        for key, label in SOURCE_LABELS:
            check = ttk.Checkbutton(sources, text=label, variable=self.source_vars[key])
            sources.add(check)
            self.source_checks.append(check)
        sources.add(ttk.Checkbutton(sources, text="Deep: also find the top people's published e-mail and contact pages (slower)",
                                    variable=self.deep))
        self._paint_sources()

        body = tk.PanedWindow(self, orient="horizontal", bg=p.BLACK, sashwidth=7, bd=0)
        body.pack(fill="both", expand=True, padx=16, pady=4)
        left = tk.Frame(body, bg=p.BLACK)
        right = tk.Frame(body, bg=p.BLACK)
        body.add(left, minsize=520, width=720)
        body.add(right, minsize=300)
        top = tk.Frame(left, bg=p.BLACK)
        top.pack(fill="x")
        tk.Label(top, text="People", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 11)).pack(side="left")
        self.earlier = ttk.Combobox(top, state="readonly", width=46)
        self.earlier.pack(side="right")
        self.earlier.bind("<<ComboboxSelected>>", lambda _event: self.open_earlier())
        tk.Label(top, text="Earlier searches", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9)).pack(side="right", padx=6)
        frame = tk.Frame(left, bg=p.BLACK)
        frame.pack(fill="both", expand=True, pady=(4, 0))
        columns = ("role", "organization", "standing", "source")
        self.table = ttk.Treeview(frame, columns=columns, show="tree headings", selectmode="extended")
        for key, title, width in (("#0", "Name", 170), ("role", "Role", 170), ("organization", "Organisation", 170),
                                  ("standing", "Standing", 200), ("source", "Source", 110)):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=80, stretch=key in {"#0", "standing"})
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview, style="Hayat.Vertical.TScrollbar")
        self.table.configure(yscrollcommand=bar.set)
        self.table.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.table.bind("<<TreeviewSelect>>", lambda _event: self.show_selected())
        self.table.bind("<Double-1>", lambda _event: self.open_source())
        actions = FlowRow(left, p.BLACK, gap=5, height=36)
        actions.pack(fill="x", pady=6)
        self.save_button = ttk.Button(actions, text="Save to contact book", style="HayatAccent.TButton", command=self.save_selected)
        actions.add(self.save_button)
        actions.add(ttk.Button(actions, text="Find their contact routes", command=self.find_routes))
        actions.add(ttk.Button(actions, text="Open source", command=self.open_source))
        actions.add(ttk.Button(actions, text="Export CSV", command=self.export_csv))

        self.detail = tk.Text(right, bg=p.PANEL, fg=p.WHITE, wrap="word", bd=0, padx=12, pady=10, font=("Segoe UI", 10),
                              cursor="arrow", height=10)
        self.detail.pack(fill="both", expand=True)
        self.detail.tag_configure("name", font=(DISPLAY, 16), foreground=p.WHITE)
        self.detail.tag_configure("muted", foreground=p.MUTED)
        self.detail.tag_configure("label", foreground=p.GOLD_SOFT, font=("Segoe UI Semibold", 10))
        self._link_count = 0
        self._show_text("Choose a person to see why they fit, where they are listed and how to reach them.")
        self.progress = ttk.Progressbar(self, mode="indeterminate", style="Hayat.Horizontal.TProgressbar")
        tk.Label(self, textvariable=self.status, bg=p.PANEL, fg=p.GOLD_SOFT, anchor="w", justify="left", wraplength=1150,
                 padx=10, pady=7).pack(fill="x", padx=16, pady=(0, 10))
        self._load_earlier()

    def _paint_sources(self) -> None:
        for check in self.source_checks:
            check.state(["disabled"] if self.auto.get() else ["!disabled"])

    # ------------------------------------------------------------------ searching
    def find(self) -> bool:
        if self.busy:
            self.status.set("A search is running; cancel it or wait for it to finish.")
            return False
        if self.make is None:
            self.status.set("People search is not available in this session.")
            return False
        goal, organizations = self.goal.get().strip(), self.organizations.get().strip()
        role, field, place = self.role.get().strip(), self.field.get().strip(), self.place.get().strip()
        if not (goal or role or field or organizations):
            self.status.set("Say who you want to reach, e.g. \"Founders of fintech startups in India\", or fill in a role, field "
                            "or organisations.")
            self.goal_entry.focus_set()
            return False
        sources = None if self.auto.get() else [key for key, variable in self.source_vars.items() if variable.get()]
        if sources == []:
            self.status.set("Tick at least one source, or let HAYAT choose them.")
            return False
        purpose = {"A job": "job", "Business / clients": "business", "Investment": "investment",
                   "Research collaboration": "collaboration", "Mentoring": "mentoring"}.get(self.purpose.get(), "networking")
        self._cancel.clear()
        self.busy = True
        self.find_button.state(["disabled"])
        self.cancel_button.state(["!disabled"])
        self.progress.pack(fill="x", padx=16, before=self.winfo_children()[-1])
        self.progress.start(12)
        deep = self.deep.get()
        self.status.set("Searching public professional sources…" + (" then their published contact details (about 1–2 minutes)"
                                                                     if deep else " (about 10–40 seconds)"))

        def check() -> None:
            if self._cancel.is_set():
                raise PermissionError("Search cancelled.")

        def work() -> None:
            try:
                finder = self.make(check)
                value = finder.find(goal, role=role, field=field, place=place, organizations=organizations, purpose=purpose,
                                    sources=sources, limit=40, deep=deep)
                self._results.put(("found", value, None))
            except Exception as exc:  # shown in the window, never raised into Tk
                self._results.put(("found", None, str(exc) or type(exc).__name__))
        threading.Thread(target=work, name="hayat-people-finder", daemon=True).start()
        return True

    def cancel(self) -> None:
        if self.busy:
            self._cancel.set()
            self.status.set("Cancelling… requests already under way may finish first; their results are discarded.")

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            while True:
                kind, value, error = self._results.get_nowait()
                if kind == "found":
                    self._finished(value, error)
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _finished(self, value: dict | None, error: str | None) -> None:
        self.busy = False
        self.progress.stop()
        self.progress.pack_forget()
        self.find_button.state(["!disabled"])
        self.cancel_button.state(["disabled"])
        if self._cancel.is_set():
            self.status.set("Search cancelled.")
            return
        if error:
            self.status.set("Could not search: " + error)
            return
        self.show_report(value)
        self._load_earlier()

    def show_report(self, report: dict[str, Any]) -> None:
        self.report = report
        self.people = {person["id"]: person for person in report.get("people") or []}
        self.table.delete(*self.table.get_children())
        for person in report.get("people") or []:
            self.table.insert("", "end", iid=person["id"], text=person["name"],
                              values=(person["role"], person["organization"], person["standing"], person["source"]))
        coverage = " · ".join(f"{name} {count}" for name, count in (report.get("coverage") or {}).items())
        problems = "; ".join(f"{row['source']}: {row['error']}" for row in report.get("errors") or [])
        found = len(self.people)
        if found:
            text = f"Found {found} {'person' if found == 1 else 'people'} for \"{report.get('goal', '')}\" ({coverage})."
        else:
            text = (f"Nobody named for \"{report.get('goal', '')}\" ({coverage or 'no source answered'}). Try a broader field, a "
                    "country rather than a city, or name organisations.")
        if report.get("deep"):
            text += " " + report["deep"]["note"]
        if report.get("organisation_routes"):
            text += " The organisations' own ways in are on the right."
        if problems:
            text += " Not every source answered: " + problems + "."
        self.status.set(text)
        if found:
            first = report["people"][0]["id"]
            self.table.selection_set(first)
            self.table.focus(first)
        else:
            self.show_organisations()

    # ------------------------------------------------------------------ the selected person
    def selected(self) -> list[dict[str, Any]]:
        return [self.people[key] for key in self.table.selection() if key in self.people]

    def _show_text(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", text, "muted")
        self.detail.configure(state="disabled")

    def _link(self, label: str, url: str) -> None:
        self._link_count += 1
        tag = f"link{self._link_count}"
        self.detail.tag_configure(tag, foreground=self.p.ROYAL_SOFT, underline=True)
        self.detail.tag_bind(tag, "<Button-1>", lambda _event, target=url: webbrowser.open(target))
        self.detail.tag_bind(tag, "<Enter>", lambda _event: self.detail.configure(cursor="hand2"))
        self.detail.tag_bind(tag, "<Leave>", lambda _event: self.detail.configure(cursor="arrow"))
        self.detail.insert("end", "↗ " + label + "\n", tag)

    def show_selected(self) -> None:
        people = self.selected()
        if not people:
            return
        person = people[0]
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", person["name"] + "\n", "name")
        self.detail.insert("end", " · ".join(filter(None, (person["role"], person["organization"]))) + "\n\n", "muted")
        self.detail.insert("end", "Why them\n", "label")
        self.detail.insert("end", person["why"] + "\n" + person["standing"][:1].upper() + person["standing"][1:] + ".\n\n")
        self.detail.insert("end", "Ways in\n", "label")
        for route in person["routes"]:
            target = "mailto:" + route["value"] if route["kind"] == "email" else route["value"]
            self._link(route["label"] + (": " + route["value"] if route["kind"] == "email" else ""), target)
        self.detail.insert("end", "\nWhere it was found\n", "label")
        self._link(person["source"], person["source_url"])
        for extra in person.get("also_in") or []:
            self._link("also " + extra["source"], extra["url"])
        self.detail.insert("end", "\nA source says what it says; open it to check it is the person you mean.", "muted")
        self.detail.configure(state="disabled")

    def show_organisations(self) -> None:
        rows = self.report.get("organisation_routes") or []
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if not rows:
            self.detail.insert("end", "No organisation routes for this search.", "muted")
        for organisation in rows:
            self.detail.insert("end", organisation["organization"] + "\n", "name")
            for route in organisation["routes"]:
                self._link(route["label"], route["value"])
            self.detail.insert("end", "\n")
        self.detail.configure(state="disabled")

    # ------------------------------------------------------------------ actions
    def save_selected(self) -> bool:
        people = self.selected()
        if not people:
            self.status.set("Choose one or more people first (Ctrl+click to choose several).")
            return False
        book = getattr(self.networking, "book", None)
        if book is None:
            self.status.set("The contact book is not available.")
            return False
        purpose = self.purpose.get()
        field = self.report.get("field") or self.field.get().strip()
        saved, failed = 0, []
        for person in people:
            channels = [{"kind": route["kind"], "value": route["value"], "status": "public_professional",
                         "source_url": route.get("source") or person["source_url"]}
                        for route in person["routes"] if route["kind"] in {"website", "profile", "email"}][:8]
            try:
                book.save(person["name"], organization=person["organization"], role=person["role"],
                          expertise=[field] if field else [], goals=[purpose], source_url=person["source_url"],
                          channels=channels, notes=f"Found by Find people to contact for \"{self.report.get('goal', '')}\". "
                                                   f"{person['why']} {person['standing']}.")
                saved += 1
            except Exception as exc:
                failed.append(f"{person['name']}: {exc}")
        refresh = getattr(self.networking, "refresh", None)
        if callable(refresh):
            refresh()
        self.status.set(f"Saved {saved} to your contact book (encrypted on this computer)."
                        + (" Not saved: " + "; ".join(failed) if failed else "")
                        + " Prepare an introduction from Private networking → Outreach draft.")
        return saved > 0

    def find_routes(self) -> bool:
        people = self.selected()
        if not people:
            self.status.set("Choose a person first.")
            return False
        person = people[0]
        if not person["organization"]:
            self.status.set("Contact routes are searched by name and organisation; this person has no organisation listed.")
            return False
        opened = self.networking.find_professional_contacts(name=person["name"], organization=person["organization"],
                                                            official_url=person.get("organization_site", ""))
        if opened:
            self.status.set(f"Contact discovery opened for {person['name']} at {person['organization']}: press Find there.")
        return opened

    def open_source(self) -> None:
        people = self.selected()
        if people:
            webbrowser.open(people[0]["source_url"])

    def export_csv(self) -> bool:
        if not self.people:
            self.status.set("Nothing to export yet.")
            return False
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile="people-to-contact.csv")
        if not path:
            return False
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Name", "Role", "Organisation", "Why", "Standing", "Source", "Ways in"])
            for person in self.people.values():
                writer.writerow([person["name"], person["role"], person["organization"], person["why"], person["standing"],
                                 person["source_url"], " | ".join(route["value"] for route in person["routes"])])
        self.status.set(f"Exported {len(self.people)} people to {path}.")
        return True

    # ------------------------------------------------------------------ earlier searches
    def _load_earlier(self) -> None:
        if self.make is None:
            return
        try:
            self._earlier = self.make().recent(30)
        except Exception:  # a missing folder or unreadable file: no earlier searches to show
            self._earlier = []
        self.earlier.configure(values=[f"{row['searched_at'][:16].replace('T', ' ')} · {row['goal']} ({row['people']})"
                                       for row in self._earlier])

    def open_earlier(self) -> None:
        index = self.earlier.current()
        if 0 <= index < len(getattr(self, "_earlier", [])):
            try:
                from app.research.people_finder import PeopleFinder
                self.show_report(PeopleFinder.load(Path(self._earlier[index]["path"])))
            except Exception as exc:
                self.status.set("Could not open that search: " + str(exc))

    def close(self) -> None:
        self._closed = True
        self._cancel.set()
        self.destroy()
