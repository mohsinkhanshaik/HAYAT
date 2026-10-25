"""Chat with the trained local model and run bounded, verified self-practice."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt")
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument("--candidate-adapter", action="store_true",
                        help="Compatibility option: the completed trained adapter is now the default.")
    parser.add_argument("--foundation", action="store_true", help="Compare using the original pretrained weights.")
    parser.add_argument("--self-practice", type=int, default=0, metavar="CYCLES",
                        help="Run 1..32 solve/review/verify cycles and persist lessons locally.")
    parser.add_argument("--practice-seconds", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if not (args.prompt and args.prompt.strip()) and not args.self_practice:
        parser.error("Provide --prompt or --self-practice CYCLES.")
    if not 0 <= args.self_practice <= 32 or not 1 <= args.practice_seconds <= 1800:
        parser.error("Use 1..32 practice cycles and 1..1800 seconds.")
    if not 1 <= args.max_new_tokens <= 2048 or args.foundation and args.candidate_adapter:
        parser.error("Invalid output budget or conflicting foundation/adapter selection.")
    from app.learning.self_practice import SelfPractice
    from app.models.hayat_open import HayatOpenSession

    session = HayatOpenSession(candidate_adapter=not args.foundation)
    suffix = "foundation" if args.foundation else "trained"
    practice = SelfPractice(ROOT / f"data/models/hayat-open-4b/self-practice/{suffix}")
    if args.self_practice:
        report = practice.run(session.generate, cycles=args.self_practice, seed=args.seed,
                              max_seconds=args.practice_seconds, model_name=session.model_name)
        print(json.dumps({key: report[key] for key in (
            "id", "state", "completed_cycles", "draft_correct", "verified_correct",
            "model_calls", "elapsed_seconds", "weights_updated", "memory_file")}, indent=2))
    if args.prompt:
        print(session.answer(args.prompt, notes=practice.notes(), max_new_tokens=args.max_new_tokens))


if __name__ == "__main__":
    main()
