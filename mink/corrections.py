"""Find and replace, plus saved fixes that run on every new transcript.

Example saved fix: "Drusk and bald" -> "Druskenvald".
"""

import re


def compile_pattern(find: str, match_case: bool, whole_word: bool) -> re.Pattern:
    pattern = re.escape(find.strip())
    if whole_word:
        pattern = rf"(?<!\w){pattern}(?!\w)"
    return re.compile(pattern, 0 if match_case else re.IGNORECASE)


def replace_in_lines(
    lines: list[dict], find: str, replace: str, match_case: bool = False, whole_word: bool = True
) -> int:
    """Replace in place across all lines. Returns how many replacements were made."""
    if not find.strip():
        return 0
    pattern = compile_pattern(find, match_case, whole_word)
    total = 0
    for line in lines:
        line["text"], count = pattern.subn(lambda _match: replace, line["text"])
        total += count
    return total


def apply_saved_corrections(lines: list[dict], corrections: list[dict]) -> int:
    return sum(
        replace_in_lines(
            lines,
            correction["find"],
            correction["replace"],
            correction.get("match_case", False),
            correction.get("whole_word", True),
        )
        for correction in corrections
    )
