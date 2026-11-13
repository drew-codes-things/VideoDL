import os
import queue
import sqlite3
import sys

import yt_dlp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db
import worker

LINUX_FILE_NAME_LIMIT_BYTES = 255


def use_scratch_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(worker, "_job_queue", queue.Queue())


def queued_ids():
    ids = []
    while not worker._job_queue.empty():
        ids.append(worker._job_queue.get_nowait())
    return ids


def test_jobs_still_waiting_at_restart_are_queued_again(tmp_path, monkeypatch):
    use_scratch_database(tmp_path, monkeypatch)
    with db.connect() as conn:
        interrupted = db.create_job(conn, "https://example.com/a", "best")
        db.update_job(conn, interrupted, status="downloading")
        waiting = db.create_job(conn, "https://example.com/b", "best")
        finished = db.create_job(conn, "https://example.com/c", "best")
        db.update_job(conn, finished, status="success")

    worker._recover_unfinished_jobs()

    assert queued_ids() == [interrupted, waiting]
    with db.connect() as conn:
        assert db.get_job(conn, interrupted)["status"] == "queued"


def test_long_titles_still_give_a_file_name_the_disk_accepts(tmp_path):
    options = {"outtmpl": worker.output_template(str(tmp_path)), "quiet": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        path = ydl.prepare_filename({"title": "long post text " * 40, "id": "7301234567890123456", "ext": "mp4"})
    part_file_name = os.path.basename(path) + ".f137.mp4.part"
    assert len(part_file_name.encode()) <= LINUX_FILE_NAME_LIMIT_BYTES
    with open(path, "w") as handle:
        handle.write("")


def test_a_job_with_a_removed_quality_is_marked_failed(tmp_path, monkeypatch):
    use_scratch_database(tmp_path, monkeypatch)
    with db.connect() as conn:
        job_id = db.create_job(conn, "https://example.com/a", "4k")

    worker._process_job(job_id, str(tmp_path))

    with db.connect() as conn:
        job = db.get_job(conn, job_id)
    assert job["status"] == "error"
    assert "4k" in job["error"]


def test_a_crashing_job_does_not_stop_the_worker(tmp_path, monkeypatch, capsys):
    use_scratch_database(tmp_path, monkeypatch)

    def crash(job_id, download_dir):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(worker, "_process_job", crash)
    worker.enqueue(41)

    worker._process_next_job(str(tmp_path))

    assert worker._job_queue.unfinished_tasks == 0
    assert "job 41" in capsys.readouterr().out


def test_a_job_interrupted_by_restarts_too_often_is_failed_not_queued(tmp_path, monkeypatch):
    use_scratch_database(tmp_path, monkeypatch)
    with db.connect() as conn:
        stuck = db.create_job(conn, "https://example.com/endless", "best")
        conn.execute("UPDATE jobs SET status = 'downloading', attempts = ? WHERE id = ?",
                     (worker.MAX_START_ATTEMPTS, stuck))
        conn.commit()

    assert worker._recover_unfinished_jobs() == []

    with db.connect() as conn:
        job = db.get_job(conn, stuck)
    assert job["status"] == "error"
    assert "restart" in job["error"]
    assert job["finished_at"]


def test_starting_a_job_counts_an_attempt(tmp_path, monkeypatch):
    use_scratch_database(tmp_path, monkeypatch)
    with db.connect() as conn:
        job_id = db.create_job(conn, "https://example.com/a", "4k")

    worker._process_job(job_id, str(tmp_path))

    with db.connect() as conn:
        assert db.get_job(conn, job_id)["attempts"] == 1


def test_a_database_from_before_attempts_were_counted_is_upgraded(tmp_path, monkeypatch):
    use_scratch_database(tmp_path, monkeypatch)
    old = sqlite3.connect(db.DB_PATH)
    old.executescript(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, quality TEXT NOT NULL, "
        "resolved_quality TEXT, title TEXT, filepath TEXT, status TEXT NOT NULL DEFAULT 'queued', error TEXT, "
        "queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);"
        "INSERT INTO jobs (url, quality, status, queued_at) VALUES ('https://example.com/a', 'best', 'downloading', 'x');"
    )
    old.close()

    assert worker._recover_unfinished_jobs() == [1]
    with db.connect() as conn:
        assert db.get_job(conn, 1)["attempts"] == 0
