from typing import Optional
from fastapi import Header, HTTPException, Query, Security, status
from backend.core.config import settings


async def verify_agent_token(authorization: str = Header(...)) -> str:
    """Validate bearer token for local Print Agent."""
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected 'Bearer <token>'",
        )
    token = authorization[len(prefix) :].strip()
    if token != settings.PRINT_AGENT_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized print agent token",
        )
    return token


async def verify_admin_secret(
    x_admin_secret: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> str:
    """
    Validate admin authorization token via header or bearer token only.
    Query param removed to prevent secret leakage in logs/history.
    """
    token = None
    if x_admin_secret:
        token = x_admin_secret
    elif authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()

    if not token or token != settings.ADMIN_SECRET:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized admin access. Provide valid X-Admin-Secret or Bearer token in headers.",
        )
    return token
