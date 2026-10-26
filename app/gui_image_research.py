"""Read a selected image off the Tk thread, then research an explicit topic.

Image bytes, OCR and inferred names are never forwarded to a search engine.
The owner supplies the topic; known-name work research uses the existing form.
"""
from __future__ import annotations

from pathlib import Path
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from app.gui_resources import StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.gui_fonts import DISPLAY


PERSON_LOOKUP = re.compile(
    r'(?i)\b(?:who\s+(?:is|are)|identify|recognize|recognise|unmask)\s+'
    r'(?:(?:this|that|the)\s+)?(?:person|man|woman|boy|girl|face|him|her|them|he|she)\b|'
    r'\b(?:find|track|locate|search|details|everything|all about)\b.{0,50}'
    r'\b(?:him|her|them|this person|that person|his|their|face)\b|'
    r'\b(?:home address|private details|personal details|phone number|social media accounts)\b'
)


class ImageResearchStudio(tk.Toplevel):
    def __init__(self, research):
        super().__init__(research)
        self.withdraw()
        self.research = research
        self.gui, p = research.gui, research.palette
        self._closed = False
        self._generation = 0
        self._poll_job = None
        self._results = queue.Queue()
        self._source_results = queue.Queue()
        self._image_path = None
        self._source_busy = False
        self._source_request = 0
        self._display_mode = 'reading'
        self._photo = None
        self.attachment = None
        self.title('HAYAT · Image research')
        self.configure(bg=p.BLACK)
        self.minsize(560, 460)
        self.transient(research)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        self.bind('<Destroy>', self._destroyed, add='+')

        head = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        head.pack(fill='x')
        tk.Label(head, text='Research an image', bg=p.BLACK, fg=p.WHITE,
                 font=(DISPLAY, 16)).pack(side='left')
        ttk.Button(head, text='Close', command=self.close, style='Hayat.TButton').pack(side='right')
        from app.gui_research_links import research_links
        self.workspace_links = research_links(head, self.gui, 'sources', image=lambda: self._image_path)
        self.guidance = tk.Label(self, text='1  Read an image     2  Enter its topic     3  Follow the evidence',
                                 bg=p.BLACK, fg=p.GOLD_SOFT, anchor='w', justify='left', wraplength=600)
        self.guidance.pack(fill='x', padx=16, pady=(0, 8))
        image_row = self.image_row = tk.Frame(self, bg=p.PANEL, padx=12, pady=10)
        image_row.pack(fill='x', padx=16)
        self.preview = tk.Label(image_row, text='No image selected', bg=p.PANEL, fg=p.MUTED,
                                width=20, height=4)
        self.preview.pack(side='left', padx=(0, 12))
        info = tk.Frame(image_row, bg=p.PANEL)
        info.pack(side='left', fill='both', expand=True)
        self.filename = StringVar(self, 'Choose a photo, screenshot or diagram')
        self.file_label = tk.Label(info, textvariable=self.filename, bg=p.PANEL, fg=p.WHITE,
                                  anchor='w', justify='left', wraplength=330)
        self.file_label.pack(fill='x', pady=(0, 8))
        self.choose_button = ttk.Button(info, text='Choose image…', command=self.choose,
                                        style='Hayat.TButton')
        self.choose_button.pack(anchor='w')
        self.status = StringVar(self, 'Image reading uses your configured OCR and vision. No web search starts automatically.')
        self.status_label = tk.Label(self, textvariable=self.status, bg=p.BLACK, fg=p.MUTED,
                                    anchor='w', justify='left', wraplength=600)
        self.status_label.pack(fill='x', padx=16, pady=6)

        # Bottom controls reserve their space before the evidence expands.
        bottom = tk.Frame(self, bg=p.BLACK, padx=16, pady=8)
        bottom.pack(side='bottom', fill='x')
        self.scope_label = tk.Label(bottom, text='Research visible objects, places and text. For people, use a known name and public work; '
                                   'no face identification or private details.',
                                   bg=p.BLACK, fg=p.MUTED, anchor='w', justify='left', wraplength=600)
        self.scope_label.pack(fill='x', pady=(0, 8))
        self.topic_label = tk.Label(bottom, text='Topic or question to send to web search', bg=p.BLACK, fg=p.WHITE,
                                    anchor='w')
        self.topic_label.pack(fill='x')
        self.topic = ttk.Entry(bottom)
        self.topic.pack(fill='x', pady=(4, 8))
        self.topic.bind('<Return>', lambda _event: self.search())
        actions = tk.Frame(bottom, bg=p.BLACK)
        actions.pack(fill='x')
        self.search_button = ttk.Button(actions, text='Deep research', command=self.search,
                                        style='HayatAccent.TButton', state='disabled')
        self.search_button.pack(side='left')
        self.person_button = ttk.Button(actions, text='Public work by name', command=self.public_work,
                                        style='Hayat.TButton')
        self.person_button.pack(side='left', padx=8)
        self.source_button = ttk.Button(actions, text='Exact image sources', command=self.image_sources, state='disabled', style='Hayat.TButton')
        self.source_button.pack(side='left', padx=4)
        page_row = tk.Frame(bottom, bg=p.BLACK)
        page_row.pack(fill='x', pady=(8, 0))
        self.source_url = ttk.Entry(page_row)
        self.source_url.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.source_url.insert(0, '')
        self.source_url.bind('<Return>', lambda _event: self.check_source_page())
        self.page_button = ttk.Button(page_row, text='Check source page', command=self.check_source_page,
                                      state='disabled', style='Hayat.TButton')
        self.page_button.pack(side='right')
        self.source_hint = tk.Label(bottom, text='Paste a public page URL to check its published images. Your file stays local.',
                                    bg=p.BLACK, fg=p.MUTED, anchor='w', justify='left', wraplength=600)
        self.source_hint.pack(fill='x', pady=(3, 0))
        self.evidence = DarkScrolledText(self, wrap='word', height=6, bg=p.PANEL, fg=p.WHITE,
                                        font=('Segoe UI', 10), padx=12, pady=10, relief='flat', state='disabled')
        self.evidence.pack(fill='both', expand=True, padx=16, pady=(0, 4))
        self.bind('<Configure>', self._fit)
        place_over(self, self.gui.root, (720, 680))
        self.deiconify()
        self.lift()
        self._poll()

    def _fit(self, event):
        if event.widget is self:
            compact = event.height < 560
            if compact and self.guidance.winfo_manager():
                self.guidance.pack_forget()
                self.scope_label.pack_forget()
                self.image_row.configure(pady=4)
            elif not compact and not self.guidance.winfo_manager():
                self.guidance.pack(fill='x', padx=16, pady=(0, 8), before=self.image_row)
                self.scope_label.pack(fill='x', pady=(0, 8), before=self.topic_label)
                self.image_row.configure(pady=10)
            for label in (self.guidance, self.status_label, self.scope_label, self.source_hint):
                label.configure(wraplength=max(200, event.width - 40))
            self.file_label.configure(wraplength=max(160, event.width - 260))

    def choose(self):
        path = filedialog.askopenfilename(parent=self, title='Read an image for research',
                                         filetypes=[('Images', '*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff')])
        if path:
            self.read_image(path)

    def prepare_image(self, path):
        """Prefill from another local workspace without reading, uploading or inferring a name."""
        if self._source_busy:
            self.status.set('Finish the current source check before preparing another image.')
            return False
        self._generation += 1
        self._source_request += 1
        self._source_busy = False
        self._image_path = str(path)
        self.attachment, self._photo = None, None
        self._display_mode = 'prepared'
        self.filename.set(Path(path).name)
        self.preview.configure(image='', text='Image selected', width=20, height=4)
        self.source_button.configure(state='normal')
        self.page_button.configure(state='normal')
        self.search_button.configure(state='disabled')
        self.status.set('Image path prepared. Exact image sources and Check source page read only when pressed. Choose image to run OCR/vision.')
        self._set_evidence('Selected local file: ' + str(path) + '\n\nNo file was read and no search started. Choose an explicit source check below.')
        return True

    def read_image(self, path):
        reader = getattr(self.gui.runtime, 'attachments', None)
        if reader is None:
            self.status.set('Image reading is unavailable in this runtime.')
            return
        self._generation += 1
        self._source_request += 1
        self._source_busy = False
        self._display_mode = 'reading'
        self._image_path = str(path)
        self.source_button.configure(state='normal')
        self.page_button.configure(state='normal')
        generation, results = self._generation, self._results
        self.attachment = None
        self._photo = None
        self.preview.configure(image='', text='Reading…', width=20, height=4)
        self.filename.set(Path(path).name)
        self.search_button.configure(state='disabled')
        self.status.set('Reading image in the background. You can enter a topic while it loads.')
        self._set_evidence('Waiting for OCR / vision…')

        def work():
            try:
                # Uses the existing admission/scan/size bounds. This view does
                # not retain the image's content in long-term memory.
                from app.research.image_sources import image_thumbnail, inspect_image
                inspect_image(path)  # reject oversized pixel headers before OCR/vision decoding
                attachment = reader.read(path)
                thumbnail = None
                if attachment.ok:
                    try:
                        thumbnail = image_thumbnail(path)
                    except Exception:
                        thumbnail = None  # textual evidence still works without a thumbnail
                results.put((generation, attachment, thumbnail, ''))
            except Exception as error:
                results.put((generation, None, None, f'Could not read image: {str(error)[:250]}'))

        threading.Thread(target=work, name='hayat-image-research', daemon=True).start()

    def _poll(self):
        self._poll_job = None
        if self._closed:
            return
        try:
            generation, request_id, detail, message = self._source_results.get_nowait()
        except queue.Empty:
            pass
        else:
            if generation == self._generation and request_id == self._source_request:
                self._source_busy = False
                self.source_button.configure(state='normal' if self._image_path else 'disabled')
                self.page_button.configure(state='normal' if self._image_path else 'disabled')
                try:
                    self._source_guard(self._source_ticket, generation)
                    self._set_evidence(detail)
                    self.status.set(message)
                except Exception as error:
                    self._set_evidence('Source result discarded: ' + str(error)[:300])
                    self.status.set('Source result discarded because access or the request changed.')
        # Discard superseded work without letting a large backlog starve Tk.
        for _ in range(20):
            try:
                generation, attachment, thumbnail, error = self._results.get_nowait()
            except queue.Empty:
                break
            if generation != self._generation:
                continue
            self.attachment = attachment
            if error or attachment is None or not attachment.ok:
                message = error or (attachment.error if attachment else '') or 'No readable text or vision description. Try a clearer image.'
                self.status.set(message)
                if self._display_mode != 'source':
                    self._set_evidence(message)
                self.preview.configure(text='Preview unavailable')
                continue
            if thumbnail is not None:
                from PIL import ImageTk
                self._photo = ImageTk.PhotoImage(thumbnail, master=self)
                self.preview.configure(image=self._photo, text='', width=140, height=80)
            else:
                self.preview.configure(text='Image read')
            if self._display_mode != 'source':
                self._set_evidence(attachment.text[:24000])
                self.status.set('Image read · OCR and vision can be mistaken. Enter a specific topic below; only that topic goes to web search.')
            self.search_button.configure(state='normal')
        self._poll_job = self.after(100, self._poll)

    def image_sources(self):
        if not self._image_path or self._source_busy:
            return
        if not self.research._search_allowed():
            self.status.set(self.research.detail_status.get())
            return
        try:
            ticket = self.gui.runtime.registry.interlock.ticket()
        except Exception as error:
            self.status.set(str(error)[:250])
            return
        self._source_busy = True
        self._source_ticket = ticket
        from app.privacy_masks import ROUTE
        self._source_route = ROUTE.snapshot()
        self._display_mode = 'source'
        self._source_request += 1
        request_id = self._source_request
        self.source_button.configure(state='disabled')
        self.page_button.configure(state='disabled')
        generation, path = self._generation, self._image_path
        self.status.set('Sending only the file hash to Wikimedia Commons, then checking candidate bytes…')
        def check():
            self._source_guard(ticket, generation)
        def run():
            try:
                from app.research.image_sources import exact_sources
                from app.tools.image_sources import format_source_report
                value = exact_sources(path, self.gui.runtime.research.fetcher, check=check)
                check()
                detail = format_source_report(value)
                if value['errors']:
                    message = 'Local metadata ready; internet source lookup was incomplete. See provider errors below.'
                elif value['matches']:
                    message = f"{len(value['matches'])} exact file source(s) verified. No person identity is established."
                else:
                    message = 'Local metadata ready. Wikimedia Commons returned no exact file match.'
            except Exception as error:
                detail = 'Source lookup failed: ' + str(error)[:300]
                message = 'Source lookup could not finish. See the error below.'
            self._source_results.put((generation, request_id, detail, message))
        threading.Thread(target=run, name='hayat-image-sources', daemon=True).start()

    def check_source_page(self):
        if not self._image_path or self._source_busy:
            return
        url = self.source_url.get().strip()
        if not url:
            self.status.set('Paste the public page URL beside Check source page.')
            self.source_url.focus_set()
            return
        if len(url) > 4096:
            self.status.set('Keep the source page URL under 4,096 characters.')
            return
        if not self.research._search_allowed():
            self.status.set(self.research.detail_status.get())
            return
        try:
            ticket = self.gui.runtime.registry.interlock.ticket()
        except Exception as error:
            self.status.set(str(error)[:250])
            return
        self._source_busy = True
        self._source_ticket = ticket
        from app.privacy_masks import ROUTE
        self._source_route = ROUTE.snapshot()
        self._display_mode = 'source'
        self._source_request += 1
        request_id, generation, path = self._source_request, self._generation, self._image_path
        self.source_button.configure(state='disabled')
        self.page_button.configure(state='disabled')
        self.status.set('Checking one public page and up to 8 image links. Your image is not uploaded…')
        def check():
            self._source_guard(ticket, generation)
        def run():
            try:
                from app.research.image_sources import check_source_page
                value = check_source_page(path, url, self.gui.runtime.research.fetcher, check=check)
                check()
                detail = self._source_page_text(value)
                if value['matches']:
                    message = f"{len(value['matches'])} exact source image(s) verified. This does not establish identity or authorship."
                elif value['outcome'] == 'failed':
                    message = 'The source page could not be retrieved. The image source remains unknown.'
                else:
                    message = 'No exact match among the checked images. The source remains unknown; see coverage below.'
            except Exception as error:
                detail = 'Source-page check failed: ' + str(error)[:300]
                message = 'Source-page verification could not finish. See the error below.'
            self._source_results.put((generation, request_id, detail, message))
        threading.Thread(target=run, name='hayat-image-source-page', daemon=True).start()

    def _source_guard(self, ticket, generation):
        runtime = self.gui.runtime
        runtime.registry.interlock.check(ticket)
        if self._closed or generation != self._generation:
            raise InterruptedError('Image check was closed or superseded.')
        from app.privacy_masks import ROUTE
        if ROUTE.snapshot() != self._source_route:
            raise PermissionError('Privacy route changed; start a fresh image-source check.')
        modes = set(getattr(getattr(runtime, 'session', None), 'effective_modes', ()))
        if getattr(getattr(runtime, 'authority', None), 'privacy_mode', False) or modes & {'PRIVATE', 'LOCAL', 'LOCAL_ONLY'}:
            raise PermissionError('Privacy or local-only mode blocks image-source retrieval.')

    @staticmethod
    def _source_page_text(value):
        lines = ['SOURCE PAGE CHECK · ' + value['outcome'].upper(), value['scope'], '',
                 'Local image SHA-256: ' + value['metadata']['sha256']]
        page = value['page']
        if page:
            lines.extend(['Page: ' + page['url'], 'Retrieved: ' + page['retrieved_at'],
                          'Page SHA-256: ' + page['sha256']])
        coverage = value['coverage']
        lines.extend(['', f"Checked {coverage['candidates_attempted']} of {coverage['candidates_found']} declared image URLs. "
                      f"Received {coverage['bytes_received']:,} bytes."])
        if coverage['limited']:
            lines.append('Coverage limited by the candidate or download budget.')
        if not coverage['candidates_found'] and page:
            lines.append('No supported static image URLs were declared. JavaScript content is not rendered.')
        for candidate in value['candidates']:
            label = {'verified_exact_bytes': 'MATCH · exact file bytes',
                     'verified_exact_pixels': 'MATCH · exact decoded pixels', 'different': 'DIFFERENT',
                     'failed': 'UNVERIFIED · retrieval or image failure'}[candidate['status']]
            lines.extend(['', label, candidate.get('image_url', candidate['requested_url'])])
            if candidate.get('retrieved_at'):
                lines.extend(['Retrieved: ' + candidate['retrieved_at'], 'SHA-256: ' + candidate['sha256']])
            if candidate.get('error'):
                lines.append(candidate['error'])
        if value['errors']:
            lines.extend(['', 'Incomplete checks: ' + '; '.join(value['errors'])])
        lines.extend(['', value['privacy']])
        return '\n'.join(lines)

    def _set_evidence(self, text):
        self.evidence._smooth_scroller.cancel()
        self.evidence.configure(state='normal')
        self.evidence.delete('1.0', 'end')
        self.evidence.insert('1.0', text)
        self.evidence.configure(state='disabled')

    def search(self):
        topic = self.topic.get().strip()
        if not self.attachment or not self.attachment.ok:
            self.status.set('Read an image before starting image research.')
            return
        if not topic:
            self.status.set('Enter a topic such as “Eiffel Tower construction history” or a product model number.')
            self.topic.focus_set()
            return
        if len(topic) > 1000:
            self.status.set('Keep the research topic under 1,000 characters.')
            return
        if PERSON_LOOKUP.search(topic):
            self.status.set('A photo cannot be used to identify or investigate someone. Use a known name for public-work research.')
            return
        if not self.research._search_allowed():
            self.status.set(self.research.detail_status.get())
            return
        # No image-derived text, path or name is added to the network query.
        try:
            job = self.research.jobs.start(topic, asked=topic, level='deep')
        except Exception as error:
            self.status.set(f'Could not start research: {str(error)[:250]}')
            return
        self.research.jobs.release(job)
        self.research.refresh()
        self.research.tree.selection_set(job.job_id)
        self.research.tree.see(job.job_id)
        self.research.show_selected()
        self.withdraw()
        self.research.deiconify()
        self.research.lift()

    def public_work(self):
        self.research.search_kind.set('Person (public work)')
        self.research._show_person_fields()
        self.withdraw()
        self.research.deiconify()
        self.research.lift()
        self.research.query.focus_set()

    def _destroyed(self, event):
        if event.widget is self:
            self._closed = True
            self._generation += 1
            if self._poll_job is not None:
                self.after_cancel(self._poll_job)
                self._poll_job = None
            self._photo = None

    def close(self):
        if not self._closed:
            self.destroy()
