"""Audio decoding and chunking.

PyAV ships its own FFmpeg, so m4a files from Voice Memos work without installing anything else.
"""

from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np

from .config import SAMPLE_RATE

SUPPORTED_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".mp4", ".mov", ".webm"}

# Browsers seek MP3 (and raw AAC/Ogg) by guessing from the bitrate, which can land seconds off.
# These get a one time m4a copy for playback, where seeking is exact.
NEEDS_PLAYBACK_COPY = {".mp3", ".aac", ".ogg", ".webm"}
PLAYBACK_BITRATE = 96_000


def load_audio(path: Path) -> np.ndarray:
    """Decode any audio file to 16 kHz mono float32 samples."""
    resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
    pieces = []
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                pieces.append(resampled.to_ndarray().reshape(-1))
        for resampled in resampler.resample(None):
            pieces.append(resampled.to_ndarray().reshape(-1))
    if not pieces:
        raise ValueError("No audio found in this file.")
    return np.concatenate(pieces).astype(np.float32)


@dataclass
class Chunk:
    start_seconds: float
    samples: np.ndarray


def split_into_chunks(samples: np.ndarray, chunk_minutes: float = 10, search_seconds: float = 15) -> list[Chunk]:
    """Split long audio into roughly equal chunks, cutting at the quietest moment near each boundary.

    Cutting in a pause means we almost never split a word in half.
    """
    target = int(chunk_minutes * 60 * SAMPLE_RATE)
    chunks = []
    start = 0
    while start < len(samples):
        ideal_end = start + target
        if ideal_end >= len(samples):
            end = len(samples)
        else:
            end = _quietest_point(samples, ideal_end, int(search_seconds * SAMPLE_RATE))
        chunks.append(Chunk(start / SAMPLE_RATE, samples[start:end]))
        start = end
    return chunks


def _quietest_point(samples: np.ndarray, center: int, radius: int) -> int:
    window = SAMPLE_RATE // 10  # 100 ms
    low = max(center - radius, 0)
    high = min(center + radius, len(samples))
    region = samples[low:high]
    usable = len(region) // window * window
    if usable == 0:
        return center
    energy = np.square(region[:usable]).reshape(-1, window).mean(axis=1)
    return low + int(np.argmin(energy)) * window + window // 2


def make_playback_copy(source: Path, target: Path) -> None:
    """Re-encode to mono AAC in an m4a container so clicking a word plays exactly that word."""
    temp = target.with_name(target.stem + ".partial.m4a")
    with av.open(str(source)) as src, av.open(str(temp), "w", format="mp4") as dst:
        rate = src.streams.audio[0].rate or 44_100
        out = dst.add_stream("aac", rate=rate, layout="mono")
        out.bit_rate = PLAYBACK_BITRATE
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=rate)
        for frame in src.decode(audio=0):
            for resampled in resampler.resample(frame):
                for packet in out.encode(resampled):
                    dst.mux(packet)
        for resampled in resampler.resample(None):
            for packet in out.encode(resampled):
                dst.mux(packet)
        for packet in out.encode(None):
            dst.mux(packet)
    temp.replace(target)
