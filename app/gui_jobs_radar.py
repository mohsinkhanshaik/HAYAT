"""Native career dashboard; snapshots and scenarios run on the Jobs worker."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from app.gui_output import DisplayVar, SafeTreeview
from app.gui_resources import BooleanVar, StringVar
from app.gui_layout import ResponsiveSplit


class CareerRadar(tk.Frame):
    def __init__(self, parent, window):
        self.window, self.p = window, window.p
        super().__init__(parent, bg=self.p.BLACK)
        p = self.p
        self.report = None
        self.rows = {}
        self._cancel = threading.Event()
        hero = tk.Frame(self, bg=p.PANEL, padx=14, pady=9,
                        highlightthickness=1, highlightbackground=p.LINE)
        hero.pack(fill="x", pady=(4, 10))
        tk.Label(hero, text="Career radar", bg=p.PANEL, fg=p.HOLO,
                 font=("Segoe UI Semibold", 16), anchor="w").pack(fill="x")
        self.note = DisplayVar(self, "A measured view of your saved jobs, evidence gaps and next steps.")
        note = tk.Label(hero, textvariable=self.note, bg=p.PANEL, fg=p.MUTED, justify="left", anchor="w", wraplength=700)
        note.pack(fill="x", pady=(5, 0))
        note.bind("<Configure>", lambda event: note.configure(wraplength=max(220, event.width - 8)))
        cards = tk.Frame(self, bg=p.BLACK)
        cards.pack(fill="x", pady=(0, 10))
        self.metrics = {}
        for column, (key, title) in enumerate((("roles", "SAVED ROLES"), ("ready", "GOOD / STRONG"), ("delta", "ADDITIONAL MATCHES"))):
            card = tk.Frame(cards, bg=p.PANEL, padx=10, pady=6,
                            highlightthickness=1, highlightbackground=p.LINE)
            card.grid(row=0, column=column, sticky="nsew", padx=3)
            cards.columnconfigure(column, weight=1, uniform="metric")
            self.metrics[key] = StringVar(self, "—")
            tk.Label(card, text=title, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8)).pack(anchor="w")
            tk.Label(card, textvariable=self.metrics[key], bg=p.PANEL, fg=p.GOLD_SOFT,
                     font=("Segoe UI Semibold", 20)).pack(anchor="w")
        controls = tk.Frame(self, bg=p.BLACK)
        controls.pack(fill="x", pady=(0, 8))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="What if I could demonstrate").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.skills = StringVar(self, "")
        self.skills_entry = ttk.Entry(controls, textvariable=self.skills)
        self.skills_entry.grid(row=0, column=1, sticky="ew")
        self.skills_entry.bind("<Return>", lambda _event: self.refresh())
        ttk.Label(controls, text="Skills separated by commas; the profile stays unchanged.").grid(row=1, column=1, sticky="w", pady=(4, 0))
        buttons = tk.Frame(self, bg=p.BLACK)
        buttons.pack(fill="x", pady=(0, 7))
        from app.gui_panels import ButtonRow
        # ButtonRow lives with the other reusable responsive controls.
        self.hours = StringVar(self, "6")
        ttk.Label(buttons, text="Practice hours/week").pack(side="left")
        ttk.Spinbox(buttons, from_=1, to=40, textvariable=self.hours, width=4).pack(side="left", padx=6)
        self.run_button = ttk.Button(buttons, text="Run scenario", command=self.refresh, style="Compact.HayatAccent.TButton")
        self.run_button.pack(side="left", padx=4)
        self.stop_button = ttk.Button(buttons, text="Stop", command=lambda: self._cancel.set(), style="Compact.Hayat.TButton")
        self.stop_button.pack(side="left")
        self.detailed = BooleanVar(self, False)
        ttk.Checkbutton(self, text="Show full requirement and score analysis", variable=self.detailed,
                        command=self.select_job).pack(anchor="w", pady=(0, 6))
        self.tabs = ttk.Notebook(self, style="Side.TNotebook")
        self.tabs.pack(fill="both", expand=True)
        opportunities = tk.Frame(self.tabs, bg=p.BLACK)
        learn = tk.Frame(self.tabs, bg=p.BLACK)
        forecast = tk.Frame(self.tabs, bg=p.BLACK)
        self.tabs.add(opportunities, text="Opportunities & requirements")
        self.tabs.add(learn, text="Learn & practise")
        self.tabs.add(forecast, text="Forecast & access")
        self.opportunity_panes = ResponsiveSplit(opportunities, p)
        self.opportunity_panes.pack(fill="both", expand=True, pady=5)
        role_list, role_detail = ttk.Frame(self.opportunity_panes), ttk.Frame(self.opportunity_panes)
        self.opportunity_panes.add(role_list, weight=1)
        self.opportunity_panes.add(role_detail, weight=1)
        self.tree = self._table(role_list, (("role", "Role", 220), ("company", "Company", 130),
                                          ("now", "Now", 55), ("scenario", "What if", 65)))
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.select_job())
        self.detail = window._text(role_detail, 10)
        self.detail.pack(fill="both", expand=True, pady=6)
        self.learning_panes = ResponsiveSplit(learn, p)
        self.learning_panes.pack(fill="both", expand=True, pady=5)
        skill_list, skill_detail = ttk.Frame(self.learning_panes), ttk.Frame(self.learning_panes)
        self.learning_panes.add(skill_list, weight=1)
        self.learning_panes.add(skill_detail, weight=1)
        self.skills_tree = self._table(skill_list, (("skill", "Skill", 200), ("required", "Required by", 90), ("only", "Only skill gap", 110)))
        self.skills_tree.bind("<<TreeviewSelect>>", lambda _event: self.select_skill())
        actions = ButtonRow(skill_list, p)
        actions.pack(side="bottom", fill="x", pady=6, before=self.skills_tree.master)
        actions.add("Add skill to scenario", self.add_skill)
        actions.add("Open official guide", self.open_guide)
        self.practice = window._text(skill_detail, 12)
        self.practice.pack(fill="both", expand=True)
        self.forecast_chart = tk.Canvas(forecast, height=110, bg=p.PANEL, highlightthickness=0)
        self.forecast_chart.pack(fill="x", pady=5)
        self.forecast_chart.bind("<Configure>", lambda _event: self.draw_forecast())
        self.forecast_hint = DisplayVar(self, "Conditional model; no forecast without complete observation windows.")
        ttk.Label(forecast, textvariable=self.forecast_hint).pack(fill="x", pady=(0, 4))
        self.forecast_chart.bind("<Motion>", self.inspect_forecast)
        self.forecast = window._text(forecast, 9)
        self.forecast.pack(fill="both", expand=True)
        routes = ButtonRow(forecast, p)
        routes.pack(fill="x", pady=6)
        routes.add("Inspect source access", lambda: window.tabs.select(5))
        routes.add("Search free APIs", self.search_free)
        self.search_term = StringVar(self, "")
        suggestions = tk.Frame(forecast, bg=p.BLACK)
        suggestions.pack(fill="x", pady=5)
        self.query_picker = ttk.Combobox(suggestions, textvariable=self.search_term, state="readonly")
        self.query_picker.pack(side="left", fill="x", expand=True)
        ttk.Button(suggestions, text="Use suggested query", command=self.use_query).pack(side="right", padx=5)

    def _table(self, parent, columns):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=(5, 0))
        keys = [item[0] for item in columns]
        tree = SafeTreeview(frame, columns=keys, show="headings", height=5, selectmode="browse")
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=50, stretch=key in {"role", "company", "skill"})
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        return tree

    def refresh(self):
        self._cancel.set()
        try:
            hours = float(self.hours.get())
        except ValueError:
            self.window._request_ids["radar"] = self.window._request_ids.get("radar", 0) + 1
            self.fail("Enter a practice budget from 1 to 40 hours.")
            return
        skills = [item.strip() for item in self.skills.get().split(",") if item.strip()]
        query, location = self.window.query.get().strip(), self.window.location.get().strip()
        self._cancel = threading.Event()
        cancel = self._cancel
        self.note.set("Reading saved evidence and comparing the scenario…")
        self.window.run("radar", lambda: self.window.center.career_radar(skills, hours, query, location,
                        check=lambda: not cancel.is_set() and not self.window._closed))

    def fail(self, message):
        self.note.set(("Previous snapshot retained. " if self.report else "") + str(message))

    def show(self, value):
        self.report = value
        coverage, scenario = value["coverage"], value["scenario"]
        self.metrics["roles"].set(str(coverage["jobs_considered"]))
        self.metrics["ready"].set(str(scenario["current_good_or_strong"]))
        self.metrics["delta"].set(f"{scenario['additional_good_or_strong']:+d}")
        self.note.set("Updated " + value["measured_at"][:16].replace("T", " ") + " UTC" + (" · 500-role limit reached" if coverage["truncated"] else "")
                      + (" · Read your CV to personalise this view" if not value["profile_ready"] else " · scenario: " + (", ".join(scenario["skills"]) or "no added skills"))
                      + " · view: " + (coverage["query"] or "all roles") + " / " + (coverage["location"] or "all locations"))
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.rows = {str(row["number"]): row for row in value["opportunities"]}
        for identifier, row in self.rows.items():
            self.tree.insert("", "end", iid=identifier, values=(row["title"], row["company"], row["score"], row["scenario_score"]))
        if self.rows:
            self.tree.selection_set(selected[0] if selected and selected[0] in self.rows else next(iter(self.rows)))
            self.select_job()
        else:
            self.window.say(self.detail, "No scored roles match this view. Search public feeds or broaden the current filters.")
        self.skills_tree.delete(*self.skills_tree.get_children())
        for item in value["recommendations"]:
            self.skills_tree.insert("", "end", iid=item["skill"], values=(item["skill"], item["required_by"], item["only_skill_gap"]))
        if value["recommendations"]:
            self.skills_tree.selection_set(value["recommendations"][0]["skill"])
        self.select_skill()
        outcomes = value["outcomes"]
        lines = [f"Observed interview-reaching responses: {outcomes['interview_reached']} / {outcomes['submitted']} submissions.",
                 outcomes["population"],
                 f"{outcomes['matured']} complete {outcomes['window_days']}-day windows; {outcomes['recent_excluded']} recent and "
                 f"{outcomes['unknown_timing_excluded']} unknown-timing records excluded."]
        prediction = outcomes["forecast"]
        if prediction:
            low, high = prediction["predictive_interval"]
            lines += [f"Conditional next {prediction['applications']} comparable applications: expected {prediction['expected_interview_reaching']:g} "
                      f"to reach interviews within {outcomes['window_days']} days; 90% model range {low}–{high}."]
        else:
            lines += ["Forecast unavailable: " + outcomes["reason"] + "."]
        lines += [outcomes["assumptions"], "", "Next actions:", *["• " + item for item in value["next_actions"]], "", "Source recovery:"]
        for name, item in value["sources"]["cooldowns"].items():
            lines.append(f"• {name}: {item['reason']}; retry at {item['retry_at']}.")
        for name, item in value["sources"]["last_observed"].items():
            if item.get("error"):
                lines.append(f"• {name}: last attempt failed — {str(item['error'])[:250]}")
        if not value["sources"]["cooldowns"]:
            lines.append("No active API cooldown is recorded. This is not a live availability check.")
        lines += ["", "A provider challenge requires manual completion in the preserved browser session. Other allowed public sources can continue.", value["basis"]]
        self.window.say(self.forecast, "\n".join(lines))
        self.draw_forecast()
        terms = value["query_suggestions"]
        self.query_picker.configure(values=terms)
        if self.search_term.get() not in terms:
            self.search_term.set(terms[0] if terms else "")

    def select_job(self):
        selection = self.tree.selection()
        if not selection or selection[0] not in self.rows:
            return
        row = self.rows[selection[0]]
        lines = [f"#{row['number']} {row['title']} · {row['company']}", row["url"],
                 f"Score {row['score']} → scenario {row['scenario_score']} ({row['score_change']:+d}); {row['verdict']}.",
                 "Last observed: " + (row["last_observed"] or "unknown"),
                 "Next step: " + row["action"], "", "Eligibility:", *(row["blocked"] or ["No recognised blocker in the current profile; confirm with the employer."]),
                 "", "Requirements missing from the profile:"]
        for item in row["requirements"]:
            lines += ["• " + item["skill"], "  Posting: " + (item["posting_excerpt"] or "No exact line retained; inspect the posting."), item["basis"]]
        if not self.detailed.get():
            lines += ["", "Remaining in scenario: " + (", ".join(row["remaining_required"]) or "none recognised"),
                      "", "Confirm:", *(row["questions"] or ["Live vacancy, employer identity and application instructions."]),
                      "", "Select Show full requirement and score analysis for experience, language, location and score details."]
            if row["warnings"]:
                lines += ["", "Posting warning signs (review; not proof of a scam):", *row["warnings"]]
            self.window.say(self.detail, "\n".join(lines))
            return
        constraints = row["constraints"]
        lines += ["", "Other posting requirements and unknowns:",
                  f"Experience asked: {constraints['asked_years'] if constraints['asked_years'] is not None else 'not extracted'} years; "
                  f"profile: {constraints['profile_years'] if constraints['profile_years'] is not None else 'unknown'}.",
                  "Languages: " + (", ".join(constraints["languages"]) or "none extracted; verify"),
                  "Education mentioned: " + ("; ".join(constraints["education"]) or "not extracted; verify"),
                  "Work mode: " + constraints["work_mode"] + "; regions: " + (", ".join(constraints["regions"]) or "none extracted"),
                  "Sponsorship: " + constraints["sponsorship"],
                  "Publisher expiry: " + (constraints["publisher_expiry"] or "not provided")]
        if row["warnings"]:
            lines += ["", "Posting warning signs (review; not proof of a scam):", *row["warnings"]]
        lines += ["Remaining in scenario: " + (", ".join(row["remaining_required"]) or "none recognised"),
                  "", "Why this score:", *[f"• {name}: {reason}" for name, reason in row["score_reasons"].items()],
                  "", "Confirm:", *(row["questions"] or ["Live vacancy, employer identity and application instructions."])]
        self.window.say(self.detail, "\n".join(lines))

    def draw_forecast(self):
        canvas, p = self.forecast_chart, self.p
        canvas.delete("all")
        prediction = ((self.report or {}).get("outcomes") or {}).get("forecast")
        width = max(100, canvas.winfo_width())
        if not prediction:
            self.forecast_hint.set("Conditional model; at least 10 complete observation windows are required.")
            canvas.create_text(width / 2, 52, text="Forecast awaits 10 complete observation windows", fill=p.MUTED,
                               font=("Segoe UI", 10), width=max(100, width - 30))
            return
        probabilities = prediction["probabilities"]
        low, high = prediction["predictive_interval"]
        step = (width - 40) / len(probabilities)
        maximum = max(probabilities) or 1
        canvas.create_text(20, 7, text=f"Peak mass {maximum:.0%}", fill=p.MUTED, font=("Segoe UI", 8), anchor="w")
        for k, probability in enumerate(probabilities):
            left = 20 + k * step
            canvas.create_rectangle(left + 2, 82 - probability / maximum * 65, left + step - 2, 82,
                                    fill=p.HOLO if low <= k <= high else p.LINE, outline="", tags=("forecast_bar",))
            canvas.create_text(left + step / 2, 96, text=str(k), fill=p.MUTED, font=("Segoe UI", 9))
        self.forecast_hint.set(f"Conditional interview counts out of {prediction['applications']} comparable applications; hover a bar for model mass.")

    def inspect_forecast(self, event):
        prediction = ((self.report or {}).get("outcomes") or {}).get("forecast")
        if prediction:
            width = max(100, self.forecast_chart.winfo_width())
            k = max(0, min(len(prediction["probabilities"]) - 1,
                           int((event.x - 20) / ((width - 40) / len(prediction["probabilities"])))))
            self.forecast_hint.set(f"{k} interview-reaching responses: {prediction['probabilities'][k]:.1%} model mass. "
                                   "Conditional model, not measured forecasting accuracy.")

    def select_skill(self):
        selection = self.skills_tree.selection()
        skill = selection[0] if selection else ""
        lines = ["Weekly practice budget; this does not predict mastery."]
        for item in (self.report or {}).get("practice_sprint", []):
            if not skill or item["skill"] == skill:
                lines += [f"\n{item['skill']} · {item['minutes_allocated']} minutes allocated", item["official_docs"] or "Find the official guide.",
                          *["• " + task for task in item["tasks"]], item["completion"]]
        if len(lines) == 1:
            item = next((item for item in (self.report or {}).get("recommendations", []) if item["skill"] == skill), None)
            lines += ["Select a skill, add it to your scenario and run the comparison to allocate practice time."]
            if item:
                lines += [item["official_docs"] or "Find the official guide.", item["exercise"]]
        self.window.say(self.practice, "\n".join(lines))

    def add_skill(self):
        selection = self.skills_tree.selection()
        if selection:
            values = [item.strip() for item in self.skills.get().split(",") if item.strip()]
            if selection[0] not in values:
                self.skills.set(", ".join([*values, selection[0]]))

    def open_guide(self):
        selection = self.skills_tree.selection()
        if selection:
            from app.jobs.insight import DOCS
            if url := DOCS.get(selection[0]):
                self.window.center.opener(url)
            else:
                self.note.set("No curated official guide is recorded for this skill.")

    def search_free(self):
        self.window.tabs.select(0)
        self.window.search()

    def use_query(self):
        if self.search_term.get():
            self.window.query.set(self.search_term.get())
            self.window.tabs.select(0)
            self.window.refresh_tab()
