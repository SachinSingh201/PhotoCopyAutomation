from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from backend.core.config import settings
from backend.core.database import get_db
from backend.core.logging import logger
from backend.services.whatsapp.service import verify_openwa_signature, whatsapp_service

router = APIRouter(prefix="/webhooks/whatsapp", tags=["WhatsApp Webhook"])


@router.get("")
async def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
):
    """
    WhatsApp webhook verification endpoint.
    - If Meta Cloud API challenge parameters are passed, performs challenge handshake.
    - If accessed directly (e.g. OpenWA webhook test or health ping), returns active provider info.
    """
    if hub_mode == "subscribe":
        if hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
            return Response(content=hub_challenge, media_type="text/plain")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verification token mismatch",
        )

    # General / OpenWA status ping
    return {
        "status": "active",
        "provider": settings.WHATSAPP_PROVIDER,
        "engine": settings.OPENWA_ENGINE if settings.WHATSAPP_PROVIDER == "openwa" else "meta-cloud",
        "openwa_session": settings.OPENWA_SESSION_ID if settings.WHATSAPP_PROVIDER == "openwa" else None,
    }


@router.post("")
async def receive_whatsapp_event(
    request: Request,
    x_openwa_signature: Optional[str] = Header(None, alias="X-OpenWA-Signature"),
    session: AsyncSession = Depends(get_db),
):
    """
    Receives inbound messages and events from WhatsApp (OpenWA Gateway or Meta Cloud API).
    """
    raw_body = await request.body()

    # If OpenWA secret is configured and signature header provided, verify authenticity
    if settings.OPENWA_WEBHOOK_SECRET and x_openwa_signature:
        if not verify_openwa_signature(raw_body, x_openwa_signature, settings.OPENWA_WEBHOOK_SECRET):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid OpenWA webhook signature",
            )

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

    events = whatsapp_service.parse_incoming_webhook(payload)
    logger.info(f"Processed {len(events)} WhatsApp inbound events via {settings.WHATSAPP_PROVIDER}")
    return {"status": "received", "event_count": len(events), "events": events}
