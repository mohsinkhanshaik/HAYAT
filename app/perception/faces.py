"""Recognising only the people the owner has enrolled, on the owner's own images.

This is deliberately *not* a people-search. It never identifies a stranger,
never queries an outside database, and never touches the public camera
network. It answers one question about a picture the owner hands it: "is this
one of the people I enrolled, or someone I don't recognise?" The enrolled set
is the owner's, built by the owner, and lives on this machine until the owner
clears it.

Two layers, so the logic is testable without a camera or a model download:

* FaceEngine -- finds faces in an image and turns each into a 128-number
  signature. Detection works out of the box (the Haar cascade ships inside
  OpenCV). Telling people apart needs two small OpenCV Zoo models placed in
  data/models/faces/ (YuNet for landmarks, SFace for the signature); until
  then detection still works and recognition says what to install.
  FaceEngine.install() fetches both, keeping a file only if its SHA-256 is
  the published one ("set up face recognition").
* FaceGallery -- the enrolled people, account-encrypted in data/faces/gallery.json so a
  sudden shutdown or a reboot does not lose them. Matching is plain cosine
  similarity, independent of OpenCV, so it can be checked with ordinary
  vectors.

A signature is sensitive biometric data, even though it is not a stored photo.
The owner can forget a person or clear everyone at any time. Similarity is not
identity proof, a calibrated probability, liveness detection or an internet search.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
import logging
import math
import os
import time
import threading
import warnings
from copy import deepcopy
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.logging import log_swallowed
from app.research.record_vault import dpapi

_log = logging.getLogger("hayat.faces")

#: Project root (…/app/perception/faces.py -> parents[2]), so paths do not depend on the working directory.
_ROOT = Path(__file__).resolve().parents[2]
#: Where the two optional OpenCV Zoo models go, if the owner adds them.
FACE_MODELS_DIR = _ROOT / "data" / "models" / "faces"
YUNET = "face_detection_yunet_2023mar.onnx"
SFACE = "face_recognition_sface_2021dec.onnx"
#: Where each model comes from (OpenCV Zoo on GitHub, Apache-2.0) and the SHA-256 it must have.
#: The hashes were checked against OpenCV's own Hugging Face mirror on 2026-10-17, so a changed
#: or tampered file is refused rather than loaded.
FACE_MODEL_SOURCES = {
    YUNET: ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/" + YUNET,
            "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    SFACE: ("https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/" + SFACE,
            "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}
#: Both models together are about 37 MB; anything far bigger is not them.
MAX_MODEL_BYTES = 64 * 1024 * 1024
#: The enrolled people, kept here so they survive a restart.
GALLERY_PATH = _ROOT / "data" / "faces" / "gallery.json"
#: SFace's example LFW cosine threshold; it is not a calibrated identity probability.
MATCH_THRESHOLD = 0.363
#: Conservative local rejection policy, not a measured accuracy/false-match guarantee.
MIN_MATCH_MARGIN = 0.08
#: At most this many signatures per person, newest kept, so the file stays small.
MAX_PER_PERSON = 5
MAX_GALLERY_BYTES = 8 * 1024 * 1024
MAX_PEOPLE = 250
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
MAX_IMAGE_DIMENSION = 16_384
_GALLERY_PREFIX = b'HAYAT-FACES-DPAPI\0'


@dataclass
class DetectedFace:
    box: tuple[int, int, int, int]  # x, y, w, h in pixels
    score: float
    embedding: list[float] | None = None  # None when no recognition model is installed


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch_https(url: str) -> bytes:
    """GET one model over HTTPS (GitHub redirects to its LFS host), capped at MAX_MODEL_BYTES."""
    import httpx

    chunks, total = [], 0
    with httpx.stream("GET", url, timeout=120, follow_redirects=True,
                      headers={"User-Agent": "HAYAT-face-models/1.0"}) as response:
        response.raise_for_status()
        if response.url.scheme != "https":
            raise PermissionError("The model download left HTTPS.")
        for chunk in response.iter_bytes(1024 * 1024):
            total += len(chunk)
            if total > MAX_MODEL_BYTES:
                raise OSError(f"The download passed {MAX_MODEL_BYTES} bytes, far more than the model.")
            chunks.append(chunk)
    return b"".join(chunks)


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError('Face signatures have different dimensions.')
    a, b = _signature(a), _signature(b)
    return max(-1.0, min(1.0, math.fsum(x * y for x, y in zip(a, b, strict=True))))


def _signature(value, dimension=None):
    if (not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 512
            or (dimension is not None and len(value) != dimension)
            or any(type(item) not in (int, float) or not math.isfinite(item) for item in value)):
        raise ValueError('A face signature must have consistent dimensions and finite numeric values.')
    norm = math.hypot(*value)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('A face signature must have a finite nonzero norm.')
    return [float(item / norm) for item in value]


class FaceEngine:
    """Find faces in an image, and (when the models are present) sign each one."""

    def __init__(self, models_dir: Path | str = FACE_MODELS_DIR) -> None:
        self.models_dir = Path(models_dir)
        self._detector: Any = None
        self._detector_kind: str = ""
        self._recognizer: Any = None
        self._lock = threading.RLock()
        self._verified_files = {}
        self.model_error = ''

    embedding_dimension = 128

    @property
    def _yunet_path(self) -> Path:
        return self.models_dir / YUNET

    @property
    def _sface_path(self) -> Path:
        return self.models_dir / SFACE

    def recognition_available(self) -> bool:
        """Telling people apart needs YuNet (for alignment) and SFace (for the signature)."""
        with self._lock:
            self.model_error = ''
            for name, (_url, expected) in FACE_MODEL_SOURCES.items():
                path = self.models_dir / name
                try:
                    info = path.stat()
                    fingerprint = (info.st_size, info.st_mtime_ns, info.st_ctime_ns, expected)
                    if info.st_size > MAX_MODEL_BYTES:
                        raise ValueError('Model is too large.')
                    if self._verified_files.get(name) != fingerprint:
                        with path.open('rb') as stream:
                            raw = stream.read(MAX_MODEL_BYTES + 1)
                        if len(raw) > MAX_MODEL_BYTES or _sha256(raw) != expected:
                            raise ValueError('Model SHA-256 does not match its pinned source.')
                        self._verified_files[name] = fingerprint
                except (OSError, ValueError):
                    self._verified_files.pop(name, None)
                    self._detector, self._detector_kind, self._recognizer = None, '', None
                    self.model_error = 'Missing or unverified OpenCV model: ' + name
                    return False
            return True

    def status(self) -> dict[str, Any]:
        try:
            import cv2  # noqa: F401
        except Exception as exc:
            return {"detection": False, "recognition": False, "reason": f"OpenCV is unavailable: {exc}"}
        reason = ""
        if not self.recognition_available():
            reason = (self.model_error + '. ' + f"Recognising enrolled people needs two verified OpenCV Zoo models in {self.models_dir}: "
                      f"{YUNET} and {SFACE} (Apache-2.0). Detection works without them.")
        if reason:
            reason += " Say 'set up face recognition' to download them (about 37 MB)."
        return {"detection": True, "recognition": self.recognition_available(),
                "models_dir": str(self.models_dir), "needs": [YUNET, SFACE], "reason": reason}

    def install(self, fetch: Callable[[str], bytes] | None = None) -> dict[str, Any]:
        """Download YuNet and SFace into models_dir, keeping only files whose SHA-256 is the published one.

        A model already present with the right hash is left alone. Each file is written beside
        its final name and moved into place, so an interrupted download never leaves a broken model.
        """
        with self._lock:
            return self._install(fetch)

    def _install(self, fetch=None):
        fetch = fetch or _fetch_https
        self.models_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for name, (url, expected) in FACE_MODEL_SOURCES.items():
            target = self.models_dir / name
            if target.is_file() and _sha256(target.read_bytes()) == expected:
                files.append({"name": name, "status": "already installed", "bytes": target.stat().st_size})
                continue
            try:
                data = fetch(url)
            except Exception as exc:
                return {"installed": False, "files": files,
                        "reason": f"Could not download {name}: {type(exc).__name__}: {exc}"}
            if _sha256(data) != expected:
                return {"installed": False, "files": files,
                        "reason": f"{name} did not match its published SHA-256, so it was not kept."}
            partial = target.with_suffix(target.suffix + ".partial")
            partial.write_bytes(data)
            os.replace(partial, target)
            files.append({"name": name, "status": "downloaded", "bytes": len(data)})
        # A detector built before the models arrived is the Haar fallback; rebuild on next use.
        self._detector, self._detector_kind, self._recognizer = None, "", None
        self._verified_files.clear()
        return {"installed": True, "files": files, "models_dir": str(self.models_dir),
                "recognition": self.recognition_available()}

    def _ensure_detector(self, size: tuple[int, int]) -> str:
        import cv2

        if self.recognition_available():
            if self._detector_kind != "yunet":
                self._detector = cv2.FaceDetectorYN.create(str(self._yunet_path), "", size, 0.6, 0.3, 5000)
                self._detector_kind = "yunet"
            else:
                self._detector.setInputSize(size)
            return "yunet"
        if self._detector_kind != "haar":
            import cv2.data

            self._detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            self._detector_kind = "haar"
        return "haar"

    def _recognizer_for(self) -> Any:
        import cv2

        if self._recognizer is None:
            self._recognizer = cv2.FaceRecognizerSF.create(str(self._sface_path), "")
        return self._recognizer

    def faces(self, image: Any) -> list[DetectedFace]:
        """Every face in a BGR image, each signed when recognition is available."""
        with self._lock:
            return self._faces(image)

    def _faces(self, image):
        import cv2

        if image is None or getattr(image, "size", 0) == 0:
            return []
        height, width = image.shape[:2]
        kind = self._ensure_detector((width, height))
        found: list[DetectedFace] = []
        if kind == "yunet":
            _, rows = self._detector.detect(image)
            if rows is None:
                return []
            recognizer = self._recognizer_for()
            for row in rows:
                x, y, w, h = (int(v) for v in row[:4])
                score = float(row[-1])
                embedding = None
                try:
                    aligned = recognizer.alignCrop(image, row)
                    embedding = _signature([float(v) for v in recognizer.feature(aligned).flatten()], self.embedding_dimension)
                except Exception:
                    log_swallowed(_log, "signing a detected face")
                found.append(DetectedFace((x, y, w, h), score, embedding))
        else:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
            for (x, y, w, h) in self._detector.detectMultiScale(gray, 1.1, 5):
                found.append(DetectedFace((int(x), int(y), int(w), int(h)), 1.0, None))
        return found

    def read_faces(self, path: Path | str) -> list[DetectedFace]:
        """Decode one bounded, orientation-correct still image, including Unicode paths.

        Dimensions are checked from the header before allocating the full image.
        Multi-frame files are rejected instead of silently comparing their first
        frame. These limits bound input size, not native inference execution time.
        """
        import numpy as np
        from PIL import Image, ImageOps, UnidentifiedImageError

        target = Path(path).expanduser()
        if not target.is_file():
            raise FileNotFoundError(str(target))
        if not 0 < target.stat().st_size <= MAX_IMAGE_BYTES:
            raise ValueError('Local face images must contain between 1 byte and 25 MiB.')
        with target.open('rb') as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError('Local face images are limited to 25 MiB.')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as picture:
                    width, height = picture.size
                    if (min(width, height) < 1 or width * height > MAX_IMAGE_PIXELS
                            or max(width, height) > MAX_IMAGE_DIMENSION):
                        raise ValueError('Local face images support at most 24 million pixels and 16,384 pixels per side.')
                    if getattr(picture, 'n_frames', 1) != 1:
                        raise ValueError('Choose one still image; animated or multi-page images are not compared.')
                    oriented = ImageOps.exif_transpose(picture)
                    try:
                        with oriented.convert('RGB') as rgb:
                            image = np.asarray(rgb)[:, :, ::-1].copy()  # OpenCV BGR, contiguous and independently owned
                    finally:
                        oriented.close()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
            raise ValueError('The local file is not a readable supported still image.') from exc
        return self.faces(image)


class FaceGallery:
    """Account-encrypted local biometric store; malformed data is never overwritten."""

    _locks = {}
    _locks_guard = threading.Lock()

    def __init__(self, path: Path | str | None = None, *, codec=None) -> None:
        self.path = Path(path if path is not None else GALLERY_PATH)
        self.codec = codec or dpapi
        with self._locks_guard:
            self._lock = self._locks.setdefault(str(self.path.resolve()).casefold(), threading.RLock())
        self.error = ''
        self.storage = 'account_encrypted'
        with self._lock:
            # Merely constructing the default runtime must not inspect biometrics.
            # Explicit status/list/compare/edit requests load the gallery on demand.
            self._people: dict[str, dict[str, Any]] = self._load() if path is not None else {}

    def _load(self) -> dict[str, dict[str, Any]]:
        self.error = ''
        if not self.path.is_file():
            return {}
        try:
            if self.path.stat().st_size > MAX_GALLERY_BYTES:
                raise ValueError('Gallery exceeds its storage limit.')
            with self.path.open('rb') as stream:
                raw = stream.read(MAX_GALLERY_BYTES + 1)
            if len(raw) > MAX_GALLERY_BYTES:
                raise ValueError('Gallery exceeds its storage limit.')
            encrypted = raw.startswith(_GALLERY_PREFIX)
            self.storage = 'account_encrypted' if encrypted else 'legacy_plaintext'
            if encrypted:
                raw = self.codec(raw[len(_GALLERY_PREFIX):], decrypt=True)
            if len(raw) > MAX_GALLERY_BYTES:
                raise ValueError('Decoded gallery exceeds its storage limit.')
            data = json.loads(raw)
            if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] not in {1, 2}
                    or not isinstance(data.get('people'), dict) or len(data['people']) > MAX_PEOPLE
                    or (data['version'] == 2 and data.get('model') != SFACE)):
                raise ValueError('Unsupported gallery format.')
            people, dimension = {}, None
            for name, record in data['people'].items():
                if not name or self._key(name) != name or not isinstance(record, dict):
                    raise ValueError('Malformed enrollment record.')
                signatures = record.get('embeddings')
                if not isinstance(signatures, list) or not 1 <= len(signatures) <= MAX_PER_PERSON:
                    raise ValueError('Malformed enrollment signatures.')
                checked = []
                for signature in signatures:
                    vector = _signature(signature, dimension)
                    dimension = len(vector)
                    checked.append(vector)
                for key in ('added', 'updated', 'consent_attested_at'):
                    stamp = record.get(key)
                    if stamp is not None and (type(stamp) not in (int, float) or not math.isfinite(stamp)):
                        raise ValueError('Invalid enrollment metadata.')
                people[name] = {**record, 'embeddings': checked}
            return people
        except Exception:
            # Never log raw names, vectors, JSON or decryption errors.
            self.error = 'The local face gallery could not be validated or decrypted. Its file was preserved; repair or restore it before editing.'
            _log.warning('Local face gallery unavailable; existing file preserved.')
            return {}

    def _save(self) -> None:
        if self.error:
            raise ValueError(self.error)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({'version': 2, 'model': SFACE, 'people': self._people}, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(payload) > MAX_GALLERY_BYTES - 1024:
            raise ValueError('Face gallery exceeds its storage limit.')
        protected = _GALLERY_PREFIX + self.codec(payload)
        if len(protected) > MAX_GALLERY_BYTES:
            raise ValueError('Encrypted face gallery exceeds its storage limit.')
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_bytes(protected)
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)
        self.storage = 'account_encrypted'

    @staticmethod
    def _key(name: str) -> str:
        if not isinstance(name, str) or len(name) > 120 or any(ord(char) < 32 for char in name):
            raise ValueError('Use an enrollment name of at most 120 characters without control characters.')
        return " ".join(name.split())

    def enroll(self, name: str, embedding: list[float], *, consent=False, check=None) -> dict[str, Any]:
        key = self._key(name)
        if not key:
            raise ValueError("A person needs a name to enrol.")
        vector = _signature(embedding)
        with self._lock:
            self._people = self._load()
            if self.error:
                raise ValueError(self.error)
            first = next(iter(self._people.values()), None)
            if first is not None:
                vector = _signature(vector, len(first['embeddings'][0]))
            if key not in self._people and len(self._people) >= MAX_PEOPLE:
                raise ValueError('The local enrollment limit was reached.')
            previous = deepcopy(self._people)
            try:
                record = self._people.setdefault(key, {'added': time.time(), 'embeddings': []})
                # Only numerical equality is deduplicated, never a new visual
                # similarity threshold. Repeated copies must not evict useful
                # varied samples from the five-template retention limit.
                duplicate = any(all(math.isclose(a, b, rel_tol=0, abs_tol=1e-12)
                                    for a, b in zip(vector, known, strict=True)) for known in record['embeddings'])
                if not duplicate:
                    record['embeddings'] = (record['embeddings'] + [vector])[-MAX_PER_PERSON:]
                record['updated'] = time.time()
                if consent is True:
                    record['consent_attested_at'] = time.time()
                if check is not None:
                    check()
                self._save()
            except Exception:
                self._people = previous
                raise
            return {'name': key, 'signatures': len(record['embeddings']), 'sample_added': not duplicate,
                    'duplicate_sample': duplicate}

    def assess(self, embedding):
        return self.assess_many([embedding])[0]

    def assess_many(self, embeddings, *, check=None):
        """Compare one image's signatures against a single consistent gallery read."""
        with self._lock:
            self._people = self._load()
            results = []
            for embedding in embeddings:
                if check is not None:
                    check()
                results.append(self._assess_loaded(embedding))
            return results

    def _assess_loaded(self, embedding):
        result = {'name': None, 'match': 'unknown', 'score': None, 'margin': None,
                  'threshold': MATCH_THRESHOLD, 'required_margin': MIN_MATCH_MARGIN,
                  'metric': 'cosine_similarity', 'calibrated_probability': False,
                  'gallery_people': len(self._people), 'compatible_people': 0}
        if self.error:
            return {**result, 'match': 'unreadable', 'reason': self.error}
        try:
            vector = _signature(embedding)
        except ValueError:
            return {**result, 'match': 'unreadable', 'reason': 'Invalid or unreadable face signature.'}
        ranked = []
        for name, record in self._people.items():
            compatible = [known for known in record['embeddings'] if len(known) == len(vector)]
            if compatible:
                ranked.append((max(_cosine(vector, known) for known in compatible), name))
        ranked.sort(reverse=True)
        result['compatible_people'] = len(ranked)
        if not ranked:
            return {**result, 'reason': 'No compatible enrolled signature is available.'}
        best, name = ranked[0]
        margin = best - ranked[1][0] if len(ranked) > 1 else None
        result.update(score=round(best, 4), margin=round(margin, 4) if margin is not None else None)
        if best < MATCH_THRESHOLD:
            return {**result, 'reason': 'No enrolled sample passed the similarity threshold.'}
        if margin is not None and margin < MIN_MATCH_MARGIN:
            return {**result, 'match': 'ambiguous', 'reason': 'Multiple enrolled identities are too similar; no name is assigned.'}
        return {**result, 'name': name, 'match': 'enrolled', 'reason': 'Local enrolled candidate passed threshold and separation checks; this is not identity proof.'}

    def match(self, embedding: list[float]) -> tuple[str | None, float]:
        """Only an unambiguous enrolled candidate; similarity is not identity certainty."""
        result = self.assess(embedding)
        return result['name'], result['score'] if result['score'] is not None else 0.0

    def people(self) -> list[dict[str, Any]]:
        with self._lock:
            self._people = self._load()
            return [{'name': name, 'signatures': len(record['embeddings']), 'added': record.get('added'),
                     'consent_attested_at': record.get('consent_attested_at')}
                    for name, record in sorted(self._people.items())]

    def forget(self, name: str) -> bool:
        key = self._key(name)
        with self._lock:
            self._people = self._load()
            if self.error:
                raise ValueError(self.error)
            if key not in self._people:
                return False
            previous = deepcopy(self._people)
            try:
                del self._people[key]
                self._save()
            except Exception:
                self._people = previous
                raise
            return True

    def clear(self) -> int:
        with self._lock:
            self._people = self._load()
            if self.error:
                raise ValueError(self.error)
            previous = self._people
            self._people = {}
            try:
                self._save()
            except Exception:
                self._people = previous
                raise
            return len(previous)


