from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import unquote, urlsplit

from app.attachments import AttachmentReader, DOCUMENT_KINDS, IMAGE_KINDS, TEXT_KINDS
from app.config import PROJECT_ROOT
from app.research.citations import citation_list
from app.research.models import ClaimStatus, ResearchMetrics, ResearchResult, SourceDocument
from app.research.injection import detect_prompt_injection
from app.research.ranking import score_for_query
from app.security.scanner import DefenderFileScanner


MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 1_000_000
CHUNK_CHARACTERS = 4_000
CHUNK_OVERLAP = 300
MAX_MEMORY_CHUNKS = 64

MIME_SUFFIXES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel.sheet.macroenabled.12": ".xlsm",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/json": ".json", "application/xml": ".xml",
    "text/csv": ".csv", "text/markdown": ".md", "text/plain": ".txt",
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
    "image/gif": ".gif", "image/tiff": ".tiff", "image/bmp": ".bmp",
}
SUPPORTED_SUFFIXES = TEXT_KINDS | IMAGE_KINDS | DOCUMENT_KINDS


class PublicDocumentLearner:
    """Download inert public documents, extract them deeply, and index provenance.

    Network data never becomes executable and never inherits instructions. Raw
    bytes stay quarantined; derived chunks are marked untrusted external data.
    """

    def __init__(self, research: Any, memory: Any, storage: Any = None,
                 *, root: Path | None = None, scanner: Any = None) -> None:
        self.research = research
        self.memory = memory
        self.storage = storage
        self.scanner = scanner or DefenderFileScanner()
        self.root = root or PROJECT_ROOT / "data/knowledge/sources"
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry = self.root / "web_documents.db"
        self._lock = threading.RLock()
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS web_documents(
                    sha256 TEXT PRIMARY KEY, url TEXT NOT NULL, final_url TEXT NOT NULL,
                    path TEXT NOT NULL, content_type TEXT NOT NULL, bytes INTEGER NOT NULL,
                    extracted_characters INTEGER NOT NULL, chunks INTEGER NOT NULL,
                    knowledge_id INTEGER, learned_at TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_web_documents_url ON web_documents(final_url)")

    def learn(self, url: str, topic: str = "", filename: str = "",
              max_bytes: int = MAX_DOCUMENT_BYTES, deep: bool = True,
              remember: bool = True) -> dict[str, Any]:
        started = time.perf_counter()
        if urlsplit(url).scheme.lower() != "https":
            raise ValueError("Public document learning requires HTTPS.")
        bounded = max(1, min(int(max_bytes), MAX_DOCUMENT_BYTES))
        resource = self.research.fetcher.fetch(url, bounded)
        content_type = str(resource.headers.get("content-type", "application/octet-stream")).split(";", 1)[0].lower()
        suffix = self._validated_suffix(resource.content, content_type, resource.final_url, filename)
        name = self._safe_name(filename or self._header_name(resource.headers.get("content-disposition", ""))
                               or Path(unquote(urlsplit(resource.final_url).path)).name
                               or f"document{suffix}")
        if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
            name += suffix
        if self.storage is not None:
            self.storage.ensure_capacity(len(resource.content))
        target = self.root / f"{resource.sha256[:12]}-{name}"
        target = target.resolve()
        target.relative_to(self.root.resolve())
        duplicate = self._existing(resource.sha256)
        scan_result: dict[str, Any]
        if duplicate and Path(duplicate["path"]).is_file():
            target = Path(duplicate["path"]).resolve()
            target.relative_to(self.root.resolve())
            scan_result = self.scan_file(target)
            if not scan_result.get("clean"):
                raise PermissionError(
                    f"The retained download failed its antimalware gate: {scan_result.get('reason')}"
                )
        else:
            partial = target.with_suffix(target.suffix + ".partial")
            try:
                partial.write_bytes(resource.content)
                if hashlib.sha256(partial.read_bytes()).hexdigest() != resource.sha256:
                    raise OSError("Quarantine hash verification failed.")
                scan_result = self.scan_file(partial)
                if not scan_result.get("clean"):
                    blocked = target.with_suffix(target.suffix + ".blocked")
                    partial.replace(blocked)
                    raise PermissionError(
                        f"Download retained as {blocked.name} but blocked by the antimalware gate: "
                        f"{scan_result.get('reason')}"
                    )
                partial.replace(target)
            except Exception:
                partial.unlink(missing_ok=True)
                raise

        reader = AttachmentReader(SimpleNamespace(
            memory=None, model=getattr(self.research, "model", None), vision=None,
        ))
        attachment = reader.read(
            target, max_read_bytes=bounded,
            max_pdf_pages=300 if deep else 60,
        )
        if not attachment.ok:
            raise ValueError(f"Downloaded file could not be understood safely: {attachment.error}")
        text = attachment.text[:MAX_EXTRACTED_CHARACTERS]
        query = " ".join(str(topic or "").split()).strip() or f"Contents of {target.name}"
        chunks = self._chunks(text, MAX_MEMORY_CHUNKS if deep else 12)
        memories_added, memory_index_added = self._remember_chunks(
            chunks, resource.sha256, resource.final_url, target, content_type,
            query, attachment.summary or " ".join(text.split())[:1200],
        ) if remember else (0, 0)

        quality, quality_reasons, source_type, primary = score_for_query(
            resource.final_url, target.name, query,
        )
        source = SourceDocument(
            resource.requested_url, resource.final_url, target.name,
            urlsplit(resource.final_url).hostname or "public-document", text,
            resource.retrieved_at, resource.status_code, content_type,
            resource.sha256, len(resource.content),
            metadata={
                "provenance": "downloaded_public_document", "quarantined_path": str(target),
                "prompt_injection_indicators": detect_prompt_injection(text), "executed": False,
                "extracted_characters": len(text), "chunks": len(chunks),
                "antimalware_scan": {key: scan_result.get(key) for key in (
                    "provider", "status", "clean", "returncode", "exclusions_ignored",
                )},
            },
            quality=quality,
            quality_reasons=[*quality_reasons, "direct public document with retained hash and URL"],
            trust="untrusted_external_data", source_type=source_type, primary=primary,
        )
        source = self.research.independence.annotate([source])[0]
        claims = []
        for chunk in chunks[:24 if deep else 8]:
            claims.extend(self.research.claim_extractor.extract(
                query, source, self._unwrap_for_claims(chunk), max_claims=4,
                strict_complete=True,
            ))
        claims = self.research._deduplicate_claims(claims)
        claims, confidence, validation = self.research.verifier.verify_claims(claims, [source])
        metrics = ResearchMetrics(
            fetch_ms=resource.elapsed_ms, pages_attempted=1, pages_retrieved=1,
            bytes_downloaded=len(resource.content), validation_ms=int(validation["validation_ms"]),
            claims_supported=sum(c.status in {ClaimStatus.SUPPORTED, ClaimStatus.PARTIALLY_SUPPORTED} for c in claims),
            claims_disputed=sum(c.status == ClaimStatus.DISPUTED for c in claims),
            claims_unsupported=sum(c.status == ClaimStatus.UNVERIFIED for c in claims),
        )
        synthesis_started = time.perf_counter()
        answer = self.research._synthesize(query, [source], claims, confidence, metrics)
        if not answer.strip():
            answer = attachment.summary or " ".join(text.split())[:1200]
        metrics.synthesis_ms = int((time.perf_counter() - synthesis_started) * 1000)
        usable = [claim for claim in claims if claim.status != ClaimStatus.UNVERIFIED]
        complete = bool(text.strip() and usable)
        run_id = f"document-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        result = ResearchResult(
            run_id, query, answer, [source], claims, citation_list([source]), confidence,
            complete, "downloaded_document_learned" if complete else "document_extracted_needs_corroboration",
            metrics, [] if complete else ["Single-document claims may need independent corroboration."],
            [], "DOCUMENT_DEEP" if deep else "DOCUMENT", {
                "source_url": resource.final_url, "sha256": resource.sha256,
                "path": str(target), "executed": False, "deep": bool(deep),
            },
        )
        knowledge_id = self.research.knowledge.store_result(result, result.mode) if remember else None
        metrics.knowledge_ms = int((time.perf_counter() - synthesis_started) * 1000) - metrics.synthesis_ms
        metrics.total_ms = int((time.perf_counter() - started) * 1000)
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO web_documents VALUES(?,?,?,?,?,?,?,?,?,?)",
                (resource.sha256, resource.requested_url, resource.final_url, str(target),
                 content_type, len(resource.content), len(text), len(chunks), knowledge_id,
                 datetime.now(UTC).isoformat()),
            )
        return {
            "url": resource.final_url, "path": str(target), "bytes": len(resource.content),
            "sha256": resource.sha256, "content_type": content_type,
            "quarantined": True, "executed": False, "deep": bool(deep),
            "antimalware_scan": scan_result,
            "extracted_characters": len(text), "chunks": len(chunks),
            "memory_chunks_added": memories_added,
            "memory_index_added": memory_index_added,
            "duplicate_content": bool(duplicate),
            "claims": len(claims), "supported_claims": metrics.claims_supported,
            "confidence": confidence, "knowledge_id": knowledge_id,
            "complete": complete, "answer": answer, "elapsed_ms": metrics.total_ms,
            "safety": "Public URL validation, robots policy, size bound, inert-type validation, hash verification, Defender custom scan, no execution.",
        }

    def status(self) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(bytes),0),COALESCE(SUM(extracted_characters),0),COALESCE(SUM(chunks),0) FROM web_documents"
            ).fetchone()
        return {
            "documents": int(row[0]), "bytes": int(row[1]),
            "extracted_characters": int(row[2]), "chunks": int(row[3]),
            "maximum_document_bytes": MAX_DOCUMENT_BYTES,
            "supported": sorted(SUPPORTED_SUFFIXES), "automatic_execution": False,
            "antimalware": self.scanner.status(),
        }

    def scan_file(self, path: str | Path) -> dict[str, Any]:
        """Scan one quarantined file; callers must require ``clean`` to use it."""
        return dict(self.scanner.scan(path))

    def _remember_chunks(self, chunks: list[str], digest: str, url: str,
                         path: Path, content_type: str, topic: str,
                         summary: str) -> tuple[int, int]:
        with self.memory.connect() as db:
            existing = {
                str(row[0]): int(row[1])
                for row in db.execute(
                    "SELECT json_extract(metadata_json,'$.kind'),COUNT(*) FROM memories "
                    "WHERE source='untrusted_external_content' "
                    "AND json_extract(metadata_json,'$.sha256')=? GROUP BY 1", (digest,),
                ).fetchall()
            }
        aliases = self._search_aliases(path, url)
        index_added = 0
        if not existing.get("web_document_index"):
            self.memory.store_memory(
                "semantic",
                f"Downloaded public document index\nDocument: {path.name}\n"
                f"Topic: {topic}\nSearch aliases: {aliases}\nSource: {url}\n"
                f"Summary: {summary[:1600]}",
                .76, "untrusted_external_content",
                metadata={
                    "kind": "web_document_index", "url": url, "path": str(path),
                    "sha256": digest, "content_type": content_type,
                    "verified": False, "neural_index": "deferred",
                },
                defer_neural=True,
            )
            index_added = 1
        if existing.get("web_document_chunk"):
            return 0, index_added
        added = 0
        for index, chunk in enumerate(chunks):
            self.memory.store_memory(
                "semantic", f"Downloaded public document: {path.name}\nTopic: {topic}\n"
                f"Search aliases: {aliases}\nSource: {url}\n"
                f"Chunk {index + 1}/{len(chunks)}\n{chunk}",
                .72, "untrusted_external_content",
                metadata={
                    "kind": "web_document_chunk", "url": url, "path": str(path),
                    "sha256": digest, "content_type": content_type,
                    "chunk": index + 1, "chunks": len(chunks), "verified": False,
                    "neural_index": "deferred",
                },
                defer_neural=True,
            )
            added += 1
        return added, index_added

    @staticmethod
    def _search_aliases(path: Path, url: str) -> str:
        """Make compact filenames searchable with naturally spaced identifiers."""
        public_name = Path(unquote(urlsplit(url).path)).stem
        value = public_name or path.stem
        value = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", value)
        value = re.sub(r"[^A-Za-z0-9]+", " ", value)
        return " ".join(dict.fromkeys(value.split()))[:500]

    @staticmethod
    def _chunks(text: str, maximum: int) -> list[str]:
        body = str(text or "").strip()
        if not body:
            return []
        result: list[str] = []
        start = 0
        while start < len(body) and len(result) < maximum:
            end = min(len(body), start + CHUNK_CHARACTERS)
            if end < len(body):
                boundary = max(body.rfind("\n", start + 1000, end), body.rfind(". ", start + 1000, end))
                if boundary > start:
                    end = boundary + 1
            result.append(body[start:end].strip())
            if end >= len(body):
                break
            start = max(start + 1, end - CHUNK_OVERLAP)
        return [chunk for chunk in result if chunk]

    @staticmethod
    def _unwrap_for_claims(text: str) -> str:
        """Join physical line wraps while preserving paragraph/bullet boundaries."""
        output: list[str] = []
        current = ""
        for raw in str(text or "").splitlines():
            line = " ".join(raw.split()).strip()
            if not line:
                if current:
                    output.append(current)
                    current = ""
                continue
            new_item = bool(re.match(r"^(?:[-*•]|\d+[.)])\s+", line))
            if new_item and current:
                output.append(current)
                current = line
            elif current:
                current += " " + line
            else:
                current = line
        if current:
            output.append(current)
        return "\n".join(output)

    @staticmethod
    def _validated_suffix(content: bytes, content_type: str, url: str, filename: str) -> str:
        if content.startswith(b"MZ") or content.startswith(b"\x7fELF"):
            raise ValueError("Executable binary downloads cannot enter the document-learning pipeline.")
        requested = Path(filename or unquote(urlsplit(url).path)).suffix.lower()
        if content.startswith(b"%PDF-"):
            return ".pdf"
        if content.startswith(b"PK\x03\x04"):
            try:
                with zipfile.ZipFile(__import__("io").BytesIO(content)) as archive:
                    entries = archive.infolist()
                    if len(entries) > 10_000 or sum(item.file_size for item in entries) > 256 * 1024 * 1024:
                        raise ValueError("Document container expands beyond the safe extraction budget.")
                    names = {item.filename for item in entries}
                if "[Content_Types].xml" not in names:
                    raise ValueError("Archives are not extracted by web learning.")
                if any(name.startswith("word/") for name in names): return ".docx"
                if any(name.startswith("xl/") for name in names): return ".xlsx" if requested != ".xlsm" else ".xlsm"
                if any(name.startswith("ppt/") for name in names): return ".pptx"
            except zipfile.BadZipFile as exc:
                raise ValueError("Invalid document container.") from exc
            raise ValueError("Only Word, Excel, and PowerPoint document containers are accepted.")
        mapped = MIME_SUFFIXES.get(content_type)
        if mapped:
            if mapped in TEXT_KINDS and requested in TEXT_KINDS:
                return requested
            if mapped in IMAGE_KINDS:
                try:
                    from PIL import Image
                    with Image.open(__import__("io").BytesIO(content)) as image:
                        image.verify()
                except Exception as exc:
                    raise ValueError("Invalid image document.") from exc
                return requested if requested in IMAGE_KINDS else mapped
            if mapped == ".xlsx" and requested == ".xlsm":
                return requested
            return mapped
        if content_type.startswith("text/") or requested in TEXT_KINDS:
            if content.count(b"\x00") > max(2, len(content) // 100):
                raise ValueError("Binary content was labelled as text.")
            return requested if requested in TEXT_KINDS else ".txt"
        raise ValueError(f"Unsupported inert document type: {content_type or 'unknown'}")

    @staticmethod
    def _safe_name(value: str) -> str:
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(value).name).strip(".-")
        return (name or "document")[:180]

    @staticmethod
    def _header_name(value: str) -> str:
        match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", str(value), re.I)
        return unquote(match.group(1).strip()) if match else ""

    def _existing(self, digest: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM web_documents WHERE sha256=?", (digest,)).fetchone()
        return dict(row) if row else None

    @contextmanager
    def _db(self):
        with self._lock:
            db = sqlite3.connect(self.registry, timeout=15)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()
