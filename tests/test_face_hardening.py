"""Local enrolled-face safeguards using synthetic vectors and isolated files.

No camera, downloaded model, owner gallery, real identity or web lookup is used.
The fixture codec is deliberately reversible; real account encryption has a
separate Windows-only roundtrip test.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path

import pytest

from app.perception.faces import DetectedFace, FaceEngine, FaceGallery, FaceRecognizer, SFACE, YUNET


PREFIX = b'HAYAT-FACES-DPAPI\0'


def fixture_codec(data, *, decrypt=False):
    if decrypt:
        assert data.startswith(b'fixture:')
        return base64.b64decode(data[len(b'fixture:'):], validate=True)
    return b'fixture:' + base64.b64encode(data)


def gallery(tmp_path, name='gallery.json'):
    return FaceGallery(tmp_path / name, codec=fixture_codec)


@pytest.mark.parametrize('signature', [
    [], [0.0, 0.0, 0.0], [float('nan'), 0.0], [float('inf'), 0.0],
    [float('-inf'), 0.0], ['not-numeric', 0.0], [True, False], [1.0] * 513,
    {'vector': [1.0, 0.0]}, None,
])
def test_invalid_signature_never_enrolls_or_creates_a_gallery(tmp_path, signature):
    book = gallery(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        book.enroll('Synthetic Sample', signature)
    assert not book.path.exists()
    assert book.people() == []


def test_stored_dimensions_are_consistent_and_mismatch_cannot_be_a_perfect_match(tmp_path):
    book = gallery(tmp_path)
    book.enroll('Synthetic Sample', [1.0, 0.0, 0.0])
    before = book.path.read_bytes()
    with pytest.raises(ValueError):
        book.enroll('Different Model Dimension', [1.0, 0.0, 0.0, 0.0])
    assert book.path.read_bytes() == before
    assert book.match([1.0, 0.0, 0.0, 0.0])[0] is None
    assert book.assess([1.0, 0.0, 0.0, 0.0])['name'] is None


def test_cosine_similarity_is_scale_invariant_and_honestly_labelled(tmp_path):
    book = gallery(tmp_path)
    book.enroll('Synthetic Sample', [5.0, 0.0, 0.0])
    result = book.assess([2.0, 0.0, 0.0])
    assert result['name'] == 'Synthetic Sample'
    assert result['score'] == pytest.approx(1.0)
    assert result['threshold'] > 0
    assert 'cosine' in result['metric'].casefold()
    assert result['calibrated_probability'] is False


@pytest.mark.parametrize('second', [[1.0, 0.0, 0.0], [0.999, 0.02, 0.0]])
def test_tied_or_close_distinct_identities_abstain_without_exposing_candidate_names(tmp_path, second):
    book = gallery(tmp_path)
    book.enroll('First Synthetic Identity', [1.0, 0.0, 0.0])
    book.enroll('Second Synthetic Identity', second)
    result = book.assess([1.0, 0.0, 0.0])
    assert result['match'] == 'ambiguous'
    assert result['name'] is None
    assert result['margin'] < 0.08
    assert book.match([1.0, 0.0, 0.0])[0] is None
    encoded = json.dumps(result)
    assert 'First Synthetic Identity' not in encoded
    assert 'Second Synthetic Identity' not in encoded


def test_repeated_samples_of_one_identity_do_not_count_as_two_competing_people(tmp_path):
    book = gallery(tmp_path)
    book.enroll('Synthetic Sample', [1.0, 0.0, 0.0])
    book.enroll('Synthetic Sample', [0.999, 0.01, 0.0])
    assert book.assess([1.0, 0.0, 0.0])['name'] == 'Synthetic Sample'


def test_valid_legacy_gallery_is_not_rewritten_until_an_explicit_edit(tmp_path):
    path = tmp_path / 'legacy.json'
    legacy = {'version': 1, 'people': {'Legacy Synthetic Sample': {
        'added': 10.0, 'updated': 20.0, 'embeddings': [[1.0, 0.0, 0.0]]}}}
    original = json.dumps(legacy).encode()
    path.write_bytes(original)
    book = FaceGallery(path, codec=fixture_codec)
    assert not book.error
    assert book.match([1.0, 0.0, 0.0])[0] == 'Legacy Synthetic Sample'
    assert path.read_bytes() == original
    book.enroll('New Synthetic Sample', [0.0, 1.0, 0.0])
    encrypted = path.read_bytes()
    assert encrypted.startswith(PREFIX)
    assert b'Legacy Synthetic Sample' not in encrypted and b'New Synthetic Sample' not in encrypted
    decoded = json.loads(fixture_codec(encrypted[len(PREFIX):], decrypt=True))
    assert decoded['version'] == 2
    assert len(FaceGallery(path, codec=fixture_codec).people()) == 2


@pytest.mark.parametrize('payload', [
    b'not JSON', b'[]', b'{"version":99,"people":{}}',
    b'{"version":1,"people":{"Bad":"not-a-record"}}',
    b'{"version":1,"people":{"Bad":{"embeddings":[[]]}}}',
    b'{"version":1,"people":{"Bad":{"embeddings":[[NaN,0,0]]}}}',
    b'{"version":1,"people":{"One":{"embeddings":[[1,0]]},"Two":{"embeddings":[[1,0,0]]}}}',
    PREFIX + b'corrupt ciphertext',
])
def test_corrupt_gallery_is_preserved_and_cannot_be_overwritten_by_mutation(tmp_path, payload):
    path = tmp_path / 'corrupt.json'
    path.write_bytes(payload)
    book = FaceGallery(path, codec=fixture_codec)
    assert book.error
    for mutation in [lambda: book.enroll('Do Not Overwrite', [1.0, 0.0, 0.0]), book.clear]:
        with pytest.raises((ValueError, RuntimeError)):
            mutation()
        assert path.read_bytes() == payload


@pytest.mark.parametrize('operation', ['enroll', 'forget', 'clear'])
def test_failed_encryption_rolls_back_memory_and_preserves_the_original_file(tmp_path, operation):
    failing = [False]
    def codec(data, *, decrypt=False):
        if failing[0] and not decrypt:
            raise RuntimeError('synthetic encryption failure')
        return fixture_codec(data, decrypt=decrypt)
    book = FaceGallery(tmp_path / 'rollback.json', codec=codec)
    book.enroll('Retained Synthetic Sample', [1.0, 0.0, 0.0])
    before = book.path.read_bytes()
    failing[0] = True
    mutation = {'enroll': lambda: book.enroll('Rejected Synthetic Sample', [0.0, 1.0, 0.0]),
                'forget': lambda: book.forget('Retained Synthetic Sample'), 'clear': book.clear}[operation]
    with pytest.raises(RuntimeError, match='synthetic encryption failure'):
        mutation()
    failing[0] = False
    assert book.path.read_bytes() == before
    assert [row['name'] for row in book.people()] == ['Retained Synthetic Sample']
    assert [row['name'] for row in FaceGallery(book.path, codec=codec).people()] == ['Retained Synthetic Sample']


def test_stale_gallery_forgetting_cannot_resurrect_an_already_deleted_person(tmp_path):
    path = tmp_path / 'shared.json'
    first = FaceGallery(path, codec=fixture_codec)
    first.enroll('First Synthetic Sample', [1.0, 0.0, 0.0])
    first.enroll('Second Synthetic Sample', [0.0, 1.0, 0.0])
    stale = FaceGallery(path, codec=fixture_codec)
    assert first.forget('First Synthetic Sample')
    assert stale.forget('Second Synthetic Sample')
    assert FaceGallery(path, codec=fixture_codec).people() == []


def test_concurrent_instances_do_not_lose_enrollments_or_share_partial_paths(tmp_path):
    path = tmp_path / 'concurrent.json'
    def add(index):
        FaceGallery(path, codec=fixture_codec).enroll(f'Synthetic Sample {index}', [1.0, 0.0, 0.0])
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(add, range(24)))
    assert len(FaceGallery(path, codec=fixture_codec).people()) == 24
    assert not list(tmp_path.glob('*.tmp'))


class SyntheticEngine:
    embedding_dimension = 3

    def __init__(self, faces):
        self.detected = faces
        self.reads = 0

    def recognition_available(self):
        return True

    def status(self):
        return {'recognition': True, 'detection': True, 'reason': ''}

    def read_faces(self, path):
        self.reads += 1
        return self.detected


def face(embedding):
    return DetectedFace((0, 0, 20, 20), 0.99, embedding)


@pytest.mark.parametrize('consent', [False, None, 1, 'yes', [], {}])
def test_enrollment_requires_literal_opt_in_before_reading_any_image(tmp_path, consent):
    engine = SyntheticEngine([face([1.0, 0.0, 0.0])])
    book = gallery(tmp_path)
    recognizer = FaceRecognizer(engine, book)
    result = recognizer.enroll('Synthetic Sample', 'never-open-this-image.jpg', consent=consent)
    assert result['enrolled'] is False
    assert engine.reads == 0
    assert not book.path.exists()


def test_one_detected_signed_face_with_opt_in_can_be_enrolled(tmp_path):
    engine = SyntheticEngine([face([1.0, 0.0, 0.0])])
    book = gallery(tmp_path)
    assert FaceRecognizer(engine, book).enroll('Synthetic Sample', 'synthetic.jpg', consent=True)['enrolled'] is True
    assert engine.reads == 1 and len(book.people()) == 1


@pytest.mark.parametrize('detected', [[face([1.0, 0.0, 0.0]), face(None)],
                                     [face(None), face([1.0, 0.0, 0.0])], [face(None)]])
def test_unsigned_extra_faces_and_unreadable_faces_cannot_be_enrolled(tmp_path, detected):
    book = gallery(tmp_path)
    result = FaceRecognizer(SyntheticEngine(detected), book).enroll('Synthetic Sample', 'synthetic.jpg', consent=True)
    assert result['enrolled'] is False
    assert not book.path.exists()


def test_production_model_dimension_is_128_and_wrong_signature_is_not_enrolled(tmp_path):
    assert FaceEngine.embedding_dimension == 128
    engine = SyntheticEngine([face([1.0, 0.0, 0.0])])
    engine.embedding_dimension = 128
    book = gallery(tmp_path)
    try:
        result = FaceRecognizer(engine, book).enroll('Synthetic Sample', 'synthetic.jpg', consent=True)
    except ValueError:
        result = {'enrolled': False}
    assert result['enrolled'] is False
    assert not book.path.exists()


def test_stop_before_or_during_image_read_prevents_persistence(tmp_path):
    stopped = [True]
    def check():
        if stopped[0]:
            raise PermissionError('synthetic stop')
    engine = SyntheticEngine([face([1.0, 0.0, 0.0])])
    book = gallery(tmp_path)
    recognizer = FaceRecognizer(engine, book)
    with pytest.raises(PermissionError, match='synthetic stop'):
        recognizer.enroll('Synthetic Sample', 'synthetic.jpg', consent=True, check=check)
    assert engine.reads == 0 and not book.path.exists()
    stopped[0] = False
    original_read = engine.read_faces
    def interrupted_read(path):
        result = original_read(path)
        stopped[0] = True
        return result
    engine.read_faces = interrupted_read
    with pytest.raises(PermissionError, match='synthetic stop'):
        recognizer.enroll('Synthetic Sample', 'synthetic.jpg', consent=True, check=check)
    assert not book.path.exists()


def test_hash_validation_rejects_existing_and_subsequently_tampered_models(tmp_path, monkeypatch):
    from app.perception import faces
    payloads = {YUNET: b'synthetic detector model', SFACE: b'synthetic recognition model'}
    monkeypatch.setattr(faces, 'FACE_MODEL_SOURCES', {
        name: ('https://example.org/' + name, hashlib.sha256(data).hexdigest()) for name, data in payloads.items()})
    folder = tmp_path / 'models'
    folder.mkdir()
    for name in payloads:
        (folder / name).write_bytes(b'wrong bytes')
    engine = FaceEngine(folder)
    assert engine.recognition_available() is False
    for name, payload in payloads.items():
        (folder / name).write_bytes(payload)
    assert engine.recognition_available() is True
    # Same-size replacement catches integrity caches that consider only file length.
    original = payloads[SFACE]
    (folder / SFACE).write_bytes(bytes(byte ^ 1 for byte in original))
    assert engine.recognition_available() is False


@pytest.mark.skipif(os.name != 'nt', reason='DPAPI account encryption requires Windows')
def test_real_windows_gallery_is_account_encrypted_and_reopens(tmp_path):
    path = tmp_path / 'real-account-gallery.json'
    book = FaceGallery(path)
    book.enroll('Synthetic Encryption Sample', [1.0, 0.0, 0.0])
    encrypted = path.read_bytes()
    assert encrypted.startswith(PREFIX)
    assert b'Synthetic Encryption Sample' not in encrypted
    assert FaceGallery(path).match([1.0, 0.0, 0.0])[0] == 'Synthetic Encryption Sample'


def test_runtime_gallery_paths_are_redirected_away_from_owner_biometrics():
    from tests.conftest import LIVE_DATA, SANDBOX
    book = FaceGallery(LIVE_DATA / 'faces' / 'gallery.json', codec=fixture_codec)
    assert Path(book.path).resolve().is_relative_to(SANDBOX.resolve())


def test_very_large_finite_vectors_are_handled_without_nan_scores(tmp_path):
    book = gallery(tmp_path)
    try:
        book.enroll('Large Synthetic Sample', [1e308, 1e308, 0.0])
    except ValueError:
        assert not book.path.exists()
        return
    assessment = book.assess([1e308, 1e308, 0.0])
    assert math.isfinite(assessment['score'])
    assert -1 <= assessment['score'] <= 1
