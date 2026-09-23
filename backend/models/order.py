from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.core.database import Base
from backend.models.base import TimestampMixin, generate_uuid

if TYPE_CHECKING:
    from backend.models.user import User
    from backend.models.file import OrderFile
    from backend.models.configuration import PrintConfiguration
    from backend.models.payment import Payment
    from backend.models.queue import PrintJob
    from backend.models.audit import AuditLog


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    order_code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)  # e.g. ORD-1027
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="CREATED", index=True, nullable=False)
    pickup_token: Mapped[Optional[str]] = mapped_column(String(16), unique=True, index=True, nullable=True)  # e.g. A127

    total_pages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    print_mode: Mapped[str] = mapped_column(String(16), default="AUTO", nullable=False)  # AUTO, MANUAL, HYBRID

    price_snapshot: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)  # JSON snapshot of pricing calculation

    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    printed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="orders")
    files: Mapped[List["OrderFile"]] = relationship("OrderFile", back_populates="order", cascade="all, delete-orphan", order_by="OrderFile.upload_sequence")
    configurations: Mapped[List["PrintConfiguration"]] = relationship("PrintConfiguration", back_populates="order", cascade="all, delete-orphan")
    payments: Mapped[List["Payment"]] = relationship("Payment", back_populates="order", cascade="all, delete-orphan")
    print_jobs: Mapped[List["PrintJob"]] = relationship("PrintJob", back_populates="order", cascade="all, delete-orphan")
    audit_logs: Mapped[List["AuditLog"]] = relationship("AuditLog", back_populates="order", cascade="all, delete-orphan")
