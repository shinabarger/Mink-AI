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
from .rematch import RematchJob

log = logging.getLogger("mink")

FULL_JOB = "full"
SPEAKERS_ONLY_JOB = "speakers"
REMATCH_JOB = "rematch"

_queue: "queue.Queue[str]" = queue.Queue()
_stop_requests: set[str] = set()
_current: dict[str, str | None] = {"session_id": None}


def start_worker() -> None:
    threading.Thread(target=_work_forever, daemon=True, name="mink-worker").start()


def is_running(session_id: str) -> bool:
    return _current["session_id"] == session_id


def enqueue(session_id: str, job: str = FULL_JOB) -> None:
    """Queue work on a recording. SPEAKERS_ONLY_JOB reuses the saved words and only redoes
    who said what. REMATCH_JOB re-sorts lines using the ones you fixed by hand."""
    if is_running(session_id):
        raise ValueError("This recording is already being processed. Stop it first.")
    _stop_requests.discard(session_id)
    store.update(session_id, status="queued", stage="Waiting to start", detail="", progress=0.0, error=None, job=job)
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
            enqueue(summary["id"], job=store.get(summary["id"]).get("job", FULL_JOB))


def _work_forever() -> None:
    while True:
        session_id = _queue.get()
        if store.get(session_id)["status"] != "queued":
            continue  # Stopped while waiting, or a duplicate entry
        _current["session_id"] = session_id
        is_rematch = store.get(session_id).get("job") == REMATCH_JOB
        job_class = RematchJob if is_rematch else TranscriptionJob
        try:
            job_class(session_id, should_stop=_stop_check_for(session_id)).run()
        except (JobStoppedError, InterruptedError):
            log.info("Stopped %s", session_id)
            if is_rematch:
                _restore_after_rematch(session_id)
            else:
                store.update(session_id, status="stopped", stage="Stopped", detail="")
        except Exception as error:  # Keep the worker alive no matter what
            log.error("Job %s failed:\n%s", session_id, traceback.format_exc())
            if is_rematch:
                _restore_after_rematch(session_id, f"Rematch speakers didn't finish, so nothing changed. ({error})")
            else:
                store.update(session_id, status="error", stage="Failed", error=str(error))
        finally:
            _current["session_id"] = None
            _stop_requests.discard(session_id)


def _restore_after_rematch(session_id: str, problem: str | None = None) -> None:
    """A rematch only saves at the very end, so the transcript is still as it was. Show it again."""
    store.discard_previous_version(session_id)
    store.update(session_id, status="done", stage="Done", detail="", notice=problem)


def _stop_check_for(session_id: str):
    return lambda: session_id in _stop_requests
