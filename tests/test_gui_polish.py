"""The 2026-10-13 interface pass: empty tables say how to start, errors say what to do, the composer hint takes no row."""
from types import SimpleNamespace
from tkinter import ttk

from app.gui_empty import show_empty
from app.gui_studio import plain_error


def test_an_empty_table_says_how_to_start_and_steps_aside_for_rows(shared_tk):
    palette = SimpleNamespace(PANEL_2="#101820", MUTED="#8899AA")
    tree = ttk.Treeview(shared_tk, columns=("a",))
    tree.pack()
    try:
        show_empty(tree, "No searches yet.", palette)
        shared_tk.update()
        label = tree._hayat_empty
        assert label.winfo_manager() == "place" and label.cget("text") == "No searches yet."
        tree.insert("", "end", text="row")
        show_empty(tree, "No searches yet.", palette)
        assert label.winfo_manager() == ""
        tree.delete(*tree.get_children())
        show_empty(tree, "Nothing learned yet.", palette)
        assert label.winfo_manager() == "place" and label.cget("text") == "Nothing learned yet."
    finally:
        tree.destroy()


def test_empty_table_guidance_wraps_inside_a_narrow_table(shared_tk):
    # Private networking's contact list is about 345 pixels wide: a fixed
    # 560-pixel wrap cut its guidance off at both edges.
    palette = SimpleNamespace(PANEL_2="#101820", MUTED="#8899AA")
    tree = ttk.Treeview(shared_tk, columns=("a",), show="headings")
    tree.place(x=0, y=0, width=345, height=300)
    try:
        show_empty(tree, "Your private contact book is empty. Add a contact, or find professional contacts "
                         "to review public sources before saving.", palette)
        shared_tk.update()
        label = tree._hayat_empty
        assert int(label.cget("wraplength")) <= 345 - 24
        assert label.winfo_reqwidth() <= 345
        tree.place_configure(width=1400)
        shared_tk.update()
        assert int(label.cget("wraplength")) == 560
    finally:
        tree.destroy()


def test_a_refused_connection_is_said_as_what_to_do():
    message = plain_error("[WinError 10061] No connection could be made because the target machine actively refused it")
    assert "model service isn't running" in message and "Refresh models" in message and "10061" not in message
    assert "loading a model" in plain_error("ReadTimeout: timed out")
    assert plain_error("Model 'x' is not installed.") == "Model 'x' is not installed."


def test_the_home_row_and_composer_after_the_pass():
    from pathlib import Path
    source = Path("app/gui.py").read_text(encoding="utf-8")
    # The newest workspaces start from home; the hint lives in the box, not on a row of its own.
    assert "('Find jobs', self._open_jobs), ('Markets', self._open_markets)" in source
    assert "Enter sends · Shift+Enter: new line" in source
    assert "media_tools.add(tk.Label(media_tools, text='Enter to send" not in source


def test_every_text_area_chooses_its_font():
    """Unset, a Tk text area falls back to Courier and plain-language results read like a printout.

    Prose uses Segoe UI; a box where the owner types JSON or code says so with Consolas.
    """
    import ast
    from pathlib import Path

    from app.config import PROJECT_ROOT

    missing = []
    for path in sorted((PROJECT_ROOT / "app").glob("gui*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            owner = getattr(getattr(func, "value", None), "id", "")
            if name == "DarkScrolledText" or (name == "Text" and owner == "tk"):
                keywords = {keyword.arg for keyword in node.keywords}
                if "font" not in keywords and None not in keywords:
                    missing.append(f"{Path(path).name}:{node.lineno}")
    assert not missing, missing
