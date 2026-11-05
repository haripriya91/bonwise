from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AudioFormat = Literal[
    "webm", "wav", "mp3", "m4a", "mp4", "ogg", "flac"
]


class TranscriptionRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
    )

    audio_base64: str = Field(
        min_length=4,
        max_length=2_800_000,
        repr=False,
    )
    audio_format: AudioFormat
    language_hint: Literal["de", "en"] | None = None
    transcription_requested: Literal[True]


class TranscriptionOut(BaseModel):
    transcript: str = Field(
        min_length=1,
        max_length=16000,
        repr=False,
    )
    needs_review: Literal[True]
    run_id: str
    model: str
    latency_ms: int
    usage: dict[str, int | float]
    trace_sent: bool
    saved: Literal[False]