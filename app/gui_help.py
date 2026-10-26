"""Help for someone new: what HAYAT can do, how to start, and what every button is for.

Two parts. ``CapabilityGuide`` is the "What can HAYAT do?" window: every area
from app/capabilities.py with what it is for, how to start, whether it is
ready (and what to do if not), example requests with a Try button that puts
them in the chat box, and the tools behind it. ``attach_tooltips`` gives the
window's buttons a one-line explanation on hover, so no button is a mystery.
"""
from __future__ import annotations

from app.gui_resources import StringVar

import tkinter as tk
from tkinter import ttk
from typing import Any

from app import capabilities
from app.gui_icons import apply_icons
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.gui_fonts import DISPLAY

#: What each button does, in a sentence a beginner understands. Keyed by the
#: button's visible text.
BUTTON_HELP: dict[str, str] = {
    'Other research tools': 'Open a related local or public research workspace. Entered fields are prepared; no search starts automatically.',
    'Report actions': 'Save or export a report, open authorized records, or request an owner-presence check.',
    'Operation view': 'Watch local desktop samples and real tool events. Open manually to move or pause the viewer.',
    'Private networking': 'Organize local contacts, inspect recorded evidence and prepare unsent introductions.',
    'Onion pages': 'Read a known public onion page through installed Tor and inspect its source evidence.',
    'Stop & correct': 'Stop the current task and prepare a correction before continuing.',
    'Emergency stop': 'Persistently halt tool execution. Explicitly resume when you are ready.',
    'All features': 'Search every feature, workspace and registered tool. Ctrl+Shift+A opens this window.',
    'Activity': 'See recorded tool actions, errors, sensor states and background monitoring. Ctrl+Shift+L opens the timeline.',
    'Stop task': 'Request cancellation of the current task and speech. Background monitoring has separate pause controls in Activity.',
    "Send": "Send what you typed to HAYAT. Enter does the same; Shift+Enter starts a new line.",
    "God mode": "Use Deep + Adversarial reasoning for decomposition, alternatives and self-checking. Replies can take longer; this does not guarantee correctness.",
    "Show checks": "Expand the actual tool checks and final-goal evidence behind this result.",
    "Hide checks": "Collapse result evidence to a compact card.",
    "+": "Attach a file for HAYAT to read: a PDF, Word or Excel file, a picture, or code.",
    "Start voice": "Talk to HAYAT out loud. Hold Ctrl+Space to speak, or use hands-free in Voice settings.",
    "Stop voice": "Stop listening and speaking.",
    "Stop": "Stop what HAYAT is doing right now.",
    "Attach file": "Give HAYAT a file to read and remember.",
    "Create image": "Make a picture from a description. It runs on this computer.",
    "Video clips": "Open download, playback and clipping controls inside chat. Pasted video links also get action cards. Play with sound, choose quality, and make ranked clips.",
    "Edit / result link": "Edit exact clip times, framing, speed, picture and audio settings inside chat, or create a local preview link.",
    "Export edit": "Save a new MP4 with the selected edits. The source is preserved; each export supports up to three minutes.",
    "Create result link": "Create a read-only preview and download link for this result. It works on this computer while HAYAT is running.",
    "Research": "Search the internet about a topic and report back with sources.",
    "More tools": "More actions: PowerShell, CMD, vision and others.",
    "More actions": "More actions: PowerShell, CMD, Research and Vision.",
    "Chat": "The conversation with HAYAT.",
    "Documents": "Files you gave HAYAT and what it understood from them.",
    "Web search": "Look something up on the internet.",
    "Code": "Ask for a program or explain an error.",
    "Voice": "Voice settings: microphone, speaker, push-to-talk and hands-free.",
    "Commands": "Search every action and window by name (Ctrl+K).",
    "Latest ↓": "Jump to the newest message.",
    "Find": "Search the conversation (Ctrl+F).",
    "Copy chat": "Copy the whole conversation.",
    "Chat actions ▾": "Save the transcript, open Team workspace, or clear the visible chat.",
    "Team": "Open the specialist team workspace: progress, handoffs, evidence and stopping controls.",
    "Revenue": "Browse freelance, sales, content and revenue workflows, local drafts, experiments and recorded results.",
    "Save": "Save the conversation to a file.",
    "Clear": "Clear the screen. Nothing is deleted from HAYAT's memory.",
    "3D workspace": "Back to the 3D workspace on the chat screen.",
    "Conversation": "Back to the conversation.",
    "Brain 5D": "See HAYAT's parts, how they connect, what they did over time, and whether each was checked.",
    "Eye": "Open the camera view. The camera turns on only when you ask.",
    "Studios ▾": "Windows for Voice, Models, languages, searches, global intelligence, knowledge, security and Business.",
    "Reset": "Put the 3D view back to its start position.",
    "Pause motion": "Stop the 3D view from moving.",
    "Enable motion": "Let the 3D view move again.",
    "Hide overview": "Hide the side panels to give the chat more room.",
    "Show overview": "Show the side panels with resources, models and the console.",
    "Focus Mode": "Hide everything except the conversation.",
    "Restore Rails": "Bring the side panels back.",
    "Help": "See everything HAYAT can do, with examples you can try.",
    "What can HAYAT do?": "See everything HAYAT can do, with examples you can try.",
    "How it works": "A step-by-step tour of how one request travels through HAYAT.",
    "List view": "The same parts as a list instead of 3D.",
    "Refresh": "Read the latest state again.",
    "Self Check": "Check every part of HAYAT and colour each one by the result.",
    "Benchmark": "Measure how fast HAYAT's parts respond.",
    "Focus links": "Show only the connections of the chosen part.",
    "Reset view": "Put the brain back to its start position.",
    "15 min": "Show the last 15 minutes of activity.",
    "1 hour": "Show the last hour of activity.",
    "6 hours": "Show the last 6 hours of activity.",
    "24 hours": "Show the last day of activity.",
    "Evidence on": "Rings around each part: green checked, gold has gaps, grey not checked yet.",
    "Evidence off": "Show the evidence rings again.",
    "Heat on": "Make busier parts glow.",
    "Heat off": "Turn the glow off.",
    "Pause 3D": "Stop the brain turning while you look.",
    "Move 3D": "Let the brain turn again.",
    "Fit": "Fit the whole timeline in view.",
    "Normal": "Change the replay speed.",
    "Replay": "Play back what happened in the chosen time window.",
    "Live": "Back to what is happening now.",
    "Details": "More detail about this computer's resources.",
    "Manage models": "See, choose and download AI models.",
    "Run": "Run the command typed in the console.",
    "Email setup": "Connect your own email so HAYAT can send invoices. The password stays in Windows.",
    "Open PDF": "Open the selected invoice or quote.",
    "Email invoice": "Send the selected invoice with its PDF attached. You approve before it is sent.",
    "Mark paid": "Record that the selected invoice was paid in full.",
    "Open documents folder": "Open the folder with your invoices, quotes and exports.",
    "Export year for taxes": "Save this year's income, expenses and invoices as an Excel file.",
    "Set up withdrawal": "Choose where withdrawals go. Only a name and last 4 digits are kept.",
    "Withdraw": "Record taking money out of your wallet. You make the actual transfer.",
    "Record income": "Add money you earned somewhere else, like a Fiverr or Upwork payout.",
    "Mark transfer done": "Confirm you made the selected withdrawal's transfer.",
    "Cancel withdrawal": "Cancel the selected pending withdrawal; the money is available again.",
    "Invoices": "Your invoices, what each is for and what is still owed.",
    "Clients": "The people and companies you work for.",
    "Leads": "Work opportunities you are following up.",
    "Expenses": "What you spent for the business.",
    "Wallet": "Everything you earned, what is available, and your withdrawals.",
    "Close": "Close this window.",
    "How HAYAT works": "The steps every request goes through, from your words to a checked answer.",
    "What HAYAT won't do": "The things HAYAT refuses or always asks about, and why. Enforced in its code.",
    "Income streams": "Every source your money came from, its share and its change, from your records only.",
    "Today's brief": "What needs your attention today, read from your books. Nothing is sent or changed.",
    "Publish website": "Put your website online. You see exactly what goes online and confirm first.",
    "Publishing setup": "Connect your free Netlify account so HAYAT can publish your website when you confirm.",
    "Confirm and do it": "Do this now. Only after you have checked every detail above.",
    "Cancel action": "Drop this action. Nothing is sent.",
    "Looks right": "You checked the result and it is correct.",
    "Something's wrong": "The result is not right; HAYAT records it and helps you fix it.",
    "Launch": "Start a business or service: ideas, a 12-step plan, prices, website and marketing kit.",
    "Start a business": "Name your business and what it offers; HAYAT makes a launch plan.",
    "Business ideas": "Businesses you could start, matched to your skills, with how to check demand.",
    "Add service": "Add something you sell, with its price and what is included.",
    "Price calculator": "Work out the hourly rate you need from the money you want to keep each month.",
    "Quote from service": "Make a quote PDF for a client from one of your services.",
    "Mark step done": "Tick off the selected launch step.",
    "Undo step": "Untick the selected launch step.",
    "Make website": "Write a one-page website for your business and open it. It is not published.",
    "Marketing kit": "Write your pitch, outreach email, gig listing and posts, ready to edit.",
}


