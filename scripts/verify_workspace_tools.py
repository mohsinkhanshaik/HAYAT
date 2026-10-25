"""Real local OCR, desktop input and installed person model on explicit fixtures.

Run: python scripts/verify_workspace_tools.py --image .test-runs/person-verification/pedestrians.png
The positive image must be a known pedestrian fixture. No camera is opened.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify_screen():
    import tkinter as tk
    from app.perception.live_screen import LiveScreenReader
    from app.safety.permissions import PermissionManager
    from app.tools.computer import ComputerTools
    with tempfile.TemporaryDirectory(prefix="hayat-desktop-fixture-") as folder:
        permissions = PermissionManager({"default": "ALLOW", "allowed_roots": [folder]}, project_root=Path(folder))
        computer = ComputerTools(permissions)
        root = tk.Tk()
        root.title("Local Screen Verification Fixture")
        root.geometry("900x420+100+100")
        root.attributes("-topmost", True)
        root.attributes("-fullscreen", True)
        label = tk.Label(root, text="Screen verification ALPHA 7319", font=("Arial", 25), bg="white", fg="black")
        label.pack(fill="both", expand=True)
        clicked = []
        button = tk.Button(root, text="Verify local input", font=("Arial", 18), command=lambda: clicked.append(True))
        button.pack(fill="x", pady=20)
        root.update()
        root.lift()
        root.focus_force()
        root.update()
        # A real native Edit control exposes ValuePattern through UIA, unlike
        # Tk's painted label. It tests accessibility across worker lifetimes.
        import win32con
        import win32gui
        win32gui.CreateWindowEx(0, "Edit", "Screen verification ALPHA 7319",
                               win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.ES_READONLY,
                               20, 20, 600, 40, root.winfo_id(), 0, 0, None)
        root.update()
        reader = LiveScreenReader()
        answers, errors = [], []
        def read():
            try:
                for method in ("ocr", "auto"):
                    answers.append(reader.read("Local Screen Verification Fixture", method))
            except Exception as exc:
                errors.append(str(exc))
        worker = threading.Thread(target=read)
        try:
            worker.start()
            deadline = time.monotonic() + 60
            while worker.is_alive() and time.monotonic() < deadline:
                root.update()
                time.sleep(.02)
            assert not worker.is_alive() and not errors, errors
            assert len(answers) == 2 and all("7319" in answer["text"] for answer in answers), answers
            worker = threading.Thread(target=lambda: answers.append(reader.read("Local Screen Verification Fixture", "accessibility")))
            worker.start()
            deadline = time.monotonic() + 30
            while worker.is_alive() and time.monotonic() < deadline:
                root.update()
                time.sleep(.02)
            assert not worker.is_alive() and len(answers) == 3 and "7319" in answers[-1]["text"], answers
            # The test fixture is the sole input target. Fail closed on a focus
            # change, as computer_click_observed also does for changed evidence.
            observed = computer.observe()
            assert "Local Screen Verification Fixture" in str(observed["active_window"])
            x = button.winfo_rootx() + button.winfo_width() // 2
            y = button.winfo_rooty() + button.winfo_height() // 2
            computer.click_observed(observed["observation_id"], x, y)
            root.update()
            assert clicked == [True], "The generated fixture did not receive the click."
            reader.start("Local Screen Verification Fixture", method="accessibility", interval=.5, duration=30)
            deadline = time.monotonic() + 15
            while reader.status()["samples"] < 1 and time.monotonic() < deadline:
                root.update()
                time.sleep(.02)
            assert reader.status()["samples"] >= 1, reader.status()
            assert reader.stop()
            assert reader.status()["latest"] is None
            return {"passed": True, "methods": [answer["method"] for answer in answers],
                    "fixture_text_found": True, "local_click_verified": True, "live_stop_cleared": True}
        finally:
            reader.stop()
            root.destroy()


def verify_people(path):
    from PIL import Image, ImageOps
    from app.perception.people import PersonDetector
    detector = PersonDetector()
    with Image.open(path) as source:
        source = source.convert("RGB")
        variants = {"original": source, "mirror": ImageOps.mirror(source), "smaller": source.resize((source.width // 2, source.height // 2)),
                    "blank": Image.new("RGB", source.size, "white")}
        rows = {}
        for name, image in variants.items():
            started = time.perf_counter()
            value = detector.frame(image)
            rows[name] = {"count": value["count"], "seconds": round(time.perf_counter() - started, 3), "people": value["people"]}
        assert all(rows[name]["count"] >= 1 for name in ("original", "mirror", "smaller")), rows
        assert rows["blank"]["count"] == 0, rows
        return {"passed": True, "fixture": str(path), "model": detector.status(), "variants": rows,
                "basis": "Four fixture checks, not a general accuracy benchmark or a guarantee for all views."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / ".test-runs/workspace-hardware.json")
    args = parser.parse_args()
    report = {"screen": verify_screen(), "people": verify_people(args.image)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
