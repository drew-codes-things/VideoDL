import os
import queue
import threading
from datetime import datetime, timezone

import yt_dlp

import db
from quality import resolve_quality

_progress_lock = threading.Lock()
_progress = {}
_job_queue = queue.Queue()

_default_ydl_factory = yt_dlp.YoutubeDL


def _now():
    return datetime.now(timezone.utc).isoformat()


def enqueue(job_id):
    _job_queue.put(job_id)


def get_progress(job_id):
    with _progress_lock:
        return dict(_progress.get(job_id, {}))


def _progress_hook(job_id):
    def hook(d):
        with _progress_lock:
            _progress[job_id] = {
                "status": d.get("status"),
                "downloaded_bytes": d.get("downloaded_bytes"),
                "total_bytes": d.get("total_bytes") or d.get("total_bytes_estimate"),
                "speed": d.get("speed"),
                "eta": d.get("eta"),
            }
    return hook


def _process_job(job_id, download_dir, ydl_factory=None):
    factory = ydl_factory or _default_ydl_factory

    with db.connect() as conn:
        job = db.get_job(conn, job_id)
        if job is None:
            return
        db.update_job(conn, job_id, status="downloading", started_at=_now())

    with _progress_lock:
        _progress[job_id] = {
            "status": "downloading",
            "downloaded_bytes": 0,
            "total_bytes": None,
            "speed": None,
            "eta": None,
        }

    tier = resolve_quality(job["quality"])
    ydl_opts = {
        "format": tier["format"],
        "outtmpl": os.path.join(download_dir, "%(title)s [%(id)s].%(ext)s"),
        "merge_output_format": "mp4",
        "postprocessors": tier["postprocessors"],
        "progress_hooks": [_progress_hook(job_id)],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with factory(ydl_opts) as ydl:
            info = ydl.extract_info(job["url"], download=True)
        filepath = ydl.prepare_filename(info)
        if tier["postprocessors"]:
            filepath = os.path.splitext(filepath)[0] + ".mp3"
        height = info.get("height")
        resolved_quality = f"{height}p" if height else None
        with db.connect() as conn:
            db.update_job(
                conn, job_id,
                status="success",
                title=info.get("title"),
                filepath=filepath,
                resolved_quality=resolved_quality,
                finished_at=_now(),
            )
    except Exception as exc:
        with db.connect() as conn:
            db.update_job(conn, job_id, status="error", error=str(exc)[:500], finished_at=_now())
    finally:
        with _progress_lock:
            _progress.pop(job_id, None)


def _worker_loop(download_dir):
    while True:
        job_id = _job_queue.get()
        try:
            _process_job(job_id, download_dir)
        finally:
            _job_queue.task_done()


def _recover_stalled_jobs():
    with db.connect() as conn:
        stalled_ids = db.reset_stalled_jobs(conn)
    for job_id in stalled_ids:
        enqueue(job_id)
    return stalled_ids


def start_worker(download_dir):
    _recover_stalled_jobs()
    thread = threading.Thread(target=_worker_loop, args=(download_dir,), daemon=True)
    thread.start()
    return thread
