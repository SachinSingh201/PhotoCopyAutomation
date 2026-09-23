from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.core.database import Base
from backend.models.base import TimestampMixin, generate_uuid

if TYPE_CHECKING:
    from backend.models.order import Order
    from backend.models.file import OrderFile


class PrintConfiguration(Base, TimestampMixin):
    __tablename__ = "print_configurations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), nullable=False, index=True)
    file_id: Mapped[str] = mapped_column(String(36), ForeignKey("order_files.id"), unique=True, nullable=False, index=True)

    color_mode: Mapped[str] = mapped_column(String(16), default="bw", nullable=False)  # "bw" | "color"
    default_sides: Mapped[str] = mapped_column(String(16), default="single", nullable=False)  # "single" | "double"
    rules_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)  # JSON list of page range rules

    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    order: Mapped["Order"] = relationship("Order", back_populates="configurations")
    file: Mapped["OrderFile"] = relationship("OrderFile", back_populates="configuration")
