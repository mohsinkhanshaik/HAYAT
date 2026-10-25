"""The Business window's Launch tab: start a business or service and see the way to the first invoice."""
from __future__ import annotations

from app.gui_resources import StringVar

import tkinter as tk
from tkinter import ttk
from typing import Any

from app.gui_theme import DarkScrolledText
from app.tools.launch import SERVICE_STEPS


class LaunchTab:
    """Ideas, the launch checklist, services and prices, website and marketing kit."""

    def __init__(self, studio: Any, switcher: tk.Frame) -> None:
        self.studio, self.launch = studio, studio.gui.runtime.launch
        p = studio.palette
        button = ttk.Button(switcher, text="Launch", style="Hayat.TButton", command=lambda: studio.show_tab("Launch"))
        button.pack(side="left", padx=(0, 4))
        studio.tab_buttons["Launch"] = button
        self.frame = tk.Frame(studio.body, bg=p.PANEL, padx=12, pady=10)
        studio.tab_frames["Launch"] = self.frame

        top = tk.Frame(self.frame, bg=p.PANEL)
        top.pack(fill="x")
        self.name_var = StringVar(studio, "")
        tk.Label(top, textvariable=self.name_var, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 13),
                 anchor="w").pack(side="left")
        self.venture_var = StringVar(studio, "")
        self.picker = ttk.Combobox(top, textvariable=self.venture_var, state="readonly", width=22)
        self.picker.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        for label, command in (("Publishing setup", self.publishing_form), ("Start a business", self.start_form),
                               ("Business ideas", self.ideas_form)):
            ttk.Button(top, text=label, style="Hayat.TButton", command=command).pack(side="right", padx=(6, 0))

        self.intro = tk.Label(self.frame, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 11), anchor="w", justify="left",
                              wraplength=880, text=(
                                  "Start a business or service in 12 steps, from an idea to your first invoice. "
                                  "Press Business ideas to find one that fits your skills, or Start a business if you "
                                  "already know what you want to offer. HAYAT ticks steps off as it sees them done in "
                                  "your books. Nothing is registered, published or paid for you.\n\nThe steps: "
                                  + " → ".join(title for _key, title, *_rest in SERVICE_STEPS) + "."))
        self.body = tk.Frame(self.frame, bg=p.PANEL)

        progress = tk.Frame(self.body, bg=p.PANEL)
        progress.pack(fill="x", pady=(6, 0))
        self.progress_var = StringVar(studio, "")
        tk.Label(progress, textvariable=self.progress_var, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 10)
                 ).pack(side="left")
        self.bar = ttk.Progressbar(progress, maximum=100, length=220, style="Hayat.Horizontal.TProgressbar")
        self.bar.pack(side="left", padx=10)
        # The step and file actions sit beside the progress, where they can never be pushed off the bottom.
        for label, command in (("Publish website", self.publish_website), ("Marketing kit", self.make_marketing),
                               ("Make website", self.make_website),
                               ("Undo step", self.undo_step), ("Mark step done", self.mark_done)):
            ttk.Button(progress, text=label, style="Hayat.TButton", command=command).pack(side="right", padx=(6, 0))
        self.next_var = StringVar(studio, "")
        tk.Label(self.body, textvariable=self.next_var, bg=p.PANEL, fg=p.GOLD_SOFT, font=("Segoe UI", 10), anchor="w",
                 justify="left", wraplength=880).pack(fill="x", pady=(4, 6))

        tables = tk.PanedWindow(self.body, orient="vertical", bg=p.PANEL, sashwidth=6, bd=0)
        tables.pack(fill="both", expand=True)
        checklist = tk.Frame(tables, bg=p.PANEL)
        self.steps = studio._tree(checklist, (("#0", "", 36), ("title", "Step", 290), ("how", "How", 440),
                                              ("by", "Ticked by", 110)), set(), height=8)
        self.steps.tag_configure("done", foreground=p.OLIVE_SOFT)
        self.steps.tag_configure("next", foreground=p.GOLD_SOFT)
        tables.add(checklist, stretch="always", minsize=120)
        lower = tk.Frame(tables, bg=p.PANEL)
        row = tk.Frame(lower, bg=p.PANEL)
        row.pack(fill="x", pady=(6, 4))
        tk.Label(row, text="Services and prices", bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 10)).pack(side="left")
        for label, command in (("Quote from service", self.quote_form), ("Add service", self.service_form),
                               ("Price calculator", self.rate_form)):
            ttk.Button(row, text=label, style="Hayat.TButton", command=command).pack(side="right", padx=(6, 0))
        self.services = studio._tree(lower, (("#0", "Service", 220), ("price", "Price", 200),
                                             ("delivery", "Delivery", 100), ("includes", "Includes", 400)),
                                     {"price"}, height=3)
        tables.add(lower, stretch="never", minsize=100, height=140)

        self.current: str | None = None

    # ------------------------------------------------------------------ data
    def refresh(self) -> None:
        ventures = self.launch.ventures()
        names = [row["name"] for row in ventures]
        self.picker["values"] = names
        if len(names) > 1:
            self.picker.pack(side="left", padx=10)
        else:
            self.picker.pack_forget()
        if not names:
            self.current = None
            self.name_var.set("Start a business")
            self.body.pack_forget()
            self.intro.pack(fill="x", pady=(10, 0))
            return
        if self.venture_var.get() not in names:
            self.venture_var.set(names[-1])
        self.current = self.venture_var.get()
        status = self.launch.status(self.current)
        self.intro.pack_forget()
        self.body.pack(fill="both", expand=True)
        self.name_var.set(f"{status['business']}: {status['offer']}")
        self.progress_var.set(status["progress"])
        self.bar["value"] = 100 * status["done"] / max(1, status["total"])
        self.next_var.set("Next: " + status["next_step"])
        upcoming = next((step["step"] for step in status["steps"] if not step["done"]), None)
        self.studio._fill(self.steps, [(step["step"], "✓" if step["done"] else "○",
                                        (step["title"], step["how"], {"HAYAT saw it in your books": "HAYAT",
                                                                      "you": "You"}.get(step["ticked_by"], "")),
                                        "done" if step["done"] else ("next" if step["step"] == upcoming else ""))
                                       for step in status["steps"]])
        self.studio._fill(self.services, [(str(service["id"]), service["name"],
                                           (service["price"], f"{service['delivery_days']} days" if service["delivery_days"]
                                            else "", "; ".join(service["includes"])))
                                          for service in status["services"]])

    def _need_venture(self) -> bool:
        if self.current is None:
            self.studio.status_var.set("Start a business first.")
            self.start_form()
            return False
        return True

    # ------------------------------------------------------------------ forms
    def ideas_form(self) -> Any:
        def submit(values: dict) -> str:
            result = self.launch.ideas(values["skills"], values["interests"], values["hours"] or None, values["budget"])
            self.ideas_window = IdeasWindow(self, result)
            return f"{len(result['ideas'])} ideas that fit. Pick one to start it."
        return self.studio._form("Business ideas", [
            ("skills", "What are you good at? (e.g. Arabic, design, Excel, teaching)", ""),
            ("interests", "What do you enjoy? (optional)", ""),
            ("hours", "Hours a week you can give (optional)", ""),
            ("budget", "Money you can put in to start (optional)", ""),
        ], submit, intro="HAYAT suggests businesses one person can start with little money, matched to you. "
                         "No idea comes with a promise of income: check demand first.")

    def start_form(self, idea: dict | None = None) -> Any:
        idea = idea or {}

        def submit(values: dict) -> str:
            result = self.launch.start(values["name"], values["offer"], values["audience"], values["kind"],
                                       idea.get("key", ""))
            self.venture_var.set(result["business"])
            self.studio.refresh()
            self.studio.show_tab("Launch")
            return f"{result['business']} started. Next: {result['next_step']}"
        return self.studio._form("Start a business", [
            ("name", "Business name", ""),
            ("offer", "What you offer (one sentence)", idea.get("first_offer", "").rstrip(".")),
            ("audience", "Who it is for", ""),
            ("kind", "Kind", ["product", "service"] if idea.get("kind") == "product" else ["service", "product"]),
        ], submit, intro=(f"Idea: {idea['title']}. " if idea else "") + "You get a 12-step launch plan. HAYAT does not "
                         "register a company or publish anything.")

    def service_form(self) -> Any:
        if not self._need_venture():
            return None
        default = self.studio.ledger.profile()["default_currency"]

        def submit(values: dict) -> str:
            result = self.launch.add_service(values["name"], values["price"], values["description"], values["unit"],
                                             values["days"] or 0, values["includes"], values["currency"], self.current)
            self.studio.refresh()
            return f"{result['service']}: {result['price']}."
        return self.studio._form("Add a service", [
            ("name", "Service name (e.g. Logo design)", ""), ("price", "Price", ""),
            ("unit", "Charged", ["fixed", "hour", "month", "session", "item", "word", "page"]),
            ("description", "Short description", ""), ("days", "Delivery in days (optional)", ""),
            ("includes", "What's included, separated by ; (optional)", ""),
            ("currency", "Currency", [default] + [c for c in ("USD", "EUR", "GBP", "CAD", "AUD", "NZD") if c != default]),
        ], submit, intro="Not sure what to charge? Use the price calculator first.")

    def rate_form(self) -> Any:
        default = self.studio.ledger.profile()["default_currency"]

        def submit(values: dict) -> str:
            result = self.launch.rate(values["goal"], values["hours"], values["expenses"] or 0, values["tax"] or 20,
                                      values["currency"])
            return result["explained"]
        return self.studio._form("Price calculator", [
            ("goal", "How much do you want to keep each month?", ""),
            ("hours", "Hours a week you can bill clients for", "20"),
            ("expenses", "Business costs per month (software, internet...)", "0"),
            ("tax", "Tax to set aside, in percent", "20"),
            ("currency", "Currency", [default] + [c for c in ("USD", "EUR", "GBP", "CAD", "AUD", "NZD") if c != default]),
        ], submit, intro="Works out the hourly rate you need, after tax, costs and 4 weeks off a year. "
                         "The answer appears at the bottom of the Business window.")

    def quote_form(self) -> Any:
        if not self._need_venture():
            return None
        services = [row["name"] for row in self.launch.services(self.current)["services"]]
        if not services:
            self.studio.status_var.set("Add a service first, then quote it.")
            return self.service_form()
        clients = [row["name"] for row in self.studio.ledger.clients()["clients"]]

        def submit(values: dict) -> str:
            if values["client"] not in clients:
                self.studio.ledger.add_client(values["client"])
            result = self.launch.quote_from_service(values["client"], values["service"], values["quantity"] or 1,
                                                    self.current)
            self.studio.refresh()
            return f"Quote {result['number']} for {result['total']} saved. Open it from the Invoices tab (quotes)."
        return self.studio._form("Quote from a service", [
            ("client", "Client name (a new one is added)", clients[0] if clients else ""),
            ("service", "Service", services), ("quantity", "Quantity", "1"),
        ], submit, intro="Makes a quote PDF you can send. Turn it into an invoice when they say yes.")

    # ------------------------------------------------------------------ actions
    def _selected_step(self) -> str | None:
        selected = self.steps.selection()
        if not selected:
            self.studio.status_var.set("Select a step first.")
            return None
        return selected[0]

    def mark_done(self) -> None:
        step = self._selected_step()
        if step:
            self.launch.step_done(step, self.current)
            self.refresh()

    def undo_step(self) -> None:
        step = self._selected_step()
        if step:
            self.launch.step_done(step, self.current, undo=True)
            self.refresh()

    def make_website(self) -> None:
        if not self._need_venture():
            return
        result = self.launch.website(self.current)
        self.refresh()
        self.studio.status_var.set(f"Website written: {result['path']}. {result['next_step']}")
        self.studio._open(result["path"])

    def publish_website(self) -> dict | None:
        """Prepare publishing; the confirmation window shows exactly what will go online."""
        if not self._need_venture():
            return None
        result = self.studio.gui.runtime.registry.execute("publish_website_prepare", {"venture": self.current})
        if not result.success:
            self.studio.status_var.set(f"Couldn't prepare publishing: {result.error}")
            return None
        self.studio.status_var.set(f"Action {result.output['action_id']} is waiting for you: check it in the "
                                   "confirmation window. Nothing is online yet.")
        return result.output

    def publishing_form(self) -> Any:
        publisher = getattr(self.studio.gui.runtime, "publisher", None)
        if publisher is None:
            self.studio.status_var.set("Publishing isn't available in this session.")
            return None

        def submit(values: dict) -> str:
            publisher.store_token(values["token"])
            return "Netlify token saved in the Windows credential store. Publishing always asks you first."
        return self.studio._form("Publishing setup", [("token", "Netlify personal access token", "")], submit,
                                 intro="To put your website online for free: make a free Netlify account, then create a "
                                       "personal access token under User settings, Applications. Paste it here; it is "
                                       "kept in the Windows credential store, never in a file. HAYAT still asks you "
                                       "to confirm every publish." + (" A token is already saved." if publisher.ready()
                                                                       else ""))

    def make_marketing(self) -> None:
        if not self._need_venture():
            return
        result = self.launch.marketing_kit(self.current)
        self.refresh()
        self.studio.status_var.set(f"Marketing kit written: {result['path']}. {result['note']}")
        self.studio._open(result["path"])


