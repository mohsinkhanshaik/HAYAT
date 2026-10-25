"""Product create/edit/generate/package flows run through the actual registry."""
import time
from types import SimpleNamespace

import pytest

from app.gui_business import BusinessStudio
from app.gui_products import ProductForm
from app.gui_theme import Palette
from app.safety.permissions import PermissionManager
from app.tools.business import BusinessLedger
from app.tools.filesystem import register_filesystem_tools
from app.tools.product_workshop import register_product_workspace_tools
from app.tools.products import ProductStore, register_product_tools
from app.tools.registry import ToolRegistry


def wait(window, predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        window.update()
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


@pytest.fixture
def studio(shared_tk, tmp_path):
    permission = PermissionManager({'default': 'ALLOW'}, tmp_path)
    registry = ToolRegistry(permission)
    filesystem = register_filesystem_tools(registry)
    products = ProductStore(tmp_path / 'products.db', tmp_path / 'artifacts/products', permission)
    ledger = BusinessLedger(tmp_path / 'business.db', tmp_path / 'artifacts/business', permission, products=products)
    register_product_tools(registry, products)
    register_product_workspace_tools(registry, products, filesystem, ledger)
    class GUI(Palette):
        def use_example(self, text):
            self.used = text
    gui = GUI()
    gui.root = shared_tk
    gui.runtime = SimpleNamespace(business=ledger, products=products, registry=registry)
    window = BusinessStudio(gui)
    window.withdraw()
    wait(window, lambda: not window.products_tab._busy)
    yield window
    window.destroy()


def fill(form, title='My creator kit', price='9.99'):
    for key, value in {'title': title, 'audience': 'Creators', 'price': price}.items():
        form.values[key].set(value)
    for key, value in {'description': 'Make content with attributable evidence.', 'license_text': 'Personal use only.'}.items():
        form.multiline[key].insert('1.0', value)


def test_product_form_validates_then_builds_edits_and_packages(studio):
    tab = studio.products_tab
    studio.show_tab('Products')
    form = ProductForm(tab)
    fill(form, price='not a price')
    form.save()
    wait(studio, lambda: not tab._busy)
    assert form.winfo_exists() and not form._saving
    assert 'monetary' in form.message.get().lower()
    form.values['price'].set('9.99')
    form.save()
    wait(studio, lambda: tab.current is not None and not tab._busy)
    assert len(tab.tree.get_children()) == 1
    assert not form.winfo_exists()
    tab.preset.set('Content calendar')
    tab.build_starter()
    wait(studio, lambda: len(tab.files.get_children()) == 5 and not tab._busy)
    tab.package()
    wait(studio, lambda: tab.current['bundle'] is not None and not tab._busy)
    tab.verify()
    wait(studio, lambda: not tab._busy)
    assert 'integrity verified' in tab.state.get()
    edit = ProductForm(tab, tab.current)
    edit.values['title'].set('Revised creator kit')
    edit.save()
    wait(studio, lambda: tab.current['product']['title'] == 'Revised creator kit' and not tab._busy)
    tab.verify()
    wait(studio, lambda: not tab._busy)
    assert 'listing changed' in tab.state.get()
    tab.refine()
    assert 'Read product_details' in studio.gui.used


def test_slow_worker_and_close_do_not_touch_tk_or_reopen_views(studio, monkeypatch):
    import threading
    tab = studio.products_tab
    release = threading.Event()
    started = threading.Event()
    execute = tab.registry.execute
    def slow(name, arguments):
        started.set()
        release.wait(3)
        return execute(name, arguments)
    monkeypatch.setattr(tab.registry, 'execute', slow)
    begin = time.monotonic()
    tab.refresh()
    assert time.monotonic() - begin < .1
    assert started.wait(1)
    try:
        tab.frame.destroy()
        assert tab._closed
    finally:
        release.set()


@pytest.mark.parametrize('size', ['980x820', '760x650'])
def test_product_actions_remain_inside_the_window_when_resized(studio, size):
    studio.show_tab('Products')
    studio.geometry(size)
    studio.deiconify()
    studio.update()
    tab = studio.products_tab
    for button in tab.actions:
        assert button.winfo_rooty() + button.winfo_height() <= studio.winfo_rooty() + studio.winfo_height(), button.cget('text')
        assert button.winfo_rootx() + button.winfo_width() <= studio.winfo_rootx() + studio.winfo_width(), button.cget('text')
