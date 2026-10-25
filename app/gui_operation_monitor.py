"""Small draggable operation viewer; frames stay local and in memory."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.gui_empty import show_empty
from app.gui_resources import BooleanVar, StringVar
from app.operation_monitor import OperationMonitor, action_label, desktop_operation


class OperationPreviewMixin:
    def _preview_privacy_event(self, event):
        # Event delivery is synchronous; no Tk work belongs in this callback.
        monitor = getattr(self, '_operation_monitor', None)
        if monitor is not None:
            monitor.invalidate()

    def _preview_allowed(self):
        registry = self.runtime.registry
        return (not self._closed and not getattr(self.runtime.authority, 'privacy_mode', False)
                and not registry.interlock.halted
                and registry.permissions.check('computer_observe').decision.value == 'ALLOW')

    def _operation_controller(self):
        monitor = getattr(self, '_operation_monitor', None)
        if monitor is None:
            monitor = self._operation_monitor = OperationMonitor(allowed=self._preview_allowed,
                              version=lambda: self.runtime.registry.interlock.epoch)
        return monitor

    def _open_operation_preview(self):
        existing = getattr(self, '_operation_preview', None)
        if existing is not None and existing.winfo_exists():
            existing.make_interactive()
            existing.lift()
            return existing
        self._operation_auto_open = True
        self._operation_preview = self._create_operation_preview()
        return self._operation_preview

    def _create_operation_preview(self, *, automatic=False):
        # Keep the partially constructed Tk object reachable if native setup fails.
        window = OperationPreviewWindow.__new__(OperationPreviewWindow)
        monitor = self._operation_controller()
        try:
            window.__init__(self, monitor, automatic=automatic)
        except Exception:
            monitor.visible(False)
            timer = getattr(window, '_timer', None)
            if timer:
                window.after_cancel(timer)
            try:
                window.destroy()
            except tk.TclError:
                pass
            self._operation_auto_open = False
            raise
        return window

    def _operation_event(self, event):
        if self._closed or not desktop_operation(str((event.get('payload') or {}).get('tool') or '')):
            return
        session = getattr(self.runtime.session, 'session_id', None)
        if event.get('session_id') not in (None, session):
            return
        if getattr(self, '_operation_auto_open', True) and event.get('event_type') == 'TOOL_STARTED':
            # No focus_set/lift on automatic activation: preserve the target app's input focus.
            existing = getattr(self, '_operation_preview', None)
            if existing is None or not existing.winfo_exists():
                self._operation_preview = self._create_operation_preview(automatic=True)
        monitor = getattr(self, '_operation_monitor', None)
        if monitor is not None:
            monitor.receive(event)

    def _close_operation_monitor(self):
        preview = getattr(self, '_operation_preview', None)
        if preview is not None and preview.winfo_exists():
            preview.close()
        monitor = getattr(self, '_operation_monitor', None)
        if monitor is not None:
            monitor.close()


class OperationPreviewWindow(tk.Toplevel):
    def __init__(self, gui, monitor, *, automatic=False):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.monitor, self.closed = gui, monitor, False
        self._frame_id, self._photo, self._image = -1, None, None
        self._event_signature = None
        self._timer = None
        self._automatic = automatic
        self.bind('<Unmap>', self._visibility_changed, add='+')
        self.bind('<Map>', self._visibility_changed, add='+')
        self.bind('<Destroy>', self._destroyed, add='+')
        self.title('HAYAT · Operation view')
        self.configure(bg=gui.BLACK)
        self.minsize(320, 330 if automatic else 450)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _e: self.close())
        self.auto = BooleanVar(self, True)
        self.paused = BooleanVar(self, False)
        top = tk.Frame(self, bg=gui.BLACK, padx=8, pady=6)
        top.pack(fill='x')
        tk.Label(top, text='What HAYAT is doing', bg=gui.BLACK, fg=gui.WHITE,
                 font=('Segoe UI Semibold', 11)).pack(anchor='w')
        from app.gui_intelligence_parts import FlowRow
        controls = self.controls = FlowRow(top, gui.BLACK, gap=4, height=32)
        controls.pack(fill='x')
        controls.add(ttk.Button(controls, text='Observe once', command=self.observe))
        controls.add(ttk.Checkbutton(controls, text='Pause view', variable=self.paused, command=self.pause))
        controls.add(ttk.Checkbutton(controls, text='Auto-open', variable=self.auto, command=self.set_auto))
        self.status = StringVar(self, 'Waiting for a desktop or browser operation.')
        self.status_label = tk.Label(self, textvariable=self.status, bg=gui.BLACK, fg=gui.GOLD_SOFT,
                                     anchor='w', justify='left', wraplength=340, padx=8)
        self.status_label.pack(fill='x')
        # Reserve correction/stop controls before the expanding preview content.
        self.footer = tk.Frame(self, bg=gui.BLACK)
        self.footer.pack(side='bottom', fill='x')
        content = tk.Frame(self, bg=gui.BLACK)
        content.pack(fill='both', expand=True, padx=8, pady=6)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=2, minsize=55)
        content.rowconfigure(1, weight=1, minsize=55)
        self.canvas = tk.Canvas(content, bg=gui.PANEL, height=190, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.canvas.bind('<Configure>', lambda _e: self.render_frame())
        frame = tk.Frame(content, bg=gui.BLACK)
        frame.grid(row=1, column=0, sticky='nsew', pady=(6, 0))
        self.events = ttk.Treeview(frame, columns=('phase', 'tool'), show='headings', height=4)
        self.events.heading('phase', text='State')
        self.events.heading('tool', text='Recorded tool')
        self.events.column('phase', width=75, stretch=False)
        self.events.column('tool', width=230)
        bar = ttk.Scrollbar(frame, command=self.events.yview)
        self.events.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.events.pack(fill='both', expand=True)
        show_empty(self.events, 'No tool activity yet. Each step HAYAT takes appears here as it happens.', gui)
        actions = self.actions = FlowRow(self.footer, gui.BLACK, gap=4, height=34)
        actions.pack(fill='x', padx=8, pady=6)
        actions.add(ttk.Button(actions, text='Stop & correct', command=gui._stop_and_correct))
        actions.add(ttk.Button(actions, text='Emergency stop', command=gui._emergency_stop))
        self.scope = tk.Label(self.footer, text='Latest primary-desktop sample only; local, memory-only. A returned tool is not proof '
                              'the task succeeded. Browser engines can run headlessly.', bg=gui.BLACK, fg=gui.MUTED,
                              justify='left', anchor='w', wraplength=340, padx=8, pady=4)
        self.scope.pack(fill='x')
        self.bind('<Configure>', self.fit)
        gui.root.update_idletasks()
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = min(390, screen_w - 20), min(610, screen_h - 70)
        x = min(max(0, gui.root.winfo_rootx() + gui.root.winfo_width()), max(0, screen_w - width))
        y = min(max(0, gui.root.winfo_rooty()), max(0, screen_h - height))
        self.geometry(f'{width}x{height}+{x}+{y}')
        # Automatic previews must not introduce a new keyboard/click target.
        if automatic:
            if self.tk.call('tk', 'windowingsystem') == 'win32':
                self.update_idletasks()
                self.attributes('-alpha', .98)
                self._native_style(automatic=True)
                self.attributes('-topmost', True)
                self.controls.pack_forget()
                self.actions.pack_forget()
                self.scope.configure(text='Automatic click-through desktop view. Open Studios → Operation view '
                                     'to move or pause it. Use HAYAT’s main Emergency stop to stop work. '
                                     'Samples stay in memory; a returned tool does not verify its result.')
            else:
                self.destroy()
                self.closed = True
                return
        if automatic:
            self._show_without_activation()
        else:
            self.deiconify()
        self._capture_exclusion(True)
        monitor.visible(True)
        self.poll()

    def _visibility_changed(self, event):
        if event.widget is self and not self.closed:
            self.monitor.visible(event.type == tk.EventType.Map)

    def _destroyed(self, event):
        if event.widget is self:
            self.closed = True
            self.gui._operation_auto_open = False
            if self._timer:
                self.after_cancel(self._timer)
                self._timer = None
            self.monitor.visible(False)
            self._image = self._photo = None

    def _native_style(self, *, automatic):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        handle = int(self.wm_frame(), 16)
        style = user32.GetWindowLongW(handle, -20)
        mask = 0x08000000 | 0x00000020  # WS_EX_NOACTIVATE | WS_EX_TRANSPARENT
        ctypes.set_last_error(0)
        previous = user32.SetWindowLongW(handle, -20, style | mask if automatic else style & ~mask)
        if not previous and ctypes.get_last_error():
            raise ctypes.WinError()

    def _show_without_activation(self):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.ShowWindow(int(self.wm_frame(), 16), 4)  # SW_SHOWNOACTIVATE

    def _capture_exclusion(self, enabled):
        if self.tk.call('tk', 'windowingsystem') != 'win32':
            return
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        self._exclusion_available = bool(user32.SetWindowDisplayAffinity(int(self.wm_frame(), 16), 0x11 if enabled else 0))
        if enabled and not self._exclusion_available:
            self.scope.configure(text=self.scope.cget('text') + ' Windows could not exclude this viewer from captures; '
                                 'the preview may appear inside the sample.')

    def make_interactive(self):
        if not self._automatic:
            return
        self._automatic = False
        self.minsize(320, 450)
        if self.tk.call('tk', 'windowingsystem') == 'win32':
            self._native_style(automatic=False)
            self.attributes('-alpha', 1)
        self.deiconify()
        self.controls.pack(fill='x')
        self.actions.pack(fill='x', padx=8, pady=6, before=self.scope)
        self.scope.configure(text='Latest primary-desktop sample only; local, memory-only. A returned tool is not '
                             'proof the task succeeded. Browser engines can run headlessly.')

    def fit(self, event):
        if event.widget is self:
            self.status_label.configure(wraplength=max(240, event.width - 20))
            self.scope.configure(wraplength=max(240, event.width - 20))

    def set_auto(self):
        self.gui._operation_auto_open = self.auto.get()

    def pause(self):
        self.monitor.pause(self.paused.get())
        self.poll(reschedule=False)

    def observe(self):
        if not self.monitor.observe_once():
            self.status.set('Observation paused or blocked by privacy, emergency stop or observation policy.')

    def render_frame(self):
        from PIL import ImageTk
        self.canvas.delete('all')
        if self._image is None:
            self.canvas.create_text(max(1, self.canvas.winfo_width() // 2), max(1, self.canvas.winfo_height() // 2),
                                    text='No current desktop sample', fill=self.gui.MUTED,
                                    width=max(20, self.canvas.winfo_width() - 16))
            return
        image = self._image.copy()
        image.thumbnail((max(1, self.canvas.winfo_width() - 8), max(1, self.canvas.winfo_height() - 8)))
        self._photo = ImageTk.PhotoImage(image, master=self)
        self.canvas.create_image(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2, image=self._photo)

    def poll(self, *, reschedule=True):
        if self.closed:
            return
        state = self.monitor.snapshot(self._frame_id)
        if state['frame_id'] != self._frame_id:
            self._frame_id, self._image = state['frame_id'], state['frame']
            if self._image is None:
                self._photo = None
            self.render_frame()
        rows = state['events']
        signature = tuple((row['sequence'], row['phase'], row['tool']) for row in rows)
        if signature != self._event_signature:
            position = self.events.yview()[0]
            self.events.delete(*self.events.get_children())
            for row in reversed(rows):
                self.events.insert('', 'end', values=(row['phase'], row['tool']))
            self.events.yview_moveto(position)
            self._event_signature = signature
            show_empty(self.events, 'No tool activity yet. Each step HAYAT takes appears here as it happens.', self.gui)
        label = action_label(rows[-1]) if rows else 'Waiting for an operation'
        self.status.set('Observation blocked; image cleared.' if state['blocked'] else 'View paused; image cleared.' if state['paused']
                        else 'Desktop sample unavailable: ' + state['error'] if state['error'] else
                        label + (f" · {state['active']} active" if state['active'] else '')
                        + ('\nSample: ' + state['captured_at'] if state['captured_at'] else ''))
        if state['dropped_starts'] or state['expired']:
            self.status.set(self.status.get() + f"\nEvent gaps: {state['dropped_starts']} starts omitted, "
                            f"{state['expired']} unfinished operations expired.")
        if reschedule:
            self._timer = self.after(250, self.poll)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.gui._operation_auto_open = False
        if self._timer:
            self.after_cancel(self._timer)
            self._timer = None
        self.monitor.visible(False)
        self._image = self._photo = None
        self.destroy()
