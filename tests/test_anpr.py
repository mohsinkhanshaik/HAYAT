"""Plate recognition: formats, the source policy, tracking, records and privacy, the pipeline and the dashboard."""
import csv
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.anpr.detector import ContourPlateDetector, Detection
from app.anpr.formats import custom_format, read_plate
from app.anpr.ocr import OcrResult
from app.anpr.pipeline import AnprPipeline, annotate
from app.anpr.samples import vehicle_scene, write_samples
from app.anpr.sources import check_source, redact
from app.anpr.store import AnprStore
from app.anpr.tracker import PlateTracker


# ---------------------------------------------------------------- formats
@pytest.mark.parametrize(("raw", "region", "plate", "valid", "repaired"), [
    ("MH 12 AB 1234", "IN", "MH12AB1234", True, 0),
    ("MH12A81234", "IN", "MH12AB1234", True, 1),      # 8 read where a letter must be
    ("MHI2AB1234", "IN", "MH12AB1234", True, 1),      # I read where a digit must be
    ("DL3CAF0001", "IN", "DL3CAF0001", True, 0),
    ("22BH1234AA", "IN", "22BH1234AA", True, 0),      # Bharat series
    ("7ABC123", "US", "7ABC123", True, 0),
    ("AB12CDE", "UK", "AB12CDE", True, 0),
    ("A812CDE", "UK", "AB12CDE", True, 1),
    ("XX", "IN", "XX", False, 0),
])
def test_plates_are_cleaned_repaired_and_validated(raw, region, plate, valid, repaired):
    reading = read_plate(raw, region)
    assert (reading.plate, reading.valid, reading.repaired) == (plate, valid, repaired)


def test_owners_add_their_own_formats():
    pakistan = custom_format("PK", "Pakistan", "LLLDDDD, LLDDDD")
    assert read_plate("LEA1234", "PK", {"PK": pakistan}).valid
    longer = read_plate("LEA12340", "PK", {"PK": pakistan})  # an extra character: the plate inside, for review
    assert (longer.plate, longer.trimmed) == ("LEA1234", True)
    regex = custom_format("AE", "Dubai", r"re:[A-Z]{1,2}\d{1,5}")
    assert read_plate("A12345", "AE", {"AE": regex}).valid
    with pytest.raises(ValueError):
        custom_format("BAD", "Bad", "LLXQ")


# ---------------------------------------------------------------- sources
def test_files_and_webcams_are_accepted(tmp_path):
    image, video = tmp_path / "car.jpg", tmp_path / "drive.mp4"
    image.write_bytes(b"x")
    video.write_bytes(b"x")
    assert check_source(str(image)).kind == "image" and check_source(str(video)).kind == "video"
    assert check_source(0).kind == "webcam" and check_source("webcam:2").target == 2
    with pytest.raises(ValueError):
        check_source(str(tmp_path / "missing.jpg"))


@pytest.mark.parametrize("source", ["https://cctv.example.com/live.m3u8", "http://192.168.1.5/cam.jpg",
                                    "cameras_delaware:KCAM001", "livestreams:makkah", "rtmp://example.com/live"])
def test_public_streams_and_global_intelligence_cameras_are_refused(source):
    with pytest.raises(PermissionError):
        check_source(source)


def test_rtsp_cameras_must_be_registered_and_on_a_private_network():
    home = "rtsp://admin:secret@192.168.1.20:554/stream1"
    with pytest.raises(PermissionError, match="Register"):
        check_source(home)
    accepted = check_source(home, [home])
    assert accepted.kind == "rtsp" and "secret" not in accepted.label
    public = "rtsp://8.8.8.8/stream"
    with pytest.raises(PermissionError, match="not on a private network"):
        check_source(public, [public])
    assert redact(home) == "rtsp://admin:***@192.168.1.20:554/stream1"


