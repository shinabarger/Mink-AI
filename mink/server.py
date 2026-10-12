"""Mink's local web server and JSON API. Listens on 127.0.0.1 only."""

import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import diarizer, editing, exporter, jobs, speakers, store, transcriber, voices
from .audio import SUPPORTED_EXTENSIONS
from .config import WHISPER_MODELS, ensure_dirs, load_settings, save_settings

STATIC_DIR = Path(__file__).parent / "static"
UPLOAD_CHUNK_BYTES = 1024 * 1024


# ---------- Request bodies ----------


class SettingsBody(BaseModel):
    model: str
    language: str = "en"
    glossary: list[str] = []
    corrections: list[dict] = []
    voice_match_threshold: float = 0.75
    auto_name_voices: bool = False
    hf_token: str = ""
    export_folder: str = ""


class TitleBody(BaseModel):
    title: str


class ReprocessBody(BaseModel):
    num_speakers: int | None = None
    speakers_only: bool = False  # Keep the words, redo who said what


class SpeakerNameBody(BaseModel):
    name: str


class LineBody(BaseModel):
    text: str | None = None
    speaker_id: str | None = None
    new_speaker_name: str | None = None


class SplitBody(BaseModel):
    cuts: list[int]
    text: str | None = None  # Edited text to save first, when splitting while editing


class TranscriptBody(BaseModel):
    speakers: dict
    lines: list[dict]


class ReplaceBody(BaseModel):
    find: str
    replace: str
    match_case: bool = False
    whole_word: bool = True
    remember: bool = False


# ---------- App setup ----------


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dirs()
    jobs.start_worker()
    jobs.recover_interrupted_jobs()
    yield


