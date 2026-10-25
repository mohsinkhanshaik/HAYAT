"""Still-image decoding, exact-template retention and consistent local comparisons.

No model, real face, camera, network or owner gallery is used here.
"""
from __future__ import annotations

from io import BytesIO
import math
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from app.perception import faces
from app.perception.faces import DetectedFace, FaceEngine, FaceGallery, FaceRecognizer, format_recognition_report
from tests.test_face_hardening import fixture_codec


def book(tmp_path):
    return FaceGallery(tmp_path / 'synthetic-local-gallery.json', codec=fixture_codec)


def synthetic_engine(vectors):
    return SimpleNamespace(recognition_available=lambda: True, embedding_dimension=3,
                           read_faces=lambda _path: [DetectedFace((i * 30, 0, 20, 20), .97, v)
                                                     for i, v in enumerate(vectors)])


@pytest.mark.parametrize('scale', [1., 2., .5, 1e200, 1e-200])
def test_numerically_same_template_does_not_consume_another_retention_slot(tmp_path, scale):
    gallery = book(tmp_path)
    gallery.enroll('Synthetic Person', [1., 2., 3.], consent=True)
    result = gallery.enroll('Synthetic Person', [scale, 2 * scale, 3 * scale], consent=True)
    assert result['duplicate_sample'] is True
    assert result['sample_added'] is False and result['signatures'] == 1
    assert gallery.people()[0]['consent_attested_at'] is not None


def test_repeated_duplicate_cannot_evict_varied_templates(tmp_path):
    gallery = book(tmp_path)
    varied = [[1., float(i), .25] for i in range(faces.MAX_PER_PERSON)]
    for signature in varied:
        assert gallery.enroll('Synthetic Person', signature, consent=True)['sample_added']
    before = gallery._load()['Synthetic Person']['embeddings']
    for _ in range(12):
        result = gallery.enroll('Synthetic Person', varied[-1], consent=True)
        assert result['duplicate_sample'] and result['signatures'] == faces.MAX_PER_PERSON
    assert gallery._load()['Synthetic Person']['embeddings'] == before


def test_similar_but_numerically_distinct_samples_remain_distinct(tmp_path):
    gallery = book(tmp_path)
    gallery.enroll('Synthetic Person', [1., 0., 0.])
    result = gallery.enroll('Synthetic Person', [1., 1e-7, 0.])
    assert result['sample_added'] is True and result['duplicate_sample'] is False
    assert result['signatures'] == 2


def test_identical_templates_for_different_names_stay_ambiguous(tmp_path):
    gallery = book(tmp_path)
    gallery.enroll('Synthetic One', [1., 0., 0.])
    gallery.enroll('Synthetic Two', [1., 0., 0.])
    result = gallery.assess([1., 0., 0.])
    assert result['name'] is None and result['match'] == 'ambiguous'
    assert result['compatible_people'] == result['gallery_people'] == 2


def test_one_image_uses_one_gallery_snapshot_even_if_file_changes_between_faces(tmp_path, monkeypatch):
    gallery = book(tmp_path)
    gallery.enroll('Synthetic Person', [1., 0., 0.])
    load, reads = gallery._load, []
    def changing_load():
        reads.append(True)
        return load() if len(reads) == 1 else {}
    monkeypatch.setattr(gallery, '_load', changing_load)
    result = FaceRecognizer(synthetic_engine([[1., 0., 0.], [1., 0., 0.]]), gallery).recognize('not-read.png')
    assert len(reads) == 1
    assert result['enrolled_known'] == 2
    assert {face['name'] for face in result['faces']} == {'Synthetic Person'}


def test_local_batch_can_be_stopped_between_faces(tmp_path, monkeypatch):
    gallery = book(tmp_path)
    gallery.enroll('Synthetic Person', [1., 0., 0.])
    assessed, original = [], gallery._assess_loaded
    def tracked(vector):
        assessed.append(vector)
        return original(vector)
    monkeypatch.setattr(gallery, '_assess_loaded', tracked)
    def check():
        if assessed:
            raise InterruptedError('synthetic stop between faces')
    with pytest.raises(InterruptedError, match='between faces'):
        FaceRecognizer(synthetic_engine([[1., 0., 0.]] * 3), gallery).recognize('not-read.png', check=check)
    assert len(assessed) == 1


def test_report_distinguishes_detection_score_and_single_person_coverage(tmp_path):
    gallery = book(tmp_path)
    gallery.enroll('Synthetic Person', [1., 0., 0.])
    result = FaceRecognizer(synthetic_engine([[1., 0., 0.]]), gallery).recognize('not-read.png')
    text = format_recognition_report(result)
    assert '0.9700 (face detection only' in text
    assert 'Cosine similarity: 1.0000' in text
    assert '1 compatible person(s) among 1 enrolled locally' in text
    assert 'separation from other people cannot be checked' in text
    assert '%' not in text


@pytest.mark.parametrize('vector', [None, [], [float('nan'), 0., 0.], [1., 0.]])
def test_bad_native_signature_is_unreadable_without_exposing_enrolled_name(tmp_path, vector):
    gallery = book(tmp_path)
    gallery.enroll('Synthetic Private Name', [1., 0., 0.])
    result = FaceRecognizer(synthetic_engine([vector]), gallery).recognize('not-read.png')
    assert result['unreadable'] == 1 and result['enrolled_known'] == 0
    assert 'Synthetic Private Name' not in format_recognition_report(result)


def capture_decode(monkeypatch):
    engine, captured = FaceEngine('models-that-are-not-read'), []
    monkeypatch.setattr(engine, 'faces', lambda image: captured.append(image) or [])
    return engine, captured