class FaceRecognizer:
    """Enrol consenting people, then recognise only them in the owner's own images."""

    def __init__(self, engine: FaceEngine | None = None, gallery: FaceGallery | None = None) -> None:
        self.engine = engine or FaceEngine()
        self.gallery = gallery or FaceGallery()

    def status(self) -> dict[str, Any]:
        people = self.gallery.people()
        return {**self.engine.status(), 'enrolled': people, 'local_only': True,
                'gallery_storage': self.gallery.storage, 'gallery_error': self.gallery.error,
                'threshold': MATCH_THRESHOLD, 'required_margin': MIN_MATCH_MARGIN,
                'limitations': 'Local enrolled candidates only; no web lookup, identity proof, calibrated match probability or liveness detection.'}

    def install_models(self) -> dict[str, Any]:
        return self.engine.install()

    def enroll(self, name: str, image_path: Path | str, *, consent=False, check=None) -> dict[str, Any]:
        if consent is not True:
            return {'enrolled': False, 'reason': 'Explicit consent is required before enrolling a person locally.'}
        if check is not None:
            check()
        key = self.gallery._key(name)
        if not key:
            return {"enrolled": False, "reason": "Give the person a name to enrol them under."}
        if not self.engine.recognition_available():
            return {"enrolled": False, "reason": self.engine.status()["reason"]}
        try:
            faces = self.engine.read_faces(image_path)
        except (FileNotFoundError, ValueError) as exc:
            return {"enrolled": False, "reason": str(exc)}
        if len(faces) > 1:
            return {'enrolled': False, 'reason': f'{len(faces)} faces were detected; use a photo of exactly one person, even if another face is unreadable.'}
        if not faces or faces[0].embedding is None:
            return {"enrolled": False, "reason": "No clear face was found in that image to enrol."}
        try:
            signature = _signature(faces[0].embedding, getattr(self.engine, 'embedding_dimension', None))
        except ValueError:
            return {'enrolled': False, 'reason': 'The face signature is invalid or has the wrong model dimensions.'}
        if check is not None:
            check()
        result = self.gallery.enroll(key, signature, consent=True, check=check)
        return {"enrolled": True, **result}

    def recognize(self, image_path: Path | str, *, check=None) -> dict[str, Any]:
        if check is not None:
            check()
        if not self.engine.recognition_available():
            # Detection still works; say how many faces, but not who.
            try:
                faces = self.engine.read_faces(image_path)
            except (FileNotFoundError, ValueError) as exc:
                return {"available": False, "reason": str(exc)}
            return {"available": False, "reason": self.engine.status()["reason"],
                    "faces_detected": len(faces)}
        try:
            faces = self.engine.read_faces(image_path)
        except (FileNotFoundError, ValueError) as exc:
            return {"available": False, "reason": str(exc)}
        if check is not None:
            check()
        signatures = []
        for face in faces:
            try:
                signature = _signature(face.embedding, getattr(self.engine, 'embedding_dimension', None))
            except ValueError:
                signature = None
            signatures.append(signature)
        assessments = self.gallery.assess_many(signatures, check=check)
        results = []
        for face, assessment in zip(faces, assessments, strict=True):
            score = face.score
            detector_score = (float(score) if type(score) in (int, float) and math.isfinite(score)
                              and 0 <= score <= 1 else None)
            results.append({'box': list(face.box), 'detector_score': detector_score, **assessment})
        return {"available": True, "faces": results, "count": len(results),
                "enrolled_known": sum(1 for r in results if r.get("name")),
                'unknown': sum(1 for r in results if r.get('match') == 'unknown'),
                'ambiguous': sum(1 for r in results if r.get('match') == 'ambiguous'),
                'unreadable': sum(1 for r in results if r.get('match') == 'unreadable'),
                'local_only': True, 'gallery_error': self.gallery.error,
                'limitations': 'Cosine similarity is not an identity probability. Close ties are unnamed; false matches and missed faces remain possible. No liveness check or internet search.'}

    def forget(self, name: str) -> dict[str, Any]:
        return {"forgotten": self.gallery.forget(name), "name": name}

    def clear(self) -> dict[str, Any]:
        return {"cleared": self.gallery.clear()}

    def people(self) -> dict[str, Any]:
        people = self.gallery.people()
        return {'enrolled': people, 'gallery_error': self.gallery.error, 'gallery_storage': self.gallery.storage}


