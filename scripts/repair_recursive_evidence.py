"""Back up knowledge and remove derived cross-claim evidence copies.

Original evidence remains attached to its source claim. The claim_conflicts
table links the opposing claims, so copied cross-* evidence is redundant.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def repair(database: Path, backup: Path, *, vacuum: bool = True) -> dict[str, object]:
    database = database.resolve(strict=True)
    backup = backup.resolve()
    if backup == database or backup.exists():
        raise ValueError("Backup must be a new file separate from the live database.")
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database, timeout=30) as db:
        db.execute("PRAGMA busy_timeout=30000")
        originals = db.execute("SELECT COUNT(*) FROM evidence WHERE id NOT LIKE 'cross-%'").fetchone()[0]
        if not originals:
            raise RuntimeError("No original evidence rows found; cleanup cancelled.")
        copied = db.execute(
            "SELECT COUNT(*) FROM evidence WHERE id >= 'cross-' AND id < 'cross.'"
        ).fetchone()[0]
        report: dict[str, object] = {
            "database": str(database), "backup": str(backup),
            "original_evidence": originals, "derived_evidence": copied,
        }
        if not copied:
            report["deleted"] = 0
            return report

        try:
            with sqlite3.connect(backup) as saved:
                db.backup(saved, pages=4096)
                if saved.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] != originals + copied:
                    raise RuntimeError("Backup evidence count does not match the live database.")
            print(f"Verified backup: {backup}", flush=True)
            with db:
                deleted = db.execute(
                    "DELETE FROM evidence WHERE id >= 'cross-' AND id < 'cross.'"
                ).rowcount
            if deleted != copied:
                raise RuntimeError(f"Expected to delete {copied} derived rows; deleted {deleted}.")
            if db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] != originals:
                raise RuntimeError("Original evidence count changed unexpectedly.")
            print(f"Removed {deleted} derived rows; {originals} original rows remain.", flush=True)
            if vacuum:
                db.execute("VACUUM")
                print("Database compacted.", flush=True)
            integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"Database integrity check failed: {integrity}")
            report.update(deleted=deleted, integrity=integrity,
                          final_bytes=database.stat().st_size)
            return report
        except Exception:
            # Keep the backup for recovery if deletion or compaction fails.
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/knowledge/knowledge.db"))
    parser.add_argument("--backup", type=Path, default=Path("output/hayat-upgrade") /
                        ("knowledge-before-evidence-repair-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + ".db"))
    parser.add_argument("--no-vacuum", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair(args.database, args.backup, vacuum=not args.no_vacuum), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
