"""Close NLTK's model-file sandbox bypass (PYSEC-2026-3740) until NLTK ships a fix.

NLTK 3.10.3 (the newest release) checks every file path against its allowed data
folders through ``nltk.pathsec`` -- except in six model save/load functions,
which still call the built-in ``open()`` and so can read or write anywhere:

    TransitionParser.train / .parse, AveragedPerceptron.save / .load,
    PerceptronTagger.save_to_json, nltk.classify.maxent.save_maxent_params

``harden`` wraps each so its path goes through NLTK's own ``validate_path``
first; a path outside the allowed folders raises PermissionError, exactly as
the guarded sibling functions already do. ``install`` applies it whenever any of
those modules is imported in HAYAT's process (HAYAT imports app first, so this
covers every use here), and adds HAYAT's own NLTK data folder, data/nltk_data,
to the allowed folders so everything NLTK needs stays inside the HAYAT folder.

When a fixed NLTK is released, ``harden`` keeps working (validation twice is
harmless); remove this module once pip-audit no longer reports the advisory.
"""

from __future__ import annotations

import functools
import importlib.abc
import importlib.machinery
import os
import sys
from pathlib import Path
from typing import Any, Callable

from app.config import PROJECT_ROOT

NLTK_DATA = PROJECT_ROOT / "data" / "nltk_data"
ADVISORY = "PYSEC-2026-3740"
#: module -> [(object path, name of the path argument, its position after self)]
TARGETS: dict[str, list[tuple[str, str, int]]] = {
    "nltk.parse.transitionparser": [("TransitionParser.train", "modelfile", 1),
                                    ("TransitionParser.parse", "modelFile", 1)],
    "nltk.tag.perceptron": [("AveragedPerceptron.save", "path", 0), ("AveragedPerceptron.load", "path", 0),
                            ("PerceptronTagger.save_to_json", "loc", 1)],
    "nltk.classify.maxent": [("save_maxent_params", "tab_dir", 4)],
}
_MARK = "_hayat_pathsec_guarded"


def _guard(function: Callable[..., Any], argument: str, position: int, method: bool, context: str) -> Callable[..., Any]:
    @functools.wraps(function)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        from nltk import pathsec

        offset = 1 if method else 0
        value = kwargs.get(argument, args[offset + position] if len(args) > offset + position else None)
        if value is not None:
            pathsec.validate_path(value, context=context)
            if not pathsec.ENFORCE:  # validate_path only warns when enforcement is off; the bypass must still close
                target = Path(str(getattr(value, "path", value))).resolve()
                if not any(target == root or target.is_relative_to(root) for root in pathsec._get_allowed_roots()):
                    raise PermissionError(f"Security Violation [{context}]: Unauthorized path {target}")
        return function(*args, **kwargs)

    setattr(guarded, _MARK, True)
    return guarded


def harden_module(module: Any) -> list[str]:
    """Wrap the vulnerable functions of one imported NLTK module; returns what was guarded."""
    done = []
    for dotted, argument, position in TARGETS.get(module.__name__, []):
        owner_name, _, attribute = dotted.rpartition(".")
        owner = getattr(module, owner_name) if owner_name else module
        current = getattr(owner, attribute)
        if getattr(current, _MARK, False):
            continue
        guarded = _guard(current, argument, position, bool(owner_name), f"{ADVISORY} {dotted}")
        setattr(owner, attribute, guarded)
        done.append(dotted)
    return done


def harden() -> list[str]:
    """Guard every affected module already imported (importing NLTK's parts as needed)."""
    import importlib

    done = []
    for name in TARGETS:
        done += harden_module(importlib.import_module(name))
    return done


class _AfterImport(importlib.abc.MetaPathFinder):
    """Hardens an affected NLTK module the moment it finishes loading."""

    def find_spec(self, fullname: str, path: Any, target: Any = None) -> importlib.machinery.ModuleSpec | None:
        if fullname not in TARGETS:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is not None and spec.loader is not None and hasattr(spec.loader, "exec_module"):
                original = spec.loader.exec_module

                def exec_module(module: Any, _original: Callable[[Any], None] = original) -> None:
                    _original(module)
                    harden_module(module)
                spec.loader.exec_module = exec_module  # type: ignore[method-assign]
                return spec
        return None


def install() -> None:
    """Guard NLTK in this process, now and on any later import, and use HAYAT's own data folder."""
    if not any(isinstance(finder, _AfterImport) for finder in sys.meta_path):
        sys.meta_path.insert(0, _AfterImport())
    for name in TARGETS:
        module = sys.modules.get(name)
        if module is not None:
            harden_module(module)
    # NLTK reads NLTK_DATA when nltk.data is first imported; a later import sees it, an earlier one gets the path added.
    folder = str(NLTK_DATA)
    paths = os.environ.get("NLTK_DATA", "")
    if folder not in paths.split(os.pathsep):
        os.environ["NLTK_DATA"] = folder + (os.pathsep + paths if paths else "")
    data = sys.modules.get("nltk.data")
    if data is not None and folder not in [str(item) for item in data.path]:
        data.path.insert(0, folder)


def status() -> dict[str, Any]:
    """Whether NLTK is installed, which version, and whether the bypass is closed in this process."""
    try:
        from importlib.metadata import version

        installed = version("nltk")
    except Exception:
        return {"installed": False}
    guarded = []
    for name, items in TARGETS.items():
        module = sys.modules.get(name)
        for dotted, _argument, _position in items:
            owner_name, _, attribute = dotted.rpartition(".")
            owner = getattr(module, owner_name, None) if module is not None and owner_name else module
            guarded.append(bool(owner is not None and getattr(getattr(owner, attribute, None), _MARK, False)))
    return {"installed": True, "version": installed, "advisory": ADVISORY,
            "import_hook": any(isinstance(finder, _AfterImport) for finder in sys.meta_path),
            "functions_guarded": f"{sum(guarded)} of {len(guarded)} loaded" if any(
                sys.modules.get(name) for name in TARGETS) else "guarded on import (none loaded yet)",
            "data_folder": str(NLTK_DATA)}
