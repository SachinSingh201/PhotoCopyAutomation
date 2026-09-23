from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.core.database import Base
from backend.models.base import TimestampMixin, generate_uuid

if TYPE_CHECKING:
    from backend.models.order import Order


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="mock", nullable=False)  # razorpay, stripe, mock
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(128), unique=True, index=True, nullable=True)
    provider_order_id: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="INITIATED", index=True, nullable=False)  # INITIATED, VERIFIED, FAILED
    raw_event_reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    order: Mapped["Order"] = relationship("Order", back_populates="payments")
