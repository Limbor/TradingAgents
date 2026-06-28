"""Skill registry — discovery, registration, and lookup."""

import importlib
import logging
import pkgutil

from .base import BaseSkill, SkillMetadata

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Central registry for all Skills.

    Supports manual registration and auto-discovery from the
    ``tradingagents.skills`` package.
    """

    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        """Register a skill instance."""
        meta = skill.metadata
        if meta.id in self._skills:
            raise ValueError(f"Skill '{meta.id}' already registered")
        self._skills[meta.id] = skill

    def get(self, skill_id: str) -> BaseSkill | None:
        """Get a skill by ID."""
        return self._skills.get(skill_id)

    def list_all(self) -> list[SkillMetadata]:
        """List metadata for all registered skills."""
        return [s.metadata for s in self._skills.values()]

    def find_by_trigger(self, text: str) -> list[BaseSkill]:
        """Find skills whose triggers match the given text."""
        text_lower = text.lower()
        matches = []
        for skill in self._skills.values():
            for trigger in skill.metadata.triggers:
                if trigger.lower() in text_lower:
                    matches.append(skill)
                    break
        return matches

    def auto_discover(self, package_path: str = "tradingagents.skills") -> None:
        """Auto-discover and register skills from subpackages.

        Convention: each skill subpackage's ``__init__.py`` exports a
        ``skill`` variable that is a BaseSkill instance.
        """
        try:
            package = importlib.import_module(package_path)
        except ImportError:
            logger.warning("Could not import skills package: %s", package_path)
            return

        for importer, modname, ispkg in pkgutil.iter_modules(
            package.__path__, prefix=package.__name__ + "."
        ):
            if not ispkg:
                continue
            try:
                mod = importlib.import_module(modname)
                if hasattr(mod, "skill") and isinstance(mod.skill, BaseSkill):
                    self.register(mod.skill)
                    logger.info("Registered skill: %s", mod.skill.metadata.id)
            except Exception as e:
                logger.warning("Failed to load skill from %s: %s", modname, e)
