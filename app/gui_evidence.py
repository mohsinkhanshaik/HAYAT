"""Compact result evidence: measured checks, not an invented accuracy score."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def outcome_evidence(outcome):
    history = list(getattr(outcome, 'history', []) or [])
    criteria = list(getattr(outcome, 'success_criteria', []) or [])
    goal = getattr(outcome, 'goal_verification', {}) or {}
    complete = getattr(outcome, 'complete', False) is True
    checked = sum(row.get('verification', {}).get('passed') is True for row in history)
    explicit = bool(criteria) and goal.get('passed') is True
    customer_pending = any(row.get('tool') in {'customer_work', 'customer_quote', 'customer_invoice'} and (row.get('result') or {}).get('success')
                           and ((row.get('result') or {}).get('output') or {}).get('prepared') is False for row in history)
    if getattr(outcome, 'needs_user_input', False) or customer_pending:
        label, tone = 'Your input is needed', 'attention'
        if customer_pending:
            label = 'Customer work paused or needs input'
    elif not complete:
        label, tone = 'Result incomplete', 'attention'
    elif explicit:
        label, tone = 'Goal verified against its checks', 'verified'
    else:
        label, tone = 'Steps checked · final goal has no separate check', 'limited'
    tool_time = 0
    details = []
    for index, row in enumerate(history, 1):
        check = row.get('verification') or {}
        result = row.get('result') or {}
        try:
            tool_time += max(0, int(result.get('duration_ms') or 0))
        except (TypeError, ValueError):
            pass
        if index > len(history) - 20:
            name = str(row.get('description') or row.get('tool') or 'Step').strip()
            details.append(f'{"✓" if check.get("passed") is True else "✗"} {index}. {name}\n'
                           + str(check.get('reason') or result.get('error') or 'No check was recorded.')[:400])
    for row in goal.get('evidence', []) if isinstance(goal.get('evidence'), list) else []:
        criterion, verification = row.get('criterion', {}), row.get('verification', {})
        details.append('Goal: ' + str(criterion.get('description') or criterion.get('type') or 'Final state')
                       + '\n' + str(verification.get('reason') or '')[:400])
    if not explicit and complete:
        details.append('Tool checks passed. There was no independent final-goal criterion; this is not a measured answer-accuracy score.')
    if not complete and getattr(outcome, 'summary', ''):
        details.append(str(outcome.summary)[-1200:])
    from app.safety.sensitive import redact_text
    detail = redact_text('\n\n'.join(details) or 'No tool evidence was recorded.')[0]
    return {'label': label, 'tone': tone, 'checked': checked, 'attempts': len(history),
            'tool_ms': tool_time, 'detail': detail, 'goal_verified': complete and explicit and not customer_pending}


class EvidenceCard(tk.Frame):
    def __init__(self, parent, gui, evidence):
        super().__init__(parent, bg=gui.PANEL_2, padx=12, pady=9,
                         highlightthickness=1, highlightbackground=gui.LINE)
        self.gui = gui
        accent = gui.OLIVE_SOFT if evidence['tone'] == 'verified' else gui.GOLD if evidence['tone'] == 'attention' else gui.MUTED
        tk.Label(self, text=evidence['label'], bg=gui.PANEL_2, fg=accent,
                 anchor='w', font=('Segoe UI Semibold', 10), wraplength=535).pack(fill='x')
        row = tk.Frame(self, bg=gui.PANEL_2)
        row.pack(fill='x', pady=(4, 0))
        summary = f'{evidence["checked"]}/{evidence["attempts"]} tool checks passed'
        if evidence['tool_ms']:
            summary += f' · {evidence["tool_ms"] / 1000:.2f}s tool time'
        tk.Label(row, text=summary, bg=gui.PANEL_2, fg=gui.MUTED, anchor='w').pack(side='left')
        self.toggle = ttk.Button(row, text='Show checks', command=self.toggle_details)
        self.toggle.pack(side='right', padx=(12, 0))
        self.details = tk.Text(self, width=66, height=7, wrap='word', bg=gui.BLACK, fg=gui.WHITE,
                               relief='flat', padx=8, pady=8, font=('Segoe UI', 9), takefocus=True)
        self.details.insert('1.0', evidence['detail'])
        self.details.configure(state='disabled')

    def toggle_details(self):
        if self.details.winfo_manager():
            self.details.pack_forget()
            self.toggle.configure(text='Show checks')
        else:
            self.details.pack(fill='both', expand=True, pady=(8, 0))
            self.toggle.configure(text='Hide checks')
