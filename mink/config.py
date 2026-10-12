"""Paths and user settings for Mink.

Everything Mink stores lives under ~/Mink so it is easy to find, back up, or delete.
"""

import json
import os
import platform
from pathlib import Path

HOME_DIR = Path(os.environ.get("MINK_HOME", Path.home() / "Mink"))
SESSIONS_DIR = HOME_DIR / "sessions"
MODELS_DIR = HOME_DIR / "models"
SETTINGS_FILE = HOME_DIR / "settings.json"
VOICES_FILE = HOME_DIR / "voices.json"

SAMPLE_RATE = 16_000

IS_APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"

# Whisper model presets. "turbo" is the sweet spot for 4 to 5 hour recordings;
# "large-v3" squeezes out a little more accuracy and takes roughly 2x longer.
WHISPER_MODELS = {
    "turbo": {
        "label": "Large v3 Turbo (recommended)",
        "mlx": "mlx-community/whisper-large-v3-turbo",
        "faster_whisper": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    },
    "large-v3": {
        "label": "Large v3 (max accuracy, slower)",
        "mlx": "mlx-community/whisper-large-v3-mlx",
        "faster_whisper": "Systran/faster-whisper-large-v3",
    },
}

DEFAULT_SETTINGS = {
    "model": "turbo",
    "language": "en",
    # Proper nouns Whisper should expect, e.g. "Philip Druskenvald".
    "glossary": [],
    # Saved find and replace fixes applied to every new transcript.
    "corrections": [],
    # How similar a voice must be to a saved voice to auto name it (0 to 1).
    "voice_match_threshold": 0.75,
    # Name returning players from the voice library automatically. Off by default
    # because a confident wrong name is more work to fix than an unnamed speaker.
    "auto_name_voices": False,
    # Free Hugging Face token. When set, Mink uses the more accurate pyannote speaker model.
    "hf_token": "",
    # Folder exports are saved to. Empty means a normal browser download (usually Downloads).
    "export_folder": "",
}


def ensure_dirs() -> None:
    for directory in (HOME_DIR, SESSIONS_DIR, MODELS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return dict(DEFAULT_SETTINGS)
    saved = json.loads(SETTINGS_FILE.read_text())
    return {**DEFAULT_SETTINGS, **saved}


def save_settings(settings: dict) -> dict:
    merged = {**DEFAULT_SETTINGS, **settings}
    if merged["model"] not in WHISPER_MODELS:
        merged["model"] = DEFAULT_SETTINGS["model"]
    write_json_atomic(SETTINGS_FILE, merged)
    return merged


def write_json_atomic(path: Path, data) -> None:
    """Write JSON via a temp file so a crash never leaves a half written file."""
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(data, indent=2))
    temp_path.replace(path)
