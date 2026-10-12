"""Who spoke when.

Uses sherpa-onnx with the pyannote segmentation model and a WeSpeaker voice
embedding model. Both are small, free downloads from GitHub and need no account.
"""

import os
import tarfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import sherpa_onnx

from .config import MODELS_DIR, SAMPLE_RATE

_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION_URL = f"{_RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
EMBEDDING_URL = f"{_RELEASES}/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx"

SEGMENTATION_MODEL = MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMBEDDING_MODEL = MODELS_DIR / "wespeaker_en_voxceleb_resnet34_LM.onnx"

# Used when the user does not say how many people were at the table.
DEFAULT_CLUSTER_THRESHOLD = 0.4
# Voice prints only use clean stretches of speech.
MIN_SEGMENT_SECONDS_FOR_VOICEPRINT = 1.5
MAX_SEGMENTS_PER_VOICEPRINT = 30


@dataclass
class SpeakerTurn:
    start: float
    end: float
    speaker: int


def ensure_models() -> None:
    if not SEGMENTATION_MODEL.exists():
        archive = MODELS_DIR / "segmentation.tar.bz2"
        urllib.request.urlretrieve(SEGMENTATION_URL, archive)
        with tarfile.open(archive) as tar:
            tar.extractall(MODELS_DIR, filter="data")
        archive.unlink()
    if not EMBEDDING_MODEL.exists():
        urllib.request.urlretrieve(EMBEDDING_URL, EMBEDDING_MODEL)


def engine_name(hf_token: str) -> str:
    return "pyannote community-1" if hf_token else "sherpa-onnx (basic)"


def diarize(
    samples: np.ndarray,
    num_speakers: int | None,
    on_progress: Callable[[float], None],
    should_stop: Callable[[], bool],
    hf_token: str = "",
) -> list[SpeakerTurn]:
    """Use pyannote when a Hugging Face token is set (more accurate), otherwise sherpa-onnx."""
    if hf_token:
        from . import pyannote_diarizer

        turns = pyannote_diarizer.diarize(samples, num_speakers, hf_token, on_progress, should_stop)
        return [SpeakerTurn(start, end, speaker) for start, end, speaker in turns]
    return _sherpa_diarize(samples, num_speakers, on_progress, should_stop)


def _sherpa_diarize(
    samples: np.ndarray,
    num_speakers: int | None,
    on_progress: Callable[[float], None],
    should_stop: Callable[[], bool],
) -> list[SpeakerTurn]:
    clustering = (
        sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers)
        if num_speakers
        else sherpa_onnx.FastClusteringConfig(threshold=DEFAULT_CLUSTER_THRESHOLD)
    )
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(SEGMENTATION_MODEL)),
            num_threads=_thread_count(),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMBEDDING_MODEL), num_threads=_thread_count()),
        clustering=clustering,
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("Speaker models are missing or invalid. Delete ~/Mink/models and try again.")

    def report(done_chunks: int, total_chunks: int) -> int:
        on_progress(done_chunks / max(total_chunks, 1))
        return 1 if should_stop() else 0  # Non zero tells sherpa-onnx to stop

    result = sherpa_onnx.OfflineSpeakerDiarization(config).process(samples, callback=report)
    return [SpeakerTurn(turn.start, turn.end, turn.speaker) for turn in result.sort_by_start_time()]


def voiceprints(samples: np.ndarray, turns: list[SpeakerTurn]) -> dict[int, list[float]]:
    """Average voice embedding per speaker, used to recognise people in future sessions."""
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMBEDDING_MODEL), num_threads=_thread_count())
    )
    prints = {}
    for speaker in sorted({turn.speaker for turn in turns}):
        longest = sorted(
            (t for t in turns if t.speaker == speaker and t.end - t.start >= MIN_SEGMENT_SECONDS_FOR_VOICEPRINT),
            key=lambda t: t.end - t.start,
            reverse=True,
        )[:MAX_SEGMENTS_PER_VOICEPRINT]
        if not longest:
            continue
        embeddings = [_embed(extractor, samples, turn) for turn in longest]
        prints[speaker] = normalize(np.mean(embeddings, axis=0)).tolist()
    return prints


def embed_spans(samples: np.ndarray, spans: list[tuple[float, float]], max_seconds: float = 10.0) -> np.ndarray:
    """One normalized voice embedding per (start, end) span. Long spans use their first max_seconds."""
    if not spans:
        return np.zeros((0, 256), dtype=np.float32)
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMBEDDING_MODEL), num_threads=_thread_count())
    )
    vectors = [_embed(extractor, samples, SpeakerTurn(start, min(end, start + max_seconds), 0)) for start, end in spans]
    return np.array(vectors, dtype=np.float32).reshape(len(spans), -1)


def normalize(vector: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


def _embed(extractor, samples: np.ndarray, turn: SpeakerTurn) -> np.ndarray:
    stream = extractor.create_stream()
    stream.accept_waveform(SAMPLE_RATE, samples[int(turn.start * SAMPLE_RATE) : int(turn.end * SAMPLE_RATE)])
    stream.input_finished()
    return normalize(np.array(extractor.compute(stream)))


def _thread_count() -> int:
    """Leave one core free so the Mac stays responsive."""
    return max(1, min(8, (os.cpu_count() or 2) - 1))