# ---------------------------------------------------------------- tracking
def test_one_plate_across_frames_is_one_track_and_the_readings_vote():
    tracker = PlateTracker(max_missing=2)
    readings = ["MH12AB1234", "MH12AB1Z34", "MH12AB1234", "MH12AB1234"]
    track = None
    for index, text in enumerate(readings):
        pairs, ended = tracker.update([Detection((100 + index * 8, 300, 300 + index * 8, 350), .9)], index)
        assert ended == [] and len(pairs) == 1
        track = pairs[0][1]
        track.readings.append((text, .8, .9))
    assert len(tracker.tracks) == 1 and track.vote() == ("MH12AB1234", .8, 3)
    _pairs, ended = tracker.update([], 10)
    assert ended == [track] and not tracker.tracks


def test_two_cars_in_one_frame_are_two_tracks():
    tracker = PlateTracker()
    pairs, _ended = tracker.update([Detection((10, 10, 110, 40), .9), Detection((500, 300, 600, 330), .8)], 1)
    assert len({track.track_id for _detection, track in pairs}) == 2


# ---------------------------------------------------------------- store
@pytest.fixture
def store(tmp_path):
    return AnprStore(tmp_path / "anpr")


def record(plate, confidence=.9, seen=None, valid=True, source="test.mp4"):
    return {"plate": plate, "raw_text": plate, "confidence": confidence, "detection_confidence": .9, "format_valid": valid,
            "region": "IN", "source": source, "source_kind": "video", "seen_at": (seen or datetime.now(UTC)).isoformat(),
            "bbox": [1, 2, 3, 4]}


def test_records_search_review_and_correct(store):
    import numpy as np
    first = store.add(record("MH12AB1234"), crop=np.zeros((30, 120, 3), np.uint8))
    store.add(record("KA05MN4321", confidence=.4))
    store.add(record("ZZ", valid=False))
    assert Path(first["crop_path"]).exists()
    assert store.search(text="mh12")["total"] == 1 and store.search()["total"] == 3
    queue = [row["plate"] for row in store.review_queue()]
    assert sorted(queue) == ["KA05MN4321", "ZZ"]  # low confidence, and no format match
    corrected = store.review(store.search(text="KA05")["rows"][0]["id"], "correct", "ka 05 mn 4322")
    assert corrected["status"] == "corrected" and corrected["corrected_plate"] == "KA05MN4322"
    store.review(first["id"], "confirm")
    assert store.search(status="confirmed")["total"] == 1 and len(store.review_queue()) == 1
    with pytest.raises(ValueError):
        store.review(first["id"], "approve")


def test_authorized_vehicles_and_parking_review_events(store):
    store.add_vehicle("mh-12-ab-1234", "Family car", "Residents")
    store.save_settings({"parking_mode": True, "max_stay_hours": 2})
    own = store.add(record("MH12AB1234"))
    assert own["events"] == []
    visitor = store.add(record("DL3CAF0001", seen=datetime.now(UTC) - timedelta(hours=3)))
    assert [event["kind"] for event in visitor["events"]] == ["not_registered"]
    later = store.add(record("DL3CAF0001"))
    assert [event["kind"] for event in later["events"]] == ["overstay"]  # not_registered is already open
    assert store.search(authorized=True)["total"] == 1 and store.search(authorized=False)["total"] == 2
    event = store.events()[0]
    store.resolve_event(event["id"], "dismissed")
    assert event["id"] not in [item["id"] for item in store.events()]
    assert store.remove_vehicle("MH12AB1234") and store.vehicles() == []


def test_retention_and_deletion_remove_records_and_images(store):
    import numpy as np
    old = store.add(record("MH12AB1234", seen=datetime.now(UTC) - timedelta(days=40)), crop=np.zeros((20, 80, 3), np.uint8))
    store.add(record("KA05MN4321"))
    store.add(record("KA05MN4321"))
    assert store.purge() == 1 and not Path(old["crop_path"]).exists()  # default 30 days
    assert store.delete_plate("ka05mn4321") == 2 and store.search()["total"] == 0
    store.add(record("TN09ZZ9999"))
    store.delete_everything()
    assert store.search()["total"] == 0
    with pytest.raises(ValueError):
        store.save_settings({"retention_days": 0})
    with pytest.raises(ValueError):
        store.save_settings({"owner_lookup": True})


