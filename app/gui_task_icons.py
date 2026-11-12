"""Small original line glyphs for workspace navigation; they depict no runtime state."""
import tkinter as tk


def task_glyph(parent, key, palette, accent):
    canvas = tk.Canvas(parent, width=28, height=28, bg=palette.PANEL_2, highlightthickness=0, takefocus=False)

    def line(*points):
        canvas.create_line(*points, fill=accent, width=1.6, capstyle='round', joinstyle='round')

    def box(*points):
        canvas.create_rectangle(*points, outline=accent, width=1.5)

    def node(x, y):
        canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=palette.PANEL_2, outline=accent, width=1.5)

    if key == 'jobs':
        box(4, 9, 24, 23)
        line(10, 9, 10, 5, 18, 5, 18, 9)
        line(4, 15, 24, 15)
    elif key == 'invention':
        canvas.create_oval(7, 3, 21, 18, outline=accent, width=1.5)
        line(10, 18, 10, 22, 18, 22, 18, 18)
        line(12, 25, 16, 25)
        line(14, 10, 14, 18)
    elif key == 'chat':
        line(4, 5, 24, 5, 24, 20, 12, 20, 7, 25, 7, 20, 4, 20, 4, 5)
        line(8, 10, 20, 10)
        line(8, 15, 17, 15)
    elif key == 'cameras':
        box(3, 8, 25, 23)
        line(8, 8, 10, 4, 18, 4, 20, 8)
        canvas.create_oval(9, 11, 19, 21, outline=accent, width=1.5)
    elif key == 'files':
        line(7, 3, 17, 3, 23, 9, 23, 25, 7, 25, 7, 3)
        line(17, 3, 17, 9, 23, 9)
        line(11, 15, 19, 15)
        line(11, 20, 19, 20)
    elif key == 'research':
        canvas.create_oval(3, 3, 20, 20, outline=accent, width=1.5)
        line(18, 18, 25, 25)
        line(8, 8, 15, 8)
        line(8, 12, 13, 12)
    elif key == 'lab':
        line(10, 3, 18, 3)
        line(11, 3, 11, 12, 4, 24, 24, 24, 17, 12, 17, 3)
        line(8, 18, 20, 18)
    else:
        for a, b in (((6, 7), (21, 6)), ((6, 7), (12, 22)), ((21, 6), (23, 21)), ((12, 22), (23, 21))):
            line(*a, *b)
        if key == 'brain':
            line(6, 7, 23, 21)
            line(21, 6, 12, 22)
        for point in ((6, 7), (21, 6), (12, 22), (23, 21)):
            node(*point)
    return canvas
