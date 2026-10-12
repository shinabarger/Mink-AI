"""Combine Whisper words with speaker turns into transcript lines."""

import bisect
import re

from .diarizer import SpeakerTurn
from .transcriber import Word

NEW_LINE_AFTER_PAUSE_SECONDS = 2.0
SENTENCE_END = re.compile(r"[.!?][\"')\]]*$")
# A pause this long ends a "sentence" for voting, even without punctuation.
SENTENCE_BREAK_PAUSE_SECONDS = 0.8


def build_lines(words: list[Word], turns: list[SpeakerTurn]) -> list[dict]:
    """Group words into lines: one sentence per line, and a new line whenever
    the speaker changes or there is a long pause."""
    speakers = _smooth_by_sentence(words, _speaker_for_each_word(words, turns))
    lines: list[dict] = []
    current: dict | None = None

    for word, speaker in zip(words, speakers, strict=True):
        if current is None or _starts_new_line(current, word, speaker):
            current = {"speaker": speaker, "start": word.start, "end": word.end, "text": "", "words": []}
            lines.append(current)
        current["text"] += word.text
        current["words"].append([round(word.start, 2), round(word.end, 2), word.text])
        current["end"] = word.end

    for index, line in enumerate(lines):
        line["id"] = index
        line["text"] = line["text"].strip()
        line["start"] = round(line["start"], 2)
        line["end"] = round(line["end"], 2)
    return [line for line in lines if line["text"]]


def _starts_new_line(line: dict, word: Word, speaker: int) -> bool:
    if speaker != line["speaker"]:
        return True
    if word.start - line["end"] > NEW_LINE_AFTER_PAUSE_SECONDS:
        return True
    # One sentence per line, so a wrong voice is a one click fix.
    return bool(SENTENCE_END.search(line["text"].strip()))


def _smooth_by_sentence(words: list[Word], speakers: list[int]) -> list[int]:
    """Give each sentence to the one speaker who said most of it.

    Whisper's word timings and the speaker model's turn boundaries rarely line
    up exactly, so a speaker change often lands a word or two early or late
    ("She had | something figured out"). People almost always finish their own
    sentence, so a majority vote per sentence puts the boundary in the right
    place. Real mid-sentence interruptions can still be split by hand (Enter).
    """
    smoothed = list(speakers)
    for start, end in _sentence_ranges(words):
        talk_time: dict[int, float] = {}
        for index in range(start, end):
            duration = max(words[index].end - words[index].start, 0.01)
            talk_time[speakers[index]] = talk_time.get(speakers[index], 0.0) + duration
        winner = max(talk_time, key=talk_time.get)
        smoothed[start:end] = [winner] * (end - start)
    return smoothed


def _sentence_ranges(words: list[Word]) -> list[tuple[int, int]]:
    """[start, end) word index ranges, split at sentence punctuation or long pauses."""
    ranges, start = [], 0
    for index, word in enumerate(words):
        is_last = index == len(words) - 1
        ends_sentence = bool(SENTENCE_END.search(word.text.strip()))
        pause_after = not is_last and words[index + 1].start - word.end > SENTENCE_BREAK_PAUSE_SECONDS
        if is_last or ends_sentence or pause_after:
            ranges.append((start, index + 1))
            start = index + 1
    return ranges


def _speaker_for_each_word(words: list[Word], turns: list[SpeakerTurn]) -> list[int]:
    """Pick the speaker whose turn overlaps a word the most. Words that fall in a
    gap between turns go to the nearest turn, or stay with the previous speaker."""
    if not turns:
        return [0] * len(words)
    starts = [turn.start for turn in turns]
    result = []
    previous = turns[0].speaker
    for word in words:
        speaker = _best_overlap(word, turns, starts)
        if speaker is None:
            speaker = _nearest_turn(word, turns, starts, max_distance=1.0)
        if speaker is None:
            speaker = previous
        result.append(speaker)
        previous = speaker
    return result


def _best_overlap(word: Word, turns: list[SpeakerTurn], starts: list[float]) -> int | None:
    best_speaker, best_overlap = None, 0.0
    # Turns can overlap (people talk over each other), so look back a few turns.
    right = bisect.bisect_right(starts, word.end)
    for turn in turns[max(0, right - 8) : right]:
        overlap = min(word.end, turn.end) - max(word.start, turn.start)
        if overlap > best_overlap:
            best_speaker, best_overlap = turn.speaker, overlap
    return best_speaker


def _nearest_turn(word: Word, turns: list[SpeakerTurn], starts: list[float], max_distance: float) -> int | None:
    middle = (word.start + word.end) / 2
    index = bisect.bisect_left(starts, middle)
    candidates = turns[max(0, index - 1) : index + 1]
    best = min(candidates, key=lambda t: _distance(middle, t), default=None)
    if best is None or _distance(middle, best) > max_distance:
        return None
    return best.speaker


def _distance(point: float, turn: SpeakerTurn) -> float:
    if turn.start <= point <= turn.end:
        return 0.0
    return min(abs(point - turn.start), abs(point - turn.end))
