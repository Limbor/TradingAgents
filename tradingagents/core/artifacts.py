"""Helpers for persisting cross-skill Library artifacts."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from tradingagents.core.persistence import Database

logger = logging.getLogger(__name__)


def save_skill_artifact(
    config: dict[str, Any],
    *,
    skill_id: str,
    artifact_type: str,
    title: str,
    subtitle: str | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    subject_name: str | None = None,
    status: str = "success",
    summary: str | None = None,
    content_markdown: str | None = None,
    payload: dict | None = None,
    tags: list[str] | None = None,
    artifact_id: str | None = None,
) -> str | None:
    """Persist a Library artifact and return its id.

    Fails soft so a DB issue never prevents the user-facing skill result from
    streaming back to Chat.
    """
    try:
        db = config.get("db") or Database()
        run_id = str(config.get("run_id", ""))
        item_id = artifact_id or str(uuid.uuid4())
        db.save_artifact(
            artifact_id=item_id,
            run_id=run_id,
            skill_id=skill_id,
            artifact_type=artifact_type,
            title=title,
            subtitle=subtitle,
            subject_type=subject_type,
            subject_id=subject_id,
            subject_name=subject_name,
            status=status,
            summary=summary,
            content_markdown=content_markdown,
            payload=payload,
            tags=tags,
        )
        return item_id
    except Exception as exc:
        logger.warning("Failed to save %s artifact for %s: %s", artifact_type, skill_id, exc)
        return None
