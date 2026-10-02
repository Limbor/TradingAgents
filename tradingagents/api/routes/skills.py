"""Skill listing and schema endpoints."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class SkillInfo(BaseModel):
    id: str
    name: str
    description: str
    version: str
    category: str
    icon: str


@router.get("/skills", response_model=list[SkillInfo])
async def list_skills(request: Request):
    """List all registered skills."""
    registry = request.app.state.registry
    return [
        SkillInfo(
            id=m.id,
            name=m.name,
            description=m.description,
            version=m.version,
            category=m.category,
            icon=m.icon,
        )
        for m in registry.list_all()
    ]


@router.get("/skills/{skill_id}/schema")
async def get_skill_schema(request: Request, skill_id: str):
    """Get the input JSON Schema for a skill."""
    registry = request.app.state.registry
    skill = registry.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    return skill.input_schema.model_json_schema()
