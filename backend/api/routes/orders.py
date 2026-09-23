import json
from typing import List
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from backend.core.database import get_db
from backend.core.exceptions import AppException
from backend.models.configuration import PrintConfiguration
from backend.models.order import Order
from backend.schemas.files import OrderFileRead
from backend.schemas.llm import LLMInterpretationResult
from backend.schemas.orders import OrderCreate, OrderInterpretRequest, OrderRead
from backend.schemas.pricing import PriceSnapshot
from backend.services.files.service import process_and_save_order_file
from backend.services.llm.parser import interpret_print_instructions
from backend.services.orders.service import create_order, get_or_create_user, get_order_by_id
from backend.services.orders.state_machine import OrderState, transition_order
from backend.services.pricing.engine import calculate_order_pricing_snapshot

router = APIRouter(prefix="/orders", tags=["Orders"])


@router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
async def create_new_order(payload: OrderCreate, session: AsyncSession = Depends(get_db)):
    user = await get_or_create_user(
        session=session,
        whatsapp_user_id=payload.whatsapp_user_id,
        phone_number=payload.phone_number,
        display_name=payload.display_name,
    )
    order = await create_order(session=session, user=user)
    await session.commit()
    return await get_order_by_id(session, order.id)


@router.get("/{order_id}", response_model=OrderRead)
async def get_order_details(order_id: str, session: AsyncSession = Depends(get_db)):
    return await get_order_by_id(session, order_id)


@router.post("/{order_id}/files", response_model=OrderFileRead, status_code=status.HTTP_201_CREATED)
async def upload_document_file(
    order_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
):
    order = await get_order_by_id(session, order_id)
    content = await file.read()
    order_file = await process_and_save_order_file(
        session=session,
        order=order,
        original_filename=file.filename or "document.pdf",
        file_bytes=content,
        mime_type=file.content_type or "application/pdf",
    )
    # Recalculate deterministic pricing snapshot
    await calculate_order_pricing_snapshot(session, order)
    await session.commit()
    return order_file


@router.post("/{order_id}/interpret", response_model=LLMInterpretationResult)
async def interpret_order_instructions(
    order_id: str,
    payload: OrderInterpretRequest,
    session: AsyncSession = Depends(get_db),
):
    order = await get_order_by_id(session, order_id)
    result = await interpret_print_instructions(
        session=session,
        order_id=order.id,
        user_text=payload.text,
        files=order.files,
    )

    # Apply configuration if not ambiguous
    if not result.ambiguity and result.file_rules:
        for f_rule in result.file_rules:
            # Match upload sequence
            matched_file = next((f for f in order.files if f.upload_sequence == f_rule.file_reference), None)
            if matched_file and matched_file.configuration:
                config = matched_file.configuration
                if f_rule.color_mode:
                    config.color_mode = f_rule.color_mode
                if f_rule.default_sides:
                    config.default_sides = f_rule.default_sides
                if f_rule.rules:
                    config.rules_json = json.dumps([r.model_dump() for r in f_rule.rules])

        # State transition: CONFIGURED -> PRICE_CALCULATED -> AWAITING_CONFIRMATION
        if order.status in [OrderState.FILES_UPLOADING.value, OrderState.FILES_UPLOADED.value]:
            await transition_order(session, order, OrderState.FILES_UPLOADED, actor_type="CUSTOMER")
            await transition_order(session, order, OrderState.CONFIGURATION_PENDING, actor_type="CUSTOMER")
        if order.status == OrderState.CONFIGURATION_PENDING.value:
            await transition_order(session, order, OrderState.CONFIGURED, actor_type="CUSTOMER")
            await transition_order(session, order, OrderState.PRICE_CALCULATED, actor_type="BACKEND")
            await transition_order(session, order, OrderState.AWAITING_CONFIRMATION, actor_type="BACKEND")

        # Recalculate price
        await calculate_order_pricing_snapshot(session, order)

    await session.commit()
    return result


@router.post("/{order_id}/confirm", response_model=PriceSnapshot)
async def confirm_order(order_id: str, session: AsyncSession = Depends(get_db)):
    order = await get_order_by_id(session, order_id)
    if order.status not in [
        OrderState.PRICE_CALCULATED.value,
        OrderState.AWAITING_CONFIRMATION.value,
        OrderState.FILES_UPLOADED.value,
        OrderState.FILES_UPLOADING.value,
    ]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot confirm order in status {order.status}",
        )

    # Calculate final price snapshot
    snapshot = await calculate_order_pricing_snapshot(session, order)

    # Transition order state
    if order.status in [OrderState.FILES_UPLOADING.value, OrderState.FILES_UPLOADED.value]:
        await transition_order(session, order, OrderState.FILES_UPLOADED, actor_type="CUSTOMER")
        await transition_order(session, order, OrderState.CONFIGURATION_PENDING, actor_type="CUSTOMER")
        await transition_order(session, order, OrderState.CONFIGURED, actor_type="CUSTOMER")
        await transition_order(session, order, OrderState.PRICE_CALCULATED, actor_type="BACKEND")

    if order.status == OrderState.PRICE_CALCULATED.value:
        await transition_order(session, order, OrderState.AWAITING_CONFIRMATION, actor_type="CUSTOMER")

    await session.commit()
    return snapshot
