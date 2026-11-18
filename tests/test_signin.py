import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app
import db
from signin import SESSION_SECONDS, FailureLimiter, OwnerAccount

COOKIE = "videodl_session"
OWNER_NAME = "owner"
PASSWORD = "correct horse battery"
HEADER = "X-Forwarded-User"


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def web(tmp_path, monkeypatch, clock):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(app, "WEB_USERNAME", "")
    monkeypatch.setattr(app, "WEB_PASSWORD", "")
    monkeypatch.setattr(app, "ALLOW_INSECURE", False)
    monkeypatch.setattr(app.OWN_SIGN_IN, "owner", OwnerAccount(tmp_path / "owner.json"))
    monkeypatch.setattr(app.OWN_SIGN_IN, "limiter", FailureLimiter(clock=clock))
    monkeypatch.setattr(app.OWN_SIGN_IN, "clock", clock)
    monkeypatch.setattr(app.OWN_SIGN_IN, "_setup_code", None)
    return app.app.test_client()


def set_up_owner(web, form=None, **kwargs):
    details = {"code": app.OWN_SIGN_IN.setup_code(), "user": OWNER_NAME, "password": PASSWORD, "confirm": PASSWORD}
    details.update(form or {})
    return web.post("/setup", data=details, **kwargs)


def sign_in(web, password=PASSWORD, **kwargs):
    return web.post("/signin", data={"user": OWNER_NAME, "password": password}, **kwargs)


def session_cookie(response) -> str:
    return next(value for value in response.headers.getlist("Set-Cookie") if value.startswith(COOKIE + "="))


def test_the_own_sign_in_is_the_default(web):
    assert app.sign_in_method() == "builtin"


def test_a_signed_out_visit_goes_to_setup_then_to_sign_in(web):
    assert web.get("/").headers["Location"].endswith("/setup")
    set_up_owner(web)
    web.delete_cookie(COOKIE)
    assert web.get("/").headers["Location"].endswith("/signin")


def test_status_and_writes_need_a_sign_in(web):
    assert web.get("/status").status_code == 401
    assert web.post("/jobs", data={"urls": "https://example.com/a"}).status_code == 401
    with db.connect() as conn:
        assert db.list_active_jobs(conn) == []


def test_the_user_header_is_ignored(web):
    set_up_owner(web)
    web.delete_cookie(COOKIE)
    assert web.get("/status", headers={HEADER: OWNER_NAME}).status_code == 401


def test_setup_needs_the_code_and_happens_once(web, tmp_path):
    assert set_up_owner(web, form={"code": "not-the-code"}).status_code == 401
    assert set_up_owner(web).status_code == 303
    assert stat.S_IMODE(os.stat(tmp_path / "owner.json").st_mode) == 0o600
    assert web.get("/").status_code == 200
    assert web.get("/status").status_code == 200
    assert set_up_owner(web, form={"user": "intruder"}).headers["Location"].endswith("/signin")
    assert app.OWN_SIGN_IN.owner.read()["user"] == OWNER_NAME


def test_wrong_passwords_are_rate_limited(web):
    set_up_owner(web)
    web.delete_cookie(COOKIE)
    for _attempt in range(5):
        assert sign_in(web, password="wrong password!").status_code == 401
    assert sign_in(web).status_code == 429
    assert sign_in(web, environ_base={"REMOTE_ADDR": "192.0.2.7"}).status_code == 303


def test_signing_out_and_expiry_end_the_session(web, clock):
    set_up_owner(web)
    assert web.get("/status").status_code == 200
    clock.now += SESSION_SECONDS + 1
    assert web.get("/status").status_code == 401
    clock.now -= SESSION_SECONDS + 1
    assert web.get("/status").status_code == 200
    assert web.post("/signout").status_code == 303
    assert web.get("/status").status_code == 401


@pytest.mark.parametrize("token", ["\u00e9.\u00e9", "abc.\udcff", "no-dot", "", None])
def test_an_odd_session_value_is_refused_not_crashed(web, token):
    assert app.OWN_SIGN_IN.user_for_token(token) is None


def test_the_session_cookie_flags(web):
    local = set_up_owner(web)
    assert "HttpOnly" in session_cookie(local) and "SameSite=Lax" in session_cookie(local)
    assert "Secure" not in session_cookie(local)
    remote = sign_in(web, base_url="http://videodl.example.com")
    assert "Secure" in session_cookie(remote)


def test_the_gate_setting_keeps_the_proxy_in_charge(web, monkeypatch):
    monkeypatch.setattr(app, "ALLOW_INSECURE", True)
    assert app.sign_in_method() == "proxy"
    assert web.get("/").status_code == 200
    assert web.get("/setup").status_code == 404
    assert web.get("/signin").status_code == 404


def test_basic_credentials_still_win_when_set(web, monkeypatch):
    monkeypatch.setattr(app, "WEB_USERNAME", "drew")
    monkeypatch.setattr(app, "WEB_PASSWORD", "a long basic password")
    assert app.sign_in_method() == "basic"
    assert web.get("/").status_code == 401
    assert web.get("/setup").status_code == 404
