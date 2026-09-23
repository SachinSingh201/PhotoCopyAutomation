from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class PaymentCreateRequest(BaseModel):
    order_id: str


class PaymentCreateResponse(BaseModel):
    payment_id: str
    order_id: str
    amount: float
    currency: str
    provider: str
    provider_order_id: Optional[str] = None


class PaymentVerifyRequest(BaseModel):
    order_id: str
    provider_payment_id: str
    signature: Optional[str] = None
