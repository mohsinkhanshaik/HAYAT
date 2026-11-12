"""Local matching optimizations preserve input validation and rejection policy.

Only synthetic vectors and blank images are used; no camera, model download,
owner enrollment or external identity service is touched.
"""
import json
import math
import random

import numpy as np
import pytest

from app.perception import faces
from app.perception.faces import FaceEngine, FaceGallery
from tests.test_face_hardening import fixture_codec


def synthetic_gallery(path, people=24, samples=5, dimension=128):
    rng = random.Random(281)
    records = {f'Synthetic sample {index}': {'embeddings': [
        [rng.gauss(0, 1) for _ in range(dimension)] for _sample in range(samples)]}
        for index in range(people)}
    raw = json.dumps({'version': 2, 'model': faces.SFACE, 'people': records}).encode()
    path.write_bytes(faces._GALLERY_PREFIX + fixture_codec(raw))
    return FaceGallery(path, codec=fixture_codec), records


def legacy_cosine(a, b):
    a, b = faces._signature(a), faces._signature(b)
    return max(-1.0, min(1.0, math.fsum(x * y for x, y in zip(a, b, strict=True))))


def test_batch_matching_avoids_pairwise_validation_without_changing_results(tmp_path, monkeypatch):
    gallery, records = synthetic_gallery(tmp_path / 'synthetic.json')
    queries = [record['embeddings'][0] for record in records.values()]
    validated = []
    original = faces._signature

    def tracked(value, dimension=None):
        validated.append(True)
        return original(value, dimension)

    monkeypatch.setattr(faces, '_signature', tracked)
    optimized = gallery.assess_many(queries)
    assert len(validated) <= 24 * 5 + 2 * len(queries)
    monkeypatch.setattr(faces, '_unit_cosine', legacy_cosine)
    reference = gallery.assess_many(queries)
    assert optimized == reference
    assert all(result['name'] is not None and not result['calibrated_probability'] for result in optimized)


@pytest.mark.parametrize('score', [faces.MATCH_THRESHOLD - 1e-10, faces.MATCH_THRESHOLD + 1e-10])
def test_similarity_threshold_neighborhood_matches_the_validated_reference(tmp_path, monkeypatch, score):
    gallery = FaceGallery(tmp_path / 'threshold.json', codec=fixture_codec)
    gallery.enroll('Synthetic threshold sample', [1., 0.])
    query = [score, math.sqrt(1 - score * score)]
    optimized = gallery.assess(query)
    monkeypatch.setattr(faces, '_unit_cosine', legacy_cosine)
    assert optimized == gallery.assess(query)
    assert (optimized['name'] is not None) == (score > faces.MATCH_THRESHOLD)


def test_detection_only_work_reuses_haar_despite_missing_optional_models(tmp_path, monkeypatch):
    import cv2
    constructed = []

    class Cascade:
        def detectMultiScale(self, image, *_args):
            return []

    def load(path):
        constructed.append(path)
        return Cascade()

    monkeypatch.setattr(cv2, 'CascadeClassifier', load, raising=False)
    engine = FaceEngine(tmp_path / 'absent-models')
    for _ in range(4):
        assert engine.faces(np.zeros((48, 48, 3), dtype=np.uint8)) == []
        assert engine.status()['recognition'] is False
    assert len(constructed) == 1
    assert engine._detector_kind == 'haar'


def test_unverified_recognition_models_still_invalidate_loaded_native_engines(tmp_path):
    engine = FaceEngine(tmp_path / 'absent-models')
    engine._detector, engine._detector_kind, engine._recognizer = object(), 'yunet', object()
    assert engine.recognition_available() is False
    assert engine._detector is None and engine._recognizer is None


def test_invalid_batch_queries_remain_unreadable_and_do_not_reveal_enrollment(tmp_path):
    gallery, _ = synthetic_gallery(tmp_path / 'synthetic.json')
    results = gallery.assess_many([[], [float('nan')], [0.] * 128, [True] * 128])
    assert all(result['match'] == 'unreadable' and result['name'] is None for result in results)


def test_build_without_legacy_detector_reports_an_actionable_unavailable_state(tmp_path, monkeypatch):
    import cv2
    from PIL import Image

    monkeypatch.setattr(cv2, 'CascadeClassifier', None, raising=False)
    engine = FaceEngine(tmp_path / 'absent-models')
    assert engine.status()['detection'] is False
    image = tmp_path / 'blank.png'
    Image.new('RGB', (48, 48)).save(image)
    gallery = FaceGallery(tmp_path / 'synthetic-gallery.json', codec=fixture_codec)
    result = faces.FaceRecognizer(engine, gallery).recognize(image)
    assert result['available'] is False and 'verified OpenCV Zoo face models' in result['reason']
    assert not gallery.path.exists()
