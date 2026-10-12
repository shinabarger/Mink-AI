from mink import exporter, timing
from mink.corrections import apply_saved_corrections, replace_in_lines
from mink.transcriber import build_prompt


def test_replace_matches_whole_words_ignoring_case():
    lines = [{"text": "Philip Drusk and bald waves. drusk and bald again"}, {"text": "Druskandbald stays"}]

    count = replace_in_lines(lines, "Drusk and bald", "Druskenvald")

    assert count == 2
    assert lines[0]["text"] == "Philip Druskenvald waves. Druskenvald again"
    assert lines[1]["text"] == "Druskandbald stays"


def test_saved_corrections_apply_to_new_transcripts():
    lines = [{"text": "the eldritch blast hits"}]
    saved = [{"find": "eldritch blast", "replace": "Eldritch Blast", "match_case": False, "whole_word": True}]

    apply_saved_corrections(lines, saved)

    assert lines[0]["text"] == "the Eldritch Blast hits"


def test_glossary_becomes_a_whisper_prompt():
    assert build_prompt([]) is None
    assert "Philip Druskenvald" in build_prompt(["Philip Druskenvald", " "])


def test_split_keeps_real_word_times():
    line = {
        "speaker": "0",
        "start": 54.0,
        "end": 57.0,
        "text": "was forced to watch",
        "words": [[54.0, 54.3, " was"], [54.4, 54.9, " forced"], [55.0, 55.2, " to"], [55.3, 57.0, " watch"]],
    }

    assert timing.time_at(line, line["text"].index("forced")) == 54.4
    pieces = timing.split(line, [4, 13])  # Select "forced to"
    assert [piece["text"] for piece in pieces] == ["was", "forced to", "watch"]
    assert (pieces[1]["start"], pieces[1]["end"]) == (54.4, 55.2)


def test_export_joins_back_to_back_lines_from_one_speaker():
    session = {
        "title": "Session 12",
        "created_at": 0,
        "duration": 302,
        "speakers": {"0": {"name": "Harkin"}, "1": {"name": "Terran"}},
        "lines": [
            {"id": 0, "speaker": "0", "start": 302, "end": 305, "text": "Spell save DC is 13."},
            {"id": 1, "speaker": "0", "start": 306, "end": 307, "text": "Good check though."},
            {"id": 2, "speaker": "1", "start": 322, "end": 323, "text": "Awesome."},
        ],
    }

    text, extension = exporter.export(session, "txt")
    without_timestamps, _ = exporter.export(session, "txt", include_timestamps=False)
    subtitles, _ = exporter.export(session, "srt")

    assert extension == "txt"
    assert "[00:05:02] Harkin: Spell save DC is 13. Good check though." in text
    assert "Harkin: Spell save DC" in without_timestamps and "[00:" not in without_timestamps
    assert "00:05:02,000 --> 00:05:05,000" in subtitles
