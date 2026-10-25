"""Move a sidebar card within the main window while keeping the same widgets and worker."""
import tkinter as tk
from tkinter import ttk

from app.gui_motion import attach


class InlinePanel:
    DEFAULT_HEIGHT = 220

    def __init__(self, card, dock_parent, gui):
        self.card, self.dock_parent, self.root, self.gui = card, dock_parent, gui.root, gui
        self.floating = self.collapsed = False
        self.box = None
        self._drag = self._resize = self._before = None
        self._dock_height = self.DEFAULT_HEIGHT
        self.grip = tk.Label(card, text='↕ Resize panel', bg=gui.PANEL_2, fg=gui.MUTED,
                             font=('Segoe UI', 8), cursor='sb_v_double_arrow', takefocus=1)
        self.grip.pack(side='bottom', fill='x', pady=(4, 0))
        self.grip.bind('<ButtonPress-1>', self._start_resize)
        self.grip.bind('<B1-Motion>', self._resize_to)
        self.grip.bind('<ButtonRelease-1>', self._stop_resize)
        self.grip.bind('<Up>', lambda _event: self.resize_by(-24))
        self.grip.bind('<Down>', lambda _event: self.resize_by(24))
        self.viewport = tk.Frame(card, bg=gui.PANEL, height=self._dock_height)
        self.viewport.pack_propagate(False)
        self.viewport.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(self.viewport, bg=gui.PANEL, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(self.viewport, orient='vertical', command=self.canvas.yview,
                                  style='HayatThin.Vertical.TScrollbar')
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.content = tk.Frame(self.canvas, bg=gui.PANEL)
        self._window = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.content.bind('<Configure>', lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(self._window, width=event.width))
        self.scroller = attach(self.canvas)
        self.card._smooth_scroller = self.scroller
        self._root_binding = self.root.bind('<Configure>', self._root_resized, add='+')
        card.bind('<Destroy>', self._dispose, add='+')

    def install_header(self, header, title):
        self.title = title
        self.menu_button = ttk.Menubutton(header, text='Panel ▾', style='Compact.Hayat.TButton')
        self.menu_button.pack(side='right', before=title)
        self.dock_button = ttk.Button(header, text='Dock', style='Compact.Hayat.TButton', command=self.dock)
        self.dock_button._hayat_icon = 'pin'
        self.menu = tk.Menu(self.menu_button, tearoff=False, bg=self.gui.PANEL, fg=self.gui.WHITE,
                            activebackground=self.gui.GOLD, activeforeground=self.gui.BLACK)
        self.menu.add_command(label='Float in main window', command=self.float)
        self.menu.add_command(label='Dock beside chat', command=self.dock)
        self.menu.add_command(label='Collapse / expand', command=self.toggle_collapsed)
        self.menu.add_command(label='Reset panel size', command=self.reset_size)
        self.menu_button.configure(menu=self.menu)
        for widget in (header, title):
            widget.configure(cursor='fleur')
            widget.bind('<ButtonPress-1>', self._start_drag)
            widget.bind('<B1-Motion>', self._drag_to)
            widget.bind('<ButtonRelease-1>', self._stop_drag)
            widget.bind('<Double-Button-1>', lambda _event: self.toggle_collapsed())
        from app.gui_help import Tooltip
        title._panel_tip = Tooltip(title,
            'Drag to move this panel around the main window. Double-click to collapse. Panel menu docks it beside chat.', type(self.gui))
        self._folds = getattr(self.gui, '_folds', None)
        if self._folds is not None:
            self._folds.panels['Global intelligence'] = lambda folded: self.set_collapsed(folded, remember=False)
            self.set_collapsed(self._folds.folded('Global intelligence'), remember=False)

    def _clamp_box(self, x, y, width, height):
        available_width = max(1, self.root.winfo_width() - 16)
        available_height = max(1, self.root.winfo_height() - 16)
        width = min(max(300, width), available_width)
        minimum_height = self.card.header.winfo_reqheight() + 12 if self.collapsed else 220
        height = min(max(minimum_height, height), available_height)
        return (min(max(8, x), max(8, self.root.winfo_width() - width - 8)),
                min(max(8, y), max(8, self.root.winfo_height() - height - 8)), width, height)

    def _place(self, box):
        self.box = self._clamp_box(*box)
        x, y, width, height = self.box
        self.card.place(in_=self.root, x=x, y=y, width=width, height=height)
        self.card.lift()

    def float(self):
        if self.floating:
            self.card.lift()
            return
        self._remember_position()
        box = (self.card.winfo_rootx() - self.root.winfo_rootx(),
               self.card.winfo_rooty() - self.root.winfo_rooty(),
               max(360, self.card.winfo_width()), self.card.winfo_height())
        self.card.pack_forget()
        self.floating = True
        self.card.pack_propagate(False)
        self.dock_button.pack(side='right', padx=(0, 4), before=self.title)
        self.grip.configure(text='↘ Resize panel', cursor='sizing')
        self._place(box)

    def _remember_position(self):
        siblings = self.dock_parent.pack_slaves()
        if self.card in siblings:
            index = siblings.index(self.card)
            self._before = siblings[index + 1] if index + 1 < len(siblings) else None

    def hide(self):
        if not self.floating:
            self._remember_position()
        self.card.pack_forget()
        self.card.place_forget()

    def dock(self):
        self.card.place_forget()
        self.floating = False
        self.card.pack_propagate(True)
        self.dock_button.pack_forget()
        self.grip.configure(text='↕ Resize panel', cursor='sb_v_double_arrow')
        options = dict(self.card._pack, in_=self.dock_parent)
        if self._before is not None and self._before.winfo_exists() and self._before.winfo_manager() == 'pack':
            options['before'] = self._before
        self.card.pack(**options)

    def show(self):
        if self.floating and self.box is not None:
            self._place(self.box)
        else:
            self.dock()

    def toggle_collapsed(self):
        self.set_collapsed(not self.collapsed)
        return 'break'

    def set_collapsed(self, collapsed, *, remember=True):
        if collapsed == self.collapsed:
            return
        self.collapsed = collapsed
        if remember and self._folds is not None:
            self._folds.set('Global intelligence', collapsed)
        actions = getattr(self.card, 'header_controls', None)
        if actions is not None:
            if self.collapsed:
                self._header_actions_pack = actions.pack_info()
                actions.pack_forget()
            else:
                actions.pack(**getattr(self, '_header_actions_pack', {'side': 'bottom', 'fill': 'x'}))
            # A floating card computes its new height from this header below.
            self.card.header.update_idletasks()
        if self.collapsed:
            self.viewport.pack_forget()
            self.grip.pack_forget()
            if self.floating:
                self._expanded_height = self.box[3]
                self._place((*self.box[:3], self.card.header.winfo_reqheight() + 12))
        else:
            self.grip.pack(side='bottom', fill='x', pady=(4, 0))
            self.viewport.pack(fill='both', expand=True)
            if self.floating:
                self._place((*self.box[:3], getattr(self, '_expanded_height', 340)))

    def reset_size(self):
        if self.collapsed:
            self.toggle_collapsed()
        self._dock_height = self.DEFAULT_HEIGHT
        self.viewport.configure(height=self._dock_height)
        if self.floating:
            self._place((*self.box[:2], 400, 340))

    def _start_drag(self, event):
        self._drag = (event.x_root, event.y_root,
                      self.card.winfo_rootx() - self.root.winfo_rootx(),
                      self.card.winfo_rooty() - self.root.winfo_rooty())
        return 'break'

    def _drag_to(self, event):
        if self._drag is not None:
            pointer_x, pointer_y, x, y = self._drag
            if not self.floating and abs(event.x_root - pointer_x) + abs(event.y_root - pointer_y) < 6:
                return 'break'
            if not self.floating:
                self.float()
            self._place((x + event.x_root - pointer_x, y + event.y_root - pointer_y, *self.box[2:]))
        return 'break'

    def _stop_drag(self, _event=None):
        self._drag = None
        return 'break'

    def _start_resize(self, event):
        self._resize = (event.x_root, event.y_root, self.card.winfo_width(),
                        self.box[3] if self.floating else self._dock_height)
        return 'break'

    def _resize_to(self, event):
        if self._resize is not None:
            x, y, width, height = self._resize
            if self.floating:
                self._place((*self.box[:2], width + event.x_root - x, height + event.y_root - y))
            else:
                self._dock_height = min(max(100, height + event.y_root - y), max(100, self.root.winfo_height() - 160))
                self.viewport.configure(height=self._dock_height)
        return 'break'

    def resize_by(self, pixels):
        if self.floating:
            self._place((*self.box[:3], self.box[3] + pixels))
        else:
            self._dock_height = min(max(100, self._dock_height + pixels), max(100, self.root.winfo_height() - 160))
            self.viewport.configure(height=self._dock_height)
        return 'break'

    def _stop_resize(self, _event=None):
        self._resize = None
        return 'break'

    def _root_resized(self, event):
        if event.widget is self.root and self.floating and self.card.winfo_manager():
            self._place(self.box)

    def _dispose(self, event):
        if event.widget is self.card:
            self.root.unbind('<Configure>', self._root_binding)
            if self._folds is not None:
                self._folds.panels.pop('Global intelligence', None)