def test_unicode_path_decodes_exact_rgb_to_contiguous_bgr_without_native_file_reader(tmp_path, monkeypatch):
    path = tmp_path / 'صورة-家族-é.png'
    Image.new('RGB', (7, 4), (11, 22, 33)).save(path)
    engine, captured = capture_decode(monkeypatch)
    assert engine.read_faces(path) == []
    assert captured[0].shape == (4, 7, 3)
    assert captured[0].flags['C_CONTIGUOUS'] and captured[0].flags['OWNDATA']
    assert captured[0][0, 0].tolist() == [33, 22, 11]


def test_exif_orientation_is_applied_before_detection(tmp_path, monkeypatch):
    path = tmp_path / 'rotated.png'
    picture = Image.new('RGB', (8, 3), (255, 0, 0))
    picture.putpixel((0, 0), (0, 255, 0))
    exif = Image.Exif()
    exif[274] = 6  # displayed 90 degrees clockwise
    picture.save(path, exif=exif)
    engine, captured = capture_decode(monkeypatch)
    engine.read_faces(path)
    assert captured[0].shape == (8, 3, 3)
    assert captured[0][0, 2].tolist() == [0, 255, 0]


@pytest.mark.parametrize('mode', ['L', 'RGBA', 'P'])
def test_supported_pixel_modes_become_three_channel_images(tmp_path, monkeypatch, mode):
    path = tmp_path / 'mode.png'
    Image.new(mode, (9, 6)).save(path)
    engine, captured = capture_decode(monkeypatch)
    engine.read_faces(path)
    assert captured[0].shape == (6, 9, 3) and captured[0].dtype == np.uint8


def test_animation_is_rejected_instead_of_silently_recognizing_first_frame(tmp_path, monkeypatch):
    path = tmp_path / 'animated.gif'
    Image.new('RGB', (8, 8), 'red').save(path, save_all=True, append_images=[Image.new('RGB', (8, 8), 'blue')])
    engine, captured = capture_decode(monkeypatch)
    with pytest.raises(ValueError, match='one still image'):
        engine.read_faces(path)
    assert not captured


def test_multi_page_tiff_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / 'pages.tiff'
    Image.new('RGB', (8, 8)).save(path, save_all=True, append_images=[Image.new('RGB', (8, 8), 'blue')])
    engine, captured = capture_decode(monkeypatch)
    with pytest.raises(ValueError, match='one still image'):
        engine.read_faces(path)
    assert not captured


@pytest.mark.parametrize(('limit', 'maximum'), [('MAX_IMAGE_BYTES', 10), ('MAX_IMAGE_PIXELS', 10), ('MAX_IMAGE_DIMENSION', 2)])
def test_declared_resource_limits_fail_before_detection(tmp_path, monkeypatch, limit, maximum):
    path = tmp_path / 'too-large.png'
    Image.new('RGB', (8, 8)).save(path)
    monkeypatch.setattr(faces, limit, maximum)
    engine, captured = capture_decode(monkeypatch)
    with pytest.raises(ValueError, match='MiB|million pixels'):
        engine.read_faces(path)
    assert not captured


@pytest.mark.parametrize('contents', [b'', b'not an image', b'\x89PNG\r\n\x1a\ntruncated'])
def test_corrupt_images_are_explained_without_native_detection(tmp_path, monkeypatch, contents):
    path = tmp_path / 'bad.png'
    path.write_bytes(contents)
    engine, captured = capture_decode(monkeypatch)
    with pytest.raises(ValueError, match='MiB|readable'):
        engine.read_faces(path)
    assert not captured


def test_pixel_limit_is_checked_before_full_decode(tmp_path, monkeypatch):
    path = tmp_path / 'header-only-check.png'
    Image.new('RGB', (30, 20)).save(path)
    monkeypatch.setattr(faces, 'MAX_IMAGE_PIXELS', 100)
    monkeypatch.setattr(Image.Image, 'load', lambda _: pytest.fail('Full decode occurred before dimension check'))
    engine, captured = capture_decode(monkeypatch)
    with pytest.raises(ValueError, match='million pixels'):
        engine.read_faces(path)
    assert not captured


def test_normalization_tolerance_is_only_machine_roundoff_not_an_identity_threshold(tmp_path):
    gallery = book(tmp_path)
    gallery.enroll('Synthetic Person', [1., math.sqrt(2), 0.])
    expected = gallery._load()['Synthetic Person']['embeddings'][0]
    for _ in range(4):
        assert gallery.enroll('Synthetic Person', expected)['duplicate_sample']
    assert gallery.people()[0]['signatures'] == 1


def test_decode_limits_are_returned_as_enrollment_failure(tmp_path):
    path = tmp_path / 'bad.png'
    path.write_bytes(b'not an image')
    engine = FaceEngine('models-that-are-not-read')
    engine.recognition_available = lambda: True
    result = FaceRecognizer(engine, book(tmp_path)).enroll('Synthetic Person', path, consent=True)
    assert result['enrolled'] is False and 'readable' in result['reason']


def test_pillow_decompression_warning_is_an_explained_failure(tmp_path, monkeypatch):
    payload = BytesIO()
    Image.new('RGB', (20, 20)).save(payload, format='PNG')
    path = tmp_path / 'bomb-policy-fixture.png'
    path.write_bytes(payload.getvalue())
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 300)
    engine, captured = capture_decode(monkeypatch)
    with pytest.raises(ValueError, match='readable'):
        engine.read_faces(path)
    assert not captured
