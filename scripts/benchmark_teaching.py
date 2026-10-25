from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(PROJECT_ROOT))

from app.learning.manager import LearningManager
from app.session import SessionState


TOPICS = ["CUDA streams", "NCCL", "GPU scheduling", "Kubernetes", "Docker", "Networking", "PyTorch Distributed", "model serving", "distributed systems", "SRE"]


def main() -> int:
    rows = []
    with tempfile.TemporaryDirectory(prefix="ai-teaching-", ignore_cleanup_errors=True) as temporary:
        learning = LearningManager(Path(temporary) / "learning.db")
        for topic in TOPICS:
            scenarios = [
                ("prerequisite_adaptation", f"Teach me {topic}"),
                ("socratic", f"Teach me {topic} by asking me questions"),
                ("exam", f"Test me on {topic}"),
                ("interview", "Run an AI infrastructure interview"),
                ("weakness_state", "What am I weak at?"),
            ]
            for kind, prompt in scenarios:
                session = SessionState(); started = time.perf_counter(); answer = learning.handle(prompt, session); latency = (time.perf_counter() - started) * 1000
                if kind == "prerequisite_adaptation": passed = "prerequisite" in answer.lower() or not learning.topic(topic)["prerequisites"]
                elif kind == "socratic": passed = "?" in answer and session.pending_learning and session.pending_learning["type"] == "socratic"
                elif kind == "exam": passed = session.pending_learning and session.pending_learning["type"] == "exam" and "no hints" in answer.lower()
                elif kind == "interview": passed = session.pending_learning and session.pending_learning["type"] == "interview" and "hypotheses" in answer.lower()
                else: passed = "measured weaknesses" in answer.lower() or "weakest measured" in answer.lower()
                rows.append({"topic": topic, "scenario": kind, "passed": bool(passed), "latency_ms": round(latency, 3), "response": answer})
        # Score one assessment and one practical lab to prove mastery fields change from evidence.
        session = SessionState(); learning.handle("Test me on NCCL", session); learning.handle("NCCL optimizes collective all-reduce based on topology", session)
        lab = learning.record_lab("NCCL", completed=True, duration_seconds=600, hints_used=1, errors=1, recovered=True, verified=True)
        state = learning.topic("NCCL")
        report = {"cases": len(rows), "accuracy": sum(row["passed"] for row in rows) / len(rows), "mean_latency_ms": round(statistics.mean(row["latency_ms"] for row in rows), 3), "mastery_evidence": {"mastery": state["mastery"], "confidence": state["confidence"], "correct_answers": state["correct_answers"], "lab": lab, "next_review": state["next_review"], "concepts": state["concepts"]}, "rows": rows}
        destination = PROJECT_ROOT / "data/benchmarks/teaching/after.json"; destination.parent.mkdir(parents=True, exist_ok=True); destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "rows"} | {"destination": str(destination)}, indent=2))
        return 0 if len(rows) >= 50 and report["accuracy"] == 1 and state["mastery"] > 0 else 1


if __name__ == "__main__": raise SystemExit(main())
