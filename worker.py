import os
import queue
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import yt_dlp

import db
import links
from quality import resolve_quality

_progress_lock = threading.Lock()
_progress = {}
_job_queue = queue.Queue()
_cancel_lock = threading.Lock()
_cancel_requested = set()

_default_ydl_factory = yt_dlp.YoutubeDL

MAX_ERROR_LENGTH = 500
MAX_TITLE_BYTES_IN_FILE_NAME = 150
OUTPUT_NAME_TEMPLATE = f"%(title).{MAX_TITLE_BYTES_IN_FILE_NAME}B [%(id)s].%(ext)s"
YOUTUBE_HOSTS = ("youtube.com", "youtu.be")
YOUTUBE_BLOCKED_HINT = (
    "YouTube refused the download, which usually means yt-dlp is out of date. "
    "Run pip install -U \"yt-dlp[default]\" and restart VideoDL."
)
BYTES_PER_MB = 1024 * 1024
SECONDS_PER_MINUTE = 60
DEFAULT_MAX_DOWNLOAD_MB = 4096
DEFAULT_MAX_VIDEO_MINUTES = 240
DEFAULT_MIN_FREE_MB = 2048
SOCKET_TIMEOUT_SECONDS = 30
REFUSED_LIVE_STATUSES = ("is_live", "is_upcoming")
MULTI_VIDEO_TYPES = ("playlist", "multi_video")
PRIVATE_MEDIA_REFUSAL = "This video is served from a private or local address, which VideoDL does not fetch."
PLAYLIST_REFUSAL = (
    "This link is a playlist, channel or page with several videos. "
    "VideoDL downloads one video per link, so paste the links of the videos you want."
)
LIVE_STREAM_REFUSAL = "Live streams can't be downloaded. Try again once the stream has ended and been saved."
CANCELLED_MESSAGE = "Cancelled."
MAX_START_ATTEMPTS = 2
GAVE_UP_AFTER_RESTARTS = (
    f"A restart interrupted this download {MAX_START_ATTEMPTS} times, so it was not tried again."
)


class JobStopped(yt_dlp.utils.DownloadCancelled):
    status = "error"


class JobCancelled(JobStopped):
    status = "cancelled"


@dataclass(frozen=True)
class DownloadLimits:
    max_bytes: int
    max_seconds: int
    min_free_bytes: int


def _setting_as_int(name, default, smallest):
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {raw!r}") from None
    if value < smallest:
        raise ValueError(f"{name} must be at least {smallest}, got {value}")
    return value


def limits_from_env():
    return DownloadLimits(
        max_bytes=_setting_as_int("MAX_DOWNLOAD_MB", DEFAULT_MAX_DOWNLOAD_MB, 1) * BYTES_PER_MB,
        max_seconds=_setting_as_int("MAX_VIDEO_MINUTES", DEFAULT_MAX_VIDEO_MINUTES, 1) * SECONDS_PER_MINUTE,
        min_free_bytes=_setting_as_int("MIN_FREE_MB", DEFAULT_MIN_FREE_MB, 0) * BYTES_PER_MB,
    )


def _free_bytes(folder):
    return shutil.disk_usage(folder).free


def _megabytes(byte_count):
    return byte_count // BYTES_PER_MB


def estimated_size(info):
    pieces = info.get("requested_formats") or [info]
    return sum(piece.get("filesize") or piece.get("filesize_approx") or 0 for piece in pieces)


def _is_live(info):
    return bool(info.get("is_live")) or info.get("live_status") in REFUSED_LIVE_STATUSES


def _free_space_refusal(free_bytes, limits):
    return (
        f"Only {_megabytes(free_bytes)} MB of disk space is free and VideoDL keeps "
        f"{_megabytes(limits.min_free_bytes)} MB free (MIN_FREE_MB)."
    )


def _media_urls(info):
    pieces = info.get("requested_formats") or [info]
    return [piece["url"] for piece in pieces if piece.get("url")]


def refusal_reason(info, limits, free_bytes):
    if info.get("_type") in MULTI_VIDEO_TYPES:
        return PLAYLIST_REFUSAL
    if any(links.address_refusal(url) for url in _media_urls(info)):
        return PRIVATE_MEDIA_REFUSAL
    if _is_live(info):
        return LIVE_STREAM_REFUSAL
    duration = info.get("duration")
    if isinstance(duration, (int, float)) and duration > limits.max_seconds:
        return f"The video is longer than {limits.max_seconds // SECONDS_PER_MINUTE} minutes (MAX_VIDEO_MINUTES)."
    size = estimated_size(info)
    if size > limits.max_bytes:
        return f"The video is about {_megabytes(size)} MB, over the {_megabytes(limits.max_bytes)} MB limit (MAX_DOWNLOAD_MB)."
    if free_bytes - size < limits.min_free_bytes:
        return _free_space_refusal(free_bytes, limits)
    return None


def cancel_job(job_id):
    with db.connect() as conn:
        if db.cancel_queued_job(conn, job_id, _now()):
            return True
        job = db.get_job(conn, job_id)
    if job is None or job["status"] != "downloading":
        return False
    with _cancel_lock:
        _cancel_requested.add(job_id)
    return True


def is_cancel_requested(job_id):
    with _cancel_lock:
        return job_id in _cancel_requested


def _forget_cancel_request(job_id):
    with _cancel_lock:
        _cancel_requested.discard(job_id)


def stop_if_cancelled(job_id):
    if is_cancel_requested(job_id):
        raise JobCancelled(CANCELLED_MESSAGE)


