"""Export a transcript as text, Markdown, or subtitles."""

import time


def format_clock(seconds: float, always_hours: bool = True) -> str:
    seconds = int(seconds or 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if always_hours or hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def merged_lines(session: dict) -> list[dict]:
    """Join back to back lines from the same speaker. Fewer speaker labels means
    fewer tokens when you paste a 5 hour session into Claude."""
    merged: list[dict] = []
    for line in session["lines"]:
        if merged and merged[-1]["speaker"] == line["speaker"]:
            merged[-1]["text"] += " " + line["text"]
            merged[-1]["end"] = line["end"]
        else:
            merged.append(dict(line))
    return merged


def export(
    session: dict, file_format: str, include_timestamps: bool = True, include_speakers: bool = True
) -> tuple[str, str]:
    """Return (file contents, file extension)."""
    if not include_speakers:
        session = _without_speakers(session)
    if file_format == "srt":
        return _srt(session), "srt"
    if file_format == "md":
        return _markdown(session, include_timestamps), "md"
    return _plain_text(session, include_timestamps), "txt"


PARAGRAPH_PAUSE_SECONDS = 1.5


def _without_speakers(session: dict) -> dict:
    """No names: lines join into paragraphs that break at pauses instead of at speaker changes."""
    lines, paragraph, previous_end = [], 0, None
    for line in session["lines"]:
        if previous_end is not None and line["start"] - previous_end > PARAGRAPH_PAUSE_SECONDS:
            paragraph += 1
        lines.append({**line, "speaker": str(paragraph)})
        previous_end = line["end"]
    nameless = {str(number): {"name": ""} for number in range(paragraph + 1)}
    return {**session, "lines": lines, "speakers": nameless}


def _header_lines(session: dict) -> list[str]:
    names = sorted({speaker["name"] for speaker in session["speakers"].values()} - {""})
    recorded = time.strftime("%B %d, %Y", time.localtime(session["created_at"]))
    header = [
        f"Session: {session['title']}",
        f"Imported: {recorded}",
        f"Length: {format_clock(session.get('duration') or 0)}",
    ]
    return header + ([f"Speakers: {', '.join(names)}"] if names else [])


def _name(session: dict, line: dict) -> str:
    return session["speakers"].get(line["speaker"], {}).get("name", "Unknown")


def _label(session: dict, line: dict, separator: str) -> str:
    name = _name(session, line)
    return f"{name}{separator}" if name else ""


def _plain_text(session: dict, include_timestamps: bool) -> str:
    out = _header_lines(session) + [""]
    for line in merged_lines(session):
        stamp = f"[{format_clock(line['start'])}] " if include_timestamps else ""
        out.append(f"{stamp}{_label(session, line, ': ')}{line['text']}")
    return "\n".join(out) + "\n"


def _markdown(session: dict, include_timestamps: bool) -> str:
    out = [f"# {session['title']}", ""] + [f"- {item}" for item in _header_lines(session)[1:]] + [""]
    for line in merged_lines(session):
        stamp = f" `{format_clock(line['start'])}`" if include_timestamps else ""
        name = _name(session, line)
        out.append(f"**{name}**{stamp}: {line['text']}" if name else f"{stamp.strip()} {line['text']}".strip())
        out.append("")
    return "\n".join(out)


def _srt(session: dict) -> str:
    def srt_time(seconds: float) -> str:
        millis = int(round((seconds or 0) * 1000))
        hours, millis = divmod(millis, 3_600_000)
        minutes, millis = divmod(millis, 60_000)
        secs, millis = divmod(millis, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    blocks = []
    for number, line in enumerate(session["lines"], start=1):
        blocks.append(
            f"{number}\n{srt_time(line['start'])} --> {srt_time(line['end'])}\n"
            f"{_label(session, line, ': ')}{line['text']}\n"
        )
    return "\n".join(blocks)
