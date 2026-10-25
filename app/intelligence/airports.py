"""Airports of the world (OurAirports, public domain): codes, names and positions.

Downloaded once (~12 MB CSV) and kept as a compact table of large and medium airports
beside HAYAT's intelligence database. Used to place FAA disruptions on the map, to
answer "what is happening at JFK", and to search by IATA or ICAO code.
"""

from __future__ import annotations

import csv
import io
import json
import threading
from pathlib import Path
from typing import Any

SOURCE = "https://davidmegginson.github.io/ourairports-data/airports.csv"
_lock = threading.Lock()
_tables: dict[str, dict[str, dict[str, Any]]] = {}


def parse(content: bytes) -> list[dict[str, Any]]:
    rows = []
    for row in csv.DictReader(io.StringIO(content.decode("utf-8", "replace"))):
        if row.get("type") not in ("large_airport", "medium_airport"):
            continue
        try:
            latitude, longitude = float(row["latitude_deg"]), float(row["longitude_deg"])
        except (KeyError, ValueError):
            continue
        rows.append({"icao": row.get("icao_code") or row.get("gps_code") or row.get("ident"), "iata": row.get("iata_code") or "",
                     "name": row.get("name", ""), "city": row.get("municipality", ""), "country": row.get("iso_country", ""),
                     "latitude": round(latitude, 4), "longitude": round(longitude, 4), "size": row["type"].split("_")[0]})
    return rows


def ensure(fetcher: Any, folder: Path) -> Path | None:
    """The cached airport table, downloading it once when missing. None when it cannot be had."""
    path = Path(folder) / "airports.json"
    if path.is_file():
        return path
    try:
        resource = fetcher.fetch(SOURCE, 16 * 1024 * 1024, {"text/csv", "text/plain", "application/octet-stream"})
        rows = parse(resource.content)
    except Exception:
        return None
    if len(rows) < 1000:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    return path


def table(folder: Path) -> dict[str, dict[str, Any]]:
    """Code -> airport, for both IATA and ICAO codes. Empty when the table was never downloaded."""
    key = str(Path(folder).resolve())
    with _lock:
        if key in _tables:
            return _tables[key]
        path = Path(folder) / "airports.json"
        codes: dict[str, dict[str, Any]] = {}
        if path.is_file():
            for row in json.loads(path.read_text(encoding="utf-8")):
                for code in (row.get("iata"), row.get("icao")):
                    if code:
                        codes.setdefault(code.upper(), row)
        if codes:
            _tables[key] = codes
        return codes


def find(folder: Path, value: str) -> dict[str, Any] | None:
    text = str(value or "").strip().upper()
    codes = table(folder)
    if text in codes:
        return codes[text]
    lowered = text.lower()
    if len(lowered) >= 4:
        for row in codes.values():
            if lowered in row["name"].lower() or lowered == row["city"].lower():
                return row
    return None
