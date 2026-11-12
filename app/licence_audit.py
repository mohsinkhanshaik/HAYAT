"""Licence audit: what every dependency, model and bundled asset is licensed under (2026-11-08).

Owner's standing rule: HAYAT is built only from genuinely open-source software (OSI-approved licences),
at no cost, with no mandatory service, account or telemetry. "Free to use" is not the same thing.

This module reads each package's own metadata (the licence it declares, not a guess), classifies it,
and lists what a requirement is for (the comment above it in the requirements file). Run it with
`python -m app.licence_audit` to print the table, or `--write` to refresh docs/licence-audit.md.
Websites HAYAT reads (job boards, search engines, public feeds) are information sources, not
software HAYAT ships; they are checked separately (robots.txt and terms, per source).
"""
from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ("requirements.txt", "requirements-perception.txt", "requirements-image-tools.txt",
                "requirements-training.txt", "requirements-voice-cloning.txt")

#: Declared licence text (lower-cased, punctuation squeezed) to an SPDX-style name. All are OSI-approved.
OSI = {
    "mit": "MIT", "mit license": "MIT", "mit-cmu": "MIT-CMU", "bsd": "BSD", "bsd license": "BSD",
    "bsd-3-clause": "BSD-3-Clause", "bsd 3-clause": "BSD-3-Clause", "bsd 3-clause license": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause", "2-clause bsd license": "BSD-2-Clause", "0bsd": "0BSD", "bsl-1.0": "BSL-1.0",
    "apache 2.0 license": "Apache-2.0", "apache license version 2.0": "Apache-2.0", "isc": "ISC", "isc license": "ISC",
    "apache-2.0": "Apache-2.0", "apache 2.0": "Apache-2.0", "apache license 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0", "apache license, version 2.0": "Apache-2.0",
    "psf": "PSF-2.0", "psfl": "PSF-2.0", "python software foundation license": "PSF-2.0", "psf-2.0": "PSF-2.0",
    "lgplv3": "LGPL-3.0", "lgpl-3.0": "LGPL-3.0", "lgpl-2.1+": "LGPL-2.1-or-later", "lgpl-2.1-or-later": "LGPL-2.1-or-later",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0",
    "gnu lesser general public license v2 or later (lgplv2+)": "LGPL-2.1-or-later",
    "gpl-3.0-or-later": "GPL-3.0-or-later", "gpl-3.0": "GPL-3.0", "gplv3": "GPL-3.0",
    "gnu general public license v3 (gplv3)": "GPL-3.0", "agpl-3.0": "AGPL-3.0", "mpl-2.0": "MPL-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0", "unlicense": "Unlicense", "the unlicense (unlicense)": "Unlicense",
    "zlib": "Zlib", "cc0-1.0": "CC0-1.0", "hpnd": "HPND", "historical permission notice and disclaimer (hpnd)": "HPND",
    "bsd 3-clause license (see license.txt)": "BSD-3-Clause", "osi approved": "OSI approved",
}


def _key(text: str) -> str:
    return " ".join(str(text or "").casefold().replace("\n", " ").split()).strip(" .")


def classify(declared: str) -> tuple[str, bool]:
    """(licence name, OSI-approved?) from a declared expression, licence field or classifier."""
    text = _key(declared)
    if not text:
        return "", False
    if text in OSI:
        return OSI[text], True
    # SPDX expressions such as "BSD-3-Clause AND 0BSD AND MIT" or "Apache-2.0 OR BSD-3-Clause"; an exception
    # ("Apache-2.0 WITH LLVM-exception") only adds permissions to the licence before it.
    text = re.sub(r"\s+with\s+[\w.-]+", "", text)
    parts = [part.strip("() ") for part in re.split(r"\s+(?:and|or)\s+", text)]
    if len(parts) > 1 and all(part in OSI for part in parts):
        return " / ".join(OSI[part] for part in parts), True
    for prefix, name in (("bsd license", "BSD"), ("bsd ", "BSD"), ("mit ", "MIT"), ("apache license", "Apache-2.0")):
        if text.startswith(prefix):
            return name, True
    if "copyright" in text and "scipy" in text:
        return "BSD-3-Clause", True
    # The licence text itself pasted into the field (ExifRead): the BSD conditions are recognisable.
    if "redistribution and use in source and binary forms" in text:
        return "BSD", True
    if "permission is hereby granted, free of charge" in text:
        return "MIT", True
    return declared.strip().splitlines()[0][:60], False


