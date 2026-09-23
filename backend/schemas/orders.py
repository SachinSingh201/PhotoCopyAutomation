from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field
from backend.schemas.files import OrderFileRead
from backend.schemas.pricing import PriceSnapshot


class OrderCreate(BaseModel):
    whatsapp_user_id: str
    phone_number: str
    display_name: Optional[str] = None


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_code: str
    user_id: str
    status: str
    pickup_token: Optional[str] = None
    total_pages: int
    total_amount: float
    currency: str
    print_mode: str
    price_snapshot: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    files: List[OrderFileRead] = Field(default_factory=list)


class OrderInterpretRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000, description="Natural language instructions from the user")


class OrderTransitionRequest(BaseModel):
    target_state: str
    actor_type: str = "BACKEND"  # CUSTOMER, BACKEND, LLM, AGENT, ADMIN
    actor_id: Optional[str] = None
    metadata: Optional[dict] = None
