"""Check every line's voice on its own.

The speaker model groups voices in big chunks, and one bad grouping drags many
lines along with it. Here each line gets its own voice embedding, which lets us:

1. refine_by_voice: after the first pass, move lines that clearly sound like a
   different speaker than the one they were given.
2. relabel_from_fixes: use the lines you assigned by hand as reference samples
   and re-sort every other line to the closest match.

Lines shorter than MIN_SECONDS ("Yeah.", "Oh no!") are too short to judge and
are left alone.
"""

import bisect

import numpy as np

MIN_SECONDS = 1.0
# How much closer another speaker must sound before refine_by_voice moves a line.
REFINE_MARGIN = 0.08
# Below this similarity, relabel_from_fixes leaves a line where it is.
RELABEL_MIN_SIMILARITY = 0.45
# If the best and second best speaker are this close, the line is flagged for review.
UNSURE_MARGIN = 0.05


def spans_to_embed(lines: list[dict]) -> list[tuple[float, float]]:
    return [(line["start"], line["end"]) for line in lines if line["end"] - line["start"] >= MIN_SECONDS]


def vectors_by_line(lines: list[dict], spans: np.ndarray, vectors: np.ndarray) -> dict[int, np.ndarray]:
    """Match stored embeddings back to lines by time, so edits and splits don't break the link."""
    if len(spans) == 0:
        return {}
    starts = list(spans[:, 0])
    found = {}
    for line in lines:
        middle = (line["start"] + line["end"]) / 2
        index = bisect.bisect_right(starts, middle) - 1
        if index >= 0 and spans[index, 0] <= middle <= spans[index, 1]:
            found[line["id"]] = vectors[index]
    return found


def refine_by_voice(lines: list[dict], vectors: dict[int, np.ndarray]) -> int:
    """Move lines that clearly sound like another speaker. Returns how many moved."""
    centroids = _centroids(lines, vectors, lambda line: True)
    moved = 0
    for line in lines:
        vector = vectors.get(line["id"])
        if vector is None or line["speaker"] not in centroids:
            continue
        scores = _scores(vector, centroids)
        best = scores[0]
        current = next(score for score, speaker in scores if speaker == line["speaker"])
        if best[1] != line["speaker"] and best[0] - current > REFINE_MARGIN:
            line["speaker"] = best[1]
            moved += 1
        _flag_if_unsure(line, scores)
    return moved


def relabel_from_fixes(lines: list[dict], vectors: dict[int, np.ndarray]) -> tuple[int, int]:
    """Re-sort lines using the ones you fixed by hand as references.

    People you taught are compared against everyone else's current voice too,
    so teaching one person can't pull in lines that belong to someone untaught.
    Lines you fixed never move. Returns (lines moved, number of people taught).
    """
    taught = _centroids(lines, vectors, lambda line: line.get("confirmed"))
    if not taught:
        return 0, 0
    untaught = _centroids(lines, vectors, lambda line: line["speaker"] not in taught)
    candidates = {**untaught, **taught}
    moved = 0
    for line in lines:
        vector = vectors.get(line["id"])
        if line.get("confirmed") or vector is None:
            continue
        scores = _scores(vector, candidates)
        best_score, best_speaker = scores[0]
        _flag_if_unsure(line, scores)
        if line["unsure"] or best_score < RELABEL_MIN_SIMILARITY:
            continue
        if best_speaker in taught and best_speaker != line["speaker"]:
            line["speaker"] = best_speaker
            moved += 1
    return moved, len(taught)


def move_unsure_lines(lines: list[dict], unknown_speaker: str) -> int:
    """Send lines Mink isn't sure about to the Unknown speaker instead of guessing.
    Lines you assigned yourself are never moved."""
    moved = 0
    for line in lines:
        if line.get("unsure") and not line.get("confirmed"):
            line["speaker"] = unknown_speaker
            line["unsure"] = False
            moved += 1
    return moved


def _centroids(lines, vectors, include) -> dict[str, np.ndarray]:
    grouped: dict[str, list[np.ndarray]] = {}
    for line in lines:
        vector = vectors.get(line["id"])
        if vector is not None and include(line):
            grouped.setdefault(line["speaker"], []).append(vector)
    return {speaker: _normalize(np.mean(group, axis=0)) for speaker, group in grouped.items()}


def _scores(vector: np.ndarray, centroids: dict[str, np.ndarray]) -> list[tuple[float, str]]:
    return sorted(((float(np.dot(vector, centroid)), speaker) for speaker, centroid in centroids.items()), reverse=True)


def _flag_if_unsure(line: dict, scores: list[tuple[float, str]]) -> None:
    runner_up = scores[1][0] if len(scores) > 1 else -1.0
    line["unsure"] = scores[0][0] - runner_up < UNSURE_MARGIN


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector
