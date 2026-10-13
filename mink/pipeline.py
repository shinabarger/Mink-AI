"""Turns an audio file into a finished transcript.

Steps: read audio, transcribe, find who is speaking, build one line per
sentence, double check each line's voice, then save.
"""

import logging
import time
from collections.abc import Callable

import numpy as np

from . import aligner, diarizer, speakers, store, transcriber, voice_check, voices
from .audio import load_audio
from .config import SAMPLE_RATE, load_settings
from .corrections import apply_saved_corrections
from .exporter import format_clock

log = logging.getLogger("mink")

# Share of the progress bar each stage gets. Transcription is the slow part.
TRANSCRIBE_SHARE = 0.75
DIARIZE_SHARE = 0.20


class JobStoppedError(Exception):
    pass


class TranscriptionJob:
    def __init__(self, session_id: str, should_stop: Callable[[], bool]):
        self.session_id = session_id
        self.should_stop = should_stop
        self.session = store.get(session_id)
        self.settings = load_settings()
        self.hf_token = self.settings["hf_token"].strip()

    def run(self) -> None:
        store.update(self.session_id, status="processing", started_at=time.time())
        samples = self._read_audio()
        words = self._transcribe(samples)
        turns = self._find_speakers(samples)
        lines, speaker_names = self._build_transcript(samples, words, turns)
        store.update(
            self.session_id, speakers=speaker_names, lines=lines, status="done", stage="Done", detail="", progress=1.0
        )
        log.info("%s  done: %d lines, %d speakers", self.session["title"], len(lines), len(speaker_names))

    # ---------- Steps ----------

    def _read_audio(self) -> np.ndarray:
        self._report("Reading audio file", 0.0)
        samples = load_audio(store.audio_path(self.session))
        store.update(self.session_id, duration=len(samples) / SAMPLE_RATE)
        store.playback_path(self.session)  # Make the exact-seeking playback copy now, not on first play
        self._report("Downloading speaker models (one time)", 0.0)
        diarizer.ensure_models()
        return samples

    def _transcribe(self, samples: np.ndarray) -> list[transcriber.Word]:
        if self.session.get("job") == "speakers":
            saved_words = store.load_words(self.session_id)
            if saved_words is not None:
                return saved_words

        self._report(f"Loading speech model ({transcriber.engine_name()})", 0.0)
        words = transcriber.transcribe(
            samples,
            model_key=self.settings["model"],
            language=self.settings["language"],
            glossary=self.settings["glossary"],
            on_progress=self._on_transcribe_progress,
            on_download=self._on_download_progress,
        )
        store.save_words(self.session_id, words)
        return words

    def _find_speakers(self, samples: np.ndarray) -> list[diarizer.SpeakerTurn]:
        engine = diarizer.engine_name(self.hf_token)
        self._report(
            "Finding who is speaking", TRANSCRIBE_SHARE, f"Loading {engine}. This can sit at one percent for a while."
        )
        return diarizer.diarize(
            samples,
            num_speakers=self.session.get("num_speakers"),
            on_progress=self._on_diarize_progress,
            should_stop=self.should_stop,
            hf_token=self.hf_token,
        )

    def _build_transcript(self, samples, words, turns) -> tuple[list[dict], dict]:
        self._report("Double checking each line's voice", TRANSCRIBE_SHARE + DIARIZE_SHARE)
        lines = aligner.build_lines(words, turns)
        speaker_names, cluster_to_speaker = _number_speakers_by_first_appearance(lines)

        vectors = line_vectors(self.session_id, lines, samples)
        moved = voice_check.refine_by_voice(lines, vectors)
        unsure = voice_check.move_unsure_lines(lines, speakers.unknown_id(speaker_names))
        log.info("%s  voice check moved %d lines, %d left as Unknown", self.session["title"], moved, unsure)

        prints = self._save_speaker_voiceprints(samples, turns, cluster_to_speaker)
        if self.settings["auto_name_voices"]:  # Off by default: a wrong name is worse than no name
            for speaker_id, name in voices.match_speakers(prints, self.settings["voice_match_threshold"]).items():
                speaker_names[speaker_id] = {"name": name, "auto_named": True}

        apply_saved_corrections(lines, self.settings["corrections"])
        used = {line["speaker"] for line in lines}
        return lines, {sid: speaker for sid, speaker in speaker_names.items() if sid in used}

    def _save_speaker_voiceprints(self, samples, turns, cluster_to_speaker) -> dict[str, list[float]]:
        prints = {
            cluster_to_speaker[cluster]: vector
            for cluster, vector in diarizer.voiceprints(samples, turns).items()
            if cluster in cluster_to_speaker
        }
        store.save_voiceprints(self.session_id, prints)
        return prints

    # ---------- Progress ----------

    def _report(self, stage: str, progress: float, detail: str = "", check_stop: bool = True) -> None:
        if check_stop and self.should_stop():
            raise JobStoppedError()
        previous = store.get(self.session_id)
        if (previous["stage"], previous.get("detail")) != (stage, detail):
            log.info("%s  %3d%%  %s %s", self.session["title"], progress * 100, stage, detail)
        store.update(self.session_id, stage=stage, detail=detail, progress=round(progress, 4), updated_at=time.time())

    def _on_download_progress(self, done_bytes: int, total_bytes: int) -> None:
        if total_bytes:
            detail = f"{done_bytes / 1e9:.2f} of {total_bytes / 1e9:.1f} GB ({done_bytes / total_bytes:.0%})"
        else:
            detail = f"{done_bytes / 1e9:.2f} GB"
        self._report("Downloading speech model (one time)", 0.0, detail)

    def _on_transcribe_progress(self, done_seconds: float, total_seconds: float) -> None:
        detail = f"{format_clock(done_seconds)} of {format_clock(total_seconds)} transcribed"
        self._report("Transcribing", done_seconds / total_seconds * TRANSCRIBE_SHARE, detail)

    def _on_diarize_progress(self, share: float) -> None:
        # Called from native code, so it must never raise. should_stop handles stopping there.
        if not self.should_stop():
            progress = TRANSCRIBE_SHARE + share * DIARIZE_SHARE
            self._report("Finding who is speaking", progress, "Grouping voices", check_stop=False)


