"""Build the public HAYAT repository from this private one.

The public repository shows the product, not how it was made: dated work
reports, audits and build evidence stay private. The result is a fresh folder
with one commit, ready to push.

    python scripts/make_public_release.py D:/HAYAT-public-2026-10-25

To update the published repository afterwards (one normal commit, no history rewrite):

    python scripts/make_public_release.py .test-runs/public-build-N --update D:/HAYAT-github --message "..."
    git -C D:/HAYAT-github push

What it does, in order:
1. exports the committed tree (never uncommitted files, never data/ that git ignores);
2. removes the build history listed below;
3. copies release/public/ (README, contributing, code of conduct, security,
   issue and pull-request templates, checks) over the export;
4. turns links that pointed at removed documents into plain text;
5. initialises a git repository there with a single commit.
"""
from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Dated work reports in docs/ ("gui-review-2026-10-24.md") are build history.
DATED = re.compile(r"20\d\d-\d\d-\d\d")
#: Other build history, audits and evidence tooling.
PRIVATE_PATHS = (
    "release",
    "docs/MAX-LEVEL-UPGRADE-AUDIT.md",
    "docs/MAX-LEVEL-UPGRADE-RESULTS.md",
    "docs/HAYAT-OPERATOR-ARCHITECTURE-REPORT.md",
    "docs/pending-checks.md",
    "docs/history.md",
    "docs/file-preview-reliability.md",
    "docs/pitch.md",
    "data/knowledge",
    "data/training/candidates",
    "scripts/audit_upgrade_evidence.py",
    "scripts/build_audit_upgrade_report.py",
    "tests/test_audit_upgrade_evidence.py",
    "tests/test_audit_upgrade_report.py",
)
LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")
#: The public copy belongs to whoever installs it: no owner is written in, so HAYAT uses the Windows account's
#: name, and the instructions say "they". The creator credit (CREATOR in app/identity.py) is unchanged.
PUBLIC_EDITS = (
    ("app/identity.py", 'OWNER_NAME = "Mohsin Khan Shaik"\n', 'OWNER_NAME = ""\n'),
    ("app/identity.py", 'OWNER_SHORT = "Mohsin"\n', 'OWNER_SHORT = ""\n'),
    ("app/identity.py", 'OWNER_PRONOUN = "he"\n', 'OWNER_PRONOUN = "they"\n'),
    ("tests/test_identity_and_watch.py", 'EXPECTED_OWNER = ("Mohsin Khan Shaik", "Mohsin")\n',
     'EXPECTED_OWNER = ("", "")\n'),
)


def apply_public_edits(target: Path) -> None:
    for name, old, new in PUBLIC_EDITS:
        path = target / name
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise SystemExit(f"{name}: expected exactly one {old.strip()!r}; the release edit needs updating.")
        path.write_text(text.replace(old, new), encoding="utf-8")


def export(target: Path) -> None:
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", "HEAD"],
                             check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(target, filter="data")


def remove_private(target: Path) -> list[str]:
    removed = []
    docs = target / "docs"
    dated = [*docs.iterdir(), *(path for path in target.iterdir() if path.is_file() and path.suffix == ".md")]
    for path in sorted(dated):
        if DATED.search(path.name):
            removed.append(str(path.relative_to(target)))
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    for name in PRIVATE_PATHS:
        path = target / name
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(name + "/")
        elif path.exists():
            path.unlink()
            removed.append(name)
    return removed


def copy_public(target: Path) -> None:
    source = ROOT / "release" / "public"
    for path in source.rglob("*"):
        if path.is_file():
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def unlink_removed(target: Path) -> int:
    """[text](gone.md) -> text, so no public document points at a missing file."""
    changed = 0
    for document in [*target.glob("*.md"), *(target / "docs").rglob("*.md")]:
        text = document.read_text(encoding="utf-8")

        def keep_or_flatten(match: re.Match) -> str:
            label, link = match.group(1), match.group(2)
            if link.startswith(("http://", "https://", "mailto:", "#")):
                return match.group(0)
            file_part = link.split("#", 1)[0]
            return match.group(0) if (document.parent / file_part).exists() else label

        updated = LINK.sub(keep_or_flatten, text)
        if updated != text:
            document.write_text(updated, encoding="utf-8")
            changed += 1
    return changed


def commit(target: Path) -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(target), *args], check=True, capture_output=True, text=True).stdout
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("-c", "user.name=AI Agent", "-c", "user.email=ai-agent@local", "commit", "-q", "-m",
        "HAYAT: a private, local-first AI assistant for Windows\n\n"
        "Public release under the GNU AGPL-3.0. See README.md, CONTRIBUTING.md, LICENSE and NOTICE.")
    return git("rev-parse", "--short", "HEAD").strip()


def sync_into(build: Path, repo: Path, message: str) -> str:
    """Make an existing public clone match a fresh build, as one ordinary commit (no history rewrite)."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout
    if git("status", "--porcelain").strip():
        raise SystemExit(f"{repo} has uncommitted changes; commit or discard them first.")
    wanted = {path.relative_to(build).as_posix() for path in build.rglob("*")
              if path.is_file() and ".git" not in path.relative_to(build).parts}
    tracked = set(git("ls-files").splitlines())
    gone = sorted(tracked - wanted)
    for start in range(0, len(gone), 100):
        git("rm", "-q", "--", *gone[start:start + 100])
    for name in sorted(wanted):
        destination = repo / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(build / name, destination)
    git("add", "-A")
    if not git("status", "--porcelain").strip():
        return "nothing changed"
    git("-c", "user.name=AI Agent", "-c", "user.email=ai-agent@local", "commit", "-q", "-m", message)
    return git("rev-parse", "--short", "HEAD").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", type=Path, help="a folder that does not exist yet")
    parser.add_argument("--no-commit", action="store_true", help="build the files only")
    parser.add_argument("--update", type=Path, metavar="PUBLIC_CLONE",
                        help="after building into TARGET, commit the result into this existing public clone")
    parser.add_argument("--message", default="Update from the HAYAT source", help="commit message for --update")
    args = parser.parse_args(argv)
    target = args.target.resolve()
    if target.exists():
        print(f"{target} already exists; choose a new folder so nothing is overwritten.", file=sys.stderr)
        return 2
    if ROOT in target.parents and ".test-runs" not in target.parts:
        print("Build outside the project (or under .test-runs) so the release is not nested in this repository.",
              file=sys.stderr)
        return 2
    target.mkdir(parents=True)
    export(target)
    removed = remove_private(target)
    apply_public_edits(target)
    copy_public(target)
    flattened = unlink_removed(target)
    print(f"Removed {len(removed)} private paths; flattened links in {flattened} documents.")
    if args.update:
        print(f"Public clone: {sync_into(target, args.update.resolve(), args.message)}")
    elif not args.no_commit:
        print(f"Committed {commit(target)} in {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
