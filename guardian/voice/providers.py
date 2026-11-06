"""Swappable STT/TTS providers. Heavy model libraries are imported lazily so
the core agent runs without them."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class VoiceUnavailable(RuntimeError):
    pass


@dataclass
class Transcript:
    text: str
    language: str | None
    seconds: float


class SpeechToText(Protocol):
    name: str

    def transcribe(self, audio_path: Path) -> Transcript: ...


class TextToSpeech(Protocol):
    name: str

    def synthesize(self, text: str, out_path: Path) -> Path: ...


class FasterWhisperSTT:
    """Whisper via faster-whisper (CPU-friendly). `model` can be a size name
    ("small", "medium", "large-v3") or a local path to a converted Malayalam
    fine-tune. Requires `pip install faster-whisper`."""

    def __init__(self, model: str = "medium", device: str = "cpu", compute_type: str = "int8"):
        self.name = f"faster-whisper:{model}"
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise VoiceUnavailable("faster-whisper not installed: pip install faster-whisper") from exc
        self._model = WhisperModel(model, device=device, compute_type=compute_type)

    def transcribe(self, audio_path: Path) -> Transcript:
        import time
        started = time.monotonic()
        segments, info = self._model.transcribe(str(audio_path), language="ml", beam_size=5)
        text = " ".join(s.text.strip() for s in segments)
        return Transcript(text, getattr(info, "language", "ml"), time.monotonic() - started)


class HFSpeechToText:
    """Any Hugging Face `automatic-speech-recognition` model, e.g. a Malayalam
    Whisper fine-tune. Requires `pip install transformers torch`."""

    def __init__(self, model_id: str):
        self.name = f"hf-asr:{model_id}"
        try:
            from transformers import pipeline  # type: ignore
        except ImportError as exc:
            raise VoiceUnavailable("transformers not installed: pip install transformers torch") from exc
        self._pipe = pipeline("automatic-speech-recognition", model=model_id, device="cpu")

    def transcribe(self, audio_path: Path) -> Transcript:
        import time
        started = time.monotonic()
        out = self._pipe(str(audio_path))
        return Transcript(out["text"].strip(), "ml", time.monotonic() - started)


class MmsTTS:
    """Meta MMS Malayalam TTS (facebook/mms-tts-mal). Licence: CC-BY-NC 4.0
    (non-commercial). Requires `pip install transformers torch scipy`."""

    def __init__(self, model_id: str = "facebook/mms-tts-mal"):
        self.name = f"mms-tts:{model_id}"
        try:
            from transformers import AutoTokenizer, VitsModel  # type: ignore
        except ImportError as exc:
            raise VoiceUnavailable("transformers not installed: pip install transformers torch scipy") from exc
        self._tok = AutoTokenizer.from_pretrained(model_id)
        self._model = VitsModel.from_pretrained(model_id)

    def synthesize(self, text: str, out_path: Path) -> Path:
        import scipy.io.wavfile  # type: ignore
        import torch  # type: ignore
        with torch.no_grad():
            wav = self._model(**self._tok(text, return_tensors="pt")).waveform[0].numpy()
        scipy.io.wavfile.write(out_path, self._model.config.sampling_rate, wav)
        return out_path


STT_PROVIDERS = {"faster-whisper": FasterWhisperSTT, "hf": HFSpeechToText}
TTS_PROVIDERS = {"mms": MmsTTS}
