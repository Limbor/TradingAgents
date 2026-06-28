"""Base abstractions for the pluggable Skill framework.

A Skill is a self-contained agent workflow with its own LangGraph
StateGraph, input/output schemas, and shared core infrastructure
(LLM clients, data flows, memory, config).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from pydantic import BaseModel


@dataclass
class SkillMetadata:
    """Skill metadata used for registration, discovery, and routing."""

    id: str
    name: str
    description: str
    version: str
    triggers: list[str] = field(default_factory=list)
    icon: str = ""
    category: str = "general"


@dataclass
class SkillEvent:
    """An event emitted during skill execution."""

    event_type: str
    data: dict[str, Any]


class BaseSkill(ABC):
    """Abstract base class for all Skills."""

    @property
    @abstractmethod
    def metadata(self) -> SkillMetadata:
        """Return skill metadata."""
        ...

    @property
    @abstractmethod
    def input_schema(self) -> type[BaseModel]:
        """Return the Pydantic model for input parameters."""
        ...

    @property
    @abstractmethod
    def output_schema(self) -> type[BaseModel]:
        """Return the Pydantic model for output results."""
        ...

    @abstractmethod
    async def execute(
        self,
        params: BaseModel,
        config: dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        """Execute the skill, yielding events as they occur.

        Args:
            params: Validated input parameters (Pydantic instance)
            config: Runtime configuration (LLM settings, etc.)

        Yields:
            SkillEvent: Execution progress and result events
        """
        ...

    @abstractmethod
    async def cancel(self) -> None:
        """Cancel a currently running execution."""
        ...

    def validate_params(self, raw: dict) -> BaseModel:
        """Validate raw input against the skill's input schema."""
        return self.input_schema.model_validate(raw)
