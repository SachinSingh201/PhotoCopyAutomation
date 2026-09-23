from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.core.database import Base
from backend.models.base import TimestampMixin, generate_uuid

if TYPE_CHECKING:
    from backend.models.order import Order


class PrintJob(Base, TimestampMixin):
    __tablename__ = "print_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True, nullable=False)  # QUEUED, PRINTING, PRINTED, FAILED, CANCELLED, HELD
    queue_position: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # higher executes earlier
    estimated_duration_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)

    is_held: Mapped[bool] = mapped_column(default=False, nullable=False)
    held_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    held_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    order: Mapped["Order"] = relationship("Order", back_populates="print_jobs")
    attempts: Mapped[List["PrintAttempt"]] = relationship("PrintAttempt", back_populates="print_job", cascade="all, delete-orphan", order_by="PrintAttempt.attempt_number")
    queue_entry: Mapped[Optional["QueueEntry"]] = relationship("QueueEntry", back_populates="print_job", uselist=False, cascade="all, delete-orphan")


class PrintAttempt(Base, TimestampMixin):
    __tablename__ = "print_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    print_job_id: Mapped[str] = mapped_column(String(36), ForeignKey("print_jobs.id"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="AUTO", nullable=False)  # AUTO, MANUAL
    status: Mapped[str] = mapped_column(String(32), default="STARTED", nullable=False)  # STARTED, SUCCESS, FAILED
    agent_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    print_job: Mapped["PrintJob"] = relationship("PrintJob", back_populates="attempts")


class QueueEntry(Base, TimestampMixin):
    __tablename__ = "queue_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    print_job_id: Mapped[str] = mapped_column(String(36), ForeignKey("print_jobs.id"), unique=True, nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="WAITING", nullable=False)  # WAITING, PROCESSING, DEQUEUED

    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dequeued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    print_job: Mapped["PrintJob"] = relationship("PrintJob", back_populates="queue_entry")
