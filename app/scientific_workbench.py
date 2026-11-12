"""Bounded, local descriptive statistics for the Scientific Workbench.

This module has no model, network or dataframe dependencies. The first CSV row
is a header; decimal points use ``.``. Statistics describe the inspected rows,
not an inferred population. Pearson correlations use pairwise complete numeric
values and are exploratory associations, with no significance or causal claim.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import math
from pathlib import Path
import statistics
import time
from typing import Callable

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_COLUMNS = 128
MAX_CELLS = 1_000_000
MAX_ROWS = 50_000
MAX_FIELD_CHARS = 16_384
CORRELATION_COLUMNS = 16
MISSING_VALUES = frozenset(("", "na", "n/a", "null", "none", "nan"))


class AnalysisCancelled(Exception):
    """The owner cancelled an analysis before it completed."""


def _check(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise AnalysisCancelled("Analysis cancelled.")


def _number(value: str) -> float | None:
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _percentile(ordered: list[float], portion: float) -> float:
    rank = (len(ordered) - 1) * portion
    lower = int(rank)
    weight = rank - lower
    if weight == 0:
        return ordered[lower]
    # Weighted terms avoid overflow when adjacent values are both very large.
    return ordered[lower] * (1 - weight) + ordered[lower + 1] * weight


def _statistics(values: list[float]) -> dict:
    ordered = sorted(values)
    scale = max(abs(ordered[0]), abs(ordered[-1])) or 1.0
    normalized = [value / scale for value in values]
    mean = statistics.fmean(normalized) * scale
    stdev = statistics.stdev(normalized) * scale if len(values) > 1 else None
    low, high = ordered[0], ordered[-1]
    if low == high:
        histogram = [{"low": low, "high": high, "count": len(values)}]
    else:
        bins = min(12, max(2, math.ceil(math.sqrt(len(values)))))
        lo, hi = low / scale, high / scale
        width = (hi - lo) / bins
        counts = [0] * bins
        for value in normalized:
            counts[min(bins - 1, max(0, int((value - lo) / width)))] += 1
        edges = [(lo + (hi - lo) * index / bins) * scale for index in range(bins + 1)]
        edges[0], edges[-1] = low, high
        histogram = [{"low": edges[index], "high": edges[index + 1], "count": count}
                     for index, count in enumerate(counts)]
    return {"mean": _finite(mean), "median": _finite(_percentile(ordered, .5)),
            "stdev": _finite(stdev) if stdev is not None else None,
            "min": low, "max": high, "q1": _finite(_percentile(ordered, .25)),
            "q3": _finite(_percentile(ordered, .75)), "histogram": histogram}


def _pearson(first: list[float], second: list[float]) -> float | None:
    xscale, yscale = max(map(abs, first)), max(map(abs, second))
    if not xscale or not yscale:
        return None
    x, y = [value / xscale for value in first], [value / yscale for value in second]
    xmean, ymean = statistics.fmean(x), statistics.fmean(y)
    dx, dy = [value - xmean for value in x], [value - ymean for value in y]
    xx, yy = math.fsum(value * value for value in dx), math.fsum(value * value for value in dy)
    if not xx or not yy:
        return None
    covariance = math.fsum(a * b for a, b in zip(dx, dy))
    return max(-1.0, min(1.0, covariance / math.sqrt(xx * yy)))


def _headers(raw: list[str], warnings: list[str]) -> list[str]:
    used: set[str] = set()
    headers = []
    changed = False
    for index, value in enumerate(raw):
        name = value.strip() or f"Column {index + 1}"
        candidate, suffix = name, 2
        while candidate in used:
            candidate, suffix = f"{name} ({suffix})", suffix + 1
        changed |= candidate != value
        headers.append(candidate)
        used.add(candidate)
    if changed:
        warnings.append("Blank, duplicate or padded headers were given unique display names.")
    return headers


def _profile(text: str, *, path: str, name: str, max_rows: int,
             cancelled: Callable[[], bool] | None = None, demo: bool = False) -> dict:
    started = time.perf_counter()
    _check(cancelled)
    warnings: list[str] = []
    if not text.strip():
        raise ValueError("The CSV is empty. Add a header row and data rows.")
    if "\x00" in text:
        raise ValueError("The file contains null bytes and is not a supported text CSV.")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        # Ragged data often defeats Sniffer. The header remains a bounded,
        # deterministic fallback; quoted headers still pass through csv.reader.
        first_line = text.partition("\n")[0].partition("\r")[0]
        delimiter = max(",;\t|", key=lambda char: first_line.count(char))
        dialect = None
    reader = csv.reader(io.StringIO(text, newline=""), dialect=dialect or "excel", delimiter=delimiter, strict=True)
    try:
        raw_headers = next(reader)
        if not raw_headers or not any(value.strip() for value in raw_headers):
            raise ValueError("The first CSV row must contain column names.")
        if len(raw_headers) > MAX_COLUMNS:
            raise ValueError(f"The CSV has too many columns; the limit is {MAX_COLUMNS}.")
        if any(len(value) > MAX_FIELD_CHARS for value in raw_headers):
            raise ValueError(f"A CSV header exceeds {MAX_FIELD_CHARS:,} characters.")
        headers = _headers(raw_headers, warnings)
        width = len(headers)
        effective_limit = min(max_rows, MAX_ROWS, MAX_CELLS // width)
        raw_columns: list[list[str]] = [[] for _ in headers]
        preview = []
        rows, ragged, reached_limit = 0, 0, False
        for row in reader:
            if not row:
                continue  # physical blank lines are not observations
            if rows >= effective_limit:
                reached_limit = True
                break
            if rows % 256 == 0:
                _check(cancelled)
            if any(len(value) > MAX_FIELD_CHARS for value in row):
                raise ValueError(f"A CSV field exceeds {MAX_FIELD_CHARS:,} characters.")
            if len(row) != width:
                ragged += 1
                row = row[:width] + [""] * max(0, width - len(row))
            cleaned = [value.strip() for value in row]
            for column, value in zip(raw_columns, cleaned):
                column.append(value)
            if len(preview) < 12:
                preview.append([value if len(value) <= 160 else value[:157] + "..." for value in cleaned])
            rows += 1
    except csv.Error as error:
        raise ValueError(f"The CSV could not be read: {error}.") from error
    if not rows:
        warnings.append("The CSV has headers but no data rows; statistics are unavailable.")
    if reached_limit:
        warnings.append(f"Only the first {rows:,} data rows were inspected (row/cell budget). This is not a random sample.")
    if ragged:
        warnings.append(f"{ragged:,} rows had a different number of fields: short rows were padded; extra fields were ignored.")

    columns, numeric_columns = [], []
    for index, (header, raw) in enumerate(zip(headers, raw_columns)):
        _check(cancelled)
        present_values = [value for value in raw if value.casefold() not in MISSING_VALUES]
        numeric = [None if value.casefold() in MISSING_VALUES else _number(value) for value in raw]
        valid = [value for value in numeric if value is not None]
        numeric_kind = bool(valid) and len(valid) / len(present_values) >= .8
        invalid = len(present_values) - len(valid) if numeric_kind else 0
        column = {"name": header, "index": index, "kind": "numeric" if numeric_kind else "text",
                  "present": len(present_values), "missing": rows - len(present_values), "invalid": invalid,
                  "unique": len(set(present_values)), "numeric_count": len(valid),
                  **dict.fromkeys(("mean", "median", "stdev", "min", "max", "q1", "q3")), "histogram": []}
        if numeric_kind:
            column.update(_statistics(valid))
            numeric_columns.append((header, numeric))
            if invalid:
                warnings.append(f"Column {header!r}: {invalid:,} nonmissing values were excluded from numeric statistics.")
            if column["stdev"] is None and len(valid) > 1:
                warnings.append(f"Column {header!r}: standard deviation exceeds the finite numeric range.")
        columns.append(column)

    correlations = []
    if len(numeric_columns) > CORRELATION_COLUMNS:
        warnings.append(f"Correlations cover only the first {CORRELATION_COLUMNS} numeric columns (at most 120 pairs).")
    selected = numeric_columns[:CORRELATION_COLUMNS]
    for position, (first_name, first) in enumerate(selected):
        for second_name, second in selected[position + 1:]:
            _check(cancelled)
            pairs = [(x, y) for x, y in zip(first, second) if x is not None and y is not None]
            if len(pairs) < 3:
                continue
            x, y = map(list, zip(*pairs))
            coefficient = _pearson(x, y)
            if coefficient is not None:
                correlations.append({"x": first_name, "y": second_name, "n": len(pairs), "r": coefficient})
    correlations.sort(key=lambda item: abs(item["r"]), reverse=True)
    _check(cancelled)
    return {"path": path, "name": name, "demo": demo, "rows": rows, "columns": columns,
            "headers": headers, "row_limit_reached": reached_limit, "delimiter": delimiter,
            "warnings": warnings, "correlations": correlations, "preview": preview,
            "elapsed_seconds": time.perf_counter() - started,
            "method": "Descriptive statistics; sample standard deviation (n-1); linearly interpolated quartiles; pairwise-complete Pearson r.",
            "limits": {"file_bytes": MAX_FILE_BYTES, "rows": effective_limit, "columns": MAX_COLUMNS,
                       "cells": MAX_CELLS, "correlation_columns": CORRELATION_COLUMNS,
                       "numeric_threshold": .8, "missing_tokens": sorted(MISSING_VALUES)}}


def profile_csv(path: str | Path, *, max_rows: int = MAX_ROWS,
                cancelled: Callable[[], bool] | None = None) -> dict:
    """Read a CSV locally and return a JSON-safe profile, without changing it.

    Files are limited to 32 MiB, 128 columns, 50,000 inspected rows and one
    million cells. Fields are limited to 16,384 characters. Invalid UTF-8 is
    rejected; UTF-8/UTF-16 byte order marks are supported. Numeric columns need
    at least 80% finite numeric values among nonmissing cells. There are no
    missing-value imputations. ``cancelled`` may return true or raise
    :class:`AnalysisCancelled`; it is polled during the computation.
    """
    if not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows < 1:
        raise ValueError("The row limit must be a positive integer.")
    _check(cancelled)
    source = Path(path).expanduser()
    if not source.is_file():
        raise ValueError("Choose an existing CSV file.")
    if source.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("The CSV exceeds the 32 MiB file limit. Choose a smaller extract.")
    with source.open("rb") as handle:
        raw = handle.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("The CSV exceeds the 32 MiB file limit. Choose a smaller extract.")
    _check(cancelled)
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        content = raw.decode(encoding)
    except UnicodeError as error:
        raise ValueError("The CSV must use UTF-8 or UTF-16 with a byte order mark.") from error
    profile = _profile(content, path=str(source.resolve()), name=source.name, max_rows=max_rows, cancelled=cancelled)
    profile["source_sha256"] = hashlib.sha256(raw).hexdigest()
    return profile


def demo_profile() -> dict:
    """A deterministic instrument example, clearly labelled and never written."""
    lines = ["Trial,Temperature C,Response mV,Elapsed s,Batch"]
    for trial in range(1, 49):
        temperature = 18 + trial * .35
        response = 4.2 * temperature + math.sin(trial * .8) * 6
        elapsed = 30 + (trial * 7) % 37
        lines.append(f"{trial},{'' if trial in (8, 31) else f'{temperature:.2f}'},{response:.3f},{elapsed},{'A' if trial <= 24 else 'B'}")
    return _profile("\n".join(lines), path="Built-in demo", name="Built-in demo", max_rows=MAX_ROWS, demo=True)


def _escape(value: object) -> str:
    value = " ".join(str(value).split())
    value = html.escape(value, quote=False)
    for character in ("\\", "`", "|", "*", "_", "[", "]", "#"):
        value = value.replace(character, "\\" + character)
    return value


def _format(value: float | None) -> str:
    return "—" if value is None else f"{value:.6g}"


def report_markdown(profile: dict) -> str:
    """Export inspected measurements and explicit methods as safe Markdown."""
    title = _escape(profile["name"])
    lines = [f"# Scientific Workbench — {title}", "", f"Source: {_escape(profile['path'])}", "",
             f"Inspected rows: {profile['rows']:,} · Columns: {len(profile['columns']):,}", ""]
    if not profile.get("demo") and profile.get("source_sha256"):
        lines += ["SHA-256 of source bytes: " + _escape(profile["source_sha256"]), ""]
    if profile.get("demo"):
        lines += ["**Built-in synthetic demonstration. These are not observations from your files.**", ""]
    lines += ["## Column measurements", "",
              "Column | Type | Present | Missing | Invalid | Unique | Mean | Median | Sample SD | Minimum | Q1 | Q3 | Maximum",
              "--- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---:"]
    for column in profile["columns"]:
        cells = [_escape(column["name"]), column["kind"], str(column["present"]), str(column["missing"]),
                 str(column["invalid"]), str(column["unique"])]
        cells += [_format(column[key]) for key in ("mean", "median", "stdev", "min", "q1", "q3", "max")]
        lines.append(" | ".join(cells))
    lines += ["", "## Exploratory correlations", "", "Pair | Pearson r | Complete pairs", "--- | ---: | ---:"]
    for pair in profile["correlations"]:
        lines.append(f"{_escape(pair['x'])} / {_escape(pair['y'])} | {pair['r']:.6g} | {pair['n']:,}")
    if not profile["correlations"]:
        lines += ["No eligible variable pairs | — | —"]
    lines += ["", "Correlations use pairwise complete observations (at least three). Constant columns are omitted. "
              "These are exploratory associations: no significance, population inference or causal claim is made.", "",
              "## Method and limits", "", profile["method"], "",
              "Numeric columns require at least 80% finite numeric entries among nonmissing cells. "
              "Missing values (blank, NA, N/A, null, none, NaN; case insensitive) and invalid numeric entries are excluded; "
              "they are never replaced. Text columns have no numeric summary. Unique counts exclude missing cells. "
              "The first row is a header; numbers use a decimal point.", "",
              "Analysis is bounded to 32 MiB, 128 columns, 50,000 rows and 1,000,000 cells. "
              "Correlations use the first 16 numeric columns. A limited extract is the beginning of the file, not a random sample.", ""]
    delimiter = {"\t": "tab", ",": "comma", ";": "semicolon", "|": "pipe"}.get(
        profile.get("delimiter"), _escape(profile.get("delimiter", "unknown")))
    lines += [f"Inspected delimiter: {delimiter}. Effective inspected-row budget: {profile['limits']['rows']:,}.", ""]
    if profile["warnings"]:
        lines += ["## Data checks", ""] + ["- " + _escape(warning) for warning in profile["warnings"]] + [""]
    return "\n".join(lines)