def test_exports_to_json_csv_and_sqlite(store, tmp_path):
    store.add(record("MH12AB1234"))
    store.add(record("KA05MN4321", confidence=.5))
    data = json.loads(store.export(tmp_path / "out.json", "json").read_text(encoding="utf-8"))
    assert data["count"] == 2
    with (tmp_path / "out.csv").open("w") as handle:
        handle.write("")
    rows = list(csv.DictReader(store.export(tmp_path / "out.csv", "csv").open(encoding="utf-8")))
    assert {row["plate"] for row in rows} == {"MH12AB1234", "KA05MN4321"}
    with sqlite3.connect(store.export(tmp_path / "out.db", "sqlite", text="MH12")) as db:
        assert db.execute("SELECT plate FROM recognitions").fetchall() == [("MH12AB1234",)]
    with pytest.raises(ValueError):
        store.export(tmp_path / "out.xml", "xml")


# ---------------------------------------------------------------- pipeline
class PlateBoxDetector:
    """Stand-in detector: finds the synthetic plate (white rectangle) where the scene puts it."""
    name, device = "test detector", "CPU"

    def detect(self, frame):
        import numpy as np
        mask = (frame[:, :, 0] > 230) & (frame[:, :, 1] > 230) & (frame[:, :, 2] > 230)
        rows, columns = np.where(mask[400:, :])
        if not len(rows):
            return []
        return [Detection((int(columns.min()), int(rows.min()) + 400, int(columns.max()), int(rows.max()) + 400), .95)]


class FixedOcr:
    name, device = "test ocr", "CPU"

    def __init__(self, texts):
        self.texts, self.calls = list(texts), 0

    def read(self, crop):
        self.calls += 1
        return OcrResult(self.texts[min(self.calls - 1, len(self.texts) - 1)], .88, self.name)


def test_an_image_gives_one_stored_plate_with_its_crop(store, tmp_path):
    truth = write_samples(tmp_path / "samples")
    pipeline = AnprPipeline(store, tmp_path / "models", detector=PlateBoxDetector(), ocr=FixedOcr(["MH12A81234"]))
    stored = pipeline.process_image(next(path for path in truth if path.endswith("MH12AB1234.png")))
    assert len(stored) == 1 and stored[0]["plate"] == "MH12AB1234" and stored[0]["repaired"] == 1
    assert Path(stored[0]["crop_path"]).exists() and stored[0]["source_kind"] == "image"


def test_a_video_stores_one_record_per_passing_plate(store, tmp_path):
    truth = write_samples(tmp_path / "samples")
    video = next(path for path in truth if path.endswith(".mp4"))
    ocr = FixedOcr(["MH12AB1234", "MH12A81234", "MH12AB1234"])
    pipeline = AnprPipeline(store, tmp_path / "models", detector=PlateBoxDetector(), ocr=ocr)
    frames = []
    result = pipeline.process_video(video, on_frame=frames.append)
    assert [row["plate"] for row in result["stored"]] == ["MH12AB1234"]
    assert result["stored"][0]["readings"] > 3 and ocr.calls > 3
    assert frames and frames[-1].annotated.shape == (640, 960, 3) and frames[-1].plates[0]["track_id"] == 1


def test_annotation_draws_boxes_and_text():
    frame = vehicle_scene("MH12AB1234")
    drawn = annotate(frame, [{"bbox": [330, 420, 630, 488], "plate": "MH12AB1234", "confidence": .91, "format_valid": True}])
    assert (drawn != frame).any() and (frame == vehicle_scene("MH12AB1234")).all()


def test_the_fallback_detector_finds_a_clear_plate():
    detections = ContourPlateDetector().detect(vehicle_scene("MH12AB1234"))
    assert any(320 <= d.box[0] <= 345 and 410 <= d.box[1] <= 430 and d.width > 250 for d in detections)
    assert all(d.box[1] > 400 for d in detections)  # not the windscreen: no characters inside it


def models_ready():
    from app.anpr import data_folder
    root = data_folder().parent / "models" / "anpr"
    try:
        import fast_plate_ocr  # noqa: F401
        import open_image_models  # noqa: F401
    except ImportError:
        return False
    return root.exists() and any(root.rglob("*.onnx"))