app = FastAPI(title="Mink", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def always_check_for_new_ui(request, call_next):
    """Have the browser re-check the page and its files, so updates show up without a hard refresh."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(store.NotFoundError)
def not_found(_request, _error):
    return Response(status_code=404, content="Not found")


@app.exception_handler(ValueError)
def bad_request(_request, error):
    return Response(status_code=400, content=str(error))


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


# ---------- Info and settings ----------


@app.get("/api/info")
def info():
    settings = load_settings()
    return {
        "engine": transcriber.engine_name(),
        "speaker_engine": diarizer.engine_name(settings["hf_token"].strip()),
        "models": {key: preset["label"] for key, preset in WHISPER_MODELS.items()},
        "settings": settings,
    }


@app.post("/api/restart")
def restart():
    """Restart Mink in place, with no Terminal needed. Any queued work resumes after."""
    threading.Timer(0.5, _restart_process).start()  # Answer the request first
    return {"ok": True}


@app.put("/api/settings")
def update_settings(body: SettingsBody):
    return save_settings(body.model_dump())


# ---------- Recordings ----------


@app.get("/api/sessions")
def list_sessions():
    return store.list_sessions()


@app.post("/api/sessions")
def upload(file: Annotated[UploadFile, File()], num_speakers: Annotated[int | None, Form()] = None):
    extension = Path(file.filename or "").suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Mink can't read {extension or 'that'} files. Try m4a, mp3, or wav.")
    session = store.create_session(_title_from_filename(file.filename), extension, num_speakers or None)
    with open(store.audio_path(session), "wb") as destination:
        shutil.copyfileobj(file.file, destination, length=UPLOAD_CHUNK_BYTES)
    jobs.enqueue(session["id"])
    return store.get(session["id"])


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    return store.get(session_id)


@app.patch("/api/sessions/{session_id}")
def rename_session(session_id: str, body: TitleBody):
    return editing.rename_session(session_id, body.title)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    speakers.forget_session_voices(store.get(session_id))
    store.delete(session_id)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/retry")
def reprocess(session_id: str, body: ReprocessBody | None = None):
    """Transcribe again, or with speakers_only, keep the words and redo who said what."""
    body = body or ReprocessBody()
    if body.speakers_only and not store.has_words(session_id):
        raise ValueError("This recording was transcribed by an older version of Mink. Use Transcribe again instead.")
    if jobs.is_running(session_id):
        raise ValueError("This recording is already being transcribed. Stop it first.")
    store.save_previous_version(session_id, "Redo speakers" if body.speakers_only else "Transcribe again")
    if not body.speakers_only:
        speakers.forget_session_voices(store.get(session_id))
    if body.num_speakers is not None:
        store.update(session_id, num_speakers=body.num_speakers or None)
    jobs.enqueue(session_id, speakers_only=body.speakers_only)
    return store.get(session_id)


@app.post("/api/sessions/{session_id}/stop")
def stop(session_id: str):
    jobs.stop(session_id)
    return store.get(session_id)


@app.get("/api/sessions/{session_id}/audio")
def audio(session_id: str):
    path = store.playback_path(store.get(session_id))
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "audio/mp4")


@app.get("/api/sessions/{session_id}/export")
def export(session_id: str, file_format: Annotated[str, Query(alias="format")] = "txt", timestamps: bool = True):
    """Download through the browser."""
    filename, content = _export_file(session_id, file_format, timestamps)
    return Response(
        content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/sessions/{session_id}/export")
def export_to_folder(
    session_id: str, file_format: Annotated[str, Query(alias="format")] = "txt", timestamps: bool = True
):
    """Save straight into the export folder from Settings."""
    folder = Path(load_settings()["export_folder"]).expanduser()
    if not folder.is_dir():
        raise ValueError(f"The export folder doesn't exist: {folder}. Pick another one in Settings.")
    filename, content = _export_file(session_id, file_format, timestamps)
    path = _unused_path(folder / filename)
    path.write_text(content)
    return {"path": str(path), "name": path.name, "folder": folder.name}


@app.post("/api/reveal")
def reveal(path: Annotated[str, Query()]):
    """Show an exported file in Finder. Only files inside the export folder."""
    folder = Path(load_settings()["export_folder"]).expanduser().resolve()
    target = Path(path).resolve()
    if folder not in target.parents or not target.exists():
        raise HTTPException(404, "File not found")
    subprocess.run(["open", "-R", str(target)], check=False)
    return {"ok": True}


# ---------- Editing ----------


@app.patch("/api/sessions/{session_id}/speakers/{speaker_id}")
def rename_speaker(session_id: str, speaker_id: str, body: SpeakerNameBody):
    return editing.rename_speaker(session_id, speaker_id, body.name)


@app.patch("/api/sessions/{session_id}/lines/{line_id}")
def edit_line(session_id: str, line_id: int, body: LineBody):
    if body.text is not None:
        editing.set_line_text(session_id, line_id, body.text)
    if body.speaker_id is not None or body.new_speaker_name:
        editing.set_line_speaker(session_id, line_id, body.speaker_id, body.new_speaker_name)
    return store.get(session_id)


@app.post("/api/sessions/{session_id}/lines/{line_id}/split")
def split_line(session_id: str, line_id: int, body: SplitBody):
    session, new_line_id = editing.split_line(session_id, line_id, body.cuts, body.text)
    return {"session": session, "focus_line_id": new_line_id}


@app.put("/api/sessions/{session_id}/transcript")
def restore_transcript(session_id: str, body: TranscriptBody):
    return editing.restore_transcript(session_id, body.speakers, body.lines)


@app.post("/api/sessions/{session_id}/previous-version/switch")
def switch_version(session_id: str):
    return editing.switch_to_previous_version(session_id)


@app.delete("/api/sessions/{session_id}/previous-version")
def keep_current_version(session_id: str):
    return store.discard_previous_version(session_id)


@app.post("/api/sessions/{session_id}/relabel")
def relabel(session_id: str):
    session, moved, people_taught = editing.relabel_from_fixes(session_id)
    return {"session": session, "moved": moved, "people": people_taught}


@app.post("/api/sessions/{session_id}/replace")
def find_and_replace(session_id: str, body: ReplaceBody):
    count, session = editing.replace_text(session_id, body.find, body.replace, body.match_case, body.whole_word)
    if body.remember and body.find.strip():
        _remember_correction(body)
    return {"count": count, "session": session}


# ---------- Voice library ----------


@app.get("/api/voices")
def list_voices():
    return voices.list_voices()


@app.delete("/api/voices/{name}")
def delete_voice(name: str):
    return {"removed": voices.delete_voice(name)}


@app.put("/api/voices/{name}")
def restore_voice(name: str, entry: dict):
    voices.restore_voice(name, entry)
    return {"ok": True}


# ---------- Helpers ----------


def _restart_process() -> None:
    os.environ["MINK_NO_BROWSER"] = "1"  # The page reloads itself; don't open another tab
    os.execv(sys.executable, [sys.executable, "-m", "mink"])


def _export_file(session_id: str, file_format: str, timestamps: bool) -> tuple[str, str]:
    session = store.get(session_id)
    content, extension = exporter.export(session, file_format, timestamps)
    title = re.sub(r"[^\w\- ]", "", session["title"]).strip() or "transcript"
    return f"{title}.{extension}", content


def _unused_path(path: Path) -> Path:
    """Never overwrite an earlier export: add (2), (3)... instead."""
    candidate, number = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        number += 1
    return candidate


def _remember_correction(body: ReplaceBody) -> None:
    """Save a find and replace so it runs on every future transcript."""
    settings = load_settings()
    find = body.find.strip()
    corrections = [saved for saved in settings["corrections"] if saved["find"].lower() != find.lower()]
    corrections.append(
        {"find": find, "replace": body.replace, "match_case": body.match_case, "whole_word": body.whole_word}
    )
    save_settings({**settings, "corrections": corrections})


def _title_from_filename(filename: str | None) -> str:
    stem = Path(filename or "Recording").stem
    return stem.replace("_", " ").strip() or "Recording"
