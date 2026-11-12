"""Measured cognitive evolution: one scorecard of what HAYAT can do, and a gate that keeps only real gains.

The owner asked that architectural changes be kept "only when tests show measurable benefits". Two parts:

**The live scorecard** (``Scorecard.measure``) reads HAYAT's own records -- held-out and reasoning accuracy,
hallucination (fabricated actions and citations), calibration, prediction lift, memory retention and retrieval
rank, learning efficiency, latency and resource use -- and keeps a dated history (``scorecard.db``), so a change
in any of them is a measured difference between two dates, not an impression.

**The gate** (``benchmarks()`` + ``compare``) runs deterministic, model-free benchmarks on fixtures: topic routing
on the labelled and held-out conversations, follow-up resolution, context contamination, and recall of a fixed set
of general-knowledge statements. ``scripts/cognitive_gate.py`` compares them with the accepted baseline in
``data/benchmarks/cognitive/baseline.json`` and fails on a regression beyond its tolerance; the regression suite
runs the same comparison, so a change that makes HAYAT measurably worse cannot pass the tests. Keeping a gain means
writing a new baseline (``--accept``): a deliberate step, never automatic.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: name -> (which way is better, the drop that still counts as noise).
GATED = {
    "topic_relation_accuracy": ("higher", 0.02),
    "topic_relation_accuracy_held_out": ("higher", 0.0),
    "topic_grouping_accuracy": ("higher", 0.02),
    "follow_up_resolution": ("higher", 0.0),
    "context_contamination": ("lower", 0.0),
    "fixture_recall_at_5": ("higher", 0.02),
    "fixture_mean_reciprocal_rank": ("higher", 0.03),
    # Robustness (app/robustness.py): the injection guard, and topic routing with 20% of words misspelt.
    "injection_detection": ("higher", 0.0),
    "injection_false_alarms": ("lower", 0.0),
    "routing_accuracy_misspelt": ("higher", 0.02),
}
BASELINE = Path(__file__).resolve().parents[1] / "data" / "benchmarks" / "cognitive" / "baseline.json"

#: General-knowledge statements for the recall fixture, each with the cue a person might use to recall it.
FACTS = [
    ("Water boils at 100 degrees Celsius at sea level and at a lower temperature on high mountains.", "water boils mountains"),
    ("Canberra is the capital city of Australia, chosen as a compromise between Sydney and Melbourne.", "capital australia"),
    ("Photosynthesis turns carbon dioxide and water into glucose and oxygen using sunlight.", "photosynthesis glucose"),
    ("The Pacific is the largest and deepest of the world's oceans.", "largest ocean"),
    ("A heat pump moves heat from outside air into a house, and loses efficiency in very cold weather.", "heat pump cold"),
    ("Python lists are mutable, while tuples cannot be changed after they are created.", "python tuples"),
    ("The speed of light in a vacuum is about 299,792 kilometres per second.", "speed light"),
    ("Mount Everest, at about 8,849 metres, is the highest mountain above sea level.", "highest mountain"),
    ("Vaccines train the immune system by showing it a harmless part or copy of a germ.", "vaccines immune"),
    ("Inflation is the rate at which the general level of prices rises over time.", "inflation prices"),
    ("The Quran has 114 surahs, and Al-Fatiha is the first of them.", "quran surahs"),
    ("A GPU with 8 GB of VRAM can run quantized language models of roughly 7 to 14 billion parameters.", "vram language models"),
    ("Bitcoin's supply is capped at 21 million coins.", "bitcoin supply"),
    ("The human heart has four chambers: two atria and two ventricles.", "heart chambers"),
    ("Solid state batteries replace the liquid electrolyte with a solid one.", "solid state batteries"),
    ("The Great Wall of China was built over many centuries by several dynasties.", "great wall"),
    ("Arabic is written from right to left and has 28 letters.", "arabic letters"),
    ("Interest rates set by a central bank influence borrowing costs across the economy.", "central bank rates"),
    ("Mercury is the closest planet to the Sun and has almost no atmosphere.", "closest planet sun"),
    ("The Amazon rainforest produces a large share of the world's river water vapour.", "amazon rainforest"),
]
#: Noise around the facts, so recall is a ranking problem and not a list of twenty.
NOISE = [
    "Opened the settings window and changed the theme.", "Looked up the weather for the weekend.",
    "Drafted a short note about the meeting.", "Checked the list of downloaded files.",
    "Compared two laptops for video editing.", "Asked for a recipe for lentil soup.",
    "Read about the history of chess openings.", "Played a song and paused it.",
    "Searched for remote developer jobs.", "Translated a greeting into French.",
]


def _fixture_recall() -> dict[str, float]:
    """Store the facts and the noise in a throwaway memory, then ask for each fact by its cue."""
    from app.memory.database import MemoryDatabase

    with tempfile.TemporaryDirectory() as folder:
        memory = MemoryDatabase(Path(folder) / "fixture.db")
        memory.initialize()
        ids = [memory.store_memory("semantic", fact, 0.6, "fixture") for fact, _cue in FACTS]
        for line in NOISE:
            memory.store_memory("episodic", line, 0.4, "fixture")
        hits = 0
        reciprocal = 0.0
        for memory_id, (_fact, cue) in zip(ids, FACTS):
            found = [int(row["id"]) for row in memory.retrieve(cue, limit=5)]
            if memory_id in found:
                hits += 1
                reciprocal += 1 / (found.index(memory_id) + 1)
    return {"fixture_recall_at_5": round(hits / len(FACTS), 3),
            "fixture_mean_reciprocal_rank": round(reciprocal / len(FACTS), 3)}


def benchmarks() -> dict[str, float]:
    """The gated, deterministic benchmarks (no model, no network, a few seconds)."""
    from app.evaluation.topic_cases import CASES, HOLDOUT_CASES, run_benchmark

    tuned, held_out = run_benchmark(CASES), run_benchmark(HOLDOUT_CASES)
    return {
        "topic_relation_accuracy": tuned["relation_accuracy"],
        "topic_relation_accuracy_held_out": held_out["relation_accuracy"],
        "topic_grouping_accuracy": round((tuned["grouping_accuracy"] + held_out["grouping_accuracy"]) / 2, 3),
        "follow_up_resolution": round((tuned["reference_resolution"] + held_out["reference_resolution"]) / 2, 3),
        "context_contamination": max(tuned["contamination_rate"], held_out["contamination_rate"]),
        **_fixture_recall(),
        **_robustness(),
    }


def _robustness() -> dict[str, float]:
    from app.robustness import injection_suite, text_suite

    guard, noisy = injection_suite(), text_suite()
    return {"injection_detection": guard["detection_rate"], "injection_false_alarms": guard["false_alarm_rate"],
            "routing_accuracy_misspelt": noisy["metric"]}


def compare(baseline: dict[str, float], current: dict[str, float]) -> dict[str, Any]:
    """Per gated metric: better, same or worse than the accepted baseline; worse beyond tolerance fails."""
    rows = []
    for name, (direction, tolerance) in GATED.items():
        before, after = baseline.get(name), current.get(name)
        if before is None or after is None:
            rows.append({"metric": name, "before": before, "after": after, "verdict": "new" if before is None else "missing"})
            continue
        change = (after - before) if direction == "higher" else (before - after)
        verdict = "better" if change > 1e-9 else "worse" if change < -tolerance - 1e-9 else "same"
        rows.append({"metric": name, "before": before, "after": after, "change": round(change, 3), "verdict": verdict})
    regressed = [row["metric"] for row in rows if row["verdict"] in {"worse", "missing"}]
    improved = [row["metric"] for row in rows if row["verdict"] == "better"]
    return {"passed": not regressed, "regressed": regressed, "improved": improved, "rows": rows}


def load_baseline(path: Path = BASELINE) -> dict[str, float]:
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")).get("metrics") or {})
    except (OSError, ValueError):
        return {}


def accept(current: dict[str, float], path: Path = BASELINE, note: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"accepted_at": datetime.now(UTC).isoformat(timespec="seconds"), "note": note,
                                "metrics": current}, indent=2) + "\n", encoding="utf-8")


class Scorecard:
    """The live scorecard from HAYAT's own records, with a dated history."""

    def __init__(self, runtime: Callable[[], Any], path: Path | str) -> None:
        self.runtime = runtime
        self.path = Path(path)
        self._lock = threading.Lock()

    def measure(self) -> dict[str, Any]:
        from app.cognitive_development import REASONING_FAMILIES, CognitiveDevelopment, _latest_holdout

        rt = self.runtime()
        metrics: dict[str, Any] = {}
        sources: dict[str, str] = {}
        data_root = Path(getattr(getattr(rt, "memory", None), "path", Path("data/memory/agent.db"))).parent.parent
        holdout = None
        try:
            holdout = _latest_holdout(data_root)
        except OSError:
            log_swallowed(_log, "reading the held-out run for the scorecard")
        if holdout:
            cases = max(int(holdout.get("completed_cases") or 0), int(holdout.get("cases") or 0))
            metrics["held_out_accuracy"] = round(int(holdout.get("correct") or 0) / cases, 3) if cases else None
            families = holdout.get("families") or {}
            rows = [families[name] for name in REASONING_FAMILIES if name in families]
            total = sum(int(row.get("cases") or 0) for row in rows)
            metrics["reasoning_accuracy"] = round(sum(int(row.get("correct") or 0) for row in rows) / total, 3) if total else None
            fabricated = [holdout.get("fabricated_action_rate"), holdout.get("fabricated_citation_rate")]
            fabricated = [float(value) for value in fabricated if value is not None]
            metrics["hallucination_rate"] = round(sum(fabricated) / len(fabricated), 3) if fabricated else None
            metrics["calibration_error"] = (holdout.get("calibration") or {}).get("ece")
            metrics["answer_latency_p50_ms"] = (holdout.get("latency_ms") or {}).get("p50")
            metrics["answer_latency_p95_ms"] = (holdout.get("latency_ms") or {}).get("p95")
            sources["held-out run"] = str(holdout.get("file"))
        development = CognitiveDevelopment(self.runtime, self.path.with_name("development.db"))
        situation = getattr(rt, "situation", None)
        if situation is not None:
            try:
                record = development._prediction_record(Path(situation.path))
                if record and record.get("checked"):
                    metrics["prediction_hit_rate"] = round(record["rate"], 3)
                    metrics["prediction_lift"] = round(record["rate"] - record["baseline"], 3)
            except sqlite3.Error:
                log_swallowed(_log, "reading predictions for the scorecard")
        retention = getattr(rt, "retention", None)
        if retention is not None:
            report = retention.report()
            metrics["memory_retention"] = report.get("retention")
            metrics["memory_mean_reciprocal_rank"] = report.get("mean_reciprocal_rank")
            metrics["recall_latency_median_ms"] = report.get("median_ms")
        try:
            counts = development._learner_counts(rt)
        except sqlite3.Error:
            counts = None
        if counts:
            learned, failed = counts["by_status"].get("learned", 0), counts["by_status"].get("failed", 0)
            metrics["learning_success_rate"] = round(learned / (learned + failed), 3) if learned + failed else None
            asked = counts["matched"] + counts["mismatched"]
            metrics["learned_ahead_hit_rate"] = round(counts["matched"] / asked, 3) if asked else None
        metrics.update(self._resources(rt))
        snapshot = {"measured_at": datetime.now(UTC).isoformat(timespec="seconds"), "metrics": metrics, "sources": sources}
        try:
            snapshot["previous"] = self._keep(snapshot)
        except sqlite3.Error:
            log_swallowed(_log, "keeping a scorecard snapshot")
        events = getattr(rt, "events", None)
        if events is not None:
            try:
                events.publish("SCORECARD_MEASURED", "scorecard", {"metrics": len(metrics)})
            except Exception:
                log_swallowed(_log, "publishing a scorecard measurement")
        return snapshot

    def status(self) -> dict[str, Any]:
        """The last measurement kept (no new measurement is taken)."""
        if not self.path.is_file():
            return {"measured": "never"}
        with self._lock, closing(sqlite3.connect(self.path, timeout=5)) as db:
            row = db.execute("SELECT at, metrics_json FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
            count = int(db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
        if row is None:
            return {"measured": "never"}
        return {"measured_at": row[0], "measurements_kept": count,
                **{key: value for key, value in json.loads(row[1]).items() if value is not None}}

    @staticmethod
    def _resources(rt: Any) -> dict[str, Any]:
        found: dict[str, Any] = {}
        try:
            import psutil

            found["process_ram_mb"] = round(psutil.Process().memory_info().rss / 1024**2, 1)
        except Exception:
            log_swallowed(_log, "reading process memory for the scorecard")
        memory = getattr(rt, "memory", None)
        folder = Path(getattr(memory, "path", "")).parent if memory is not None else None
        if folder is not None and folder.is_dir():
            found["memory_store_mb"] = round(sum(item.stat().st_size for item in folder.glob("*.db*")) / 1024**2, 1)
        try:
            from app.hardware import profile

            gpu = profile().gpu
            if gpu.available and gpu.used_bytes is not None:
                found["gpu_memory_used_mb"] = round(gpu.used_bytes / 1024**2)
        except Exception:
            log_swallowed(_log, "reading GPU memory for the scorecard")
        return found

    def _keep(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock, closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
                       "metrics_json TEXT NOT NULL)")
            last = db.execute("SELECT at, metrics_json FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
            db.execute("INSERT INTO snapshots(at, metrics_json) VALUES(?,?)",
                       (snapshot["measured_at"], json.dumps(snapshot["metrics"])))
            db.execute("DELETE FROM snapshots WHERE id NOT IN (SELECT id FROM snapshots ORDER BY id DESC LIMIT 500)")
        return {"at": last[0], "metrics": json.loads(last[1])} if last else None

    def history(self, metric: str, limit: int = 60) -> list[tuple[str, Any]]:
        if not self.path.is_file():
            return []
        with self._lock, closing(sqlite3.connect(self.path, timeout=5)) as db:
            rows = db.execute("SELECT at, metrics_json FROM snapshots ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [(at, json.loads(values).get(metric)) for at, values in reversed(rows)]


def register_scorecard_tools(registry: Any, scorecard: Scorecard) -> None:
    def run(action: str = "measure") -> dict[str, Any]:
        if action == "gate":
            started = time.perf_counter()
            current = benchmarks()
            result = compare(load_baseline(), current)
            return {**result, "current": current, "seconds": round(time.perf_counter() - started, 2)}
        return scorecard.measure()

    registry.register(
        "cognitive_scorecard",
        "HAYAT's measured abilities from its own records: held-out and reasoning accuracy, hallucination rate, "
        "calibration, prediction lift, memory retention and retrieval rank, learning efficiency, latency and resource "
        "use, with the previous measurement beside them. action=gate runs the deterministic benchmarks and compares "
        "them with the accepted baseline (a regression fails).",
        {"type": "object", "properties": {"action": {"type": "string", "enum": ["measure", "gate"]}},
         "additionalProperties": False},
        "low", "system_read", run)


#: Questions about HAYAT's measured abilities, its forecasts and its recall, and the tool that answers each.
_MIND_QUESTIONS = (
    ("forecast_ledger", r"(?i)\b(?:how\s+(?:accurate|good|often\s+right)\s+(?:are|were)\s+(?:your|ur)\s+(?:forecasts|predictions)"
                        r"|(?:which|what)\s+(?:of\s+)?(?:your\s+)?(?:forecasts|predictions)\s+(?:are|is)\s+due"
                        r"|(?:show|list)\s+(?:me\s+)?(?:your|ur)\s+(?:past\s+)?(?:forecasts|predictions))\b"),
    ("memory_retention", r"(?i)\b(?:how\s+(?:well|much)\s+(?:do|can)\s+(?:you|u)\s+(?:remember|recall|retain)"
                         r"|(?:your|ur)\s+(?:memory\s+)?retention|what\s+are\s+you\s+forgetting)\b"),
    ("cognitive_scorecard", r"(?i)\b(?:(?:show|what(?:'s|\s+is))\s+(?:me\s+)?(?:your|ur|hayat'?s)\s+(?:cognitive\s+)?scorecard"
                            r"|how\s+(?:smart|intelligent|capable)\s+are\s+you\s+(?:really|measurably|now)"
                            r"|(?:what\s+is|what's)\s+your\s+hallucination\s+rate)\b"),
)


def mind_request(text: str) -> str | None:
    """The tool that answers a question about HAYAT's measured mind, or None."""
    import re

    value = " ".join(str(text or "").split())
    return next((tool for tool, pattern in _MIND_QUESTIONS if re.search(pattern, value)), None)


def direct_mind_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    tool = mind_request(text)
    if tool is None or tool not in set(names):
        return None
    arguments = {"action": "due"} if tool == "forecast_ledger" and "due" in text.lower() else {}
    return [{"description": "Read HAYAT's measured record for this question.", "tool": tool, "arguments": arguments,
             "expected": {"type": "tool_success"}, "max_retries": 0}]
