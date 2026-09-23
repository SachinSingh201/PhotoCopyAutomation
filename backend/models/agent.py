from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from backend.core.database import Base
from backend.models.base import TimestampMixin, generate_uuid, utc_now


class AgentHeartbeat(Base, TimestampMixin):
    __tablename__ = "agent_heartbeats"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    agent_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ONLINE", nullable=False)  # ONLINE, OFFLINE, BUSY
    printer_status: Mapped[str] = mapped_column(String(32), default="READY", nullable=False)  # READY, PRINTING, ERROR, OUT_OF_PAPER
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
