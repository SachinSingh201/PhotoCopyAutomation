from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from backend.core.database import Base
from backend.models.base import TimestampMixin, generate_uuid


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    order_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("orders.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(128), default="System Notification", nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)  # PAYMENT_RECEIVED, ORDER_QUEUED, READY_FOR_PICKUP, ALERT, SYSTEM
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)  # PENDING, SENT, FAILED
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
