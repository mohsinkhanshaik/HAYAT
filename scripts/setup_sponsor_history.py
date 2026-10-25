"""Install a pinned public mirror of historical FY2024 employer decisions.

This is a reproducible research dataset, not a current USCIS verification.
No source code from the mirror is downloaded or executed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

COMMIT = "ec36bd986f6684eae6325a73a3097940aad8ae49"
URL = f"https://raw.githubusercontent.com/karthiks233/H1B/{COMMIT}/h1b_datahubexport-2024.csv"
SHA256 = "17bbdbf3abaa98defc8817626ba8821a69cb5d4942fe1d2229a24deb3db7c17e"
PROVENANCE = ("Public GitHub mirror of an attributed USCIS FY2024 extract; not independently verified against USCIS. "
              "Historical research leads only; completeness and current sponsorship are unverified. "
              "Employer names aggregate reported rows and do not disambiguate tax IDs or worksites.")


def normalize_snapshot(raw: bytes) -> tuple[bytes, dict]:
    text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    if not text.strip():
        raise ValueError("The mirror contains no employer records.")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t" if "\t" in text.splitlines()[0] else ",")
    key = lambda value: " ".join(re.findall(r"[a-z0-9]+", value.lower()))
    headers = {key(name): name for name in reader.fieldnames or []}
    aliases = {"Employer": ("employer", "employer petitioner name"), "Fiscal Year": ("fiscal year",),
               **{name: (name.lower(),) for name in ("Initial Approval", "Initial Denial", "Continuing Approval", "Continuing Denial")}}
    selected = {}
    for name, choices in aliases.items():
        selected[name] = next((headers[item] for item in choices if item in headers), None)
        if selected[name] is None:
            raise ValueError("Historical mirror is missing column: " + name)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(selected))
    writer.writeheader()
    rows = omitted = 0
    for source in reader:
        row = {name: (source.get(header) or "").strip() for name, header in selected.items()}
        if row["Fiscal Year"] != "2024":
            raise ValueError("The pinned FY2024 mirror contains an unexpected fiscal year.")
        if not row["Employer"]:
            omitted += 1  # An unidentified petitioner cannot be an employer research lead.
            continue
        for name in list(selected)[2:]:
            row[name] = row[name].replace(",", "")
            if not re.fullmatch(r"\d{1,10}", row[name]):
                raise ValueError("The mirror contains a missing, suppressed or invalid count; no history was installed.")
        writer.writerow(row)
        rows += 1
    if not rows:
        raise ValueError("The mirror contains no named employer decisions.")
    return output.getvalue().encode("utf-8"), {"source_rows": rows + omitted, "named_rows": rows,
                                                "omitted_unidentified_rows": omitted, "fiscal_years": [2024]}


def install(destination: Path, *, fetcher, replace: bool = False) -> dict:
    from app.jobs.sponsorship import import_history
    from app.tools.file_editing import atomic_save
    destination = destination.resolve()
    if destination.exists() and not replace:
        raise FileExistsError("Existing employer history preserved. Use --replace explicitly to replace it with a backed-up snapshot.")
    resource = fetcher.fetch(URL, max_bytes=50_000_000, accept=("text/csv", "text/plain", "application/octet-stream"))
    digest = hashlib.sha256(resource.content).hexdigest()
    if digest != SHA256:
        raise ValueError("The public mirror does not match the pinned SHA-256; no history was installed.")
    normalized, counts = normalize_snapshot(resource.content)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hayat-sponsor-stage-", dir=destination.parent) as temporary:
        folder = Path(temporary)
        csv_file, staged = folder / "uscis-fy2024-normalized.csv", folder / "history.json"
        csv_file.write_bytes(normalized)
        summary = import_history(csv_file, staged)
        value = json.loads(staged.read_text(encoding="utf-8"))
        value["provenance"] = PROVENANCE
        value["source"] = {"url": URL, "commit": COMMIT, "sha256": digest, "normalized_sha256": summary["sha256"],
                           "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"), "kind": "public mirror",
                           "official_source": "https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub",
                           "verified_against_official_source": False, **counts}
        archive = destination.parent / "sources" / "uscis-fy2024-mirror.csv"
        atomic_save(archive, resource.content, backup=False)
        saved = atomic_save(destination, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))
    return {**summary, **counts, "path": str(destination), "source": value["source"], "provenance": PROVENANCE,
            "previous_backup": saved["backup"], "archive": str(archive)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    from app.research.fetcher import PublicFetcher
    fetcher = PublicFetcher("HAYAT-public-employer-history/1.0", timeout=30)
    try:
        print(json.dumps(install(ROOT / "data/jobs/sponsor-history.json", fetcher=fetcher, replace=args.replace), indent=2))
    finally:
        fetcher.close()


if __name__ == "__main__":
    main()
