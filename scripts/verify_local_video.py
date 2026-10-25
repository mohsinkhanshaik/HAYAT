"""Manual, bounded local video smoke test; does no GPU work without --run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from app.models.diffusion import ImageGenerator

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly run one tiny local GPU generation.")
    parser.add_argument("--motion", choices=["auto", "v2", "v3"], default="auto")
    parser.add_argument("--steps", type=int, choices=range(1, 51), default=2, metavar="1..50")
    arguments = parser.parse_args()
    folder = ROOT / ".test-runs" / f"local-video-{uuid.uuid4().hex[:10]}"
    generator = ImageGenerator(output_root=folder / "images", video_root=folder / "videos", timeout=240)
    if not arguments.run:
        print(json.dumps(generator.video_status(), indent=2))
        print("Pass --run to use the local GPU. This verifies execution and encoding, not visual quality.")
        return 0
    print("Running one 8-frame 256px local clip; no account, upload, or paid API.", flush=True)
    result = generator.generate_video("a red apple on a wooden table, gentle camera movement", frames=8,
                                      width=256, height=256, steps=arguments.steps, fps=8,
                                      quality="fast", enhance=False, seed=20261024,
                                      motion_module=arguments.motion)
    folder.mkdir(parents=True, exist_ok=True)
    report = folder / "verification.json"
    report.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    print(json.dumps(result.to_dict(), indent=2))
    print(f"Verification report: {report}")
    return 0 if result.ok and result.video and result.video.get("decoded_verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
