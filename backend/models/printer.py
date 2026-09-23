from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from backend.core.database import Base
from backend.models.base import TimestampMixin, generate_uuid


class Printer(Base, TimestampMixin):
    __tablename__ = "printers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    connection_type: Mapped[str] = mapped_column(String(32), default="USB", nullable=False)  # USB, Network
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="connected", nullable=False)  # connected, available, driver_missing, offline
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_color: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    supports_duplex: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    paper_tray_status: Mapped[Optional[str]] = mapped_column(String(128), default="Tray 1: A4 (85% full)", nullable=True)
    toner_level: Mapped[Optional[int]] = mapped_column(Integer, default=85, nullable=True)
    last_test_page_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
