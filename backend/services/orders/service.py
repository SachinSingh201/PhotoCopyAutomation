import random
import shutil
import string
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from backend.core.config import settings
from backend.core.exceptions import AppException, ErrorCode
from backend.core.logging import logger
from backend.models.file import OrderFile
from backend.models.order import Order
from backend.models.user import ConversationSession, User
from backend.services.orders.state_machine import OrderState, transition_order
from backend.services.whatsapp.service import whatsapp_service


def generate_pickup_token() -> str:
    """Generate human-readable pickup token, e.g. A127"""
    letter = random.choice(string.ascii_uppercase)
    digits = f"{random.randint(100, 999)}"
    return f"{letter}{digits}"


def generate_order_code() -> str:
    """Generate order code, e.g. ORD-1027"""
    num = random.randint(1000, 9999)
    return f"ORD-{num}"


async def get_or_create_user(
    session: AsyncSession,
    whatsapp_user_id: str,
    phone_number: str,
    display_name: Optional[str] = None,
) -> User:
    query = select(User).where(User.whatsapp_user_id == whatsapp_user_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            whatsapp_user_id=whatsapp_user_id,
            phone_number=phone_number,
            display_name=display_name,
        )
        session.add(user)
        await session.flush()
    return user


async def create_order(
    session: AsyncSession,
    user: User,
) -> Order:
    order_code = generate_order_code()
    pickup_token = generate_pickup_token()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=30)

    order = Order(
        order_code=order_code,
        user_id=user.id,
        status=OrderState.CREATED.value,
        pickup_token=pickup_token,
        total_pages=0,
        total_amount=0.0,
        currency="INR",
        expires_at=expires_at,
    )
    session.add(order)
    await session.flush()

    # Create / update conversation session
    sess_query = select(ConversationSession).where(
        ConversationSession.user_id == user.id,
        ConversationSession.status == "ACTIVE",
    )
    sess_res = await session.execute(sess_query)
    conv_session = sess_res.scalar_one_or_none()

    if conv_session:
        conv_session.active_order_id = order.id
        conv_session.last_activity_at = now
        conv_session.expires_at = now + timedelta(minutes=15)
    else:
        conv_session = ConversationSession(
            user_id=user.id,
            status="ACTIVE",
            active_order_id=order.id,
            last_activity_at=now,
            expires_at=now + timedelta(minutes=15),
        )
        session.add(conv_session)

    await session.flush()
    return order


async def get_order_by_id(session: AsyncSession, order_id: str) -> Order:
    query = (
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.files).selectinload(OrderFile.configuration),
            selectinload(Order.configurations),
            selectinload(Order.payments),
            selectinload(Order.print_jobs),
        )
    )
    result = await session.execute(query)
    order = result.scalar_one_or_none()
    if not order:
        raise AppException(
            code=ErrorCode.ORDER_NOT_FOUND,
            message=f"Order with ID {order_id} not found",
            status_code=404,
        )
    return order


async def cleanup_expired_orders(session: AsyncSession) -> List[str]:
    """
    Finds orders in PAYMENT_PENDING (or draft) that exceeded their expiration time (5-minute payment window).
    Transitions them to EXPIRED, physically deletes stored PDF files from disk,
    marks order files as DELETED, and notifies the customer on WhatsApp.
    """
    now = datetime.now(timezone.utc)
    query = (
        select(Order)
        .where(
            Order.status == OrderState.PAYMENT_PENDING.value,
            Order.expires_at.is_not(None),
            Order.expires_at <= now,
        )
        .options(
            selectinload(Order.files),
            selectinload(Order.user),
        )
    )
    result = await session.execute(query)
    expired_orders = result.scalars().all()

    cleaned_codes: List[str] = []
    for order in expired_orders:
        try:
            logger.info(f"Expiring unpaid order #{order.order_code} (5-min payment timeout exceeded)")
            await transition_order(
                session=session,
                order=order,
                target_state=OrderState.EXPIRED,
                actor_type="SYSTEM",
                metadata={"reason": "5-minute payment window expired without payment"},
            )

            # Physically delete uploaded PDF files from private storage
            order_dir = settings.storage_path / "orders" / order.id
            if order_dir.exists():
                shutil.rmtree(order_dir, ignore_errors=True)

            for f in order.files:
                f.status = "DELETED"
                f.deleted_at = now

            await session.commit()
            cleaned_codes.append(order.order_code)

            # Notify customer via WhatsApp
            if order.user and order.user.phone_number:
                exp_msg = (
                    f"⏱️ *Order #{order.order_code} Expired & Deleted*\n\n"
                    f"Your 5-minute payment window for Token *{order.pickup_token}* has expired without payment.\n"
                    f"• *Status:* EXPIRED\n"
                    f"• *Uploaded Files:* Safely deleted from disk\n\n"
                    f"✨ You can send a new PDF document anytime to place a fresh order!"
                )
                try:
                    await whatsapp_service.send_text_message(
                        to_phone=order.user.phone_number,
                        text=exp_msg,
                    )
                except Exception as notify_err:
                    logger.warning(f"Failed to send expiration WhatsApp notice: {notify_err}")

        except Exception as err:
            logger.error(f"Error during expiration cleanup for Order #{order.order_code}: {err}")
            await session.rollback()

    return cleaned_codes
