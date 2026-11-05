import base64
import binascii
import hashlib
from datetime import datetime, timezone
from io import BytesIO
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from openai import OpenAIError
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from app.ai.purchase_extractor import (
    PROMPT,
    assess,
    model_client,
    write_trace,
)
from app.core.config import get_settings
from app.core.privacy import has_detectable_pii
from app.schemas.extraction import ExtractedPurchase


MAX_INPUT_BYTES = 2_000_000
MAX_PIXELS = 16_000_000
MAX_OUTPUT_BYTES = 1_800_000


def prepare_image(encoded):
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(
            422,
            "Invalid base64 image.",
        ) from None

    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise HTTPException(
            413,
            "Image must be non-empty and at most 2 MB.",
        )

    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG"}:
                raise HTTPException(
                    415,
                    "Only JPEG/PNG images are supported in this step.",
                )

            if getattr(source, "n_frames", 1) != 1:
                raise HTTPException(
                    415,
                    "Use one still image, not an animation.",
                )

            if source.width * source.height > MAX_PIXELS:
                raise HTTPException(
                    413,
                    "Image exceeds 16 megapixels. Resize it first.",
                )

            source.load()

            oriented = ImageOps.exif_transpose(source).convert("RGBA")

            # A fresh image removes source metadata.
            # Flatten transparent pixels onto white.
            clean = Image.new("RGB", oriented.size, "white")
            clean.paste(
                oriented,
                mask=oriented.getchannel("A"),
            )

            clean.thumbnail(
                (2000, 4000),
                Image.Resampling.LANCZOS,
            )

            for quality in (90, 80, 70):
                output = BytesIO()

                clean.save(
                    output,
                    format="JPEG",
                    quality=quality,
                    optimize=True,
                )

                prepared = output.getvalue()

                if len(prepared) <= MAX_OUTPUT_BYTES:
                    return prepared

    except HTTPException:
        raise

    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
    ):
        raise HTTPException(
            422,
            "Unreadable or unsupported image.",
        ) from None

    raise HTTPException(
        413,
        "Prepared image is too large. Crop or resize the receipt.",
    )


def extract_image(encoded):
    prepared = prepare_image(encoded)

    data_url = (
        "data:image/jpeg;base64,"
        + base64.b64encode(prepared).decode("ascii")
    )

    settings = get_settings()
    run_id = uuid4()
    start = datetime.now(timezone.utc)
    clock = perf_counter()
    status = "failed"
    usage = {}

    try:
        today = datetime.now(
            ZoneInfo("Europe/Berlin")
        ).date()

        response = model_client().responses.parse(
            model=settings.vision_model,
            instructions=(
                PROMPT
                + f"\nReference date in Europe/Berlin: {today}."
            ),
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Extract the printed purchase fields "
                                "from this privacy-reviewed receipt image. "
                                "Never infer unreadable fields."
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": data_url,
                            "detail": "high",
                        },
                    ],
                }
            ],
            text_format=ExtractedPurchase,
            store=False,
            max_output_tokens=6000,
        )

        if response.usage:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        result = response.output_parsed

        if response.status != "completed" or result is None:
            status = "incomplete"

            raise HTTPException(
                422,
                "Image extraction incomplete. Try a clearer crop "
                "or enter missing fields manually.",
            )

        if has_detectable_pii(
            result.model_dump(mode="json")
        ):
            status = "privacy_blocked"

            raise HTTPException(
                422,
                "Potential personal information in extracted "
                "fields; result discarded.",
            )

        answer = assess(result)
        status = "success"

    except OpenAIError as error:
        status = "provider_error"

        raise HTTPException(
            502,
            f"Image extraction failed ({type(error).__name__}). "
            "No purchase saved.",
        ) from None

    except ValidationError:
        status = "invalid_output"

        raise HTTPException(
            422,
            "Invalid extracted fields. Try a clearer image.",
        ) from None

    finally:
        latency_ms = round(
            (perf_counter() - clock) * 1000
        )

        traced = write_trace(
            run_id,
            start,
            datetime.now(timezone.utc),
            status,
            usage,
            operation="purchase_image_extraction",
            model_name=settings.vision_model,
        )

    return {
        **answer,
        "run_id": str(run_id),
        "model": settings.vision_model,
        "latency_ms": latency_ms,
        "usage": usage,
        "trace_sent": traced,
        "saved": False,
        "source_image_data_url": data_url,
        "source_sha256": hashlib.sha256(prepared).hexdigest(),
    }