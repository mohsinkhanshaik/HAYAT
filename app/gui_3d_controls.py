"""Compact camera menus shared by all interactive 3D workspaces."""
from tkinter import Menu, ttk


def camera_menu(parent, scene):
    button = ttk.Menubutton(parent, text='3D view ▾', style='Hayat.TButton')
    menu = Menu(button, tearoff=False)
    for name in ('Home', 'Front', 'Left', 'Right', 'Above'):
        menu.add_command(label=name, command=lambda value=name: scene.camera_view(value))
    menu.add_separator()
    menu.add_command(label='Remember this angle', command=scene.remember_camera)
    menu.add_command(label='Restore remembered angle', command=scene.restore_camera)
    # Orbit scenes spin on request; the brain navigator has ambient motion.
    # Toggling a market view must not change the global reduced-motion preference.
    if hasattr(scene, 'set_spin'):
        menu.add_command(label='Spin / stop', command=lambda: scene.set_spin(not scene.spinning))
    else:
        menu.add_command(label='Pause / resume motion', command=lambda: scene.set_motion(not scene.motion))
    button.configure(menu=menu)
    return button
