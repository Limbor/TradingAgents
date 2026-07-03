"""Base abstractions for the pluggable Skill framework.

A Skill is a self-contained agent workflow with its own LangGraph
StateGraph, input/output schemas, and shared core infrastructure
(LLM clients, data flows, memory, config).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

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


SkillProgressStatus = Literal["queued", "running", "completed", "failed"]


def skill_progress(
    *,
    stage_id: str,
    stage_label: str,
    status: SkillProgressStatus = "running",
    step_id: str | None = None,
    step_label: str | None = None,
    detail: str | None = None,
    agent: str | None = None,
    progress_pct: float | None = None,
    data: dict[str, Any] | None = None,
) -> SkillEvent:
    """Build a normalized progress event for frontend timeline rendering.

    This is the skill-to-frontend interaction contract. Skills may still emit
    domain events such as ``agent_status`` and ``report_chunk``; this event is
    the generic hierarchy used by Chat/Dashboard to render stages consistently.
    """

    payload: dict[str, Any] = {
        "stage_id": stage_id,
        "stage_label": stage_label,
        "status": status,
    }
    if step_id:
        payload["step_id"] = step_id
    if step_label:
        payload["step_label"] = step_label
    if detail:
        payload["detail"] = detail
    if agent:
        payload["agent"] = agent
    if progress_pct is not None:
        payload["progress_pct"] = progress_pct
    if data:
        payload["data"] = data
    return SkillEvent(event_type="skill_progress", data=payload)


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
