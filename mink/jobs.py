"""The background job queue.

Recordings are processed one at a time on a single worker thread, so two
5 hour recordings never fight over memory.
"""

import logging
import queue
import threading
import traceback

from . import store
from .pipeline import JobStoppedError, TranscriptionJob

log = logging.getLogger("mink")

FULL_JOB = "full"
SPEAKERS_ONLY_JOB = "speakers"

_queue: "queue.Queue[str]" = queue.Queue()
_stop_requests: set[str] = set()
_current: dict[str, str | None] = {"session_id": None}


def start_worker() -> None:
    threading.Thread(target=_work_forever, daemon=True, name="mink-worker").start()


def is_running(session_id: str) -> bool:
    return _current["session_id"] == session_id


def enqueue(session_id: str, speakers_only: bool = False) -> None:
    """Queue a recording. speakers_only reuses the saved words and only redoes
    who said what, which takes minutes instead of a full transcription."""
    if is_running(session_id):
        raise ValueError("This recording is already being transcribed. Stop it first.")
    _stop_requests.discard(session_id)
    store.update(
        session_id,
        status="queued",
        stage="Waiting to start",
        detail="",
        progress=0.0,
        error=None,
        job=SPEAKERS_ONLY_JOB if speakers_only else FULL_JOB,
    )
    _queue.put(session_id)


def stop(session_id: str) -> None:
    """Stop a queued or running job. A running job stops after the piece of audio it is on."""
    if is_running(session_id):
        _stop_requests.add(session_id)
        store.update(session_id, stage="Stopping after the current piece of audio...")
    else:
        store.update(session_id, status="stopped", stage="Stopped")


def recover_interrupted_jobs() -> None:
    """If Mink was closed mid job, put those recordings back in the queue."""
    for summary in store.list_sessions():
        if summary["status"] in ("queued", "processing"):
            speakers_only = store.get(summary["id"]).get("job") == SPEAKERS_ONLY_JOB
            enqueue(summary["id"], speakers_only=speakers_only)


def _work_forever() -> None:
    while True:
        session_id = _queue.get()
        if store.get(session_id)["status"] != "queued":
            continue  # Stopped while waiting, or a duplicate entry
        _current["session_id"] = session_id
        try:
            TranscriptionJob(session_id, should_stop=_stop_check_for(session_id)).run()
        except (JobStoppedError, InterruptedError):
            log.info("Stopped %s", session_id)
            store.update(session_id, status="stopped", stage="Stopped", detail="")
        except Exception as error:  # Keep the worker alive no matter what
            log.error("Job %s failed:\n%s", session_id, traceback.format_exc())
            store.update(session_id, status="error", stage="Failed", error=str(error))
        finally:
            _current["session_id"] = None
            _stop_requests.discard(session_id)


def _stop_check_for(session_id: str):
    return lambda: session_id in _stop_requests
