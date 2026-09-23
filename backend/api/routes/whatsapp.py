from typing import Any, Dict
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from backend.core.config import settings
from backend.core.database import get_db
from backend.core.logging import logger
from backend.services.whatsapp.service import whatsapp_service

router = APIRouter(prefix="/webhooks/whatsapp", tags=["WhatsApp Webhook"])


@router.get("")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    """WhatsApp Cloud API webhook verification challenge handshake."""
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Verification token mismatch",
    )


@router.post("")
async def receive_whatsapp_event(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Receives inbound messages and events from WhatsApp."""
    payload = await request.json()
    events = whatsapp_service.parse_incoming_webhook(payload)
    logger.info(f"Processed {len(events)} WhatsApp inbound events")
    return {"status": "received", "event_count": len(events)}
