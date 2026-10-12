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


def export(session: dict, file_format: str, include_timestamps: bool = True) -> tuple[str, str]:
    """Return (file contents, file extension)."""
    if file_format == "srt":
        return _srt(session), "srt"
    if file_format == "md":
        return _markdown(session, include_timestamps), "md"
    return _plain_text(session, include_timestamps), "txt"


def _header_lines(session: dict) -> list[str]:
    names = sorted({speaker["name"] for speaker in session["speakers"].values()})
    recorded = time.strftime("%B %d, %Y", time.localtime(session["created_at"]))
    return [
        f"Session: {session['title']}",
        f"Imported: {recorded}",
        f"Length: {format_clock(session.get('duration') or 0)}",
        f"Speakers: {', '.join(names)}",
    ]


def _name(session: dict, line: dict) -> str:
    return session["speakers"].get(line["speaker"], {}).get("name", "Unknown")


def _plain_text(session: dict, include_timestamps: bool) -> str:
    out = _header_lines(session) + [""]
    for line in merged_lines(session):
        stamp = f"[{format_clock(line['start'])}] " if include_timestamps else ""
        out.append(f"{stamp}{_name(session, line)}: {line['text']}")
    return "\n".join(out) + "\n"


def _markdown(session: dict, include_timestamps: bool) -> str:
    out = [f"# {session['title']}", ""] + [f"- {item}" for item in _header_lines(session)[1:]] + [""]
    for line in merged_lines(session):
        stamp = f" `{format_clock(line['start'])}`" if include_timestamps else ""
        out.append(f"**{_name(session, line)}**{stamp}: {line['text']}")
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
            f"{number}\n{srt_time(line['start'])} --> {srt_time(line['end'])}\n{_name(session, line)}: {line['text']}\n"
        )
    return "\n".join(blocks)
