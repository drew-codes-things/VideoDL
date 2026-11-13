import os
import queue
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db
import worker
from fake_ytdl import CHUNK_BYTES, fake_factory

PLAIN_VIDEO = {"id": "abc123", "title": "clip", "duration": 60, "is_live": False}
PLENTY_OF_SPACE = 500 * 1024 * 1024 * 1024


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(worker, "_job_queue", queue.Queue())
    monkeypatch.setattr(worker, "_free_bytes", lambda folder: PLENTY_OF_SPACE, raising=False)
    for name in ("MAX_DOWNLOAD_MB", "MAX_VIDEO_MINUTES", "MIN_FREE_MB"):
        monkeypatch.delenv(name, raising=False)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    return downloads


def run_job(downloads, factory, url="https://example.com/watch/1"):
    with db.connect() as conn:
        job_id = db.create_job(conn, url, "best")
    worker._process_job(job_id, str(downloads), ydl_factory=factory)
    with db.connect() as conn:
        return db.get_job(conn, job_id)


def test_a_plain_video_still_downloads(scratch):
    job = run_job(scratch, fake_factory(PLAIN_VIDEO))
    assert job["status"] == "success"
    assert os.path.isfile(job["filepath"])


@pytest.mark.parametrize("live_fields", [{"is_live": True}, {"live_status": "is_live"}, {"live_status": "is_upcoming"}])
def test_a_live_stream_is_refused_before_anything_is_downloaded(scratch, live_fields):
    factory = fake_factory({**PLAIN_VIDEO, **live_fields})
    job = run_job(scratch, factory)
    assert job["status"] == "error"
    assert "live" in job["error"].lower()
    assert factory.downloads_started == 0
    assert os.listdir(scratch) == []


def test_a_video_longer_than_the_limit_is_refused(scratch, monkeypatch):
    monkeypatch.setenv("MAX_VIDEO_MINUTES", "30")
    factory = fake_factory({**PLAIN_VIDEO, "duration": 31 * 60})
    job = run_job(scratch, factory)
    assert job["status"] == "error"
    assert "30 minutes" in job["error"]
    assert factory.downloads_started == 0


def test_a_video_whose_known_size_is_over_the_limit_is_refused(scratch, monkeypatch):
    monkeypatch.setenv("MAX_DOWNLOAD_MB", "100")
    formats = [{"filesize": 90 * 1024 * 1024}, {"filesize_approx": 20 * 1024 * 1024}]
    factory = fake_factory({**PLAIN_VIDEO, "requested_formats": formats})
    job = run_job(scratch, factory)
    assert job["status"] == "error"
    assert "100 MB" in job["error"]
    assert factory.downloads_started == 0


def test_a_download_that_grows_past_the_limit_is_stopped_and_its_part_file_removed(scratch, monkeypatch):
    monkeypatch.setenv("MAX_DOWNLOAD_MB", "1")
    factory = fake_factory(PLAIN_VIDEO, chunk_count=40)
    job = run_job(scratch, factory)
    assert job["status"] == "error"
    assert "1 MB" in job["error"]
    assert os.listdir(scratch) == []


def test_a_download_is_refused_when_the_disk_is_already_near_its_floor(scratch, monkeypatch):
    monkeypatch.setenv("MIN_FREE_MB", "2048")
    monkeypatch.setattr(worker, "_free_bytes", lambda folder: 1024 * 1024 * 1024, raising=False)
    factory = fake_factory(PLAIN_VIDEO)
    job = run_job(scratch, factory)
    assert job["status"] == "error"
    assert "free" in job["error"].lower()
    assert factory.downloads_started == 0


def test_a_download_stops_when_free_space_falls_to_the_floor(scratch, monkeypatch):
    monkeypatch.setenv("MIN_FREE_MB", "10")
    space = {"free": 12 * 1024 * 1024}

    def shrink(chunk_number):
        space["free"] -= CHUNK_BYTES

    monkeypatch.setattr(worker, "_free_bytes", lambda folder: space["free"], raising=False)
    job = run_job(scratch, fake_factory(PLAIN_VIDEO, chunk_count=40, after_chunk=shrink))
    assert job["status"] == "error"
    assert "free" in job["error"].lower()
    assert os.listdir(scratch) == []


def test_a_job_stopped_by_a_limit_is_not_queued_again_after_a_restart(scratch, monkeypatch):
    monkeypatch.setenv("MAX_DOWNLOAD_MB", "1")
    run_job(scratch, fake_factory(PLAIN_VIDEO, chunk_count=40))
    assert worker._recover_unfinished_jobs() == []


def test_limits_come_from_the_environment_with_defaults(monkeypatch):
    for name in ("MAX_DOWNLOAD_MB", "MAX_VIDEO_MINUTES", "MIN_FREE_MB"):
        monkeypatch.delenv(name, raising=False)
    defaults = worker.limits_from_env()
    assert defaults.max_bytes == worker.DEFAULT_MAX_DOWNLOAD_MB * 1024 * 1024
    assert defaults.max_seconds == worker.DEFAULT_MAX_VIDEO_MINUTES * 60
    assert defaults.min_free_bytes == worker.DEFAULT_MIN_FREE_MB * 1024 * 1024
    monkeypatch.setenv("MAX_DOWNLOAD_MB", "250")
    monkeypatch.setenv("MIN_FREE_MB", "0")
    assert worker.limits_from_env().max_bytes == 250 * 1024 * 1024
    assert worker.limits_from_env().min_free_bytes == 0


@pytest.mark.parametrize("name,value", [("MAX_DOWNLOAD_MB", "lots"), ("MAX_VIDEO_MINUTES", "0"), ("MIN_FREE_MB", "-5")])
def test_a_bad_limit_setting_is_reported_by_name(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        worker.limits_from_env()


def test_a_stopped_fragment_download_leaves_no_fragments_behind(scratch, monkeypatch):
    monkeypatch.setenv("MAX_DOWNLOAD_MB", "1")

    def write_fragment_leftovers(chunk_number):
        part = next(name for name in os.listdir(scratch) if name.endswith(".part"))
        for leftover in (f"{part}-Frag{chunk_number + 1}", part[: -len(".part")] + ".ytdl"):
            (scratch / leftover).write_bytes(b"\0")

    (scratch / "earlier download [old].mp4").write_bytes(b"keep me")
    run_job(scratch, fake_factory(PLAIN_VIDEO, chunk_count=40, after_chunk=write_fragment_leftovers))
    assert os.listdir(scratch) == ["earlier download [old].mp4"]