class Tooltip:
    """A small dark label that appears after a short hover."""

    DELAY_MS = 550

    def __init__(self, widget: tk.Misc, text: str, palette: Any) -> None:
        self.widget, self.text, self.palette = widget, text, palette
        self.tip: tk.Toplevel | None = None
        self._after: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<MouseWheel>", self.hide, add="+")
        widget.bind("<Unmap>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")
        widget._hayat_tooltip = self  # type: ignore[attr-defined]

    def _schedule(self, _event: Any = None) -> None:
        self._cancel()
        self._after = self.widget.after(self.DELAY_MS, self.show)

    def _cancel(self) -> None:
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def show(self) -> None:
        self._cancel()
        if self.tip is not None or not self.widget.winfo_exists() or not self.widget.winfo_viewable():
            return
        p = self.palette
        self.tip = tk.Toplevel(self.widget)
        self.tip.withdraw()
        self.tip.wm_overrideredirect(True)
        try:
            self.tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(self.tip, text=self.text, bg=p.PANEL_2, fg=p.WHITE, font=("Segoe UI", 9), justify="left",
                 wraplength=320, padx=10, pady=6, highlightthickness=1, highlightbackground=p.GOLD_SOFT).pack()
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip.wm_geometry(f"+{x}+{y}")
        self.tip.deiconify()

    def hide(self, _event: Any = None) -> None:
        self._cancel()
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None


