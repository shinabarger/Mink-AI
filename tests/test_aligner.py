from conftest import make_turns, make_words

from mink import aligner


def speakers_and_text(lines: list[dict]) -> list[tuple[int, str]]:
    return [(line["speaker"], line["text"]) for line in lines]


def test_new_line_on_speaker_change_sentence_end_and_long_pause():
    words = make_words(
        (0.0, 0.4, " Roll"),
        (0.5, 0.9, " initiative."),
        (1.2, 1.5, " Nat"),
        (1.6, 2.0, " twenty!"),
        (6.0, 6.5, " Nice."),
    )
    turns = make_turns((0.0, 1.0, 3), (1.1, 7.0, 5))

    lines = aligner.build_lines(words, turns)

    assert speakers_and_text(lines) == [(3, "Roll initiative."), (5, "Nat twenty!"), (5, "Nice.")]


def test_word_in_a_gap_goes_to_the_nearest_turn():
    words = make_words((0.0, 0.5, " Hello"), (2.05, 2.2, " there"))
    turns = make_turns((0.0, 0.6, 0), (2.3, 3.0, 1))

    lines = aligner.build_lines(words, turns)

    assert [line["speaker"] for line in lines] == [0, 1]


def test_speaker_change_snaps_to_the_end_of_the_sentence():
    words = make_words(
        (0.0, 0.3, " She"), (0.3, 0.6, " had"), (0.6, 0.9, " it"), (0.9, 1.4, " figured."), (1.6, 2.0, " Yeah.")
    )
    turns = make_turns((0.0, 0.8, 0), (0.8, 2.0, 1))  # Boundary lands one word early

    lines = aligner.build_lines(words, turns)

    assert speakers_and_text(lines) == [(0, "She had it figured."), (1, "Yeah.")]
