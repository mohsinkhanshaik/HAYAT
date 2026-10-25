"""Historical mirrors must be pinned, attributable and installed without losing data."""
import hashlib
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.jobs.sponsorship import history_for, research, research_lines
from scripts import setup_sponsor_history as setup


def extract(rows="Acme & Co\t2024\t1,200\t2\t3\t4\n\t2024\t0\t0\t0\t0\n"):
    header = "Employer (Petitioner) Name\tFiscal Year\tInitial Approval\tInitial Denial\tContinuing Approval\tContinuing Denial\n"
    return (header + rows).encode("utf-16")


def test_utf16_tab_mirror_preserves_counts_and_reports_unnamed_rows():
    normalized, counts = setup.normalize_snapshot(extract())
    assert "Acme & Co,2024,1200,2,3,4" in normalized.decode()
    assert counts == {"source_rows": 2, "named_rows": 1, "omitted_unidentified_rows": 1, "fiscal_years": [2024]}


@pytest.mark.parametrize("raw,reason", [
    (b"", "no employer records"),
    (b"Company,Year\nAcme,2024", "missing column"),
    (extract("Acme\t2025\t1\t2\t3\t4\n"), "unexpected fiscal year"),
    (extract("Acme\t2024\tD\t2\t3\t4\n"), "invalid count"),
    (extract("\t2024\t1\t2\t3\t4\n"), "no named employer"),
])
def test_invalid_extracts_are_rejected(raw, reason):
    with pytest.raises(ValueError, match=reason):
        setup.normalize_snapshot(raw)


class Fetcher:
    def __init__(self, raw):
        self.raw, self.calls = raw, []

    def fetch(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return SimpleNamespace(content=self.raw)


def test_changed_remote_bytes_cannot_replace_local_history(tmp_path):
    dest = tmp_path / "history.json"
    dest.write_bytes(b"previous history")
    with pytest.raises(ValueError, match="pinned SHA-256"):
        setup.install(dest, fetcher=Fetcher(b"untrusted bytes"), replace=True)
    assert dest.read_bytes() == b"previous history"
    assert not (tmp_path / "sources").exists()


def test_existing_history_is_preserved_without_even_fetching(tmp_path):
    dest = tmp_path / "history.json"
    dest.write_bytes(b"previous history")
    fetcher = Fetcher(extract())
    with pytest.raises(FileExistsError, match="preserved"):
        setup.install(dest, fetcher=fetcher)
    assert not fetcher.calls
    assert dest.read_bytes() == b"previous history"


def test_installed_history_is_queryable_and_shows_historical_mirror_evidence(tmp_path, monkeypatch):
    raw = extract()
    monkeypatch.setattr(setup, "SHA256", hashlib.sha256(raw).hexdigest())
    dest = tmp_path / "history.json"
    fetcher = Fetcher(raw)
    installed = setup.install(dest, fetcher=fetcher)
    found = history_for(dest, "ACME AND CO")
    assert found["record"]["years"]["2024"]["initial_approval"] == 1200
    assert found["source"]["verified_against_official_source"] is False
    assert found["source"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert "not independently verified" in found["provenance"]
    rendered = "\n".join(research_lines(research(None, {"work_status": "stem_opt"}, dest, "Acme & Co")))
    assert "FY2024: initial approvals 1200" in rendered
    assert setup.URL in rendered and "unnamed petitioners omitted: 1" in rendered
    assert "current sponsorship are unverified" in rendered
    assert installed["employers"] == 1
    assert fetcher.calls[0][1]["max_bytes"] == 50_000_000
    assert (tmp_path / "sources/uscis-fy2024-mirror.csv").read_bytes() == raw
    dest.write_text(json.dumps({"previous": "retained in backup"}), encoding="utf-8")
    replacement = setup.install(dest, fetcher=fetcher, replace=True)
    assert json.loads(Path(replacement["previous_backup"]).read_text()) == {"previous": "retained in backup"}
