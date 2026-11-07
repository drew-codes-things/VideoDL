import os
import sys

import pytest
import yt_dlp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db
import worker

FORBIDDEN = "ERROR: unable to download video data: HTTP Error 403: Forbidden"


@pytest.mark.parametrize("url", [
    "https://youtu.be/8SbUC-UaAxE?si=e29znt2r5fMoD0z0",
    "https://www.youtube.com/watch?v=82ytqMyD_es",
    "https://music.youtube.com/watch?v=82ytqMyD_es",
])
def test_youtube_403_explains_that_yt_dlp_needs_updating(url):
    message = worker.describe_error(Exception(FORBIDDEN), url)
    assert message.startswith(worker.YOUTUBE_BLOCKED_HINT)
    assert "HTTP Error 403" in message


def test_other_sites_keep_the_plain_error():
    assert worker.describe_error(Exception(FORBIDDEN), "https://vm.tiktok.com/ZN8RUhDAC/") == FORBIDDEN
    assert worker.describe_error(Exception("Unsupported URL"), "https://youtu.be/x") == "Unsupported URL"


def test_long_errors_are_cut_to_the_stored_length():
    assert len(worker.describe_error(Exception("x" * 2000), "https://example.com")) == worker.MAX_ERROR_LENGTH


class BlockedYoutubeDL:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download):
        raise yt_dlp.utils.DownloadError(FORBIDDEN)


def test_failed_youtube_job_stores_the_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "jobs.db"))
    with db.connect() as conn:
        job_id = db.create_job(conn, "https://youtu.be/8SbUC-UaAxE", "best")
    worker._process_job(job_id, str(tmp_path), ydl_factory=BlockedYoutubeDL)
    with db.connect() as conn:
        job = db.get_job(conn, job_id)
    assert job["status"] == "error"
    assert job["error"].startswith(worker.YOUTUBE_BLOCKED_HINT)
