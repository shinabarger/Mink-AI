"""Map a character position in a line's text to a time in the audio.

Each line keeps Whisper's words as [start, end, text]. While the line text still
matches those words exactly we use real word times. Once the text has been
edited we fall back to spreading the line's time evenly across its characters,
which is close enough to land within a second or two.
"""


def word_spans(line: dict) -> list[dict] | None:
    """Character range and time of every word, or None if the text was edited."""
    words = line.get("words") or []
    if not words:
        return None
    joined = "".join(text for _start, _end, text in words)
    if joined.strip() != line["text"]:
        return None
    lead = len(joined) - len(joined.lstrip())
    spans, position = [], 0
    for start, end, text in words:
        word_start = position + len(text) - len(text.lstrip()) - lead  # Skip the word's leading space
        spans.append({"from": word_start, "to": position + len(text) - lead, "start": start, "end": end, "text": text})
        position += len(text)
    return spans


def time_at(line: dict, offset: int) -> float:
    spans = word_spans(line)
    if spans:
        word = next((span for span in spans if span["to"] > offset), spans[-1])
        return word["start"]
    share = offset / max(len(line["text"]), 1)
    return round(line["start"] + (line["end"] - line["start"]) * share, 2)


def split(line: dict, cuts: list[int]) -> list[dict]:
    """Split a line at character offsets. Returns the new pieces (empty pieces dropped),
    each with its own text, start, end and words. Pieces keep the original speaker."""
    text = line["text"]
    bounds = [0, *sorted({c for c in cuts if 0 < c < len(text)}), len(text)]
    spans = word_spans(line)
    pieces = []
    for low, high in zip(bounds, bounds[1:], strict=False):
        piece_text = text[low:high].strip()
        if not piece_text:
            continue
        piece = {"speaker": line["speaker"], "text": piece_text, "cut_at": low}
        if spans:
            # A word belongs to the piece where it starts.
            inside = [s for s in spans if low <= s["from"] < high]
            piece["words"] = [[s["start"], s["end"], s["text"]] for s in inside]
            piece["start"] = inside[0]["start"] if inside else time_at(line, low)
            piece["end"] = inside[-1]["end"] if inside else time_at(line, high)
        else:
            piece["start"], piece["end"] = time_at(line, low), time_at(line, high)
        pieces.append(piece)
    return pieces
