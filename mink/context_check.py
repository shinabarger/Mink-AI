"""A second opinion on close calls, from a small language model running on this Mac.

The voice match decides most lines. For the ones it couldn't settle, the model
reads the conversation around each line and picks from that line's likeliest
voices: who was just addressed by name, who answers a question, the GM narrating
and calling for rolls. When the words give no real clue it keeps the voice's
guess, so a clear voice match is never overruled by a hunch.
"""

import json
import logging
import re
from collections.abc import Callable

log = logging.getLogger("mink")

MODEL_ID = "mlx-community/Qwen2.5-7B-Instruct-4bit"
MODEL_LABEL = "Qwen 2.5 7B"
# Each request covers a stretch of transcript with up to this many close calls in it.
MAX_TARGETS_PER_WINDOW = 12
LINES_BEFORE = 15
LINES_AFTER = 6
KEEP = "keep"

SYSTEM_PROMPT = (
    "You check who said each line in transcripts of tabletop roleplaying game sessions recorded with one "
    "microphone. You only change a speaker when the conversation makes it clear, and you answer with JSON only."
)

# (system prompt, user prompt) -> the model's reply
Complete = Callable[[str, str], str]


def is_available() -> bool:
    """The model runs on Apple Silicon through MLX. Elsewhere this step is skipped."""
    try:
        import mlx_lm  # noqa: F401
    except ImportError:
        return False
    return True


def load_model(model_path: str) -> Complete:
    from mlx_lm import generate, load

    model, tokenizer = load(model_path)

    def complete(system: str, user: str) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        return generate(model, tokenizer, prompt=prompt, max_tokens=400, verbose=False)

    return complete


def check_close_calls(
    lines: list[dict],
    names: dict[str, str],
    close_calls: dict[int, list[str]],
    players: str,
    complete: Complete,
    on_progress: Callable[[int, int], None],
) -> int:
    """Let the model reassign close calls from the conversation. Returns how many lines changed.

    names maps speaker id to display name. close_calls maps line id to that line's
    likeliest speaker ids (from the voice match). Only those ids are ever chosen.
    """
    windows = _windows(lines, close_calls)
    changed = 0
    for done, (start, end, targets) in enumerate(windows, start=1):
        prompt = _prompt(lines[start:end], names, targets, close_calls, players)
        answer = _parse(complete(SYSTEM_PROMPT, prompt))
        changed += _apply(answer, lines[start:end], names, targets, close_calls)
        on_progress(done, len(windows))
    return changed


def _windows(lines: list[dict], close_calls: dict[int, list[str]]) -> list[tuple[int, int, set[int]]]:
    """Group close calls into stretches of transcript: (first line index, end index, target line ids)."""
    target_indexes = [index for index, line in enumerate(lines) if line["id"] in close_calls]
    windows = []
    while target_indexes:
        batch = target_indexes[:MAX_TARGETS_PER_WINDOW]
        # Keep a stretch readable: stop the batch where the next close call is far away.
        for cut in range(1, len(batch)):
            if batch[cut] - batch[0] > 40:
                batch = batch[:cut]
                break
        target_indexes = target_indexes[len(batch) :]
        start = max(0, batch[0] - LINES_BEFORE)
        end = min(len(lines), batch[-1] + LINES_AFTER + 1)
        windows.append((start, end, {lines[index]["id"] for index in batch}))
    return windows


def _prompt(window: list[dict], names, targets: set[int], close_calls, players: str) -> str:
    def as_text(line: dict) -> str:
        marker = "(?) " if line["id"] in targets else ""
        return f"[{line['id']}] {marker}{names.get(line['speaker'], 'Unknown')}: {line['text']}"

    transcript = "\n".join(as_text(line) for line in window)
    options = "\n".join(
        f"{line['id']}: {', '.join(names[speaker] for speaker in close_calls[line['id']] if speaker in names)}"
        for line in window
        if line["id"] in targets
    )
    return f"""Players at the table (player = character):
{players.strip() or "Not given."}

Transcript. Each line is [number] Speaker: text. Lines marked (?) are close calls: the voice match wasn't sure,
and the name shown is only its best guess.

{transcript}

For each (?) line, decide who most likely said it from the conversation: who was just addressed by name, who is
answering a question, who is reacting to whom, the GM narrating and calling for rolls, players talking about their
own character in the first person. Choose only from that line's options. If the words give no real clue, answer
"{KEEP}".

Options:
{options}

Answer with one JSON object and nothing else, for example {{"121": "Wes", "130": "{KEEP}"}}."""


def _parse(reply: str) -> dict[str, str]:
    found = re.search(r"\{.*\}", reply, re.DOTALL)
    if not found:
        log.info("Context check: no answer in reply %r", reply[:200])
        return {}
    try:
        answer = json.loads(found.group(0))
    except json.JSONDecodeError:
        return {}
    return {str(key): str(value) for key, value in answer.items()} if isinstance(answer, dict) else {}


def _apply(answer: dict[str, str], window: list[dict], names, targets: set[int], close_calls) -> int:
    changed = 0
    for line in window:
        choice = answer.get(str(line["id"]), KEEP).strip().lower()
        if line["id"] not in targets or choice == KEEP:
            continue
        by_name = {names[speaker].lower(): speaker for speaker in close_calls[line["id"]] if speaker in names}
        speaker = by_name.get(choice)
        if speaker and speaker != line["speaker"]:
            line["speaker"] = speaker
            changed += 1
    return changed