class DownloadGuard:
    def __init__(self, job_id, download_dir, limits):
        self.job_id = job_id
        self.download_dir = os.path.realpath(download_dir)
        self.limits = limits
        self.bytes_by_file = {}
        self.written_paths = set()

    def __call__(self, progress):
        if progress.get("status") != "downloading":
            return
        self._remember(progress)
        stop_if_cancelled(self.job_id)
        downloaded = sum(self.bytes_by_file.values())
        if downloaded > self.limits.max_bytes:
            raise JobStopped(
                f"Stopped at {_megabytes(downloaded)} MB, over the {_megabytes(self.limits.max_bytes)} MB limit (MAX_DOWNLOAD_MB)."
            )
        free_bytes = _free_bytes(self.download_dir)
        if free_bytes < self.limits.min_free_bytes:
            raise JobStopped("Stopped: " + _free_space_refusal(free_bytes, self.limits))

    def _remember(self, progress):
        filename = progress.get("filename")
        self.bytes_by_file[filename] = progress.get("downloaded_bytes") or 0
        for path in (filename, progress.get("tmpfilename")):
            if path:
                self.written_paths.add(path)

    def remove_partial_files(self):
        for path in self.written_paths:
            inside_download_dir = os.path.realpath(path).startswith(self.download_dir + os.sep)
            if inside_download_dir and os.path.isfile(path):
                os.remove(path)


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


def is_youtube_url(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == name or host.endswith("." + name) for name in YOUTUBE_HOSTS)


def describe_error(exc, url):
    message = str(exc)
    if is_youtube_url(url) and "HTTP Error 403" in message:
        message = f"{YOUTUBE_BLOCKED_HINT} ({message})"
    return message[:MAX_ERROR_LENGTH]


def _final_filepath(ydl, info, tier):
    downloads = info.get("requested_downloads") or []
    if downloads and downloads[0].get("filepath"):
        filepath = downloads[0]["filepath"]
    else:
        filepath = ydl.prepare_filename(info)
        if tier["postprocessors"]:
            filepath = os.path.splitext(filepath)[0] + ".mp3"
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"yt-dlp finished but the file it reported is missing: {filepath}")
    return filepath


def output_template(download_dir):
    return os.path.join(download_dir, OUTPUT_NAME_TEMPLATE)


def _download(job, download_dir, tier, factory, limits, guard):
    link_problem = links.link_refusal(job["url"])
    if link_problem:
        raise JobStopped(f"This link {link_problem}.")
    ydl_opts = {
        "format": tier["format"],
        "outtmpl": output_template(download_dir),
        "merge_output_format": "mp4",
        "postprocessors": tier["postprocessors"],
        "progress_hooks": [_progress_hook(job["id"]), guard],
        "noplaylist": True,
        "extract_flat": "in_playlist",
        "playlistend": 1,
        "socket_timeout": SOCKET_TIMEOUT_SECONDS,
        "quiet": True,
        "no_warnings": True,
    }
    with factory(ydl_opts) as ydl:
        probed = ydl.extract_info(job["url"], download=False)
        refusal = refusal_reason(probed, limits, _free_bytes(download_dir))
        if refusal:
            raise JobStopped(refusal)
        stop_if_cancelled(job["id"])
        info = ydl.process_ie_result(probed, download=True)
        return info, _final_filepath(ydl, info, tier)


def _process_job(job_id, download_dir, ydl_factory=None, limits=None):
    factory = ydl_factory or _default_ydl_factory

    with db.connect() as conn:
        job = db.get_job(conn, job_id)
        if job is None or not db.claim_queued_job(conn, job_id, _now()):
            return

    with _progress_lock:
        _progress[job_id] = {
            "status": "downloading",
            "downloaded_bytes": 0,
            "total_bytes": None,
            "speed": None,
            "eta": None,
        }

    guard = None
    try:
        limits = limits or limits_from_env()
        guard = DownloadGuard(job_id, download_dir, limits)
        tier = resolve_quality(job["quality"])
        info, filepath = _download(job, download_dir, tier, factory, limits, guard)
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
    except JobStopped as stop:
        guard.remove_partial_files()
        with db.connect() as conn:
            db.update_job(conn, job_id, status=stop.status, error=str(stop)[:MAX_ERROR_LENGTH], finished_at=_now())
    except Exception as exc:
        with db.connect() as conn:
            db.update_job(conn, job_id, status="error", error=describe_error(exc, job["url"]), finished_at=_now())
    finally:
        _forget_cancel_request(job_id)
        with _progress_lock:
            _progress.pop(job_id, None)


def _process_next_job(download_dir):
    job_id = _job_queue.get()
    try:
        _process_job(job_id, download_dir)
    except Exception as exc:
        print(f"ERROR: job {job_id} stopped unexpectedly: {exc!r}", flush=True)
    finally:
        _job_queue.task_done()


def _worker_loop(download_dir):
    while True:
        _process_next_job(download_dir)


def _recover_unfinished_jobs():
    with db.connect() as conn:
        db.reset_stalled_jobs(conn, MAX_START_ATTEMPTS, GAVE_UP_AFTER_RESTARTS)
        unfinished_ids = [job["id"] for job in db.list_active_jobs(conn)]
    for job_id in unfinished_ids:
        enqueue(job_id)
    return unfinished_ids


def start_worker(download_dir):
    limits_from_env()
    _recover_unfinished_jobs()
    thread = threading.Thread(target=_worker_loop, args=(download_dir,), daemon=True)
    thread.start()
    return thread
