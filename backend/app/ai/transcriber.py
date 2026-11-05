import base64
import binascii
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException
from openai import OpenAIError

from app.ai.purchase_extractor import model_client, write_trace
from app.core.config import get_settings


MAX_AUDIO_BYTES = 2_000_000

MIME = {
    "webm": "audio/webm",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
}


def decode_audio(encoded, audio_format):
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(
            422,
            "Invalid base64 audio.",
        ) from None

    if not raw or len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(
            413,
            "Audio must be non-empty and at most 2 MB.",
        )

    valid = {
        "webm": raw.startswith(b"\x1a\x45\xdf\xa3"),
        "wav": (
            raw.startswith(b"RIFF")
            and raw[8:12] == b"WAVE"
        ),
        "mp3": (
            raw.startswith(b"ID3")
            or (
                len(raw) >= 2
                and raw[0] == 255
                and raw[1] & 224 == 224
            )
        ),
        "m4a": raw[4:8] == b"ftyp",
        "mp4": raw[4:8] == b"ftyp",
        "ogg": raw.startswith(b"OggS"),
        "flac": raw.startswith(b"fLaC"),
    }

    if not valid.get(audio_format):
        raise HTTPException(
            415,
            "Audio container does not match the declared format.",
        )

    # Header check only. The provider decodes the audio stream.
    return raw


def transcribe(body):
    raw = decode_audio(
        body.audio_base64,
        body.audio_format,
    )

    settings = get_settings()
    start = datetime.now(timezone.utc)
    clock = perf_counter()
    run_id = uuid4()
    status = "failed"
    usage = {}

    try:
        options = (
            {}
            if body.language_hint is None
            else {"language": body.language_hint}
        )

        response = model_client().audio.transcriptions.create(
            model=settings.transcription_model,
            file=(
                f"purchase.{body.audio_format}",
                raw,
                MIME[body.audio_format],
            ),
            response_format="json",
            **options,
        )

        transcript = (response.text or "").strip()

        provider_usage = getattr(
            response,
            "usage",
            None,
        )

        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "seconds",
        ):
            value = (
                provider_usage.get(key)
                if isinstance(provider_usage, dict)
                else getattr(provider_usage, key, None)
            )

            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value >= 0
            ):
                usage[key] = value

        if not transcript or len(transcript) > 16000:
            status = "unusable_transcript"

            raise HTTPException(
                422,
                "No usable transcript. Record again "
                "or type the purchase.",
            )

        status = "success"

    except OpenAIError as error:
        status = "provider_error"

        raise HTTPException(
            502,
            f"Transcription failed ({type(error).__name__}). "
            "You can retry explicitly or use text entry.",
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
            operation="purchase_voice_transcription",
            model_name=settings.transcription_model,
            prompt_version="not_applicable",
        )

    return {
        "transcript": transcript,
        "needs_review": True,
        "run_id": str(run_id),
        "model": settings.transcription_model,
        "latency_ms": latency_ms,
        "usage": usage,
        "trace_sent": traced,
        "saved": False,
    }