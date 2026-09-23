from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.core.database import Base
from backend.models.base import TimestampMixin, generate_uuid

if TYPE_CHECKING:
    from backend.models.order import Order
    from backend.models.configuration import PrintConfiguration


class OrderFile(Base, TimestampMixin):
    __tablename__ = "order_files"
    __table_args__ = (
        UniqueConstraint("order_id", "upload_sequence", name="uq_order_upload_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), nullable=False, index=True)
    upload_sequence: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-indexed (1, 2, 3...)
    original_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256
    mime_type: Mapped[str] = mapped_column(String(128), default="application/pdf", nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="UPLOADED", nullable=False)  # UPLOADED, VALIDATED, DELETED

    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    order: Mapped["Order"] = relationship("Order", back_populates="files")
    configuration: Mapped[Optional["PrintConfiguration"]] = relationship("PrintConfiguration", back_populates="file", uselist=False, cascade="all, delete-orphan")
