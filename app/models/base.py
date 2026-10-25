from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Message:
    role: str
    content: str
    # Set only by prompt builders for reference snapshots/recaps. Providers
    # receive role/content only; this never grants source text authority.
    optional_context: bool = False


@dataclass(slots=True)
class ModelResponse:
    content: str
    model: str
    provider: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    #: Why generation stopped, as the provider reported it: "stop" when the
    #: model chose to end, "length" when it ran out of budget. Both providers
    #: have always sent this and nothing read it, so an answer cut off at the
    #: token limit was indistinguishable from one that had finished.
    finish_reason: str = ""

    @property
    def truncated(self) -> bool:
        """Whether the provider says this stopped early."""
        return str(self.finish_reason).lower() in {"length", "max_tokens", "max_length"}


class ModelError(RuntimeError):
    """A provider failure that carries what the provider knew when it failed.

    The caller sees the exception long after the socket did; by then the only
    evidence left is a string. Attaching the endpoint, the dependency name and
    the classification here means "connection refused to localhost:11434" stays
    a downed local dependency all the way to the failure record, instead of
    being re-guessed as a possible network problem.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        endpoint: str | None = None,
        dependency: str | None = None,
        failure_class: Any = None,
        http_status: int | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.endpoint = endpoint
        self.dependency = dependency
        self.failure_class = failure_class
        self.http_status = http_status
        self.model = model

    def failure_fields(self) -> dict[str, Any]:
        """The structured evidence, ready to hand to FailureLog.record()."""
        fields = {
            "provider": self.provider, "endpoint": self.endpoint,
            "dependency": self.dependency, "http_status": self.http_status,
        }
        if self.failure_class is not None:
            fields["failure_class"] = self.failure_class
        return {key: value for key, value in fields.items() if value is not None}


class ModelProvider(ABC):
    name: str

    @abstractmethod
    def generate(
        self,
        messages: list[Message] | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> list[str]:
        raise NotImplementedError

    def is_available(self) -> bool:
        try:
            return bool(self.list_models())
        except Exception:
            return False

    def generate_vision(
        self,
        prompt: str,
        image_path: str | Path,
        temperature: float | None = None,
    ) -> ModelResponse:
        raise ModelError(f"{self.name} does not implement vision input.")


def normalize_messages(messages: list[Message] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"role": item.role, "content": item.content} if isinstance(item, Message) else item
        for item in messages
    ]


def model_context_limit(model: Any) -> int | None:
    """Smallest declared limit, including a provider's configured window."""
    limits = [getattr(model, key, None) for key in ("context", "n_ctx", "context_length", "num_ctx")]
    valid = [value for value in limits if type(value) is int and value > 0]
    return min(valid) if valid else None
