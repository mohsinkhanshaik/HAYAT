"""Interactive recorded evidence and an explicit research-to-invention draft handoff."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from urllib.parse import urlsplit

from app.gui_output import DisplayVar, SafeTreeview
from app.gui_presentation import safe_text
from app.gui_resources import StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over


def evidence_records(result):
    """Link only recorded excerpts to matching documents; never infer support from a URL."""
    data = result.to_dict() if callable(getattr(result, 'to_dict', None)) else result if isinstance(result, dict) else {}
    nodes, urls, edges = {}, {}, []
    sources, claims = data.get('sources') or [], data.get('claims') or []
    for index, source in enumerate(sources[:200]):
        key = f'source:{index}'
        url = str(source.get('final_url') or source.get('url') or '')
        nodes[key] = {'kind': 'Source', 'label': str(source.get('title') or url or 'Untitled source'),
                      'record': source, 'url': url}
        for field in ('url', 'final_url', 'canonical_url'):
            if source.get(field):
                urls.setdefault(str(source[field]), key)
    for index, claim in enumerate(claims[:200]):
        key = f'claim:{index}'
        nodes[key] = {'kind': 'Claim', 'label': str(claim.get('text') or 'Untitled claim'), 'record': claim}
        for field, stance in (('evidence', 'Recorded evidence'), ('contradictions', 'Contradiction')):
            for item in (claim.get(field) or [])[:100]:
                source = urls.get(str(item.get('source_url') or ''))
                if source:
                    edges.append({'source': source, 'target': key, 'kind': stance,
                                  'support_type': str(item.get('support_type') or 'UNSUPPORTED'), 'record': item})
    return {'nodes': nodes, 'edges': edges, 'source_total': len(sources), 'claim_total': len(claims),
            'truncated': len(sources) > 200 or len(claims) > 200, 'run_id': str(data.get('run_id') or ''),
            'gaps': data.get('knowledge_gaps') or []}


class ResearchEvidenceMap(tk.Frame):
    def __init__(self, parent, palette, list_width=245):
        super().__init__(parent, bg=palette.BLACK)
        self.p, self.model, self.selected = palette, evidence_records(None), None
        self.status = DisplayVar(self, 'Select a completed search with recorded source evidence.')
        top = tk.Frame(self, bg=palette.BLACK)
        top.pack(fill='x', pady=(4, 6))
        #: Actions on the evidence being read (the window adds "Draft invention…" here, beside what it acts on).
        self.toolbar = tk.Frame(top, bg=palette.BLACK)
        self.toolbar.pack(side='right')
        summary = tk.Label(top, textvariable=self.status, bg=palette.BLACK, fg=palette.MUTED,
                           anchor='w', wraplength=700, justify='left')
        summary.pack(side='left', fill='x', expand=True)
        summary.bind('<Configure>', lambda event: summary.configure(wraplength=max(160, event.width - 6)))
        split = tk.PanedWindow(self, orient='horizontal', bg=palette.BLACK, sashwidth=6, bd=0)
        split.pack(fill='both', expand=True)
        left = tk.Frame(split, bg=palette.BLACK)
        right = tk.Frame(split, bg=palette.PANEL)
        split.add(left, minsize=150, width=list_width, stretch='always')
        split.add(right, minsize=180, stretch='always')
        self.query = StringVar(self)
        entry = ttk.Entry(left, textvariable=self.query)
        entry.pack(fill='x', pady=(0, 5))
        entry.bind('<Escape>', lambda _event: (self.query.set(''), 'break')[1])
        self.tree = SafeTreeview(left, columns=('kind',), selectmode='browse', height=4)
        self.tree.heading('#0', text='Evidence item')
        self.tree.column('#0', width=210, minwidth=110)
        self.tree.heading('kind', text='Type')
        self.tree.column('kind', width=65, minwidth=50, stretch=False)
        bar = ttk.Scrollbar(left, command=self.tree.yview)
        xbar = ttk.Scrollbar(left, orient='horizontal', command=self.tree.xview)
        xbar.pack(side='bottom', fill='x')
        bar.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.configure(yscrollcommand=bar.set, xscrollcommand=xbar.set)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.inspect())
        self.query.trace_add('write', lambda *_: self.filter())
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill='both', expand=True)
        map_page = tk.Frame(self.tabs, bg=palette.PANEL)
        details = tk.Frame(self.tabs, bg=palette.PANEL)
        self.tabs.add(map_page, text='Evidence map')
        self.tabs.add(details, text='Details')
        self.canvas = tk.Canvas(map_page, bg=palette.PANEL, highlightthickness=0, height=120)
        scroll = ttk.Scrollbar(map_page, command=self.canvas.yview)
        scroll.pack(side='right', fill='y')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.bind('<Configure>', lambda _event: self.draw())
        self.canvas.bind('<Button-1>', self.pick)
        self.canvas.bind('<MouseWheel>', lambda event: self.canvas.yview_scroll(-int(event.delta / 120), 'units'))
        self.details = DarkScrolledText(details, height=4, wrap='word', bg=palette.PANEL, fg=palette.WHITE,
                                        font=('Segoe UI', 10), padx=10, pady=8)
        self.details.pack(fill='both', expand=True)
        self._boxes = []

    def set_result(self, result):
        self.model = evidence_records(result)
        self.status.set(f"{self.model['source_total']} source documents · {self.model['claim_total']} claims · "
                        f"{len(self.model['edges'])} recorded evidence links" +
                        (' · first 200 sources and claims shown' if self.model['truncated'] else ''))
        self.filter()

    def filter(self):
        previous = self.selected
        self.tree.delete(*self.tree.get_children())
        words = self.query.get().casefold().split()
        for key, node in self.model['nodes'].items():
            searchable = ' '.join((node['label'], node['kind'], str(node.get('url', '')),
                                   str(node['record'].get('status', '')))).casefold()
            if all(word in searchable for word in words):
                self.tree.insert('', 'end', iid=key, text=node['label'], values=(node['kind'],))
        rows = self.tree.get_children()
        chosen = previous if previous in rows else rows[0] if rows else None
        if chosen:
            self.tree.selection_set(chosen)
            self.tree.focus(chosen)
        self.inspect()

    def inspect(self):
        selected = self.tree.selection()
        self.selected = selected[0] if selected else None
        node = self.model['nodes'].get(self.selected)
        lines = ['No structured evidence is available for this selection.']
        if node:
            record = node['record']
            lines = [node['label'], '', 'Recorded research run: ' + (self.model['run_id'] or 'not supplied')]
            if node['kind'] == 'Source':
                lines += ['URL: ' + node['url'], 'Retrieved: ' + str(record.get('retrieved_at') or 'not recorded'),
                          'Primary source: ' + ('yes' if record.get('primary') is True else 'not established')]
            else:
                from app.research.evidence_explanation import explain_claim, explanation_lines

                lines += ['Recorded claim status: ' + str(record.get('status') or 'UNVERIFIED')]
                sources = [item['record'] for item in self.model['nodes'].values() if item['kind'] == 'Source']
                lines += ['', *explanation_lines(explain_claim(record, sources))]
                for field in ('evidence', 'contradictions'):
                    for item in (record.get(field) or [])[:100]:
                        lines += ['', field.title() + ' · ' + str(item.get('support_type') or 'UNSUPPORTED'),
                                  str(item.get('source_url') or 'Source URL missing'),
                                  'Retrieved: ' + str(item.get('retrieved_at') or 'not recorded'), str(item.get('excerpt') or '')]
            lines += ['', 'Lines represent recorded excerpts. A citation alone does not validate an invention.']
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', safe_text('\n'.join(lines)))
        self.details.configure(state='disabled')
        self.draw()

    def draw(self):
        canvas, p = self.canvas, self.p
        canvas.delete('all')
        self._boxes = []
        node = self.model['nodes'].get(self.selected)
        if not node:
            canvas.create_text(14, 18, anchor='nw', text='Select a source or claim to inspect its links.', fill=p.MUTED, width=250)
            return
        links = [edge for edge in self.model['edges'] if self.selected in (edge['source'], edge['target'])]
        neighbors = list(dict.fromkeys(edge['target'] if edge['source'] == self.selected else edge['source'] for edge in links))
        width = max(220, canvas.winfo_width())
        height = max(140, canvas.winfo_height(), 64 * len(neighbors) + 40)
        canvas.configure(scrollregion=(0, 0, width, height))
        source_selected = node['kind'] == 'Source'
        x, other = (width*.22, width*.76) if source_selected else (width*.76, width*.22)
        cy = height / 2
        for index, key in enumerate(neighbors):
            y = 32 + 64 * index
            edges = [edge for edge in links if key in (edge['source'], edge['target'])]
            conflict = any(edge['kind'] == 'Contradiction' for edge in edges)
            start, end = ((x, cy), (other, y)) if source_selected else ((other, y), (x, cy))
            canvas.create_line(*start, *end, fill=p.ALERT if conflict else p.ROYAL_SOFT,
                               arrow='last', dash=(4, 3) if conflict else ())
            self._node(key, other, y, width*.4)
        self._node(self.selected, x, cy, width*.4, chosen=True)
        if not neighbors:
            canvas.create_text(width/2, height-20, text='No matching document-to-claim link recorded.', fill=p.MUTED,
                               width=width-20, font=('Segoe UI', 9))

    def _node(self, key, x, y, width, chosen=False):
        node, p = self.model['nodes'][key], self.p
        text = node['label']
        if node['kind'] == 'Source' and node.get('url'):
            text = urlsplit(node['url']).hostname or text
        text = text[:56] + ('…' if len(text) > 56 else '')
        self.canvas.create_oval(x-5, y-5, x+5, y+5, fill=p.GOLD if chosen else p.HOLO, outline=p.PANEL)
        self.canvas.create_text(x, y+9, text=safe_text(text), fill=p.GOLD_SOFT if chosen else p.WHITE,
                                anchor='n', width=width, font=('Segoe UI', 9))
        self._boxes.append((x-width/2, y-8, x+width/2, y+54, key))

    def pick(self, event):
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for x1, y1, x2, y2, key in reversed(self._boxes):
            if x1 <= x <= x2 and y1 <= y <= y2:
                if not self.tree.exists(key):
                    self.query.set('')
                self.tree.selection_set(key)
                self.tree.see(key)
                self.inspect()
                return


class EvidenceWindow(tk.Toplevel):
    """The Sources & claims view at a readable size; it shows what the Searches window has selected."""

    def __init__(self, studio):
        super().__init__(studio)
        self.withdraw()
        p = studio.palette
        self.title('HAYAT · Sources & claims')
        self.configure(bg=p.BLACK)
        self.minsize(640, 460)
        self.heading = tk.Label(self, text='', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 13), anchor='w',
                                justify='left')
        self.heading.pack(fill='x', padx=16, pady=(12, 0))
        self.heading.bind('<Configure>', lambda event: self.heading.configure(wraplength=max(200, event.width - 8)))
        # Room for the whole "Type" column beside the titles: at the tab's width it was cut off at the pane's edge.
        self.view = ResearchEvidenceMap(self, p, list_width=400)
        self.view.pack(fill='both', expand=True, padx=16, pady=(4, 14))
        self.bind('<Escape>', lambda _event: (self.destroy(), 'break')[1])
        place_over(self, studio, (980, 680))

    def show(self, job):
        finished = job is not None and job.status == 'done'
        self.heading.configure(text=safe_text(job.query) if job is not None else 'No search selected')
        self.view.set_result(job.result if finished else None)


class ResearchDraftDialog(tk.Toplevel):
    def __init__(self, studio, job):
        super().__init__(studio)
        self.withdraw()
        self.studio, self.job, self.preview, self.project = studio, job, None, None
        self._closed, self._busy, self._timer = False, False, None
        self._results = queue.Queue()
        p = studio.palette
        self.title('HAYAT · Research into an invention draft')
        self.configure(bg=p.BLACK)
        self.minsize(560, 480)
        self.bind('<Destroy>', self._dispose, add='+')
        tk.Label(self, text='Turn evidence into a testable idea', bg=p.BLACK, fg=p.WHITE,
                 font=('Segoe UI Semibold', 16), anchor='w').pack(fill='x', padx=16, pady=(14, 8))
        tk.Label(self, text='Describe what you want to build or test. Research stays attached as evidence.', bg=p.BLACK,
                 fg=p.MUTED, wraplength=650, justify='left', anchor='w').pack(fill='x', padx=16)
        self.goal = StringVar(self)
        entry = ttk.Entry(self, textvariable=self.goal)
        entry.pack(fill='x', padx=16, pady=(10, 6))
        tk.Label(self, text='Limits it must respect (optional; separate several with a semicolon)', bg=p.BLACK, fg=p.MUTED,
                 font=('Segoe UI', 9), anchor='w').pack(fill='x', padx=16)
        self.constraints = StringVar(self)
        limits = ttk.Entry(self, textvariable=self.constraints)
        limits.pack(fill='x', padx=16, pady=(2, 10))
        self.constraints.trace_add('write', self.invalidate)
        for field in (entry, limits):
            field.bind('<Return>', lambda _event: (self.review(), 'break')[1])
        row = tk.Frame(self, bg=p.BLACK)
        row.pack(fill='x', padx=16)
        self.review_button = ttk.Button(row, text='Review draft evidence', command=self.review)
        self.review_button.pack(side='left')
        self.create_button = ttk.Button(row, text='Create draft', command=self.create, state='disabled', style='HayatAccent.TButton')
        self.create_button.pack(side='left', padx=8)
        self.open_button = ttk.Button(row, text='Open in Invention', command=self.open_project, state='disabled')
        self.open_button.pack(side='left')
        self.status = DisplayVar(self, 'A draft is saved only after you review the evidence and press Create draft.')
        tk.Label(self, textvariable=self.status, bg=p.BLACK, fg=p.GOLD_SOFT, anchor='w', justify='left',
                 wraplength=650).pack(fill='x', padx=16, pady=10)
        self.report = DarkScrolledText(self, height=10, wrap='word', bg=p.PANEL, fg=p.WHITE,
                                       font=('Segoe UI', 10), padx=10, pady=8)
        self.report.pack(fill='both', expand=True, padx=16, pady=(0, 16))
        self.goal.trace_add('write', self.invalidate)
        place_over(self, studio, (740, 580))
        self.deiconify()
        entry.focus_set()

    def invalidate(self, *_):
        self.preview = None
        self.create_button.configure(state='disabled')

    def _limits(self):
        """The owner's own constraints, as typed; research text never supplies one."""
        return [part.strip() for part in self.constraints.get().split(';') if part.strip()]

    def review(self):
        if self._busy:
            return
        try:
            from app.invention_handoff import prepare_research_draft
            self.preview = prepare_research_draft(self.job.result, self.goal.get().strip(), self._limits())
            preview = self.preview
            text = (f"Goal: {preview['goal']}\n"
                    + ''.join(f'Limit: {limit}\n' for limit in preview['constraints'])
                    + f"\nResearch run: {preview['run_id']}\n"
                    f"{preview['source_count']} sources · {preview['claim_count']} claims\n"
                    f"{preview['traceable_claims']} traceable claims · {preview['conflicting_claims']} conflicting claims\n\n"
                    'Open questions\n' + '\n'.join('• ' + str(gap) for gap in preview['knowledge_gaps']) +
                    '\n\n' + str(preview['scope']) + '\n\nNo tests or acceptance values are inferred from source text.')
            self.report.configure(state='normal')
            self.report.delete('1.0', 'end')
            self.report.insert('1.0', safe_text(text))
            self.report.configure(state='disabled')
            self.create_button.configure(state='normal' if self.project is None else 'disabled')
            self.status.set('Evidence reviewed. Create saves a draft; fitting and research runs remain separate actions.')
        except Exception as exc:
            self.preview = None
            self.create_button.configure(state='disabled')
            self.status.set(str(exc))

    def create(self):
        if self._busy or self.preview is None or self.project is not None:
            return
        arguments = {'job_id': self.job.job_id, 'goal': self.preview['goal'],
                     'expected_evidence_hash': self.preview['evidence_hash']}
        if self.preview['constraints']:
            arguments['constraints'] = self.preview['constraints']
        self._busy = True
        self.create_button.configure(state='disabled')
        self.review_button.configure(state='disabled')
        self.status.set('Saving the reviewed draft…')
        runtime, results = self.studio.gui.runtime, self._results
        def work():
            try:
                from app.gui_features import run_declared_tool
                result = run_declared_tool(runtime, 'invention_from_research', arguments)
                results.put((result.output if result.success else None, result.error))
            except Exception as exc:
                results.put((None, str(exc)))
        threading.Thread(target=work, name='hayat-research-draft', daemon=True).start()
        self._timer = self.after(50, self.poll)

    def poll(self):
        self._timer = None
        if self._closed:
            return
        try:
            project, error = self._results.get_nowait()
        except queue.Empty:
            self._timer = self.after(50, self.poll)
            return
        self._busy = False
        self.review_button.configure(state='normal')
        if error or not isinstance(project, dict) or not project.get('id'):
            self.status.set('Draft was not created: ' + str(error or 'No project identifier returned.'))
            self.invalidate()
        else:
            self.project = project
            self.open_button.configure(state='normal')
            self.status.set('Draft saved. Open it to add acceptance cases before running an experiment.')

    def open_project(self):
        if self.project:
            self.studio.gui._open_invention_project(self.project['id'])

    def _dispose(self, event):
        if event.widget is self:
            self._closed = True
            if self._timer is not None:
                self.after_cancel(self._timer)
                self._timer = None
