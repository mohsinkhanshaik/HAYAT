"""Manual navigation between related research windows; no automatic operations."""
from tkinter import Menu, ttk


DESTINATIONS = (
    ('images', 'Local image tools'), ('faces', 'Enrolled people (local)'),
    ('sources', 'Image-source checks'), ('professional', 'Professional research by name'),
    ('contacts', 'Public professional contacts'),
)


def research_links(parent, gui, current, *, image=None, name=None, context=None):
    """Only explicitly entered fields cross a handoff; images never become names."""
    style = ttk.Style(parent)
    style.configure('Hayat.Research.TMenubutton', background=gui.PANEL_2, foreground=gui.WHITE, padding=(8, 5))
    style.map('Hayat.Research.TMenubutton', foreground=[('disabled', gui.MUTED), ('active', gui.BLACK)],
              background=[('active', gui.GOLD_SOFT)])
    button = ttk.Menubutton(parent, text='Other research tools', style='Hayat.Research.TMenubutton')
    menu = Menu(button, tearoff=False, bg=gui.PANEL_2, fg=gui.WHITE, activebackground=gui.GOLD_SOFT, activeforeground=gui.BLACK)
    button.configure(menu=menu)
    launch = getattr(gui, '_open_research_workspace', None)
    for key, title in DESTINATIONS:
        if key == current:
            continue
        def open_workspace(kind=key):
            arguments = {}
            if kind in {'images', 'faces', 'sources'} and image is not None:
                arguments['image_path'] = str(image() or '').strip()
            if kind in {'professional', 'contacts'}:
                if name is not None:
                    arguments['name'] = str(name() or '').strip()
                if context is not None:
                    arguments['context'] = str(context() or '').strip()
            launch(kind, **arguments)
        menu.add_command(label=title, command=open_workspace, state='normal' if callable(launch) else 'disabled')
    button.pack(side='right', padx=6)
    button.research_menu = menu
    return button
