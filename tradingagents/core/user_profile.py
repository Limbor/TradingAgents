"""User investment profile models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


InvestmentStyle = Literal["short_term", "medium_term", "long_term"]
RiskTolerance = Literal["low", "moderate", "high"]


class UserProfile(BaseModel):
    investment_style: InvestmentStyle = Field(default="long_term")
    risk_tolerance: RiskTolerance = Field(default="moderate")
    sector_prefs: list[str] = Field(default_factory=list)
    updated_at: str | None = None


class UserProfileUpdate(BaseModel):
    investment_style: InvestmentStyle | None = None
    risk_tolerance: RiskTolerance | None = None
    sector_prefs: list[str] | None = None
