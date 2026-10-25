import hashlib
import json

import pytest

from app.brain_asset import ASSET, LIMITS, MODEL_CREDITS, asset_facets, read_facets


def test_bundled_model_has_bounded_geometry_and_reproducible_credit():
    manifest = json.loads(ASSET.with_name('manifest.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(ASSET.read_bytes()).hexdigest() == manifest['geometry_sha256']
    assert manifest['license'] == 'CC-BY-4.0'
    assert manifest['source_triangles'] == 4850
    assert manifest['source_url'] in MODEL_CREDITS
    assert manifest['creator'] in MODEL_CREDITS and manifest['license_url'] in MODEL_CREDITS
    for detail, budget in LIMITS.items():
        facets = asset_facets(detail)
        assert 0 < len(facets) <= budget
        assert len(facets) == manifest['triangles'][detail]
        assert all(len(face) == 3 and all(len(vertex) == 3 for vertex in face) for face in facets)
    assert len(asset_facets('Low')) < len(asset_facets('Normal')) < len(asset_facets('High')) < len(asset_facets('Ultra'))
    assert len(asset_facets('Ultra')) == manifest['source_triangles']


@pytest.mark.parametrize('vertices,faces', [
    ([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 99]]),
    ([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 1]]),
    ([[0, 0, 0], [float('nan'), 0, 0], [0, 1, 0]], [[0, 1, 2]]),
    ([[0, 0, 0], [100, 0, 0], [0, 1, 0]], [[0, 1, 2]]),
    ([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]] * 385),
])
def test_invalid_geometry_is_rejected_before_canvas_rendering(tmp_path, vertices, faces):
    path = tmp_path / 'mesh.json'
    path.write_text(json.dumps({'schema': 1, 'levels': {'Low': {'vertices': vertices, 'faces': faces}}}))
    with pytest.raises(ValueError):
        read_facets(path, 'Low')


def test_graph_switches_models_without_changing_subsystems_and_falls_back_on_missing_asset(shared_tk, monkeypatch):
    from tests.conftest import open_window, close_window
    from app.gui_brain import BrainGraph
    from app.gui_theme import Palette
    root = open_window(shared_tk, '720x480-4000-4000')
    graph = BrainGraph(root, Palette, lambda _: None)
    graph.pack(fill='both', expand=True)
    graph.set_motion(False)
    root.update()
    try:
        assert graph.asset_loaded
        positions = dict(graph._positions)
        assert graph.find_withtag('model-credit')
        assert not graph.find_withtag('fissure')
        graph.set_geometry('Procedural')
        assert not graph.asset_loaded and graph._positions == positions
        assert graph.find_withtag('fissure') and not graph.find_withtag('model-credit')
        def missing(_):
            raise FileNotFoundError('asset missing')
        monkeypatch.setattr('app.brain_asset.asset_facets', missing)
        graph.set_geometry('Sketchfab brain')
        assert not graph.asset_loaded and graph.asset_problem
        assert graph._positions == positions
        assert 'unavailable' in graph.itemcget(graph.find_withtag('model-credit')[0], 'text')
    finally:
        close_window(root)


def test_ultra_geometry_reduces_during_drag_and_restores_without_animation(shared_tk):
    from types import SimpleNamespace
    from tests.conftest import open_window, close_window
    from app.gui_brain import BrainGraph
    from app.gui_theme import Palette
    root = open_window(shared_tk, '900x600-4000-4000')
    graph = BrainGraph(root, Palette, lambda _: None)
    graph.pack(fill='both', expand=True)
    graph.set_motion(False)
    root.update()
    try:
        graph.set_surface('Folded', detail='Ultra')
        assert graph.geometry_status()['triangles'] == 4850 and graph.asset_loaded
        keys = set(graph._positions)
        graph._press(SimpleNamespace(x=100, y=100))
        graph._drag(SimpleNamespace(x=150, y=130))
        assert graph.render_detail == 'Low' and len(graph._shell) == 384
        graph._release(SimpleNamespace(x=150, y=130))
        assert graph.render_detail == 'Ultra' and len(graph._shell) == 4850
        assert set(graph._positions) == keys
        graph.set_surface('Folded', detail='Auto')
        assert graph.render_detail == 'High'
        root.geometry('500x320-4000-4000')
        root.update()
        assert graph.render_detail == 'Normal'
        assert graph.geometry_status()['requested_detail'] == 'Auto'
    finally:
        close_window(root)
