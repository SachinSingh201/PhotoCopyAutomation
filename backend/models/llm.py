from typing import Optional
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from backend.core.database import Base
from backend.models.base import TimestampMixin, generate_uuid


class LLMInteraction(Base, TimestampMixin):
    __tablename__ = "llm_interactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    order_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("orders.id"), nullable=True, index=True)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    structured_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON output
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), default="SUCCESS", nullable=False)  # SUCCESS, INVALID_SCHEMA, AMBIGUOUS
