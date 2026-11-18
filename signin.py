from __future__ import annotations

import base64
import binascii
import collections
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, Response, redirect, request
from markupsafe import Markup, escape
from werkzeug.http import parse_cookie

OWNER_FILE_NAME = "owner.json"
SESSION_SECONDS = 30 * 24 * 3600
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256
MAX_FIELD_LENGTH = 512
USER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._@+-]{1,64}$", re.ASCII)
SCRYPT_COST = 2 ** 14
SCRYPT_BLOCK = 8
SCRYPT_PARALLEL = 1
SCRYPT_LENGTH = 32
SALT_BYTES = 16
GENERATION_BYTES = 16
SETUP_CODE_BYTES = 9
FAILURES_PER_ADDRESS = 5
FAILURE_WINDOW_SECONDS = 15 * 60
MAX_TRACKED_ADDRESSES = 10000
SESSION_PURPOSE = b"owner session\x00"
LOOPBACK_HOST_NAMES = ("localhost", "127.0.0.1", "::1")
SECURE_COOKIE_CHOICES = ("auto", "true", "false")
SIGN_IN_PATH = "/signin"
SETUP_PATH = "/setup"
SIGN_OUT_PATH = "/signout"
STYLESHEET_PATH = "/signin.css"
SIGN_IN_PATHS = (SIGN_IN_PATH, SETUP_PATH, SIGN_OUT_PATH, STYLESHEET_PATH)
WRONG_DETAILS_TEXT = "wrong user name or password"
COOKIE_DROPPED_TEXT = ("The password was right, but the browser did not keep the sign-in. This happens on plain http: "
                       "open the app over https, or set SECURE_COOKIES=false on a home network you trust.")
SIGNED_IN_CHECK = "done"
STYLESHEET = """
:root { color-scheme: light dark; --page: #f4f4f5; --card: #ffffff; --text: #18181b; --muted: #52525b; --line: #d4d4d8; --accent: #b91c1c; }
@media (prefers-color-scheme: dark) { :root { --page: #0f0f11; --card: #1c1c1f; --text: #f4f4f5; --muted: #a1a1aa; --line: #3f3f46; --accent: #f87171; } }
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px; background: var(--page); color: var(--text); font: 16px/1.5 system-ui, sans-serif; }
main { width: 100%; max-width: 380px; background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 28px 24px; }
h1 { margin: 0 0 4px; font-size: 1.4rem; }
p { margin: 0 0 16px; color: var(--muted); }
label { display: block; margin: 12px 0 4px; font-weight: 600; }
input { width: 100%; min-height: 44px; padding: 8px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--page); color: var(--text); font: inherit; }
button { width: 100%; min-height: 44px; margin-top: 20px; border: 0; border-radius: 8px; background: var(--accent); color: #ffffff; font: inherit; font-weight: 600; cursor: pointer; }
.problem { color: var(--accent); font-weight: 600; }
"""

log = logging.getLogger(__name__)


class SignInError(ValueError):
    def __init__(self, message: str, status: int = 400, retry_after: int = 0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class OwnerFileError(RuntimeError):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_COST, r=SCRYPT_BLOCK, p=SCRYPT_PARALLEL, dklen=SCRYPT_LENGTH)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def password_matches(password: str, stored: str) -> bool:
    try:
        scheme, salt_text, digest_text = stored.split("$")
        salt = base64.b64decode(salt_text, validate=True)
        expected = base64.b64decode(digest_text, validate=True)
    except (ValueError, binascii.Error):
        return False
    if scheme != "scrypt" or not expected:
        return False
    actual = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_COST, r=SCRYPT_BLOCK, p=SCRYPT_PARALLEL, dklen=len(expected))
    return hmac.compare_digest(actual, expected)


def clean_user_name(value) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if not USER_NAME_PATTERN.match(name):
        raise SignInError("use 1 to 64 letters, digits or . _ @ + - for the user name")
    return name


def check_new_password(password, confirm) -> str:
    if not isinstance(password, str) or not isinstance(confirm, str):
        raise SignInError("enter the password twice")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SignInError(f"use at least {MIN_PASSWORD_LENGTH} characters for the password")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise SignInError("that password is too long")
    if password != confirm:
        raise SignInError("the two passwords do not match")
    return password


