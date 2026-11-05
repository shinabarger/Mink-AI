from mink import context_check


def test_context_check_only_picks_from_each_lines_options():
    lines = [
        {"id": 0, "speaker": "0", "text": "Terran, what do you do?"},
        {"id": 1, "speaker": "1", "text": "I swing my axe."},  # Voice guessed Wes; the GM just asked Terran
        {"id": 2, "speaker": "1", "text": "Nice."},
    ]
    names = {"0": "GM", "1": "Wes", "2": "Terran"}
    close_calls = {1: ["1", "2"], 2: ["1", "2"]}

    def fake_model(system: str, user: str) -> str:
        assert "[1] (?) Wes: I swing my axe." in user
        return 'Sure: {"1": "Terran", "2": "Gandalf"}'  # Gandalf isn't an option, so it's ignored

    changed = context_check.check_close_calls(lines, names, close_calls, "", fake_model, lambda done, total: None)

    assert changed == 1
    assert [line["speaker"] for line in lines] == ["0", "2", "1"]
