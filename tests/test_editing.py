from conftest import make_line

from mink import editing, store, voices


def test_rename_updates_every_line_and_teaches_the_voice_library(new_session):
    session_id = new_session(
        lines=[make_line(0, "0", "Hi"), make_line(1, "1", "Yo"), make_line(2, "0", "Bye")],
        names={"0": "Speaker 1", "1": "Speaker 2"},
        voiceprints={"0": [1.0, 0.0], "1": [0.0, 1.0]},
    )

    session = editing.rename_speaker(session_id, "0", "Harkin")

    assert session["speakers"]["0"]["name"] == "Harkin"
    assert [voice["name"] for voice in voices.list_voices()] == ["Harkin"]


def test_fixing_a_typo_does_not_leave_a_stale_voice(new_session):
    session_id = new_session(lines=[make_line(0, "0", "Hi")], names={"0": "Speaker 1"}, voiceprints={"0": [1.0, 0.0]})

    editing.rename_speaker(session_id, "0", "Terren")
    editing.rename_speaker(session_id, "0", "Terran")

    assert "Terren" not in [voice["name"] for voice in voices.list_voices()]


def test_renaming_to_an_existing_name_merges_the_speakers(new_session):
    session_id = new_session(
        lines=[make_line(0, "0", "A"), make_line(1, "1", "B")], names={"0": "Terran", "1": "Speaker 2"}
    )

    session = editing.rename_speaker(session_id, "1", "terran")

    assert list(session["speakers"]) == ["0"]
    assert {line["speaker"] for line in session["lines"]} == {"0"}


def test_moving_one_line_marks_it_confirmed_and_undo_restores_it(new_session):
    session_id = new_session(lines=[make_line(0, "0", "A"), make_line(1, "0", "B")], names={"0": "DM"})
    before = store.get(session_id)

    session = editing.set_line_speaker(session_id, 1, None, "Maggie May")

    assert session["speakers"]["1"]["name"] == "Maggie May"
    assert session["lines"][1]["confirmed"] is True
    restored = editing.restore_transcript(session_id, before["speakers"], before["lines"])
    assert [line["speaker"] for line in restored["lines"]] == ["0", "0"]


def test_split_gives_the_new_part_its_own_voice(new_session):
    session_id = new_session(
        lines=[make_line(0, "0", "Roll initiative. Nat twenty!", end=2.0)], names={"0": "Speaker 1", "1": "Wes"}
    )

    session, new_line_id = editing.split_line(session_id, 0, [17])

    new_line = next(line for line in session["lines"] if line["id"] == new_line_id)
    assert new_line["text"] == "Nat twenty!"
    assert session["speakers"][new_line["speaker"]]["name"] == "Speaker 2"


def test_a_redo_can_be_undone_by_switching_versions(new_session):
    session_id = new_session(lines=[make_line(0, "0", "Before redo")], names={"0": "Terran"})
    store.save_previous_version(session_id, "Redo speakers")
    store.update(session_id, lines=[make_line(0, "0", "After redo")], speakers={"0": {"name": "Speaker 1"}})

    restored = editing.switch_to_previous_version(session_id)

    assert restored["lines"][0]["text"] == "Before redo"
    assert restored["speakers"]["0"]["name"] == "Terran"
    assert editing.switch_to_previous_version(session_id)["lines"][0]["text"] == "After redo"


def test_moving_a_block_moves_every_line_in_it(new_session):
    lines = [make_line(0, "0", "A"), make_line(1, "0", "B"), make_line(2, "1", "C")]
    session_id = new_session(lines=lines, names={"0": "Terran", "1": "Harkin"})

    session = editing.set_lines_speaker(session_id, [0, 1], "1", None)

    assert [line["speaker"] for line in session["lines"]] == ["1", "1", "1"]
