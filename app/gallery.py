"""Permission-gated previews shared by the gallery and image tools."""
import base64
import io
import json


def gallery_records(images, memory, query=""):
    rows = [{**row, "origin": "Generated"} for row in images.history(200)]
    with memory.connect() as db:
        records = db.execute("SELECT metadata_json FROM memories WHERE source='attachment' ORDER BY id DESC LIMIT 1000").fetchall()
    for record in records:
        try:
            metadata = json.loads(record[0])
            if isinstance(metadata, dict) and metadata.get("file_kind") == "image":
                rows.append({"path": metadata["path"], "prompt": metadata.get("name", ""), "origin": "Attached"})
        except (ValueError, KeyError, TypeError):
            continue
    unique = {}
    for row in reversed(rows):
        path = str(row.get("path") or "")
        if path and query.casefold() in (path + " " + str(row.get("prompt", ""))).casefold():
            unique.setdefault(path.casefold(), row)
    return list(unique.values())


def register_gallery_tools(registry, permissions, scanner, images, memory):
    def image_preview(path):
        from PIL import Image, ImageOps
        target = permissions.validate_project_path(path)
        if target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            raise ValueError("Unsupported preview format")
        if target.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Preview file exceeds 32 MiB")
        if scanner is None or not scanner.scan(target).get("clean"):
            raise ValueError("Preview blocked by file admission scanner")
        with Image.open(target) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError("Preview exceeds 40 million pixels")
            source.draft("RGB", (640, 480))
            preview = ImageOps.exif_transpose(source).convert("RGB")
            preview.thumbnail((640, 480))
            stream = io.BytesIO()
            preview.save(stream, format="PNG")
        return {"path": str(target), "png": base64.b64encode(stream.getvalue()).decode("ascii")}

    def missing(path):
        """A record whose file is gone. Anything the permission check refuses
        is left for image_preview to report, so a refusal is never hidden as
        a missing file."""
        try:
            return not permissions.validate_project_path(path).exists()
        except Exception:
            return False

    def image_gallery(query="", page=1):
        rows = gallery_records(images, memory, query[:500])
        offset = (max(1, min(int(page), 100)) - 1) * 12
        # Records outlive the files they name. Marking a deleted file here lets
        # the gallery skip previewing it, instead of every such card being
        # logged as an image_preview failure and the tool looking unreliable.
        page_rows = [{**row, "missing": missing(row["path"])} for row in rows[offset:offset + 12]]
        return {"images": page_rows, "has_more": len(rows) > offset + 12}

    registry.register("image_preview", "Create a bounded local preview of an admitted image file.",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        "low", "filesystem_read", image_preview)
    registry.register("image_gallery", "Browse generated and previously attached images, with search and pagination.",
        {"type": "object", "properties": {"query": {"type": "string", "maxLength": 500},
         "page": {"type": "integer", "minimum": 1, "maximum": 100}}, "additionalProperties": False},
        "low", "filesystem_read", image_gallery)