@pytest.mark.skipif(not models_ready(), reason="the YOLO and plate OCR models are not downloaded yet")
def test_real_models_read_the_synthetic_plates(tmp_path):
    from app.anpr import data_folder
    pipeline = AnprPipeline(AnprStore(tmp_path / "anpr"), data_folder().parent / "models" / "anpr")
    truth = write_samples(tmp_path / "samples")
    right = sum(expected in [row["plate"] for row in pipeline.process_image(path)]
                for path, expected in truth.items() if not path.endswith(".mp4"))
    assert right >= len(truth) - 2, pipeline.status()  # at least 5 of the 6 still images


# ---------------------------------------------------------------- dashboard
def test_the_dashboard_shows_records_reviews_and_registers_only_private_cameras(shared_tk, store, tmp_path):
    from app.gui_anpr import AnprDashboard
    from app.gui_theme import Palette
    from tests.conftest import close_window, open_window
    parent = open_window(shared_tk, "400x300-4000-4000")

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root = parent
    store.add(record("MH12AB1234"))
    store.add(record("KA05MN4321", confidence=.4))
    pipeline = AnprPipeline(store, tmp_path / "models", detector=PlateBoxDetector(), ocr=FixedOcr(["MH12AB1234"]))
    window = AnprDashboard(gui, store, pipeline)
    try:
        window.geometry("1400x880-4000-4000")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not window.records.get_children():
            window.update()
            time.sleep(.02)
        assert len(window.records.get_children()) == 2 and len(window.queue_tree.get_children()) == 1
        assert "test detector" in window.status.get()
        window.queue_tree.selection_set(window.queue_tree.get_children()[0])
        window.update()
        window.correction.set("KA05MN4322")
        window.review("correct")
        assert store.search(status="corrected")["total"] == 1 and not window.queue_tree.get_children()
        window.new_camera.set("rtsp://8.8.4.4/live")  # a public address
        window.register_camera()
        assert "not on a private network" in window.progress.get() and store.settings()["authorized_cameras"] == []
        window.new_camera.set("rtsp://192.168.1.20/live")
        window.register_camera()
        assert store.settings()["authorized_cameras"] == ["rtsp://192.168.1.20/live"]
        window.vehicle_fields["plate"].set("MH12AB1234")
        window.add_vehicle()
        assert store.vehicles()[0]["plate"] == "MH12AB1234"
        truth = write_samples(tmp_path / "samples")
        window.open_image(next(path for path in truth if path.endswith("MH12AB1234.png")))
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and "plate(s)" not in window.progress.get():
            window.update()
            time.sleep(.02)
        assert "MH12AB1234" in window.progress.get() and window._photo is not None
    finally:
        window.close()
        close_window(parent)


def test_nothing_in_the_system_looks_up_owners_or_issues_fines():
    root = Path(__file__).resolve().parents[1] / "app"
    code = "".join(path.read_text(encoding="utf-8").lower() for path in [*root.joinpath("anpr").glob("*.py"), root / "gui_anpr.py"])
    for forbidden in ("vahan", "parivahan", "dmv", "ncic", "registered owner", "owner_name", "owner_address", "issue_fine",
                      "send_notice", "challan"):
        assert forbidden not in code.replace("no owner lookup", ""), forbidden


# ---------------------------------------------------------------- accuracy aids (measured on real photos, 2026-10-11)
def test_a_plate_is_found_inside_longer_text_and_marked_for_review():
    reading = read_plate("AFREENMH09S2112", "IN")  # a dealer's banner read with the plate
    assert (reading.plate, reading.valid, reading.trimmed) == ("MH09S2112", True, True)
    assert read_plate("THZI773", "UK").plate == "THZ1773"  # Northern Ireland layout, I repaired to 1


def test_easyocr_boxes_become_main_characters_in_reading_order():
    from app.anpr.ocr import assemble
    box = lambda x, y, height, text, confidence=.9: ([(x, y), (x + 50, y), (x + 50, y + height), (x, y + height)], text, confidence)
    # Two-line plate with a small banner above and small print below.
    results = [box(300, 120, 60, "S2112"), box(100, 10, 60, "MH09"), box(120, -40, 20, "AFREEN"), box(40, 190, 12, "IND")]
    assert assemble(results) == ("MH09S2112", .9)
    angled = [box(10, 100, 80, "WB06"), box(200, 70, 80, "F5977")]  # one line, tilted
    assert assemble(angled)[0] == "WB06F5977"