def format_recognition_report(value):
    """Explain local comparisons without converting similarity to a probability."""
    if not isinstance(value, dict):
        return 'No readable local face result was returned.'
    if 'faces' not in value:
        if value.get('available') is False:
            return 'Local recognition unavailable.\n' + str(value.get('reason', 'Check model and gallery setup.'))
        if isinstance(value.get('enrolled'), bool):
            return ('Enrolled locally: ' + str(value.get('name', '')) if value['enrolled'] else
                    'Not enrolled: ' + str(value.get('reason', 'No valid enrollment.')))
        if 'forgotten' in value:
            return 'Local samples removed.' if value['forgotten'] else 'No enrollment with that name was found.'
        lines = ['Local enrolled-face workspace', 'Recognition models: ' + ('verified and available' if value.get('recognition') else 'not ready')]
        if value.get('reason'):
            lines.append(str(value['reason']))
        if value.get('gallery_error'):
            lines.append(str(value['gallery_error']))
        lines.append('Enrolled people: ' + str(len(value.get('enrolled', []))))
        lines.append('Gallery storage: ' + str(value.get('gallery_storage', 'not reported')))
        return '\n'.join(lines)
    lines = ['Local enrolled-face comparison',
             'Similarity scores are raw cosine values, not identity percentages or verified identity.', '']
    for index, face in enumerate(value['faces'], 1):
        state = str(face.get('match', 'unreadable'))
        label = str(face['name']) + ' (enrolled candidate)' if face.get('name') else state.capitalize() + ' — no name assigned'
        lines.append(f'Face {index}: {label}')
        if face.get('gallery_people') is not None:
            lines.append(f"Gallery comparison: {face.get('compatible_people', 0)} compatible person(s) among {face['gallery_people']} enrolled locally.")
        score = face.get('score')
        if type(score) in (int, float) and math.isfinite(score):
            lines.append(f"Cosine similarity: {score:.4f}; threshold: {face.get('threshold', MATCH_THRESHOLD)}")
        if face.get('margin') is not None:
            lines.append(f"Distinct-person separation: {face['margin']}; required: {face.get('required_margin', MIN_MATCH_MARGIN)}")
        elif face.get('compatible_people') == 1:
            lines.append('Only one compatible person is enrolled, so separation from other people cannot be checked.')
        detector_score = face.get('detector_score')
        if type(detector_score) in (int, float) and math.isfinite(detector_score):
            lines.append(f'Detector score: {detector_score:.4f} (face detection only; separate from template similarity).')
        lines += ['Box (x, y, width, height): ' + ', '.join(map(str, face.get('box', []))), str(face.get('reason', '')), '']
    if not value['faces']:
        lines.append('No face was detected. This does not prove nobody is present.')
    if value.get('gallery_error'):
        lines.append(str(value['gallery_error']))
    lines.append(str(value.get('limitations') or 'No web lookup or liveness verification. Unknown faces remain unnamed.'))
    return '\n'.join(lines)
