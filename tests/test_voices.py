import numpy as np

from mink import voice_check, voices


def test_library_match_needs_a_clear_winner():
    voices.learn("Harkin", "old-session:0", [1.0, 0.0])
    voices.learn("Terran", "old-session:1", [0.0, 1.0])

    matches = voices.match_speakers({"0": [0.99, 0.14], "1": [0.7, 0.7]}, threshold=0.75)

    assert matches == {"0": "Harkin"}  # Speaker 1 sounds equally like both, so it stays unnamed


def test_relabel_uses_confirmed_lines_and_never_moves_them():
    lines = [
        {"id": 0, "speaker": "0", "confirmed": True},
        {"id": 1, "speaker": "1", "confirmed": True},
        {"id": 2, "speaker": "0"},  # Sounds like speaker 1
        {"id": 3, "speaker": "0"},  # Too short to judge, so no vector
    ]
    vectors = {0: np.array([1.0, 0.0]), 1: np.array([0.0, 1.0]), 2: np.array([0.1, 0.99])}

    moved, people_taught = voice_check.relabel_from_fixes(lines, vectors)

    assert (moved, people_taught) == (1, 2)
    assert [line["speaker"] for line in lines] == ["0", "1", "1", "0"]


def test_unsure_lines_go_to_unknown_instead_of_a_guess():
    lines = [{"id": 0, "speaker": "0", "unsure": True}, {"id": 1, "speaker": "0", "unsure": True, "confirmed": True}]

    moved = voice_check.move_unsure_lines(lines, unknown_speaker="9")

    assert moved == 1
    assert [line["speaker"] for line in lines] == ["9", "0"]


def test_rematch_guesses_unknown_lines_instead_of_leaving_them():
    lines = [
        {"id": 0, "speaker": "0", "confirmed": True, "start": 0, "end": 2},
        {"id": 1, "speaker": "1", "confirmed": True, "start": 3, "end": 5},
        {"id": 2, "speaker": "9", "start": 6, "end": 8},  # Unknown, sounds like speaker 1
        {"id": 3, "speaker": "9", "start": 8.2, "end": 8.6},  # Unknown and too short to judge
    ]
    vectors = {0: np.array([1.0, 0.0]), 1: np.array([0.0, 1.0]), 2: np.array([0.2, 0.98])}

    moved, _ = voice_check.relabel_from_fixes(lines, vectors, unknown_speaker="9")

    assert moved == 2
    assert [line["speaker"] for line in lines] == ["0", "1", "1", "1"]


def test_rematch_samples_short_lines_instead_of_copying_a_neighbour():
    lines = [
        {"id": 0, "speaker": "0", "confirmed": True, "start": 0, "end": 2},
        {"id": 1, "speaker": "1", "confirmed": True, "start": 3, "end": 5},
        {"id": 2, "speaker": "9", "start": 5.1, "end": 5.5},  # "Yeah." right after speaker 1, but sounds like 0
    ]
    vectors = {0: np.array([1.0, 0.0]), 1: np.array([0.0, 1.0])}

    voice_check.relabel_from_fixes(lines, vectors, unknown_speaker="9", short_vectors={2: np.array([0.95, 0.3])})

    assert lines[2]["speaker"] == "0"