def test_a_second_reader_decides_when_the_first_does_not_fit(store, tmp_path):
    class Reader:
        def __init__(self, name, text, confidence):
            self.name, self.text, self.confidence, self.calls = name, text, confidence, 0

        def read(self, crop):
            self.calls += 1
            return OcrResult(self.text, self.confidence, self.name)
    first, second = Reader("fast-plate-ocr (ONNX)", "02T4699", .89), Reader("EasyOCR", "GAO2T4691", .6)
    pipeline = AnprPipeline(store, tmp_path / "models", detector=PlateBoxDetector(), ocr=first)
    pipeline._second, pipeline._second_tried = second, True
    reading, _crop = pipeline._read(vehicle_scene("GA02T4691"), Detection((330, 420, 630, 488), .95), "IN", {})
    assert reading["plate"] == "GA02T4691" and reading["engine"] == "EasyOCR" and second.calls == 1
    assert reading["confidence"] <= .7  # the readers disagreed: it goes to the review queue
    sure = Reader("fast-plate-ocr (ONNX)", "MH12AB1234", .97)
    pipeline._ocr = sure
    reading, _crop = pipeline._read(vehicle_scene("MH12AB1234"), Detection((330, 420, 630, 488), .95), "IN", {})
    assert reading["plate"] == "MH12AB1234" and second.calls == 1  # a sure, valid reading needs no second opinion


# ------------------------------------------------------------------ the GPU, shared with the brain model
def test_cuda_comes_first_with_a_memory_cap_and_the_cpu_last(monkeypatch):
    import onnxruntime
    from app.anpr import detector
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"])
    monkeypatch.setattr(detector, "_cuda_ready", True)
    providers = detector.onnx_providers("auto")
    name, options = providers[0]
    assert name == "CUDAExecutionProvider" and options["gpu_mem_limit"] == detector.GPU_MEMORY_LIMIT <= 1 << 30
    assert providers[-1] == "CPUExecutionProvider" and detector.onnx_providers("off") == ["CPUExecutionProvider"]
    monkeypatch.setattr(detector, "_cuda_ready", False)  # the CUDA libraries would not load
    assert detector.onnx_providers("auto") == ["CPUExecutionProvider"]


def test_the_device_shown_is_where_the_model_really_runs():
    from types import SimpleNamespace
    from app.anpr.detector import session_device
    def model(*providers):
        return SimpleNamespace(model=SimpleNamespace(get_providers=lambda: list(providers)))
    assert session_device(model("CUDAExecutionProvider", "CPUExecutionProvider")) == "GPU (CUDA)"
    assert session_device(model("CPUExecutionProvider")) == "CPU"  # CUDA asked for, but it fell back
    assert session_device(SimpleNamespace()) == "CPU"


def test_a_gpu_failure_moves_the_model_to_the_cpu_once():
    import pytest
    from app.anpr.detector import with_cpu_fallback

    class Owner:
        device = "GPU (CUDA)"
        rebuilt = []

        def rebuild(self, providers):
            self.rebuilt.append(providers)
            self.device = "CPU"
    owner, calls = Owner(), []

    def run():
        calls.append(owner.device)
        if owner.device != "CPU":
            raise RuntimeError("CUDA failure 2: out of memory")
        return "counted"
    assert with_cpu_fallback(owner, run) == "counted" and owner.rebuilt == [["CPUExecutionProvider"]]
    assert calls == ["GPU (CUDA)", "CPU"]
    with pytest.raises(ValueError):  # other errors are not hidden by a CPU retry
        with_cpu_fallback(Owner(), lambda: (_ for _ in ()).throw(ValueError("bad image")))
    cpu = Owner()
    cpu.device = "CPU"
    with pytest.raises(RuntimeError):  # already on the CPU: nothing to fall back to
        with_cpu_fallback(cpu, lambda: (_ for _ in ()).throw(RuntimeError("CUDA")))
