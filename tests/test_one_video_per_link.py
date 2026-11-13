import os
import queue
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db
import worker
from fake_ytdl import FakeYoutubeDL, fake_factory

PLENTY_OF_SPACE = 500 * 1024 * 1024 * 1024


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(worker, "_job_queue", queue.Queue())
    monkeypatch.setattr(worker, "_free_bytes", lambda folder: PLENTY_OF_SPACE)
    folder = tmp_path / "downloads"
    folder.mkdir()
    return folder


def run_job(downloads, factory):
    with db.connect() as conn:
        job_id = db.create_job(conn, "https://example.com/playlist?list=PL1", "best")
    worker._process_job(job_id, str(downloads), ydl_factory=factory)
    with db.connect() as conn:
        return db.get_job(conn, job_id)


@pytest.mark.parametrize("kind", ["playlist", "multi_video"])
def test_a_playlist_or_channel_link_is_refused_before_any_download(downloads, kind):
    factory = fake_factory({"_type": kind, "id": "PL1", "title": "channel uploads", "entries": []})
    job = run_job(downloads, factory)
    assert job["status"] == "error"
    assert "playlist" in job["error"].lower()
    assert factory.downloads_started == 0
    assert os.listdir(downloads) == []


def test_the_probe_reads_only_the_first_playlist_entry_flat(downloads):
    seen = {}

    class RecordingYoutubeDL(FakeYoutubeDL):
        info = {"_type": "playlist", "id": "PL1", "entries": []}

        def __init__(self, options):
            super().__init__(options)
            seen.update(options)

    job = {"id": 1, "url": "https://example.com/playlist?list=PL1"}
    limits = worker.DownloadLimits(max_bytes=1, max_seconds=1, min_free_bytes=0)
    with pytest.raises(worker.JobStopped):
        worker._download(job, str(downloads), {"format": "best", "postprocessors": []}, RecordingYoutubeDL,
                         limits, worker.DownloadGuard(1, str(downloads), limits))
    assert seen["noplaylist"] is True
    assert seen["extract_flat"] == "in_playlist"
    assert seen["playlistend"] == 1


def test_history_never_records_a_file_that_does_not_exist(downloads):
    class VanishingYoutubeDL(FakeYoutubeDL):
        info = {"id": "v1", "title": "clip", "duration": 10}

        def process_ie_result(self, info, download):
            return dict(info, requested_downloads=[{"filepath": os.path.join(self.download_dir, "gone.mp4")}])

    job = run_job(downloads, VanishingYoutubeDL)
    assert job["status"] == "error"
    assert "gone.mp4" in job["error"]
    assert not job["filepath"]


def test_history_records_the_file_that_was_written(downloads):
    job = run_job(downloads, fake_factory({"id": "v1", "title": "clip", "duration": 10}))
    assert job["status"] == "success"
    assert job["filepath"] == os.path.join(str(downloads), "clip [v1].mp4")
    assert os.path.isfile(job["filepath"])
