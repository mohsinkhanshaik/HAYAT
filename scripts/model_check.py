from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.models.base import Message
from app.models.router import ModelRouter
from app.portable import activate


def main() -> int:
    activate()
    provider = ModelRouter().get()
    if provider is None:
        print("No local or configured cloud model provider is reachable.")
        return 1
    response = provider.generate(
        [Message("system", "Reply briefly and do not call tools."), Message("user", "Reply with exactly: LOCAL_MODEL_OK")],
        temperature=0.0,
    )
    print(f"provider={response.provider}")
    print(f"model={response.model}")
    print(f"content={response.content.strip()[:500]}")
    return 0 if response.content.strip() == "LOCAL_MODEL_OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
