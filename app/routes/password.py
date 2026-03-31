from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
import hashlib
import hmac

from app.config import get_settings

router = APIRouter()


class PasswordVerifyRequest(BaseModel):
    password: str


class PasswordVerifyResponse(BaseModel):
    valid: bool
    message: str


@router.post("/verify-password", response_model=PasswordVerifyResponse)
async def verify_password(request: PasswordVerifyRequest):
    """
    Verify if the provided password matches the master password.
    Requires X-API-Key header.
    """
    settings = get_settings()
    
    is_valid = hmac.compare_digest(
        request.password.encode(),
        settings.MASTER_PASSWORD.encode()
    )
    
    if is_valid:
        return PasswordVerifyResponse(
            valid=True,
            message="Password verified successfully"
        )
    else:
        return PasswordVerifyResponse(
            valid=False,
            message="Invalid password"
        )