def host_name(host: str) -> str:
    try:
        return (urlsplit(f"//{host or ''}").hostname or "").lower()
    except ValueError:
        return ""


def _write_private_file(path: Path, record: dict) -> Path:
    descriptor, temporary = tempfile.mkstemp(prefix=".owner-", dir=path.parent)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(record, handle)
        handle.flush()
        os.fsync(handle.fileno())
    return Path(temporary)


class OwnerAccount:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._unused_hash = hash_password(secrets.token_hex(16))

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> dict | None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            record = json.loads(text)
        except ValueError as error:
            raise OwnerFileError(f"{self.path} is not readable JSON; stop the app, delete the file and start it again "
                                 "to make the owner account again") from error
        if not isinstance(record, dict) or not all(isinstance(record.get(key), str) for key in ("user", "password", "generation")):
            raise OwnerFileError(f"{self.path} is missing the user, password or generation field")
        return record

    def create(self, user: str, password: str) -> dict:
        record = {"user": user, "password": hash_password(password), "generation": secrets.token_hex(GENERATION_BYTES)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = _write_private_file(self.path, record)
        try:
            os.link(temporary, self.path)
        except FileExistsError as error:
            raise SignInError("setup is already finished", status=403) from error
        finally:
            temporary.unlink()
        return record

    def check(self, user: str, password: str) -> dict | None:
        record = self.read()
        stored = record["password"] if record else self._unused_hash
        password_is_right = password_matches(password, stored)
        if record is None or not password_is_right or record["user"] != user:
            return None
        return record

    def new_generation(self) -> None:
        with self._lock:
            record = self.read()
            if record is None:
                return
            record["generation"] = secrets.token_hex(GENERATION_BYTES)
            temporary = _write_private_file(self.path, record)
            os.replace(temporary, self.path)


class FailureLimiter:
    def __init__(self, clock=time.monotonic, per_address: int = FAILURES_PER_ADDRESS, window: float = FAILURE_WINDOW_SECONDS):
        self.clock = clock
        self.per_address = per_address
        self.window = window
        self._lock = threading.Lock()
        self._failures: collections.OrderedDict[str, collections.deque] = collections.OrderedDict()

    def _recent(self, address: str) -> collections.deque:
        moments = self._failures.get(address) or collections.deque()
        cutoff = self.clock() - self.window
        while moments and moments[0] <= cutoff:
            moments.popleft()
        return moments

    def seconds_blocked(self, address: str) -> int:
        with self._lock:
            moments = self._recent(address)
            if len(moments) < self.per_address:
                return 0
            return max(1, int(moments[0] + self.window - self.clock()) + 1)

    def failed(self, address: str) -> None:
        with self._lock:
            moments = self._recent(address)
            moments.append(self.clock())
            self._failures.pop(address, None)
            self._failures[address] = moments
            while len(self._failures) > MAX_TRACKED_ADDRESSES:
                self._failures.popitem(last=False)

    def forget(self, address: str) -> None:
        with self._lock:
            self._failures.pop(address, None)


def _encode(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode(text: str) -> dict | None:
    try:
        data = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
    except (ValueError, binascii.Error):
        return None
    return data if isinstance(data, dict) else None


class PasswordAuth:
    def __init__(self, owner: OwnerAccount, secret: bytes, cookie_name: str, secure_cookies: str = "auto",
                 allowed_hosts=(), clock=time.time, limiter: FailureLimiter | None = None):
        if secure_cookies not in SECURE_COOKIE_CHOICES:
            raise ValueError(f"secure_cookies must be one of {', '.join(SECURE_COOKIE_CHOICES)}, got {secure_cookies!r}")
        self.owner = owner
        self.secret = secret
        self.cookie_name = cookie_name
        self.secure_cookies = secure_cookies
        self.allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self.clock = clock
        self.limiter = limiter or FailureLimiter()
        self._setup_code: str | None = None
        self._setup_lock = threading.Lock()

    def accepts_host(self, host: str) -> bool:
        return not self.allowed_hosts or host_name(host) in self.allowed_hosts

    def needs_setup(self) -> bool:
        return not self.owner.exists()

    def entry_path(self) -> str:
        return SETUP_PATH if self.needs_setup() else SIGN_IN_PATH

    def setup_code(self) -> str:
        with self._setup_lock:
            if self._setup_code is None:
                self._setup_code = secrets.token_urlsafe(SETUP_CODE_BYTES)
                log.warning("no owner account yet: open %s and enter the setup code %s", SETUP_PATH, self._setup_code)
            return self._setup_code

    def _signature(self, payload: str) -> str:
        return hmac.new(self.secret, SESSION_PURPOSE + payload.encode(), hashlib.sha256).hexdigest()

    def _start_session(self, record: dict) -> str:
        payload = _encode({"user": record["user"], "expires": int(self.clock()) + SESSION_SECONDS, "generation": record["generation"]})
        return f"{payload}.{self._signature(payload)}"

    def user_for_token(self, token) -> str | None:
        if not isinstance(token, str) or token.count(".") != 1:
            return None
        payload, signature = token.split(".")
        if not hmac.compare_digest(self._signature(payload), signature):
            return None
        data = _decode(payload)
        record = self.owner.read()
        if data is None or record is None:
            return None
        expires = data.get("expires")
        if not isinstance(expires, int) or expires <= self.clock():
            return None
        if data.get("user") != record["user"] or data.get("generation") != record["generation"]:
            return None
        return record["user"]

    def user_for(self, remote_address: str, headers) -> str | None:
        return self.user_for_token(parse_cookie(headers.get("Cookie") or "").get(self.cookie_name))

    def _refuse_if_blocked(self, address: str) -> None:
        wait = self.limiter.seconds_blocked(address)
        if wait:
            raise SignInError(f"too many wrong attempts, try again in {wait // 60 + 1} min", status=429, retry_after=wait)

    def sign_in(self, user, password, address: str) -> str:
        if not isinstance(user, str) or not isinstance(password, str) or len(user) > MAX_FIELD_LENGTH or len(password) > MAX_PASSWORD_LENGTH:
            raise SignInError("the user name or password is too long")
        self._refuse_if_blocked(address)
        record = self.owner.check(user.strip(), password)
        if record is None:
            self.limiter.failed(address)
            log.warning("wrong sign-in from %s", address)
            raise SignInError(WRONG_DETAILS_TEXT, status=401)
        self.limiter.forget(address)
        return self._start_session(record)

    def set_up(self, code, user, password, confirm, address: str) -> str:
        if not self.needs_setup():
            raise SignInError("setup is already finished", status=403)
        self._refuse_if_blocked(address)
        if not isinstance(code, str) or not hmac.compare_digest(code.strip().encode(), self.setup_code().encode()):
            self.limiter.failed(address)
            log.warning("wrong setup code from %s", address)
            raise SignInError("that setup code is wrong; the app writes it to its log when it starts", status=401)
        record = self.owner.create(clean_user_name(user), check_new_password(password, confirm))
        log.info("owner account %r made from %s", record["user"], address)
        return self._start_session(record)

    def sign_out(self, token) -> None:
        if self.user_for_token(token) is not None:
            self.owner.new_generation()

    def cookie_is_secure(self, is_https: bool, host: str) -> bool:
        if self.secure_cookies != "auto":
            return self.secure_cookies == "true"
        return is_https or host_name(host) not in LOOPBACK_HOST_NAMES


def _is_same_origin() -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc == request.host


def _field(name: str) -> str:
    value = request.form.get(name, "")
    if len(value) > MAX_FIELD_LENGTH:
        raise SignInError(f"the {name} field is too long")
    return value


def _page(app_name: str, heading: str, intro: str, form: Markup, problem: str = "", status: int = 200) -> Response:
    problem_html = Markup(f'<p class="problem" role="alert">{escape(problem)}</p>') if problem else Markup("")
    body = Markup(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(heading)} - {escape(app_name)}</title>"
        f'<link rel="stylesheet" href="{STYLESHEET_PATH}"></head>'
        f"<body><main><h1>{escape(app_name)}</h1><p>{escape(intro)}</p>{problem_html}{form}</main></body></html>"
    )
    response = Response(str(body), status=status, mimetype="text/html")
    response.headers["Cache-Control"] = "no-store"
    return response


def _text_input(label: str, name: str, kind: str, autocomplete: str) -> Markup:
    return Markup(f'<label for="{name}">{escape(label)}</label>'
                  f'<input id="{name}" name="{name}" type="{kind}" autocomplete="{autocomplete}" required>')


def add_sign_in_pages(app: Flask, auth: PasswordAuth, app_name: str) -> None:
    def sign_in_page(problem: str = "", status: int = 200) -> Response:
        form = Markup(f'<form method="post" action="{SIGN_IN_PATH}">') + _text_input("User name", "user", "text", "username") \
            + _text_input("Password", "password", "password", "current-password") + Markup("<button>Sign in</button></form>")
        return _page(app_name, "Sign in", "Sign in with the owner account.", form, problem, status)

    def setup_page(problem: str = "", status: int = 200) -> Response:
        auth.setup_code()
        form = Markup(f'<form method="post" action="{SETUP_PATH}">') + _text_input("Setup code", "code", "text", "one-time-code") \
            + _text_input("User name", "user", "text", "username") \
            + _text_input("Password", "password", "password", "new-password") \
            + _text_input("Password again", "confirm", "password", "new-password") + Markup("<button>Make the account</button></form>")
        intro = (f"First run: make the owner account. The setup code is in the app's log. "
                 f"Use at least {MIN_PASSWORD_LENGTH} characters for the password.")
        return _page(app_name, "Set up", intro, form, problem, status)

    def signed_in(token: str) -> Response:
        response = redirect(f"{SIGN_IN_PATH}?{SIGNED_IN_CHECK}=1", code=303)
        response.set_cookie(auth.cookie_name, token, max_age=SESSION_SECONDS, path="/", httponly=True, samesite="Lax",
                            secure=auth.cookie_is_secure(request.is_secure, request.host))
        return response

    def refused(error: SignInError, page) -> Response:
        response = page(str(error), error.status)
        if error.retry_after:
            response.headers["Retry-After"] = str(error.retry_after)
        return response

    @app.get(STYLESHEET_PATH)
    def sign_in_stylesheet():
        return Response(STYLESHEET, mimetype="text/css")

    @app.get(SIGN_IN_PATH)
    def show_sign_in():
        if auth.needs_setup():
            return redirect(SETUP_PATH, code=303)
        if auth.user_for_token(request.cookies.get(auth.cookie_name)) is not None:
            return redirect("/", code=303)
        return sign_in_page(COOKIE_DROPPED_TEXT if request.args.get(SIGNED_IN_CHECK) else "")

    @app.post(SIGN_IN_PATH)
    def sign_in():
        if not _is_same_origin():
            return sign_in_page("this form was sent from another site", 403)
        if auth.needs_setup():
            return redirect(SETUP_PATH, code=303)
        try:
            token = auth.sign_in(_field("user"), _field("password"), request.remote_addr or "")
        except SignInError as error:
            return refused(error, sign_in_page)
        return signed_in(token)

    @app.get(SETUP_PATH)
    def show_setup():
        if not auth.needs_setup():
            return redirect(SIGN_IN_PATH, code=303)
        return setup_page()

    @app.post(SETUP_PATH)
    def set_up():
        if not _is_same_origin():
            return setup_page("this form was sent from another site", 403)
        try:
            token = auth.set_up(_field("code"), _field("user"), _field("password"), _field("confirm"), request.remote_addr or "")
        except SignInError as error:
            if error.status == 403:
                return redirect(SIGN_IN_PATH, code=303)
            return refused(error, setup_page)
        return signed_in(token)

    @app.get(SIGN_OUT_PATH)
    def show_sign_out():
        if auth.user_for_token(request.cookies.get(auth.cookie_name)) is None:
            return redirect(auth.entry_path(), code=303)
        form = Markup(f'<form method="post" action="{SIGN_OUT_PATH}"><button>Sign out</button></form>')
        return _page(app_name, "Sign out", "Signing out ends the session on every device.", form)

    @app.post(SIGN_OUT_PATH)
    def sign_out():
        if not _is_same_origin():
            return Response("this form was sent from another site", status=403, mimetype="text/plain")
        auth.sign_out(request.cookies.get(auth.cookie_name))
        response = redirect(SIGN_IN_PATH, code=303)
        response.delete_cookie(auth.cookie_name, path="/", httponly=True, samesite="Lax",
                               secure=auth.cookie_is_secure(request.is_secure, request.host))
        return response
