import os
import tempfile

# Point Mink at a throwaway data folder before any mink module reads its config.
os.environ["MINK_HOME"] = tempfile.mkdtemp(prefix="mink-test-")

import pytest  # noqa: E402

from mink import store  # noqa: E402
from mink.config import VOICES_FILE, ensure_dirs  # noqa: E402
from mink.diarizer import SpeakerTurn  # noqa: E402
from mink.transcriber import Word  # noqa: E402

ensure_dirs()


def make_words(*items: tuple[float, float, str]) -> list[Word]:
    return [Word(start, end, text) for start, end, text in items]


def make_turns(*items: tuple[float, float, int]) -> list[SpeakerTurn]:
    return [SpeakerTurn(start, end, speaker) for start, end, speaker in items]


def make_line(line_id: int, speaker: str, text: str, start: float = 0.0, end: float = 1.0) -> dict:
    return {"id": line_id, "speaker": speaker, "start": start, "end": end, "text": text}


@pytest.fixture
def new_session():
    """Create a finished session on disk from lines and speaker names."""

    def create(lines: list[dict], names: dict[str, str], voiceprints: dict | None = None) -> str:
        session = store.create_session("Test", ".m4a", None)
        session.update(
            status="done",
            duration=3700,
            lines=lines,
            speakers={speaker_id: {"name": name, "auto_named": False} for speaker_id, name in names.items()},
        )
        store.save(session)
        store.save_voiceprints(session["id"], voiceprints or {})
        return session["id"]

    return create


@pytest.fixture(autouse=True)
def empty_voice_library():
    """Every test starts with no saved voices."""
    VOICES_FILE.unlink(missing_ok=True)
