from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.image_capture import ImageExtractionRequest
from app.schemas.purchase import PurchaseOut


class ReceiptAttachInput(ImageExtractionRequest):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReceiptRemoveInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class LinkedProductOut(BaseModel):
    id: UUID
    name: str
    brand: str | None
    pack_base_quantity: Decimal | None
    base_unit: str | None


class PurchaseDetailsOut(PurchaseOut):
    source_type: Literal["image", "text", "voice", "seed"]
    receipt_available: bool
    products: list[LinkedProductOut]