class IdeasWindow(tk.Toplevel):
    """The ideas that fit, each with a button to start it."""

    def __init__(self, tab: LaunchTab, result: dict) -> None:
        studio = tab.studio
        super().__init__(studio)
        p = studio.palette
        self.title("HAYAT · Business ideas")
        self.transient(studio)
        self.configure(bg=p.BLACK, padx=16, pady=12)
        self.bind("<Escape>", lambda _event: (self.destroy(), "break")[1])
        tk.Label(self, text="Business ideas that fit you", bg=p.BLACK, fg=p.WHITE,
                 font=("Segoe UI Semibold", 14), anchor="w").pack(fill="x")
        for note in result["notes"]:
            tk.Label(self, text=note, bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                     wraplength=760).pack(fill="x")
        text = DarkScrolledText(self, height=18, wrap="word", bg=p.PANEL, fg=p.WHITE, relief="flat",
                                font=("Segoe UI", 10), padx=12, pady=10)
        text.pack(fill="both", expand=True, pady=(8, 8))
        text.tag_configure("title", font=("Segoe UI Semibold", 12), foreground=p.GOLD_SOFT, spacing1=10)
        self.start_buttons: list[ttk.Button] = []
        for idea in result["ideas"]:
            text.insert("end", f"{idea['title']} ({idea['kind']})\n", "title")
            text.insert("end", f"{idea['what']}\n{idea['why']}\nTo start: {idea['startup']}\n"
                               f"First clients: {idea['first_clients']}\nFirst offer: {idea['first_offer']}\n"
                               f"HAYAT helps with: {idea['hayat_helps_with']}\n"
                               f"Check demand: ask \"{idea['check_demand'][0]}\"\n")
            button = ttk.Button(text, text=f"Start: {idea['title']}", style="HayatAccent.TButton",
                                command=lambda chosen=idea: (self.destroy(), tab.start_form(chosen)))
            text.window_create("end", window=button)
            text.insert("end", "\n")
            self.start_buttons.append(button)
        text.configure(state="disabled")
        ttk.Button(self, text="Close", style="Hayat.TButton", command=self.destroy).pack(side="right")
        self.geometry("820x640")
        self.lift()


def attach(studio: Any, switcher: tk.Frame) -> LaunchTab | None:
    """Add the Launch tab when this runtime has the launch toolkit."""
    if getattr(getattr(studio.gui, "runtime", None), "launch", None) is None:
        return None
    return LaunchTab(studio, switcher)

