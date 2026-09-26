from fastapi import APIRouter, Depends

from app.modules.identity.dependencies import require_user
from app.modules.identity.models import User
from app.modules.identity.schemas import UserProfileResponse

router = APIRouter(tags=["user"])


@router.get("/me", response_model=UserProfileResponse)
def me(user: User = Depends(require_user)) -> UserProfileResponse:
    return UserProfileResponse(
        id=user.id,
        display_name=user.display_name,
        email=user.email if user.email_verified_at is not None else None,
        email_verified=user.email_verified_at is not None,
    )
