"""Bounded live completion, vision, structured-output, tool and thinking checks."""
from __future__ import annotations

import base64
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from PIL import Image, ImageDraw, ImageFont
    from app.config import load_yaml
    from scripts.audit_local_models import request
    config = load_yaml("config/models.yaml")
    endpoint = config["providers"]["ollama"]["base_url"]
    model = config["default_model"]["model"]
    vision = config["vision_model"]["model"]
    advisor = config["escalation_model"]["model"]
    report = {"endpoint": endpoint, "checks": [], "local_only": True}
    image = Image.new("RGB", (600, 240), "white")
    ImageDraw.Draw(image).text((80, 50), "8327", fill="black", font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 120))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image_data = base64.b64encode(buffer.getvalue()).decode()
    tests = [
        ("completion", model, {"messages": [{"role": "user", "content": "Reply with exactly LOCAL_MODEL_OK"}]},
         lambda d: d["message"]["content"].strip() == "LOCAL_MODEL_OK"),
        ("structured_json", model, {"messages": [{"role": "user", "content": "What is 6 times 7? Return a JSON object with an integer result field."}],
          "format": {"type": "object", "properties": {"result": {"type": "integer"}}, "required": ["result"], "additionalProperties": False}},
         lambda d: json.loads(d["message"]["content"]) == {"result": 42}),
        ("tool_call_only", model, {"messages": [{"role": "user", "content": "Use the calculator tool to calculate 81 * 19. Do not calculate it yourself."}],
          "tools": [{"type": "function", "function": {"name": "calculator", "description": "Evaluate a numeric expression",
              "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}}]},
         lambda d: any(c.get("function", {}).get("name") == "calculator" and
                       c["function"].get("arguments", {}).get("expression", "").replace(" ", "") == "81*19"
                       for c in d["message"].get("tool_calls", []))),
        ("vision", vision, {"messages": [{"role": "user", "content": "Read the four-digit number in this image. Return only the number.", "images": [image_data]}]},
         lambda d: d["message"]["content"].strip() == "8327"),
        ("deep_thinking", advisor, {"think": True, "messages": [{"role": "user", "content": "Compute 11 * 12. Return only the number in your final answer."}]},
         lambda d: d["message"]["content"].strip() == "132" and bool(d["message"].get("thinking"))),
    ]
    for name, selected, payload, check in tests:
        started = time.monotonic()
        item = {"name": name, "model": selected}
        try:
            data = request(endpoint, "/api/chat", {"model": selected, "stream": False, "think": False,
                "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 512}, "keep_alive": 0, **payload})
            item.update(passed=check(data), response=data.get("message"), seconds=time.monotonic()-started)
        except Exception as exc:
            item.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        report["checks"].append(item)
        (ROOT / "data/models/hayat-deep-check-20261009.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"name": name, "passed": item["passed"]}), flush=True)
    return 0 if all(c["passed"] for c in report["checks"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
