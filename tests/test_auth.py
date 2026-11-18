import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app
import db

USERNAME = "owner"
PASSWORD_WITH_POUND_SIGN = "p£ssword"


def basic_auth(username, password):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(app, "WEB_USERNAME", USERNAME)
    monkeypatch.setattr(app, "WEB_PASSWORD", PASSWORD_WITH_POUND_SIGN)
    return app.app.test_client()


def test_a_password_with_a_non_ascii_character_signs_in(client):
    assert client.get("/", headers=basic_auth(USERNAME, PASSWORD_WITH_POUND_SIGN)).status_code == 200


def test_a_wrong_password_is_refused_when_the_real_one_is_not_ascii(client):
    assert client.get("/", headers=basic_auth(USERNAME, "wrong")).status_code == 401


def test_a_non_ascii_username_is_refused_not_crashed(client):
    assert client.get("/", headers=basic_auth("dréw", "wrong")).status_code == 401


def test_no_credentials_are_refused(client):
    assert client.get("/").status_code == 401


def test_a_non_ascii_csrf_token_is_refused_not_crashed(client):
    headers = basic_auth(USERNAME, PASSWORD_WITH_POUND_SIGN)
    client.get("/", headers=headers)
    response = client.post("/jobs", headers=headers, data={"csrf_token": "töken", "urls": "https://example.com/a"})
    assert response.status_code == 403
    with db.connect() as conn:
        assert db.list_active_jobs(conn) == []
