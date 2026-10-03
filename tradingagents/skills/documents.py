"""Versioned, bounded business skill instructions, loaded only when selected."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from importlib.resources import files

_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_DOCUMENT_CHARS = 12_000


@dataclass(frozen=True)
class SkillDocument:
    skill_id: str
    version: str
    execution: str
    description: str
    instructions: str
    digest: str

    def reference(self) -> dict:
        return {"skill_id": self.skill_id, "version": self.version,
                "execution": self.execution, "sha256": self.digest}


def load_skill_document(skill_id: str) -> SkillDocument | None:
    """Read packaged SKILL.md, never a user/model-supplied filesystem path.

    Simple scalar front matter deliberately avoids executable YAML extensions.
    Missing documents preserve compatibility with external Python plugins.
    """
    if not _ID.fullmatch(skill_id):
        raise ValueError("Invalid skill identifier")
    path = files("tradingagents.skills").joinpath(skill_id, "SKILL.md")
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if len(text) > MAX_DOCUMENT_CHARS:
        raise ValueError(f"Skill document too large: {skill_id}")
    parts = text.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError(f"Missing skill front matter: {skill_id}")
    metadata = dict(line.split(":", 1) for line in parts[1].strip().splitlines())
    metadata = {key.strip(): value.strip() for key, value in metadata.items()}
    if metadata.get("name") != skill_id or metadata.get("execution") not in {"research", "workflow"}:
        raise ValueError(f"Invalid skill front matter: {skill_id}")
    if not metadata.get("version") or not metadata.get("description") or not parts[2].strip():
        raise ValueError(f"Incomplete skill document: {skill_id}")
    return SkillDocument(skill_id, metadata["version"], metadata["execution"],
                         metadata["description"], parts[2].strip(), hashlib.sha256(text.encode()).hexdigest())


def skill_contract(skill) -> dict:
    """Documents guide reasoning; Python schemas and permissions remain authoritative."""
    document = load_skill_document(skill.metadata.id)
    return {"skill_id": skill.metadata.id, "name": skill.metadata.name,
            "description": document.description if document else skill.metadata.description,
            "document": document.reference() if document else None,
            "instructions": document.instructions if document else "",
            "parameters": skill.input_schema.model_json_schema()}
