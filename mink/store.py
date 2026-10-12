"""Session storage on disk. One folder per recording:

~/Mink/sessions/<id>/session.json       transcript, speakers, job status
~/Mink/sessions/<id>/audio.<ext>        the original recording
~/Mink/sessions/<id>/playback.m4a       exact-seeking copy (MP3 and similar only)
~/Mink/sessions/<id>/words.json         Whisper's raw words, for redoing speakers
~/Mink/sessions/<id>/voiceprints.json   one voice embedding per speaker
~/Mink/sessions/<id>/line_prints.npz    one voice embedding per line
~/Mink/sessions/<id>/previous/          the version from before the last redo, so a redo can be undone
"""

import json
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from .audio import NEEDS_PLAYBACK_COPY, make_playback_copy
from .config import SESSIONS_DIR, write_json_atomic
from .transcriber import Word

SUMMARY_FIELDS = ("id", "title", "created_at", "duration", "status", "progress", "stage", "detail")

_session_lock = threading.RLock()
_playback_lock = threading.Lock()


class NotFoundError(Exception):
    pass


# ---------- Sessions ----------


def create_session(title: str, audio_extension: str, num_speakers: int | None) -> dict:
    session_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    _folder(session_id).mkdir(parents=True)
    session = {
        "id": session_id,
        "title": title,
        "created_at": time.time(),
        "audio_file": "audio" + audio_extension,
        "num_speakers": num_speakers,
        "duration": None,
        "status": "queued",
        "stage": "Waiting to start",
        "progress": 0.0,
        "error": None,
        "speakers": {},
        "lines": [],
    }
    save(session)
    return session


def get(session_id: str) -> dict:
    path = _folder(session_id) / "session.json"
    if not path.exists():
        raise NotFoundError(session_id)
    with _session_lock:
        return json.loads(path.read_text())


def save(session: dict) -> None:
    with _session_lock:
        write_json_atomic(_folder(session["id"]) / "session.json", session)


def update(session_id: str, **fields) -> dict:
    with transaction(session_id) as session:
        session.update(fields)
    return session


@contextmanager
def transaction(session_id: str) -> Iterator[dict]:
    """Load a session, let the caller change it, then save it, all under one lock."""
    with _session_lock:
        session = get(session_id)
        yield session
        save(session)


def list_sessions() -> list[dict]:
    summaries = []
    for path in SESSIONS_DIR.glob("*/session.json"):
        session = json.loads(path.read_text())
        summaries.append({field: session.get(field) for field in SUMMARY_FIELDS})
    return sorted(summaries, key=lambda summary: summary["created_at"], reverse=True)


def delete(session_id: str) -> None:
    with _session_lock:
        get(session_id)  # Raises if it doesn't exist
        shutil.rmtree(_folder(session_id))


# ---------- Audio ----------


def audio_path(session: dict) -> Path:
    return _folder(session["id"]) / session["audio_file"]


def playback_path(session: dict) -> Path:
    """The file the browser plays. MP3s get an exact-seeking m4a copy, made once and reused."""
    original = audio_path(session)
    if original.suffix.lower() not in NEEDS_PLAYBACK_COPY:
        return original
    playback_copy = original.with_name("playback.m4a")
    with _playback_lock:
        if not playback_copy.exists():
            make_playback_copy(original, playback_copy)
    return playback_copy


# ---------- Analysis artifacts ----------


def save_words(session_id: str, words: list[Word]) -> None:
    """Keep Whisper's raw words so speaker labels can be redone without transcribing again."""
    rows = [[word.start, word.end, word.text] for word in words]
    write_json_atomic(_folder(session_id) / "words.json", rows)


def load_words(session_id: str) -> list[Word] | None:
    path = _folder(session_id) / "words.json"
    if path.exists():
        rows = json.loads(path.read_text())
    else:
        # Older recordings: rebuild the word list from the lines, if every line still has its words.
        lines = get(session_id)["lines"]
        if not lines or not all(line.get("words") for line in lines):
            return None
        rows = [word for line in lines for word in line["words"]]
    return [Word(start, end, text) for start, end, text in rows]


def has_words(session_id: str) -> bool:
    return load_words(session_id) is not None


def save_voiceprints(session_id: str, prints: dict[str, list[float]]) -> None:
    write_json_atomic(_folder(session_id) / "voiceprints.json", prints)


def load_voiceprints(session_id: str) -> dict[str, list[float]]:
    path = _folder(session_id) / "voiceprints.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_line_prints(session_id: str, spans: np.ndarray, vectors: np.ndarray) -> None:
    """Per line voice embeddings, stored with their times so edits and splits can still find them."""
    np.savez_compressed(
        _folder(session_id) / "line_prints.npz",
        spans=np.asarray(spans, dtype=np.float32).reshape(-1, 2),
        vectors=np.asarray(vectors, dtype=np.float16),
    )


def load_line_prints(session_id: str) -> tuple[np.ndarray, np.ndarray] | None:
    path = _folder(session_id) / "line_prints.npz"
    if not path.exists():
        return None
    data = np.load(path)
    return data["spans"], data["vectors"].astype(np.float32)


# ---------- Previous version (undo for redo) ----------

# Everything a redo replaces. Speakers and lines live in session.json and are swapped separately.
ANALYSIS_FILES = ("words.json", "voiceprints.json", "line_prints.npz")


def save_previous_version(session_id: str, label: str) -> None:
    """Keep the current transcript before a redo replaces it. Only the most recent one is kept."""
    with transaction(session_id) as session:
        if not session.get("lines"):
            return
        backup = _folder(session_id) / "previous"
        shutil.rmtree(backup, ignore_errors=True)
        backup.mkdir()
        for name in ANALYSIS_FILES:
            if (_folder(session_id) / name).exists():
                shutil.copy2(_folder(session_id) / name, backup / name)
        write_json_atomic(backup / "transcript.json", {"speakers": session["speakers"], "lines": session["lines"]})
        session["previous_version"] = {"label": label, "saved_at": time.time()}


def swap_previous_version(session_id: str) -> dict:
    """Switch the current transcript with the saved one. Swapping again switches back."""
    with transaction(session_id) as session:
        if not session.get("previous_version"):
            raise ValueError("There is no earlier version of this transcript to switch to.")
        folder, backup = _folder(session_id), _folder(session_id) / "previous"
        for name in ANALYSIS_FILES:
            _swap_files(folder / name, backup / name)
        saved = json.loads((backup / "transcript.json").read_text())
        write_json_atomic(backup / "transcript.json", {"speakers": session["speakers"], "lines": session["lines"]})
        session.update(speakers=saved["speakers"], lines=saved["lines"])
        session["previous_version"]["swapped"] = not session["previous_version"].get("swapped", False)
    return session


def discard_previous_version(session_id: str) -> dict:
    shutil.rmtree(_folder(session_id) / "previous", ignore_errors=True)
    with transaction(session_id) as session:
        session.pop("previous_version", None)
    return session


def _swap_files(a: Path, b: Path) -> None:
    temporary = a.with_suffix(a.suffix + ".swap")
    if a.exists():
        a.rename(temporary)
    if b.exists():
        b.rename(a)
    if temporary.exists():
        temporary.rename(b)


def _folder(session_id: str) -> Path:
    return SESSIONS_DIR / session_id
