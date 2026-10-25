"""Bounded, provenance-preserving public web research."""

from app.research.models import ResearchResult

__all__ = ["ResearchManager", "ResearchResult"]


def __getattr__(name: str):
    # Local knowledge recall also runs in the training environment, which does
    # not need the web extractor's optional application dependencies.
    if name == "ResearchManager":
        from app.research.manager import ResearchManager
        globals()[name] = ResearchManager
        return ResearchManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
