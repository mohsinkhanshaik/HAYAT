"""Local consent, path, interruption and private-diagnostic boundaries."""
from types import SimpleNamespace
import json

import pytest

from app.events import EventBus
from app.perception.faces import DetectedFace, FaceGallery, FaceRecognizer, format_recognition_report
from app.safety.permissions import PermissionManager
from app.tools.faces import face_controls, register_face_tools
from app.tools.registry import ToolRegistry


def tools(tmp_path):
    calls = []
    engine = SimpleNamespace(recognition_available=lambda: True,
                             status=lambda: {'detection': True, 'recognition': True})
    engine.read_faces = lambda path: calls.append(path) or [DetectedFace((0, 0, 10, 10), .99, [1., 0., 0.])]
    gallery = FaceGallery(tmp_path / 'isolated-gallery.json')
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    register_face_tools(registry, FaceRecognizer(engine, gallery))
    return registry, gallery, engine, calls


def test_missing_or_false_consent_never_reads_image_or_writes_a_gallery(tmp_path):
    registry, gallery, _engine, calls = tools(tmp_path)
    missing = registry.execute('face_enroll', {'name': 'Local Sample', 'path': 'sample.png'})
    assert not missing.success
    refused = registry.execute('face_enroll', {'name': 'Local Sample', 'path': 'sample.png', 'consent': False})
    assert refused.success and refused.output['enrolled'] is False
    assert not calls and not gallery.path.exists()


def test_write_permission_does_not_bypass_image_read_denial(tmp_path):
    registry, gallery, _engine, calls = tools(tmp_path)
    registry.permissions.policies['filesystem_read'] = 'DENY'
    result = registry.execute('face_enroll', {'name': 'Local Sample', 'path': 'sample.png', 'consent': True})
    assert not result.success and not calls and not gallery.path.exists()


@pytest.mark.parametrize('path', ['https://example.org/photo.png', 'file:///private/photo.png',
                                '//private-server/photo.png', '\\\\private-server\\photo.png'])
def test_urls_and_network_shares_are_not_local_face_inputs(tmp_path, path):
    registry, gallery, _engine, calls = tools(tmp_path)
    for tool, arguments in [('face_recognize', {'path': path}),
                            ('face_enroll', {'name': 'Local Sample', 'path': path, 'consent': True})]:
        assert not registry.execute(tool, arguments).success
    assert not calls and not gallery.path.exists()


def test_protected_and_outside_paths_never_reach_native_reader(tmp_path):
    registry, _gallery, _engine, calls = tools(tmp_path)
    registry.permissions.config['protected_project_files'] = ['.env']
    for path in (tmp_path / '.env', tmp_path.parent / 'outside.png'):
        assert not registry.execute('face_recognize', {'path': str(path)}).success
    assert not calls


def test_cancel_during_native_read_prevents_enrollment_and_context_does_not_leak(tmp_path):
    registry, gallery, engine, calls = tools(tmp_path)
    active = [True]
    original = engine.read_faces
    def read(path):
        result = original(path)
        active[0] = False
        return result
    engine.read_faces = read
    arguments = {'name': 'Local Sample', 'path': 'sample.png', 'consent': True}
    with face_controls(check=lambda: active[0]):
        result = registry.execute('face_enroll', arguments)
    assert not result.success and calls and not gallery.path.exists()
    assert registry.execute('face_enroll', arguments).output['enrolled']


def test_local_names_and_image_paths_are_absent_from_durable_tool_events(tmp_path):
    registry, _gallery, _engine, _calls = tools(tmp_path)
    bus = EventBus(tmp_path / 'events.db')
    registry.events = bus
    try:
        result = registry.execute('face_enroll', {'name': 'BiometricNameCanary', 'path': 'BiometricPathCanary.png', 'consent': True})
        assert result.success and result.output['enrolled']
        compared = registry.execute('face_recognize', {'path': 'BiometricPathCanary.png'})
        assert compared.output['faces'][0]['name'] == 'BiometricNameCanary'
        bus.flush()
        serialized = json.dumps(bus.recent(100))
        assert 'BiometricNameCanary' not in serialized and 'BiometricPathCanary' not in serialized
    finally:
        bus.close()


def test_default_gallery_construction_does_not_inspect_biometrics(monkeypatch):
    monkeypatch.setattr(FaceGallery, '_load', lambda _: pytest.fail('Default runtime construction must stay lazy'))
    FaceGallery()


def test_report_and_chat_keep_ambiguous_faces_unnamed_and_scores_nonprobabilistic():
    from app.observed_reply import render
    result = {'available': True, 'enrolled_known': 0, 'count': 1, 'unknown': 0, 'ambiguous': 1,
              'faces': [{'name': None, 'match': 'ambiguous', 'score': .9998, 'metric': 'cosine_similarity',
                         'threshold': .363, 'margin': .001, 'required_margin': .08, 'box': [0, 0, 10, 10]}]}
    text = format_recognition_report(result)
    assert 'Ambiguous' in text and 'no name assigned' in text and '0.9998' in text
    assert '99.98%' not in text and 'not identity percentages' in text
    assert render(result) == text
