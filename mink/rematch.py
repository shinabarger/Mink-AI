"""Rematch speakers: re-sort every line using the lines you fixed by hand.

Runs on the background worker because the context check can take several minutes
on a long recording. The transcript is only saved at the end, so stopping a
rematch leaves everything exactly as it was.
"""

import logging
import time
from collections.abc import Callable

from . import context_check, diarizer, model_download, pipeline, speakers, store, voice_check
from .config import load_settings
from .pipeline import JobStoppedError

log = logging.getLogger("mink")

VOICE_SHARE = 0.2  # Rough share of the progress bar for the voice step; the context check gets the rest


def check_ready(session: dict) -> None:
    if not any(line.get("confirmed") for line in session["lines"]):
        raise ValueError(
            "First pick the right speaker on a few lines for each person (click a name on a line), then try again."
        )


class RematchJob:
    def __init__(self, session_id: str, should_stop: Callable[[], bool]):
        self.session_id = session_id
        self.should_stop = should_stop
        self.settings = load_settings()

    def run(self) -> None:
        store.update(self.session_id, status="processing", started_at=time.time())
        session = store.get(self.session_id)
        lines = session["lines"]
        result = self._match_voices(session)
        ai_changes = self._check_context(session, result.close_calls)
        speakers.drop_unused(session)
        summary = _summary(result, ai_changes)
        store.update(
            self.session_id,
            lines=lines,
            speakers=session["speakers"],
            rematch_summary=summary,
            status="done",
            stage="Done",
            detail="",
            progress=1.0,
        )
        log.info("%s  rematch: %s", session["title"], summary)

    def _match_voices(self, session: dict) -> voice_check.RelabelResult:
        self._report("Downloading speaker models (one time)", 0.01)
        diarizer.ensure_models()
        self._report("Comparing every line's voice", 0.02)
        vectors = pipeline.line_vectors(self.session_id, session["lines"])
        self._report("Listening to short lines", 0.08)
        short_vectors = pipeline.short_line_vectors(self.session_id, session["lines"])
        unknown = speakers.find_by_name(session["speakers"], speakers.UNKNOWN_NAME)
        return voice_check.relabel_from_fixes(session["lines"], vectors, unknown, short_vectors)

    def _check_context(self, session: dict, close_calls: dict[int, list[str]]) -> int | None:
        """Returns how many lines the context check changed, or None if it didn't run."""
        if not close_calls or not self.settings["context_check"] or not context_check.is_available():
            return None
        try:
            self._report("Downloading the context model (one time)", VOICE_SHARE, context_check.MODEL_LABEL)
            path = model_download.download(context_check.MODEL_ID, self._on_download)
            self._report("Loading the context model", VOICE_SHARE, context_check.MODEL_LABEL)
            complete = context_check.load_model(path)
        except JobStoppedError:
            raise
        except Exception:
            log.exception("Context check skipped: the model couldn't load")
            return None
        names = {speaker_id: speaker["name"] for speaker_id, speaker in session["speakers"].items()}
        return context_check.check_close_calls(
            session["lines"], names, close_calls, self.settings["players"], complete, self._on_context_progress
        )

    # ---------- Progress ----------

    def _report(self, stage: str, progress: float, detail: str = "") -> None:
        if self.should_stop():
            raise JobStoppedError()
        store.update(self.session_id, stage=stage, detail=detail, progress=round(progress, 4), updated_at=time.time())

    def _on_download(self, done_bytes: int, total_bytes: int) -> None:
        total = f" of {total_bytes / 1e9:.1f}" if total_bytes else ""
        detail = f"{done_bytes / 1e9:.2f}{total} GB"
        self._report("Downloading the context model (one time)", VOICE_SHARE, detail)

    def _on_context_progress(self, done: int, total: int) -> None:
        progress = VOICE_SHARE + (1 - VOICE_SHARE) * done / total
        self._report("Checking close calls against the conversation", progress, f"{done} of {total} passages")


def _summary(result: voice_check.RelabelResult, ai_changes: int | None) -> str:
    lines = "1 line" if result.moved == 1 else f"{result.moved} lines"
    people = "1 person" if result.people_taught == 1 else f"{result.people_taught} people"
    text = f"Moved {lines} by voice, using your fixes for {people}"
    if ai_changes is None:
        return text + "."
    more = "1 more line" if ai_changes == 1 else f"{ai_changes} more lines"
    return text + f", and {more} from the conversation."