def _button_text(widget: tk.Misc) -> str:
    try:
        return " ".join(str(widget.cget("text")).split())
    except tk.TclError:
        return ""


def attach_tooltips(root: tk.Misc, palette: Any, extra: dict[str, str] | None = None) -> int:
    """Give every known button under ``root`` its explanation on hover. Returns how many were added."""
    texts = {**BUTTON_HELP, **(extra or {})}
    added = 0
    stack = [root]
    while stack:
        widget = stack.pop()
        stack.extend(widget.winfo_children())
        if widget.winfo_class() not in {"TButton", "Button", "TMenubutton", "Menubutton"}:
            continue
        text = _button_text(widget)
        if text in texts and getattr(widget, "_hayat_tooltip", None) is None:
            Tooltip(widget, texts[text], palette)
            added += 1
    return added


GETTING_STARTED = (
    "1. Type what you want in the box at the bottom, in your own words, and press Send. Or press Start voice and speak.",
    "2. HAYAT does the work and shows you the result. It only sends, installs or changes things when you ask, and checks with you before risky steps.",
    "3. Not sure? Ask \"can you ...?\" about anything, or pick an area below and press Try.",
)

STATE_WORDS = {"ready": "Ready", "setup": "Needs setup", "off": "Off"}


class CapabilityGuide(tk.Toplevel):
    """The "What can HAYAT do?" window."""

    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        self.title("HAYAT · What can HAYAT do?")
        self.transient(gui.root)
        self.configure(bg=gui.BLACK)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: (self.destroy(), "break")[1])
        self.rows: dict[str, dict[str, Any]] = {}
        self._build()
        apply_icons(self, self.palette)
        attach_tooltips(self, self.palette)
        place_over(self, gui.root, (1000, 760))
        self.deiconify()
        self.lift()
        self.refresh()

    def _build(self) -> None:
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=18, pady=12)
        header.pack(fill="x")
        titles = tk.Frame(header, bg=p.BLACK)
        titles.pack(side="left")
        tk.Label(titles, text="What can HAYAT do?", bg=p.BLACK, fg=p.WHITE,
                 font=(DISPLAY, 17)).pack(anchor="w")
        from app.identity import CREATOR, PROJECT_URL
        # The creator's credit, kept by the licence's attribution term (NOTICE) in every copy.
        tk.Label(titles, text=f"Created by {CREATOR} · free, open-source software (AGPL-3.0) · {PROJECT_URL}",
                 bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        ttk.Button(header, text="Close", style="Hayat.TButton", command=self.destroy).pack(side="right")
        ttk.Button(header, text="What HAYAT won't do", style="Hayat.TButton",
                   command=self.show_limits).pack(side="right", padx=(0, 6))
        ttk.Button(header, text="How HAYAT works", style="Hayat.TButton",
                   command=self.show_how).pack(side="right", padx=(0, 6))

        start = tk.Frame(self, bg=p.PANEL, padx=16, pady=10, highlightthickness=1, highlightbackground=p.LINE)
        start.pack(fill="x", padx=18, pady=(0, 10))
        tk.Label(start, text="Getting started", bg=p.PANEL, fg=p.GOLD_SOFT, font=("Segoe UI Semibold", 11),
                 anchor="w").pack(fill="x")
        for line in GETTING_STARTED:
            tk.Label(start, text=line, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 10), anchor="w", justify="left",
                     wraplength=920).pack(fill="x", pady=(2, 0))

        search = tk.Frame(self, bg=p.BLACK, padx=18)
        search.pack(fill="x")
        tk.Label(search, text="Find an ability:", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI", 10)).pack(side="left")
        self.query = StringVar(self, "")
        entry = ttk.Entry(search, textvariable=self.query, width=40)
        entry.pack(side="left", padx=8)
        entry.bind("<KeyRelease>", lambda _event: self.refresh())
        self.count_var = StringVar(self, "")
        tk.Label(search, textvariable=self.count_var, bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9)).pack(side="left")

        body = tk.Frame(self, bg=p.BLACK, padx=18, pady=10)
        body.pack(fill="both", expand=True)
        listing = tk.Frame(body, bg=p.PANEL)
        listing.pack(side="left", fill="y")
        self.areas = ttk.Treeview(listing, columns=("state",), show="tree", selectmode="browse", height=18)
        self.areas.column("#0", width=270, anchor="w")
        self.areas.column("state", width=100, anchor="w")
        bar = ttk.Scrollbar(listing, orient="vertical", command=self.areas.yview, style="Hayat.Vertical.TScrollbar")
        self.areas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.areas.tag_configure("ready", foreground=p.WHITE)
        self.areas.tag_configure("setup", foreground=p.GOLD_SOFT)
        self.areas.tag_configure("off", foreground=p.MUTED)
        self.areas.pack(side="left", fill="y")
        self.areas.bind("<<TreeviewSelect>>", lambda _event: self.show(self._selected()))

        detail = tk.Frame(body, bg=p.PANEL, padx=18, pady=14, highlightthickness=1, highlightbackground=p.LINE)
        detail.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self.title_var = StringVar(self, "")
        tk.Label(detail, textvariable=self.title_var, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 15),
                 anchor="w").pack(fill="x")
        self.state_var = StringVar(self, "")
        self.state_label = tk.Label(detail, textvariable=self.state_var, bg=p.PANEL, fg=p.OLIVE_SOFT,
                                    font=("Segoe UI Semibold", 10), anchor="w", justify="left", wraplength=480)
        self.state_label.pack(fill="x", pady=(2, 8))
        self.summary_var = StringVar(self, "")
        tk.Label(detail, textvariable=self.summary_var, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 11), anchor="w",
                 justify="left", wraplength=480).pack(fill="x")
        tk.Label(detail, text="How to start", bg=p.PANEL, fg=p.GOLD_SOFT, font=("Segoe UI Semibold", 10),
                 anchor="w").pack(fill="x", pady=(12, 0))
        self.how_var = StringVar(self, "")
        tk.Label(detail, textvariable=self.how_var, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 10), anchor="w",
                 justify="left", wraplength=480).pack(fill="x")
        tk.Label(detail, text="Try saying", bg=p.PANEL, fg=p.GOLD_SOFT, font=("Segoe UI Semibold", 10),
                 anchor="w").pack(fill="x", pady=(12, 2))
        self.examples = tk.Frame(detail, bg=p.PANEL)
        self.examples.pack(fill="x")
        self.open_button = ttk.Button(detail, text="", style="Hayat.TButton", command=self.open_window)
        self.behind_label = tk.Label(detail, text="Behind the scenes", bg=p.PANEL, fg=p.MUTED,
                                     font=("Segoe UI Semibold", 9), anchor="w")
        self.behind_label.pack(fill="x", pady=(14, 0))
        self.tools_text = DarkScrolledText(detail, height=4, wrap="word", bg=p.PANEL, fg=p.MUTED, relief="flat",
                                           font=("Segoe UI", 9), bd=0, highlightthickness=0)
        self.tools_text.pack(fill="both", expand=True)
        self.tools_text.configure(state="disabled")
        self.current: capabilities.Capability | None = None

    # ------------------------------------------------------------------ data
    def _selected(self) -> str | None:
        selected = self.areas.selection()
        return selected[0] if selected else None

    def refresh(self) -> None:
        query = self.query.get().strip().lower()
        runtime = self.gui.runtime
        shown = []
        for capability in capabilities.CATALOG:
            haystack = " ".join((capability.title, capability.summary, " ".join(capability.keywords),
                                 " ".join(capability.examples))).lower()
            if query and not all(word in haystack for word in query.split()):
                continue
            state, _note = capabilities.readiness(capability, runtime)
            self.rows[capability.key] = {"state": state}
            shown.append((capability, state))
        keep = self._selected()
        self.areas.delete(*self.areas.get_children())
        for capability, state in shown:
            self.areas.insert("", "end", iid=capability.key, text=f"{capability.icon}  {capability.title}",
                              values=(STATE_WORDS[state] if state != "ready" else "",), tags=(state,))
        self.count_var.set(f"{len(shown)} of {len(capabilities.CATALOG)} areas")
        target = keep if keep and self.areas.exists(keep) else (shown[0][0].key if shown else None)
        if target:
            self.areas.selection_set(target)
            self.show(target)

    def show(self, key: str | None) -> None:
        if not key or key not in capabilities.BY_KEY:
            return
        p = self.palette
        capability = capabilities.BY_KEY[key]
        self.current = capability
        info = capabilities.describe(capability, self.gui.runtime)
        self.title_var.set(f"{capability.icon}  {capability.title}")
        note = info["note"]
        if note.startswith("Ready."):
            note = note[len("Ready."):].strip()
        self.state_var.set(f"{STATE_WORDS[info['state']]}. {note}".strip())
        self.state_label.configure(fg={"ready": p.OLIVE_SOFT, "setup": p.GOLD, "off": p.MUTED}[info["state"]])
        self.summary_var.set(capability.summary)
        self.how_var.set(capability.how)
        for child in self.examples.winfo_children():
            child.destroy()
        for example in capability.examples:
            row = tk.Frame(self.examples, bg=p.PANEL)
            row.pack(fill="x", pady=2)
            ttk.Button(row, text="Try", style="HayatAccent.TButton",
                       command=lambda text=example: self.try_example(text)).pack(side="left")
            tk.Label(row, text=f"“{example}”", bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 10), anchor="w",
                     justify="left", wraplength=420).pack(side="left", padx=8, fill="x")
        if capability.opens:
            self.open_button.configure(text=f"Open {capability.opens_label}")
            self.open_button.pack(anchor="w", pady=(10, 0), before=self.behind_label)
        else:
            self.open_button.pack_forget()
        tools = info["tools"]
        self.tools_text.configure(state="normal")
        self.tools_text.delete("1.0", "end")
        self.tools_text.insert("end", (f"{len(tools)} tool{'s' if len(tools) != 1 else ''} HAYAT uses for this: "
                                       + ", ".join(tools)) if tools else
                               "No special tool: HAYAT does this with its AI model and its own windows.")
        self.tools_text.configure(state="disabled")

    # ------------------------------------------------------------------ about HAYAT itself
    def show_how(self) -> tk.Toplevel:
        from app import self_knowledge

        return self._info("How HAYAT works", self_knowledge.how_i_work_answer())

    def show_limits(self) -> tk.Toplevel:
        from app import self_knowledge

        return self._info("What HAYAT won't do", self_knowledge.limits_answer(self.gui.runtime))

    def _info(self, title: str, text: str) -> tk.Toplevel:
        p = self.palette
        window = tk.Toplevel(self)
        window.title(f"HAYAT · {title}")
        window.transient(self)
        window.configure(bg=p.BLACK, padx=16, pady=12)
        window.bind("<Escape>", lambda _event: (window.destroy(), "break")[1])
        tk.Label(window, text=title, bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 14), anchor="w").pack(fill="x")
        body = DarkScrolledText(window, height=20, width=90, wrap="word", bg=p.PANEL, fg=p.WHITE, relief="flat",
                                font=("Segoe UI", 10), padx=12, pady=10)
        body.pack(fill="both", expand=True, pady=(8, 8))
        body.insert("1.0", text)
        body.configure(state="disabled")
        ttk.Button(window, text="Close", style="Hayat.TButton", command=window.destroy).pack(side="right")
        window.body = body
        self.info_window = window
        return window

    # ------------------------------------------------------------------ actions
    def try_example(self, text: str) -> None:
        """Put the example in the chat box, ready to send or change."""
        use = getattr(self.gui, "use_example", None)
        if callable(use):
            use(text)
        self.destroy()

    def open_window(self) -> None:
        if self.current is None or not self.current.opens:
            return
        target = self.current.opens
        if target.startswith("view:"):
            self.gui.show_view(target[5:])
        elif target.startswith("studio:"):
            self.gui._open_control_studio(target[7:])
        else:
            getattr(self.gui, target)()
