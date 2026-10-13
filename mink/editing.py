"""Changes you make to a finished transcript: names, lines, text, and undo."""

from . import jobs, pipeline, speakers, store, timing, voice_check
from .corrections import replace_in_lines


def rename_speaker(session_id: str, speaker_id: str, new_name: str) -> dict:
    """Rename a speaker everywhere. If another speaker already has that name,
    the two are merged into one (handy when one person was split in two)."""
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("Name cannot be empty.")
    with store.transaction(session_id) as session:
        if speaker_id not in session["speakers"]:
            raise store.NotFoundError(speaker_id)
        existing = speakers.find_by_name(session["speakers"], new_name, exclude=speaker_id)
        if existing is None:
            session["speakers"][speaker_id].update(name=new_name, auto_named=False)
        else:
            speakers.merge(session, source=speaker_id, target=existing)
    speakers.sync_voice_library(session)
    return session


def set_line_speaker(session_id: str, line_id: int, speaker_id: str | None, new_speaker_name: str | None) -> dict:
    """Give one line to a different speaker, or to a brand new one by name."""
    return set_lines_speaker(session_id, [line_id], speaker_id, new_speaker_name)


def set_lines_speaker(
    session_id: str, line_ids: list[int], speaker_id: str | None, new_speaker_name: str | None
) -> dict:
    """Give a block of lines to a different speaker, or to a brand new one by name."""
    with store.transaction(session_id) as session:
        lines = [_find_line(session, line_id) for line_id in line_ids]
        if new_speaker_name:
            name = new_speaker_name.strip()
            speaker_id = speakers.find_by_name(session["speakers"], name) or speakers.add(session["speakers"], name)
        if speaker_id not in session["speakers"]:
            raise store.NotFoundError(speaker_id)
        for line in lines:
            line["speaker"] = speaker_id
            line["confirmed"] = True  # You said who this is, so it can teach "Rematch speakers"
            line["unsure"] = False
    return session


def split_line(session_id: str, line_id: int, cuts: list[int], text: str | None = None) -> tuple[dict, int]:
    """Split a line at character offsets (Enter in the transcript).

    The piece that starts at the first cut gets its own new voice, ready to rename
    or reassign. Returns the session and that piece's line id.
    """
    with store.transaction(session_id) as session:
        line = _find_line(session, line_id)
        if text is not None:
            line["text"] = text.strip()
        pieces = timing.split(line, cuts)
        if len(pieces) < 2:
            raise ValueError("Put the cursor in the middle of a line, or select some words, then press Enter.")

        next_id = max(existing["id"] for existing in session["lines"]) + 1
        first_cut = min(cuts)
        new_piece_id = line_id
        for index, piece in enumerate(pieces):
            piece["id"] = line_id if index == 0 else next_id + index - 1
            if piece.pop("cut_at") == first_cut:
                new_piece_id = piece["id"]

        new_voice = speakers.add(session["speakers"], speakers.next_default_name(session["speakers"]))
        next(piece for piece in pieces if piece["id"] == new_piece_id)["speaker"] = new_voice
        position = session["lines"].index(line)
        session["lines"][position : position + 1] = pieces
    return session, new_piece_id


def set_line_text(session_id: str, line_id: int, text: str) -> dict:
    with store.transaction(session_id) as session:
        _find_line(session, line_id)["text"] = text.strip()
    return session


def replace_text(session_id: str, find: str, replace: str, match_case: bool, whole_word: bool) -> tuple[int, dict]:
    with store.transaction(session_id) as session:
        count = replace_in_lines(session["lines"], find, replace, match_case, whole_word)
    return count, session


def restore_transcript(session_id: str, saved_speakers: dict, lines: list[dict]) -> dict:
    """Put back an earlier version of the speakers and lines (Undo)."""
    with store.transaction(session_id) as session:
        session.update(speakers=saved_speakers, lines=lines)
    speakers.sync_voice_library(session)
    return session


def switch_to_previous_version(session_id: str) -> dict:
    """Undo a redo: switch back to the transcript from before it (and forward again)."""
    if jobs.is_running(session_id):
        raise ValueError("This recording is still being processed.")
    speakers.forget_session_voices(store.get(session_id))
    session = store.swap_previous_version(session_id)
    speakers.sync_voice_library(session)
    return session


def rename_session(session_id: str, title: str) -> dict:
    return store.update(session_id, title=title.strip() or "Untitled session")


def relabel_from_fixes(session_id: str) -> tuple[dict, int, int]:
    """Re-sort every line using the lines you assigned by hand as voice samples.
    Returns (session, lines moved, number of people taught)."""
    if jobs.is_running(session_id):
        raise ValueError("This recording is still being processed.")
    with store.transaction(session_id) as session:
        lines = session["lines"]
        if not any(line.get("confirmed") for line in lines):
            raise ValueError(
                "First pick the right speaker on a few lines for each person (click a name on a line), then try again."
            )
        vectors = pipeline.line_vectors(session_id, lines)
        moved, people_taught = voice_check.relabel_from_fixes(lines, vectors)
        voice_check.move_unsure_lines(lines, speakers.unknown_id(session["speakers"]))
        speakers.drop_unused(session)
    return session, moved, people_taught


def _find_line(session: dict, line_id: int) -> dict:
    for line in session["lines"]:
        if line["id"] == line_id:
            return line
    raise store.NotFoundError(f"line {line_id}")
