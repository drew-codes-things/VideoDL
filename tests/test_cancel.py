import os
import queue
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app
import db
import worker
from fake_ytdl import fake_factory

PLAIN_VIDEO = {"id": "abc123", "title": "clip", "duration": 60}
PLENTY_OF_SPACE = 500 * 1024 * 1024 * 1024


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(worker, "_job_queue", queue.Queue())
    monkeypatch.setattr(worker, "_free_bytes", lambda folder: PLENTY_OF_SPACE)
    monkeypatch.setattr(app, "WEB_USERNAME", "")
    monkeypatch.setattr(app, "WEB_PASSWORD", "")
    folder = tmp_path / "downloads"
    folder.mkdir()
    return folder


@pytest.fixture
def client(downloads):
    return app.app.test_client()


def csrf_token(client):
    client.get("/")
    with client.session_transaction() as session:
        return session["_csrf_token"]


def new_job(url="https://example.com/watch/1"):
    with db.connect() as conn:
        return db.create_job(conn, url, "best")


def job_row(job_id):
    with db.connect() as conn:
        return db.get_job(conn, job_id)


def test_the_queue_shows_a_cancel_button_for_each_waiting_job(client):
    job_id = new_job()
    page = client.get("/").get_data(as_text=True)
    assert f'action="/jobs/{job_id}/cancel"' in page


def test_cancelling_a_waiting_job_moves_it_to_history_and_the_worker_skips_it(client, downloads):
    job_id = new_job()
    response = client.post(f"/jobs/{job_id}/cancel", data={"csrf_token": csrf_token(client)})
    assert response.status_code == 302
    assert job_row(job_id)["status"] == "cancelled"
    assert "cancelled" in client.get("/").get_data(as_text=True)

    factory = fake_factory(PLAIN_VIDEO)
    worker._process_job(job_id, str(downloads), ydl_factory=factory)
    assert factory.downloads_started == 0
    assert job_row(job_id)["status"] == "cancelled"


def test_cancelling_a_running_download_stops_it_and_removes_the_partial_file(downloads):
    job_id = new_job()

    def cancel_after_first_chunk(chunk_number):
        worker.cancel_job(job_id)

    worker._process_job(job_id, str(downloads), ydl_factory=fake_factory(PLAIN_VIDEO, chunk_count=40,
                                                                          after_chunk=cancel_after_first_chunk))
    job = job_row(job_id)
    assert job["status"] == "cancelled"
    assert os.listdir(downloads) == []
    assert worker._recover_unfinished_jobs() == []


def test_cancel_needs_the_form_token(client):
    job_id = new_job()
    assert client.post(f"/jobs/{job_id}/cancel").status_code == 403
    assert job_row(job_id)["status"] == "queued"


def test_cancelling_a_finished_job_changes_nothing(client):
    job_id = new_job()
    with db.connect() as conn:
        db.update_job(conn, job_id, status="success")
    response = client.post(f"/jobs/{job_id}/cancel", data={"csrf_token": csrf_token(client)})
    assert response.status_code == 302
    assert job_row(job_id)["status"] == "success"
