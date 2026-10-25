from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.memory.database import MemoryDatabase
from app.observability.tool_benchmark import ToolBenchmark
from app.research.documents import PublicDocumentLearner
from app.research.fetcher import FetchedResource
from app.research.knowledge import KnowledgeStore
from app.research.manager import ResearchManager
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from app.tools.understanding import ToolUnderstanding


class Fetcher:
    def __init__(self, content: bytes, content_type: str = "text/plain") -> None:
        self.content = content
        self.content_type = content_type

    def fetch(self, url: str, max_bytes: int):
        if len(self.content) > max_bytes:
            raise ValueError("too large")
        import hashlib
        return FetchedResource(
            url, url, 200, {"content-type": self.content_type}, self.content, 2,
            datetime.now(UTC).isoformat(), hashlib.sha256(self.content).hexdigest(),
        )

    def close(self) -> None:
        pass


class Scanner:
    def __init__(self, clean: bool = True) -> None:
        self.clean = clean
        self.scanned: list[str] = []

    def status(self):
        return {"available": True, "provider": "test_scanner", "fail_closed": True}

    def scan(self, path):
        self.scanned.append(str(path))
        return self.status() | {
            "clean": self.clean, "status": "clean" if self.clean else "blocked",
            "reason": "No threat detected." if self.clean else "test detection",
        }


def _learner(tmp_path: Path, content: bytes, scanner: Scanner | None = None):
    knowledge = KnowledgeStore(tmp_path / "knowledge.db")
    research = ResearchManager({"database": str(tmp_path / "knowledge.db"), "respect_robots_txt": False},
                               knowledge=knowledge)
    research.fetcher.close()
    research.fetcher = Fetcher(content)
    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    learner = PublicDocumentLearner(
        research, memory, root=tmp_path / "sources", scanner=scanner or Scanner(),
    )
    return learner, research, memory


def test_public_document_is_quarantined_deeply_indexed_and_deduplicated(tmp_path):
    content = (
        b"Widget version 2 supports safe mode for local deployments. "
        b"The published benchmark measured 95 percent task accuracy. " * 80
    )
    learner, research, memory = _learner(tmp_path, content)
    try:
        first = learner.learn("https://docs.example.test/widget.txt", "Widget benchmark", deep=True)
        second = learner.learn("https://docs.example.test/widget.txt", "Widget benchmark", deep=True)
    finally:
        research.close()

    assert Path(first["path"]).is_file()
    assert first["quarantined"] is True and first["executed"] is False
    assert first["antimalware_scan"]["clean"] is True
    assert first["deep"] is True and first["extracted_characters"] == len(content)
    assert first["chunks"] >= 2 and first["memory_chunks_added"] == first["chunks"]
    assert first["memory_index_added"] == 1
    assert second["duplicate_content"] is True and second["memory_chunks_added"] == 0
    assert second["memory_index_added"] == 0
    assert memory.stats()["memories"]["semantic"] == first["chunks"] + 1
    recalled = memory.retrieve("Widget benchmark", "semantic", strategy="lexical")
    assert any(item["metadata"]["kind"] == "web_document_index" for item in recalled)
    assert learner.status()["documents"] == 1


def test_public_document_learning_rejects_executable_binary(tmp_path):
    learner, research, _ = _learner(tmp_path, b"MZ" + b"\0" * 200)
    try:
        with pytest.raises(ValueError, match="Executable binary"):
            learner.learn("https://downloads.example.test/payload.exe", "inspect")
    finally:
        research.close()


def test_document_claim_unwrap_joins_physical_lines_but_keeps_bullets():
    text = (
        "A wrapped requirement MUST NOT be\nchanged during serialization.\n\n"
        "* First complete rule.\n* Second complete rule."
    )
    assert PublicDocumentLearner._unwrap_for_claims(text).splitlines() == [
        "A wrapped requirement MUST NOT be changed during serialization.",
        "* First complete rule.",
        "* Second complete rule.",
    ]


def test_public_document_learning_blocks_scanner_detection_before_extraction(tmp_path):
    scanner = Scanner(clean=False)
    learner, research, memory = _learner(
        tmp_path, b"A normal-looking document that the antimalware layer rejects.", scanner,
    )
    try:
        with pytest.raises(PermissionError, match="blocked by the antimalware gate"):
            learner.learn("https://docs.example.test/rejected.txt", "Rejected document")
    finally:
        research.close()

    assert scanner.scanned
    assert list((tmp_path / "sources").glob("*.blocked"))
    assert memory.stats()["memories"] == {}


def test_every_tool_gets_static_benchmark_but_only_passive_probe_executes(tmp_path):
    permissions = PermissionManager({
        "default": "ASK", "permissions": {"filesystem_read": "ALLOW", "filesystem_write_project": "ALLOW"},
    }, project_root=tmp_path)
    registry = ToolRegistry(permissions)
    calls = {"passive": 0, "mutating": 0}
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    registry.register("system_status", "Passive status", empty, "low", "filesystem_read",
                      lambda: calls.__setitem__("passive", calls["passive"] + 1) or {"ok": True})
    registry.register("write_file", "Mutating write", empty, "medium", "filesystem_write_project",
                      lambda: calls.__setitem__("mutating", calls["mutating"] + 1) or {"ok": True})
    understanding = ToolUnderstanding(tmp_path / "tools.db")
    registry.understanding = understanding
    benchmark = ToolBenchmark(registry, understanding, artifact=tmp_path / "benchmark.json")

    report = benchmark.run(deep=True)

    assert report["summary"]["registered"] == 2
    assert report["summary"]["static_passed"] == 2
    assert report["summary"]["live_probed"] == 1
    assert calls == {"passive": 1, "mutating": 0}
    assert all(row["static"]["passed"] for row in report["tools"])
    assert (tmp_path / "benchmark.json").is_file()


def test_bullet_points_start_new_items_when_wrapped_lines_are_joined():
    from app.research.documents import PublicDocumentLearner

    text = "• First point\n  continues here\n• Second point\n- Third\n1) Fourth"
    assert PublicDocumentLearner._unwrap_for_claims(text).splitlines() == [
        "• First point continues here", "• Second point", "- Third", "1) Fourth",
    ]