def line_vectors(session_id: str, lines: list[dict], samples: np.ndarray | None = None) -> dict:
    """Voice embedding per line. Computed when samples are given (or nothing is saved yet), else reused."""
    saved = store.load_line_prints(session_id)
    if saved is None or samples is not None:
        if samples is None:
            samples = load_audio(store.audio_path(store.get(session_id)))
        spans = np.array(voice_check.spans_to_embed(lines), dtype=np.float32).reshape(-1, 2)
        saved = (spans, diarizer.embed_spans(samples, [tuple(span) for span in spans]))
        store.save_line_prints(session_id, *saved)
    return voice_check.vectors_by_line(lines, *saved)


def short_line_vectors(session_id: str, lines: list[dict]) -> dict:
    """Voice embeddings for lines under a second ("Yeah.", "Okay."). Only missing ones are computed,
    so a second Rematch is quick."""
    saved = store.load_line_prints(session_id, kind="short")
    spans, vectors = saved if saved is not None else (np.zeros((0, 2), np.float32), np.zeros((0, 256), np.float32))
    have = voice_check.vectors_by_line(lines, spans, vectors)
    missing = [
        span
        for line, span in zip(lines, voice_check.short_spans(lines), strict=True)
        if span and line["id"] not in have
    ]
    if missing:
        samples = load_audio(store.audio_path(store.get(session_id)))
        new_vectors = diarizer.embed_spans(samples, missing)
        order = np.argsort(np.concatenate([spans[:, 0], np.array(missing, dtype=np.float32)[:, 0]]))
        spans = np.concatenate([spans, np.array(missing, dtype=np.float32)])[order]
        vectors = np.concatenate([vectors, new_vectors])[order]
        store.save_line_prints(session_id, spans, vectors, kind="short")
    return voice_check.vectors_by_line(lines, spans, vectors)


def _number_speakers_by_first_appearance(lines: list[dict]) -> tuple[dict, dict[int, str]]:
    """Diarization cluster ids are arbitrary. Renumber in place so whoever talks
    first is Speaker 1. Returns the speakers and the cluster to speaker id map."""
    cluster_to_speaker: dict[int, str] = {}
    for line in lines:
        cluster = line["speaker"]
        if cluster not in cluster_to_speaker:
            cluster_to_speaker[cluster] = str(len(cluster_to_speaker))
        line["speaker"] = cluster_to_speaker[cluster]
    speaker_names = {
        speaker_id: {"name": speakers.default_name(int(speaker_id)), "auto_named": False}
        for speaker_id in cluster_to_speaker.values()
    }
    return speaker_names, cluster_to_speaker
