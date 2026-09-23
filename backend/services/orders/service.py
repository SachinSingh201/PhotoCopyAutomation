import random
import string
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from backend.core.exceptions import AppException, ErrorCode
from backend.models.file import OrderFile
from backend.models.order import Order
from backend.models.user import ConversationSession, User
from backend.services.orders.state_machine import OrderState, transition_order


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
