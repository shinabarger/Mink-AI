"""Speech to text with Whisper.

On Apple Silicon Macs we use mlx-whisper, which runs on the GPU and handles a
5 hour session in roughly 15 to 30 minutes. Everywhere else we fall back to
faster-whisper on the CPU (slower, same accuracy).
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from . import model_download
from .audio import split_into_chunks
from .config import IS_APPLE_SILICON, SAMPLE_RATE, WHISPER_MODELS


@dataclass
class Word:
    start: float
    end: float
    text: str  # Includes Whisper's leading space, e.g. " dragon"


# on_progress(seconds_of_audio_done, total_seconds). on_download(bytes_done, bytes_total).
ProgressCallback = Callable[[float, float], None]
DownloadCallback = Callable[[int, int], None]

# Short chunks mean steady progress updates and a Stop button that responds quickly.
CHUNK_MINUTES = 3


def build_prompt(glossary: list[str]) -> str | None:
    """Prime Whisper with names it would otherwise misspell (Druskenvald, Harkin...)."""
    terms = [term.strip() for term in glossary if term.strip()]
    if not terms:
        return None
    return "A Dungeons & Dragons session. Names and terms: " + ", ".join(terms) + "."


def transcribe(
    samples: np.ndarray,
    model_key: str,
    language: str,
    glossary: list[str],
    on_progress: ProgressCallback,
    on_download: DownloadCallback,
) -> list[Word]:
    engine_class = _MlxEngine if IS_APPLE_SILICON else _FasterWhisperEngine
    engine = engine_class(model_key, on_download)
    prompt = build_prompt(glossary)
    total_seconds = len(samples) / SAMPLE_RATE
    words: list[Word] = []

    # Chunking keeps memory flat, gives us real progress updates, and stops one
    # bad stretch of audio from derailing the rest of a 5 hour recording.
    on_progress(0.0, total_seconds)
    for chunk in split_into_chunks(samples, chunk_minutes=CHUNK_MINUTES):
        for word in engine.transcribe_chunk(chunk.samples, language, prompt):
            words.append(Word(word.start + chunk.start_seconds, word.end + chunk.start_seconds, word.text))
        chunk_end = chunk.start_seconds + len(chunk.samples) / SAMPLE_RATE
        on_progress(min(chunk_end, total_seconds), total_seconds)
    return words


def engine_name() -> str:
    return "mlx-whisper (Apple GPU)" if IS_APPLE_SILICON else "faster-whisper (CPU)"


class _MlxEngine:
    def __init__(self, model_key: str, on_download: DownloadCallback):
        import mlx_whisper  # Only installed on Apple Silicon

        self._mlx_whisper = mlx_whisper
        self._repo = model_download.download(WHISPER_MODELS[model_key]["mlx"], on_download)

    def transcribe_chunk(self, samples: np.ndarray, language: str, prompt: str | None) -> list[Word]:
        result = self._mlx_whisper.transcribe(
            samples,
            path_or_hf_repo=self._repo,
            language=language,
            word_timestamps=True,
            initial_prompt=prompt,
            condition_on_previous_text=True,
            hallucination_silence_threshold=2.0,
            verbose=None,
        )
        return [
            Word(float(word["start"]), float(word["end"]), word["word"])
            for segment in result["segments"]
            for word in segment.get("words", [])
        ]


class _FasterWhisperEngine:
    def __init__(self, model_key: str, on_download: DownloadCallback):
        from faster_whisper import WhisperModel

        path = model_download.download(
            WHISPER_MODELS[model_key]["faster_whisper"], on_download, model_download.FASTER_WHISPER_FILES
        )
        self._model = WhisperModel(path, device="cpu", compute_type="int8")

    def transcribe_chunk(self, samples: np.ndarray, language: str, prompt: str | None) -> list[Word]:
        segments, _info = self._model.transcribe(
            samples,
            language=language,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            initial_prompt=prompt,
            hotwords=prompt,
            condition_on_previous_text=True,
        )
        return [Word(word.start, word.end, word.word) for segment in segments for word in (segment.words or [])]
