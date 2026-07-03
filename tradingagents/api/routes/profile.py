"""User investment profile endpoints."""

from fastapi import APIRouter, Request

from tradingagents.core.user_profile import UserProfile, UserProfileUpdate

router = APIRouter()


@router.get("/profile", response_model=UserProfile)
async def get_profile(request: Request):
    db = request.app.state.db
    return UserProfile.model_validate(db.get_user_profile())


@router.put("/profile", response_model=UserProfile)
async def update_profile(request: Request, body: UserProfileUpdate):
    db = request.app.state.db
    profile = db.update_user_profile(
        investment_style=body.investment_style,
        risk_tolerance=body.risk_tolerance,
        sector_prefs=body.sector_prefs,
    )
    request.app.state.config["investment_style"] = profile["investment_style"]
    return UserProfile.model_validate(profile)
