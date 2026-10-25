from __future__ import annotations

import yaml

from app.memory.database import MemoryDatabase, now_iso


class ProceduralMemory:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def save_candidate(self, name: str, description: str, definition: dict, trusted: bool = False) -> None:
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO procedures(name,description,definition_yaml,trusted,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET description=excluded.description,definition_yaml=excluded.definition_yaml,trusted=excluded.trusted,updated_at=excluded.updated_at",
                (name, description, yaml.safe_dump(definition, sort_keys=False), int(trusted), now_iso()),
            )

    def trusted(self) -> list[dict]:
        with self.database.connect() as db:
            rows = db.execute("SELECT * FROM procedures WHERE trusted=1 ORDER BY confidence DESC, success_count DESC").fetchall()
        return [dict(row) | {"definition": yaml.safe_load(row["definition_yaml"])} for row in rows]

    def record_result(self, name: str, succeeded: bool, latency_ms: float = 0) -> dict:
        """Update experience statistics for an existing reviewed procedure."""
        with self.database.connect() as db:
            row = db.execute("SELECT * FROM procedures WHERE name=?", (name,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown procedure: {name}")
            successes = int(row["success_count"]) + int(succeeded)
            failures = int(row["failure_count"]) + int(not succeeded)
            total = successes + failures
            previous_total = int(row["success_count"]) + int(row["failure_count"])
            average = ((float(row["average_latency_ms"]) * previous_total) + max(0.0, float(latency_ms))) / max(1, total)
            confidence = (successes + 1) / (total + 2)
            db.execute(
                "UPDATE procedures SET success_count=?,failure_count=?,average_latency_ms=?,last_used_at=?,confidence=?,updated_at=? WHERE name=?",
                (successes, failures, average, now_iso(), confidence, now_iso(), name),
            )
        return {"name": name, "success_count": successes, "failure_count": failures, "average_latency_ms": round(average, 2), "confidence": round(confidence, 4)}
