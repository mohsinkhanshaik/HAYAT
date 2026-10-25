"""Plate recognition from the command line (the dashboard is in HAYAT: Studios → Plate recognition).

    python scripts/anpr.py image photo.jpg [more.jpg ...]
    python scripts/anpr.py video drive.mp4
    python scripts/anpr.py webcam 0 --seconds 60
    python scripts/anpr.py camera rtsp://192.168.1.20/stream   (registered first in the dashboard)
    python scripts/anpr.py samples [folder]          synthetic vehicles with known plates
    python scripts/anpr.py check [folder]            make samples, read them, report accuracy
    python scripts/anpr.py search MH12 [--status review]
    python scripts/anpr.py export csv out.csv        (json, csv or sqlite)
    python scripts/anpr.py purge [--days N]
    python scripts/anpr.py status

For the owner's own vehicles, authorized cameras, parking sites and test footage.
No owner lookup, no government or police databases, no fines or notices.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    from app.anpr import data_folder, open_system
    parser = argparse.ArgumentParser(description="HAYAT plate recognition")
    parser.add_argument("command", choices=["image", "video", "webcam", "camera", "samples", "check", "search", "export",
                                            "purge", "status"])
    parser.add_argument("values", nargs="*")
    parser.add_argument("--seconds", type=float, default=None, help="stop a live source after this long")
    parser.add_argument("--status", default="", help="search: new, confirmed, corrected, rejected or review")
    parser.add_argument("--days", type=int, default=None, help="purge: keep this many days")
    parser.add_argument("--folder", default="", help="data folder (default data/anpr)")
    args = parser.parse_args(argv)
    folder = Path(args.folder) if args.folder else None
    if args.command == "samples":
        from app.anpr.samples import write_samples
        target = Path(args.values[0]) if args.values else (folder or data_folder()) / "samples"
        print(json.dumps(write_samples(target), indent=1))
        return 0
    store, pipeline = open_system(folder)
    if args.command == "status":
        print(json.dumps({**pipeline.status(), "records": store.search(limit=1)["total"], "settings": store.settings()}, indent=1))
    elif args.command == "image":
        for path in args.values:
            for row in pipeline.process_image(path):
                print(f"{path}\t{row['plate']}\t{row['confidence']:.2f}\t{'valid' if row['format_valid'] else 'unmatched'}")
    elif args.command in ("video", "webcam", "camera"):
        source = int(args.values[0]) if args.command == "webcam" else args.values[0]
        stop = threading.Event()
        if args.seconds:
            threading.Timer(args.seconds, stop.set).start()
        result = pipeline.process_video(source, stop=stop)
        for row in result["stored"]:
            when = f"{row['video_offset']} s" if row.get("video_offset") is not None else row["seen_at"]
            print(f"{when}\t{row['plate']}\t{row['confidence']:.2f}\t{row['readings']} readings")
        print(f"{result['frames']} frames, {len(result['stored'])} plate(s)", file=sys.stderr)
    elif args.command == "check":
        from app.anpr.samples import write_samples
        target = Path(args.values[0]) if args.values else store.folder / "samples"
        truth = write_samples(target)
        right = 0
        for path, expected in truth.items():
            if path.endswith(".mp4"):
                found = [row["plate"] for row in pipeline.process_video(path)["stored"]]
            else:
                found = [row["plate"] for row in pipeline.process_image(path)]
            ok = expected in found
            right += ok
            print(f"{'OK  ' if ok else 'MISS'} {Path(path).name}: expected {expected}, read {found}")
        print(f"{right}/{len(truth)} samples read correctly · {pipeline.status()}")
        return 0 if right == len(truth) else 1
    elif args.command == "search":
        result = store.search(text=args.values[0] if args.values else "", status=args.status)
        for row in result["rows"]:
            print(f"{row['id']}\t{row['seen_at'][:19]}\t{row['corrected_plate'] or row['plate']}\t{row['confidence']:.2f}\t"
                  f"{row['status']}\t{row['source']}")
        print(f"{result['total']} matching", file=sys.stderr)
    elif args.command == "export":
        if len(args.values) != 2:
            parser.error("export needs a kind (json, csv, sqlite) and a target file")
        print(store.export(args.values[1], args.values[0]))
    elif args.command == "purge":
        print(f"{store.purge(args.days)} record(s) deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
