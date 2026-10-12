"""Helpers for the speakers in a session, plus keeping the voice library in sync.

A session's speakers are a dict of {speaker_id: {"name": ..., "auto_named": ...}}
and every line points at one speaker_id.
"""

from . import store, voices

DEFAULT_NAME_PREFIX = "Speaker "
# Where lines go when Mink isn't sure who said them. Never learned as a voice.
UNKNOWN_NAME = "Unknown"


def default_name(index: int) -> str:
    """Speaker 1, Speaker 2, ... for a zero based index."""
    return f"{DEFAULT_NAME_PREFIX}{index + 1}"


def is_placeholder(name: str) -> bool:
    return name.startswith(DEFAULT_NAME_PREFIX) or name == UNKNOWN_NAME


def add(speakers: dict, name: str) -> str:
    new_id = str(max((int(speaker_id) for speaker_id in speakers), default=-1) + 1)
    speakers[new_id] = {"name": name, "auto_named": False}
    return new_id


def find_by_name(speakers: dict, name: str, exclude: str | None = None) -> str | None:
    wanted = name.strip().lower()
    for speaker_id, speaker in speakers.items():
        if speaker_id != exclude and speaker["name"].lower() == wanted:
            return speaker_id
    return None


def unknown_id(speakers: dict) -> str:
    """Find or add the Unknown speaker."""
    return find_by_name(speakers, UNKNOWN_NAME) or add(speakers, UNKNOWN_NAME)


def next_default_name(speakers: dict) -> str:
    """The next unused "Speaker N" name."""
    numbers = [
        int(speaker["name"].removeprefix(DEFAULT_NAME_PREFIX))
        for speaker in speakers.values()
        if speaker["name"].removeprefix(DEFAULT_NAME_PREFIX).isdigit()
    ]
    return default_name(max(numbers, default=len(speakers)))


def merge(session: dict, source: str, target: str) -> None:
    """Move every line from one speaker to another and remove the first."""
    for line in session["lines"]:
        if line["speaker"] == source:
            line["speaker"] = target
    del session["speakers"][source]


def drop_unused(session: dict) -> None:
    used = {line["speaker"] for line in session["lines"]}
    session["speakers"] = {sid: speaker for sid, speaker in session["speakers"].items() if sid in used}


def sync_voice_library(session: dict) -> None:
    """Teach the library names you typed yourself. Auto names are never learned from,
    so one wrong guess can't reinforce itself session after session."""
    prints = store.load_voiceprints(session["id"])
    for speaker_id, speaker in session["speakers"].items():
        source = voice_source(session["id"], speaker_id)
        voices.forget_source(source)
        named_by_you = not is_placeholder(speaker["name"]) and not speaker.get("auto_named")
        if named_by_you and speaker_id in prints:
            voices.learn(speaker["name"], source, prints[speaker_id])


def forget_session_voices(session: dict) -> None:
    """Remove everything this session taught the voice library."""
    for speaker_id in session["speakers"]:
        voices.forget_source(voice_source(session["id"], speaker_id))


def voice_source(session_id: str, speaker_id: str) -> str:
    return f"{session_id}:{speaker_id}"
