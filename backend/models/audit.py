from typing import TYPE_CHECKING, Optional
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.core.database import Base
from backend.models.base import TimestampMixin, generate_uuid

if TYPE_CHECKING:
    from backend.models.order import Order


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    order_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("orders.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)  # e.g. ORDER_CREATED, PAYMENT_VERIFIED
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)  # CUSTOMER, BACKEND, LLM, AGENT, ADMIN
    actor_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    order: Mapped[Optional["Order"]] = relationship("Order", back_populates="audit_logs")
