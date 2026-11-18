import os
import queue
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app
import db
import worker
from fake_ytdl import fake_factory

MAX_LINKS = 50
MAX_LINK_LENGTH = 2000
PLENTY_OF_SPACE = 500 * 1024 * 1024 * 1024


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(worker, "_job_queue", queue.Queue())
    monkeypatch.setattr(worker, "_free_bytes", lambda folder: PLENTY_OF_SPACE)
    monkeypatch.setattr(app, "WEB_USERNAME", "")
    monkeypatch.setattr(app, "WEB_PASSWORD", "")
    monkeypatch.setattr(app, "ALLOW_INSECURE", True)
    folder = tmp_path / "downloads"
    folder.mkdir()
    return folder


@pytest.fixture
def client(downloads):
    return app.app.test_client()


def submit(client, urls):
    client.get("/")
    with client.session_transaction() as session:
        token = session["_csrf_token"]
    return client.post("/jobs", data={"csrf_token": token, "urls": urls, "quality": "best"})


def queued_urls():
    with db.connect() as conn:
        return [job["url"] for job in db.list_active_jobs(conn)]


def test_good_links_are_queued(client):
    response = submit(client, "https://www.youtube.com/watch?v=abc\nhttp://example.com/clip.mp4")
    assert response.status_code == 302
    assert queued_urls() == ["https://www.youtube.com/watch?v=abc", "http://example.com/clip.mp4"]


def test_more_than_fifty_links_at_once_are_refused(client):
    response = submit(client, "\n".join(f"https://example.com/{n}" for n in range(MAX_LINKS + 1)))
    assert response.status_code == 400
    assert "50" in response.get_data(as_text=True)
    assert queued_urls() == []


def test_fifty_links_are_still_accepted(client):
    assert submit(client, "\n".join(f"https://example.com/{n}" for n in range(MAX_LINKS))).status_code == 302
    assert len(queued_urls()) == MAX_LINKS


def test_an_over_long_link_is_refused(client):
    response = submit(client, "https://example.com/" + "x" * MAX_LINK_LENGTH)
    assert response.status_code == 400
    assert queued_urls() == []


def test_a_huge_form_body_is_refused_before_it_is_read(client):
    response = submit(client, "x" * (2 * 1024 * 1024))
    assert response.status_code == 413
    assert queued_urls() == []


@pytest.mark.parametrize("url", [
    "not a url",
    "file:///etc/hostname",
    "javascript:alert(1)",
    "ftp://example.com/a.mp4",
    "ytsearch5:cats",
    "--version",
    "http://",
    "http://example.com:99999/a",
])
def test_anything_but_an_http_or_https_link_is_refused(client, url):
    response = submit(client, url)
    assert response.status_code == 400
    assert queued_urls() == []


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:5002/status",
    "http://localhost/",
    "http://[::1]/",
    "http://192.168.1.10:5003/",
    "http://nas.lan/video.mp4",
    "http://rebind.example/",
    "http://metadata.example/latest",
    "http://2130706433/",
    "http://[::ffff:127.0.0.1]/",
    "http://0.0.0.0/",
    "http://no-such-host.invalid/",
])
def test_links_to_private_local_or_unknown_hosts_are_refused(client, url):
    response = submit(client, url)
    assert response.status_code == 400
    assert queued_urls() == []


def test_one_bad_line_names_its_line_and_queues_nothing(client):
    response = submit(client, "https://example.com/a\nhttp://nas.lan/b")
    page = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "Line 2" in page
    assert queued_urls() == []


def run_job(downloads, url, factory):
    with db.connect() as conn:
        job_id = db.create_job(conn, url, "best")
    worker._process_job(job_id, str(downloads), ydl_factory=factory)
    with db.connect() as conn:
        return db.get_job(conn, job_id)


def test_a_private_link_already_in_the_queue_is_refused_by_the_worker(downloads):
    factory = fake_factory({"id": "v1", "title": "clip", "duration": 10})
    job = run_job(downloads, "http://192.168.1.10:5003/", factory)
    assert job["status"] == "error"
    assert "private" in job["error"]
    assert factory.downloads_started == 0


def test_a_public_page_whose_video_is_on_a_private_address_is_refused(downloads):
    info = {"id": "v1", "title": "clip", "duration": 10,
            "requested_formats": [{"url": "http://192.168.1.5/secret.mp4"}, {"url": "https://cdn.example/a.m4a"}]}
    factory = fake_factory(info)
    job = run_job(downloads, "https://example.com/page", factory)
    assert job["status"] == "error"
    assert "private" in job["error"]
    assert factory.downloads_started == 0


def test_long_media_addresses_from_a_public_host_are_fine(downloads):
    info = {"id": "v1", "title": "clip", "duration": 10, "url": "https://cdn.example/v?sig=" + "a" * 3000}
    job = run_job(downloads, "https://example.com/page", fake_factory(info))
    assert job["status"] == "success"
