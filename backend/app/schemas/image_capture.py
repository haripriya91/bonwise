from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.extraction import ExtractionOut


class ImageExtractionRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
    )

    image_base64: str = Field(
        min_length=4,
        max_length=2_800_000,
        repr=False,
    )
    privacy_reviewed: Literal[True]


class ImageExtractionOut(ExtractionOut):
    source_image_data_url: str = Field(repr=False)
    source_sha256: str