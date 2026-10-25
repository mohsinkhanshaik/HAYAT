"""Verified owner feedback for adaptive local image/video defaults."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.safety.sensitive import redact_text


class MediaFeedback:
    def __init__(self, generator: Any, path: Path | None = None) -> None:
        self.generator = generator
        self.path = path or PROJECT_ROOT / "data/memory/media_feedback.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS ratings(artifact TEXT PRIMARY KEY,kind TEXT NOT NULL,score INTEGER NOT NULL,"
                "notes TEXT NOT NULL,model TEXT,quality TEXT,style TEXT,rated_at TEXT NOT NULL)"
            )

    def record(self, artifact: str, score: int, notes: str = "") -> dict[str, Any]:
        rating = max(1, min(int(score), 5))
        target = Path(artifact).resolve()
        rows = [("image", row) for row in self.generator.history(200)]
        rows += [("video", row) for row in self.generator.video_history(200)]
        match = next(((kind, row) for kind, row in rows
                      if Path(str(row.get("path", ""))).resolve() == target), None)
        if match is None:
            return {"recorded": False, "reason": "artifact is not in HAYAT's generated-media manifests"}
        kind, metadata = match
        cleaned, _ = redact_text(" ".join(str(notes).split())[:500])
        model = str(metadata.get("model") or getattr(getattr(self.generator, "model_path", None), "name", ""))
        with self._connect() as db:
            db.execute(
                "INSERT INTO ratings(artifact,kind,score,notes,model,quality,style,rated_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(artifact) DO UPDATE SET score=excluded.score,notes=excluded.notes,rated_at=excluded.rated_at",
                (str(target), kind, rating, cleaned, model, metadata.get("quality"), metadata.get("style"), datetime.now(UTC).isoformat()),
            )
        return {"recorded": True, "artifact": str(target), "kind": kind, "score": rating,
                "recommendation": self.recommend(kind)}

    def recommend(self, kind: str = "image", minimum_samples: int = 3) -> dict[str, Any]:
        selected = "video" if str(kind).lower() == "video" else "image"
        with self._connect() as db:
            rows = db.execute(
                "SELECT quality,style,model,COUNT(*),AVG(score) FROM ratings WHERE kind=? "
                "GROUP BY quality,style,model ORDER BY AVG(score) DESC,COUNT(*) DESC", (selected,),
            ).fetchall()
            total = int(db.execute("SELECT COUNT(*) FROM ratings WHERE kind=?", (selected,)).fetchone()[0])
        best = rows[0] if rows else None
        trusted = bool(best and int(best[3]) >= max(1, int(minimum_samples)))
        return {
            "kind": selected, "ratings": total, "trusted": trusted,
            "quality": best[0] if trusted else None, "style": best[1] if trusted else None,
            "model": best[2] if trusted else None,
            "mean_score": round(float(best[4]), 3) if best else None,
            "samples_for_profile": int(best[3]) if best else 0,
            "policy": "owner ratings only; no model self-grading",
        }

    def status(self) -> dict[str, Any]:
        return {"database": str(self.path), "image": self.recommend("image"),
                "video": self.recommend("video"), "automatic_weight_training": False}

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()
