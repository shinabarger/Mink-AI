"""The voice library: remembers what each named person sounds like.

When you rename "Speaker 2" to "Harkin", Mink saves Harkin's voice print.
Next month's session recognises Harkin automatically.

Each name keeps one print per session it was learned from, keyed by
"<session_id>:<speaker_id>". That way renaming or merging a speaker later can
cleanly take back exactly what it contributed.
"""

import json

import numpy as np

from .config import VOICES_FILE, write_json_atomic
from .diarizer import normalize

# How much better the best voice must score than the second best.
MATCH_MARGIN = 0.03


def load_library() -> dict:
    if not VOICES_FILE.exists():
        return {}
    return json.loads(VOICES_FILE.read_text())


def list_voices() -> list[dict]:
    return [
        {"name": name, "sessions": len(entry["prints"])}
        for name, entry in sorted(load_library().items(), key=lambda item: item[0].lower())
    ]


def learn(name: str, source_key: str, embedding: list[float]) -> None:
    library = load_library()
    library.setdefault(name, {"prints": {}})["prints"][source_key] = embedding
    write_json_atomic(VOICES_FILE, library)


def forget_source(source_key: str) -> None:
    library = load_library()
    for name in list(library):
        library[name]["prints"].pop(source_key, None)
        if not library[name]["prints"]:
            del library[name]
    write_json_atomic(VOICES_FILE, library)


def delete_voice(name: str) -> dict | None:
    """Forget a voice. Returns what was removed, so it can be put back."""
    library = load_library()
    removed = library.pop(name, None)
    write_json_atomic(VOICES_FILE, library)
    return removed


def restore_voice(name: str, entry: dict) -> None:
    library = load_library()
    library[name] = entry
    write_json_atomic(VOICES_FILE, library)


def match_speakers(prints: dict[str, list[float]], threshold: float) -> dict[str, str]:
    """Return {speaker_id: name} for speakers that sound like someone in the library.

    A match needs to clear the similarity threshold AND beat that speaker's next
    best candidate by a clear margin, so two similar voices are left for you to
    name instead of guessed. Pairs are assigned from most to least similar and
    each saved voice is used at most once per session.
    """
    library = load_library()
    if not library or not prints:
        return {}
    voice_vectors = {name: _average(entry["prints"].values()) for name, entry in library.items()}

    candidates = []
    for speaker_id, speaker_vector in prints.items():
        scores = sorted(
            ((float(np.dot(speaker_vector, vector)), name) for name, vector in voice_vectors.items()),
            reverse=True,
        )
        best_score, best_name = scores[0]
        runner_up = scores[1][0] if len(scores) > 1 else -1.0
        if best_score >= threshold and best_score - runner_up >= MATCH_MARGIN:
            candidates.append((best_score, speaker_id, best_name))

    matches: dict[str, str] = {}
    for _score, speaker_id, name in sorted(candidates, reverse=True):
        if name not in matches.values():
            matches[speaker_id] = name
    return matches


def _average(vectors) -> np.ndarray:
    return normalize(np.mean([np.array(v) for v in vectors], axis=0))