def requirements(root: Path = ROOT) -> list[dict[str, str]]:
    """Each requirement with the file it is in and the comment that says what it is for."""
    rows = []
    for name in REQUIREMENTS:
        path = root / name
        if not path.is_file():
            continue
        comment: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                text = stripped.lstrip("# ").strip()
                if text and not text.startswith((".\\", "./", "-")):
                    comment.append(text)
                continue
            if not stripped or stripped.startswith("-"):
                comment = []
                continue
            package = re.split(r"[<>=!~\[; ]", stripped)[0]
            inline = stripped.split("#", 1)[1].strip() if "#" in stripped else ""
            rows.append({"package": package, "file": name, "purpose": inline or " ".join(comment)[:160]})
            comment = []
    return rows


def package_licence(package: str) -> dict[str, Any]:
    try:
        meta = metadata.metadata(package)
    except metadata.PackageNotFoundError:
        return {"installed": False, "version": "", "licence": "", "osi": None, "home": ""}
    classifiers = [item.split(" :: ")[-1] for item in meta.get_all("Classifier") or [] if item.startswith("License ::")]
    candidates = [meta.get("License-Expression") or "", (meta.get("License") or "").strip(), *classifiers]
    licence, osi = "", False
    for candidate in candidates:
        name, approved = classify(candidate)
        if approved:
            licence, osi = name, True
            break
        licence = licence or name
    urls = meta.get_all("Project-URL") or []
    home = meta.get("Home-page") or next((url.split(",", 1)[1].strip() for url in urls
                                          if url.split(",", 1)[0].strip().lower() in ("source", "repository", "homepage",
                                                                                      "source code", "code", "github")), "")
    return {"installed": True, "version": meta.get("Version", ""), "licence": licence, "osi": osi, "home": home}


def audit(root: Path = ROOT) -> dict[str, Any]:
    rows = []
    for item in requirements(root):
        rows.append({**item, **package_licence(item["package"])})
    checked = [row for row in rows if row["installed"]]
    return {"packages": rows, "checked": len(checked), "osi": sum(bool(row["osi"]) for row in checked),
            "not_osi": [row["package"] for row in checked if not row["osi"]],
            "not_installed": [row["package"] for row in rows if not row["installed"]]}


def markdown(result: dict[str, Any]) -> str:
    lines = ["| Package | File | Version | Licence | Open source (OSI) | Source | What it is for |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in result["packages"]:
        status = "yes" if row["osi"] else "not installed here" if not row["installed"] else "**check**"
        lines.append(f"| {row['package']} | {row['file']} | {row['version']} | {row['licence'] or '—'} | {status} | "
                     f"{row['home'] or '—'} | {row['purpose'].replace('|', '/') or '—'} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    result = audit()
    table = markdown(result)
    if "--write" in argv:
        path = ROOT / "docs" / "licence-audit.md"
        text = path.read_text(encoding="utf-8")
        start, end = "<!-- packages:start -->", "<!-- packages:end -->"
        before, rest = text.split(start, 1)
        _old, after = rest.split(end, 1)
        path.write_text(before + start + "\n" + table + "\n" + end + after, encoding="utf-8")
    else:
        print(table)
    print(f"\n{result['osi']} of {result['checked']} installed packages declare an OSI-approved licence; "
          f"needs a look: {result['not_osi'] or 'none'}; not installed here: {len(result['not_installed'])}.")
    return 1 if result["not_osi"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
