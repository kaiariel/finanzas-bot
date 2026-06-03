from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from finance_bot.config import Settings


class LocalTranscriptionUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=2)
def _load_model(model_name: str, device: str, compute_type: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise LocalTranscriptionUnavailable(
            "Falta faster-whisper. Instala: pip install -r requirements-voice.txt"
        ) from exc

    return WhisperModel(model_name, device=device, compute_type=compute_type)


def transcribe_voice_file(path: Path, settings: Settings) -> str:
    if not settings.voice_transcription_enabled:
        raise LocalTranscriptionUnavailable("VOICE_TRANSCRIPTION_ENABLED no esta activo")

    model = _load_model(
        settings.voice_transcription_model,
        settings.voice_transcription_device,
        settings.voice_transcription_compute_type,
    )
    segments, _ = model.transcribe(str(path), language="es", beam_size=5)
    return " ".join(segment.text.strip() for segment in segments).strip()

