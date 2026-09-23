from datetime import datetime, time, date
from typing import Optional
from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column
from backend.core.database import Base
from backend.models.base import TimestampMixin, generate_uuid


class ShopSetting(Base, TimestampMixin):
    __tablename__ = "shop_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    shop_name: Mapped[str] = mapped_column(String(128), default="Print Express Hub", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    order_acceptance_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PrintingSchedule(Base, TimestampMixin):
    __tablename__ = "printing_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # 0=Monday, 6=Sunday
    open_time: Mapped[time] = mapped_column(Time, nullable=False)  # e.g. 08:00:00
    close_time: Mapped[time] = mapped_column(Time, nullable=False)  # e.g. 20:00:00
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ScheduleException(Base, TimestampMixin):
    __tablename__ = "schedule_exceptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    exception_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    exception_type: Mapped[str] = mapped_column(String(32), nullable=False)  # CLOSED, HOLIDAY, SPECIAL_HOURS
    open_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    close_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)


class PrintServiceControl(Base, TimestampMixin):
    __tablename__ = "print_service_control"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    manual_override: Mapped[str] = mapped_column(
        String(32), default="AUTO", nullable=False
    )  # AUTO, PAUSED, FORCED_OPEN, EMERGENCY_STOP
    effective_status: Mapped[str] = mapped_column(
        String(32), default="RUNNING", nullable=False
    )  # RUNNING, PAUSED, CLOSED
    reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(64), default="SYSTEM", nullable=True)
