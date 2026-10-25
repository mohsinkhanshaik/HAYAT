"""Open HAYAT by itself from this folder (what HAYAT.lnk runs). See app/launcher.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.launcher import main  # noqa: E402

raise SystemExit(main())
