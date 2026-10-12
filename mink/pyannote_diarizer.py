"""Who spoke when, using pyannote's free community-1 model (the more accurate option).

Needs a free Hugging Face token with the model's terms accepted:
https://huggingface.co/pyannote/speaker-diarization-community-1
"""

import logging
import os
from collections.abc import Callable

# pyannote sends anonymous usage stats by default. Mink keeps everything on this Mac.
os.environ["PYANNOTE_METRICS_ENABLED"] = "false"

import numpy as np

from .config import SAMPLE_RATE

MODEL_ID = "pyannote/speaker-diarization-community-1"
MODEL_PAGE = f"https://huggingface.co/{MODEL_ID}"

# Rough share of the work each pyannote step takes, for the progress bar.
STEP_WEIGHTS = {"segmentation": (0.0, 0.3), "embeddings": (0.3, 0.95)}

log = logging.getLogger("mink")
_pipeline_cache: dict[str, object] = {}


def diarize(
    samples: np.ndarray,
    num_speakers: int | None,
    hf_token: str,
    on_progress: Callable[[float], None],
    should_stop: Callable[[], bool],
):
    """Return [(start, end, speaker_index)] with no overlapping turns."""
    import torch

    pipeline = _load_pipeline(hf_token)
    audio = {"waveform": torch.from_numpy(samples).unsqueeze(0), "sample_rate": SAMPLE_RATE}

    def hook(step_name, _artifact, file=None, total=None, completed=None):
        if should_stop():
            raise InterruptedError("Stopped")
        if step_name in STEP_WEIGHTS and total:
            low, high = STEP_WEIGHTS[step_name]
            on_progress(low + (high - low) * (completed or 0) / total)

    try:
        output = _run_on(pipeline, _best_device(), audio, num_speakers, hook)
    except InterruptedError:
        raise
    except RuntimeError as error:
        # Apple's GPU backend occasionally lacks an operation. The CPU always works, just slower.
        log.warning("Speaker model failed on the GPU (%s). Retrying on the CPU.", error)
        output = _run_on(pipeline, torch.device("cpu"), audio, num_speakers, hook)

    # The "exclusive" version gives one speaker at a time, which is what we need to label words.
    labels: dict[str, int] = {}
    turns = []
    for segment, _track, label in output.exclusive_speaker_diarization.itertracks(yield_label=True):
        speaker = labels.setdefault(label, len(labels))
        turns.append((segment.start, segment.end, speaker))
    return turns


def _run_on(pipeline, device, audio, num_speakers, hook):
    pipeline.to(device)
    return pipeline(audio, num_speakers=num_speakers, hook=hook)


def _load_pipeline(hf_token: str):
    if hf_token not in _pipeline_cache:
        from pyannote.audio import Pipeline

        try:
            pipeline = Pipeline.from_pretrained(MODEL_ID, token=hf_token)
        except Exception as error:
            raise RuntimeError(
                "Hugging Face wouldn't give Mink the speaker model. Check that your token in Settings is right "
                f"and that you clicked 'Agree' on {MODEL_PAGE}. ({error})"
            ) from error
        if pipeline is None:
            raise RuntimeError(f"Could not load the speaker model. Did you accept the terms on {MODEL_PAGE}?")
        _pipeline_cache[hf_token] = pipeline
    return _pipeline_cache[hf_token]


def _best_device():
    import torch

    return torch.device("mps") if torch.backends.mps.is_available() else torch.device("cpu")
