from datetime import datetime
from sqlalchemy import DateTime, String, Index
from sqlalchemy.orm import Mapped, mapped_column
from backend.core.database import Base
from backend.models.base import TimestampMixin, generate_uuid


class ProcessedWhatsAppMessage(Base, TimestampMixin):
    """
    Tracks processed WhatsApp message IDs to prevent duplicate event processing.
    Critical for webhook idempotency when WhatsApp/Meta retries delivery.
    """
    __tablename__ = "processed_whatsapp_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    message_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    sender: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)  # "meta" or "openwa"
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "text", "document", "interactive"
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_message_id_sender", "message_id", "sender"),
    )
