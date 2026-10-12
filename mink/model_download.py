"""Download a Whisper model from Hugging Face with progress you can see.

We measure progress by watching the model's cache folder grow, which works no
matter how huggingface_hub reports (or doesn't report) its own progress.
"""

import os

# Plain HTTP downloads write straight into the cache folder, so its size is a true progress signal.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import threading
from collections.abc import Callable
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download
from huggingface_hub.constants import HF_HUB_CACHE

FASTER_WHISPER_FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


def download(repo_id: str, on_progress: Callable[[int, int], None], allow_patterns: list[str] | None = None) -> str:
    """Return the local folder for repo_id, downloading it first if needed.

    on_progress(downloaded_bytes, total_bytes) is called about once a second.
    """
    try:
        return snapshot_download(repo_id, allow_patterns=allow_patterns, local_files_only=True)
    except Exception:
        pass  # Not cached yet

    total = _repo_size(repo_id)
    folder = Path(HF_HUB_CACHE) / ("models--" + repo_id.replace("/", "--"))
    done = threading.Event()
    result: dict = {}

    def run() -> None:
        try:
            result["path"] = snapshot_download(repo_id, allow_patterns=allow_patterns)
        except Exception as error:
            result["error"] = error
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    while not done.wait(1.0):
        on_progress(_folder_size(folder), total)
    if "error" in result:
        raise RuntimeError(f"Could not download the speech model. Check your internet connection. ({result['error']})")
    return result["path"]


def _repo_size(repo_id: str) -> int:
    try:
        info = HfApi().model_info(repo_id, files_metadata=True)
        return sum(sibling.size or 0 for sibling in info.siblings)
    except Exception:
        return 0  # Unknown size: we still show megabytes downloaded


def _folder_size(folder: Path) -> int:
    if not folder.exists():
        return 0
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file() and not path.is_symlink())
