import base64
import hashlib
import logging
import socket
import threading
import time

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import signin
from signin import FailureLimiter, OwnerAccount, PasswordAuth, SignInError, check_new_password

SECRET = b"k" * 32
OTHER_SECRET = b"o" * 32
COOKIE = "app_session"
OWNER_NAME = "owner"
PASSWORD = "correct horse battery"
WRONG = "wrong password!"
THIRTY_DAYS = 30 * 24 * 3600
FIFTEEN_MINUTES = 15 * 60
ATTACKER = "203.0.113.9"
OWNER_ADDRESS = "198.51.100.4"
PROXY = "192.0.2.10"


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def auth(tmp_path, clock):
    return PasswordAuth(OwnerAccount(tmp_path / "owner.json"), SECRET, COOKIE, clock=clock, limiter=FailureLimiter(clock=clock))


def new_auth(tmp_path, clock, **options):
    options.setdefault("limiter", FailureLimiter(clock=clock))
    options.setdefault("device_limiter", FailureLimiter(clock=clock, before_strict=None))
    return PasswordAuth(OwnerAccount(tmp_path / "owner.json"), SECRET, COOKIE, clock=clock, **options)


def set_up(auth, address=OWNER_ADDRESS):
    return auth.set_up(auth.setup_code(), OWNER_NAME, PASSWORD, PASSWORD, address)


def status_of(action) -> int:
    try:
        action()
    except SignInError as error:
        return error.status
    return 200


def wrong_sign_in(auth, address, device=None) -> int:
    if device is None:
        return status_of(lambda: auth.sign_in(OWNER_NAME, WRONG, address))
    return status_of(lambda: auth.sign_in(OWNER_NAME, WRONG, address, device))


def test_wrong_passwords_sent_at_once_cannot_outrun_the_limit(auth, monkeypatch):
    set_up(auth)
    checked = []
    real_check = auth.owner.check

    def slow_check(user, password):
        checked.append(user)
        time.sleep(0.3)
        return real_check(user, password)

    monkeypatch.setattr(auth.owner, "check", slow_check)
    start = threading.Barrier(16)
    statuses = []

    def attempt():
        start.wait()
        statuses.append(wrong_sign_in(auth, ATTACKER))

    threads = [threading.Thread(target=attempt) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(checked) == 5
    assert sorted(statuses) == [401] * 5 + [429] * 11


@pytest.mark.parametrize("address, bucket", [
    ("203.0.113.9", "203.0.113.9"),
    ("::ffff:203.0.113.9", "203.0.113.9"),
    ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
    ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
    ("not an address", "not an address"),
])
def test_ipv6_addresses_are_counted_by_their_64_network(address, bucket):
    assert signin.failure_bucket(address) == bucket


def test_addresses_in_one_ipv6_network_share_one_allowance(auth):
    set_up(auth)
    for host in range(1, 6):
        assert wrong_sign_in(auth, f"2001:db8:1:2::{host}") == 401
    assert wrong_sign_in(auth, "2001:db8:1:2::ffff") == 429
    assert wrong_sign_in(auth, "2001:db8:1:3::1") == 401


def test_many_failures_from_everywhere_leave_each_address_one_try(tmp_path, clock):
    auth = new_auth(tmp_path, clock, limiter=FailureLimiter(clock=clock, before_strict=4))
    set_up(auth)
    for host in range(1, 5):
        assert wrong_sign_in(auth, f"203.0.113.{host}") == 401
    assert wrong_sign_in(auth, "198.51.100.1") == 401
    assert wrong_sign_in(auth, "198.51.100.1") == 429
    clock.now += FIFTEEN_MINUTES + 1
    assert wrong_sign_in(auth, "198.51.100.1") == 401
    assert wrong_sign_in(auth, "198.51.100.1") == 401


def test_a_blocked_address_is_not_pushed_out_by_other_addresses(clock):
    limiter = FailureLimiter(clock=clock, max_tracked=10)
    for _attempt in range(5):
        assert limiter.take_turn(ATTACKER) == 0
    for host in range(1, 51):
        limiter.take_turn(f"198.51.100.{host}")
    assert limiter.seconds_blocked(ATTACKER) > 0
    assert limiter.take_turn(ATTACKER) > 0


def test_a_full_table_of_blocked_addresses_refuses_newcomers_until_one_expires(clock):
    limiter = FailureLimiter(clock=clock, per_address=1, before_strict=None, max_tracked=3)
    for host in range(1, 4):
        assert limiter.take_turn(f"198.51.100.{host}") == 0
    assert limiter.take_turn("198.51.100.200") > 0
    clock.now += FIFTEEN_MINUTES + 1
    assert limiter.take_turn("198.51.100.200") == 0


def test_a_right_password_gives_its_turn_back(auth):
    set_up(auth)
    for _round in range(3):
        for _attempt in range(4):
            assert wrong_sign_in(auth, ATTACKER) == 401
        assert auth.sign_in(OWNER_NAME, PASSWORD, ATTACKER)
    assert auth.limiter.seconds_blocked(ATTACKER) == 0


@pytest.mark.parametrize("peer, forwarded, client", [
    (PROXY, "203.0.113.9", "203.0.113.9"),
    (PROXY, "198.51.100.1, 203.0.113.9", "203.0.113.9"),
    (PROXY, "203.0.113.9, 10.1.1.1", "203.0.113.9"),
    (PROXY, "10.1.1.1", "10.1.1.1"),
    (PROXY, "", PROXY),
    (PROXY, "not-an-address", PROXY),
    (PROXY, "203.0.113.9, not-an-address", PROXY),
    ("192.0.2.11", "203.0.113.9", "192.0.2.11"),
    ("::1", "203.0.113.9", "::1"),
])
def test_the_forwarded_address_is_believed_only_from_a_trusted_proxy(tmp_path, clock, peer, forwarded, client):
    auth = new_auth(tmp_path, clock, trusted_proxies=(PROXY, "10.0.0.0/8"))
    headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    assert auth.client_address(peer, headers) == client


def test_without_trusted_proxies_the_forwarded_address_is_never_read(auth):
    assert auth.client_address("127.0.0.1", {"X-Forwarded-For": ATTACKER}) == "127.0.0.1"


@pytest.mark.parametrize("host", ["localhost:8097", "LOCALHOST", "127.0.0.1:8097", "[::1]:8097", "10.1.2.3:8097",
                                  "[fd00::5]:80", "notes.localhost:8097"])
def test_the_own_sign_in_answers_on_local_names_by_default(auth, host):
    assert auth.accepts_host(host)


def test_the_own_sign_in_answers_on_this_machine_s_name_by_default(auth):
    assert auth.accepts_host(socket.gethostname() + ":8097")


@pytest.mark.parametrize("host", ["rebind.evil.example:8097", "localhost.evil.example", "", "127.0.0.1:8097@evil.example"])
def test_the_own_sign_in_refuses_other_names_by_default(auth, host):
    assert not auth.accepts_host(host)


def test_allowed_hosts_replace_the_default_names(tmp_path, clock):
    auth = new_auth(tmp_path, clock, allowed_hosts=("Notes.Example.com",))
    assert auth.accepts_host("notes.example.com:443")
    assert not auth.accepts_host("localhost:8097")


def test_a_known_browser_keeps_its_own_allowance(tmp_path, clock):
    auth = new_auth(tmp_path, clock)
    set_up(auth)
    device = auth.new_device_token()
    for _attempt in range(6):
        wrong_sign_in(auth, ATTACKER)
    assert wrong_sign_in(auth, ATTACKER) == 429
    assert auth.sign_in(OWNER_NAME, PASSWORD, ATTACKER, device)


def test_a_known_browser_is_limited_too(tmp_path, clock):
    auth = new_auth(tmp_path, clock)
    set_up(auth)
    device = auth.new_device_token()
    for _attempt in range(5):
        assert wrong_sign_in(auth, OWNER_ADDRESS, device) == 401
    assert wrong_sign_in(auth, OWNER_ADDRESS, device) == 429
    assert wrong_sign_in(auth, OWNER_ADDRESS) == 401


@pytest.mark.parametrize("token", ["", "no-dot", "a.b.c", "abc." + "0" * 64, ".", "é.é", None])
def test_a_forged_device_cookie_counts_as_the_address(tmp_path, clock, token):
    auth = new_auth(tmp_path, clock)
    set_up(auth)
    assert auth.device_for_token(token) is None
    for _attempt in range(5):
        wrong_sign_in(auth, ATTACKER, token)
    assert status_of(lambda: auth.sign_in(OWNER_NAME, PASSWORD, ATTACKER, token)) == 429


def test_a_device_cookie_from_another_key_is_not_believed(tmp_path, clock):
    other = PasswordAuth(OwnerAccount(tmp_path / "other.json"), OTHER_SECRET, COOKIE)
    assert new_auth(tmp_path, clock).device_for_token(other.new_device_token()) is None


def test_the_setup_code_works_only_once(auth, caplog):
    caplog.set_level(logging.WARNING)
    first_code = auth.setup_code()
    auth.set_up(first_code, OWNER_NAME, PASSWORD, PASSWORD, OWNER_ADDRESS)
    auth.owner.path.unlink()
    assert status_of(lambda: auth.set_up(first_code, "intruder", PASSWORD, PASSWORD, ATTACKER)) == 401
    assert not auth.owner.exists()
    second_code = auth.setup_code()
    assert second_code != first_code
    assert second_code in caplog.text
    assert auth.set_up(second_code, OWNER_NAME, PASSWORD, PASSWORD, OWNER_ADDRESS)


def test_a_right_code_with_a_weak_password_keeps_the_code(auth):
    code = auth.setup_code()
    assert status_of(lambda: auth.set_up(code, OWNER_NAME, "short", "short", OWNER_ADDRESS)) == 400
    assert auth.set_up(code, OWNER_NAME, PASSWORD, PASSWORD, OWNER_ADDRESS)


def test_setup_after_the_owner_exists_is_refused_without_counting(auth):
    set_up(auth)
    for _attempt in range(6):
        assert status_of(lambda: auth.set_up("wrong-code", "intruder", PASSWORD, PASSWORD, ATTACKER)) == 403
    assert auth.sign_in(OWNER_NAME, PASSWORD, ATTACKER)


def test_wrong_setup_codes_are_counted(auth):
    code = auth.setup_code()
    for _attempt in range(5):
        assert status_of(lambda: auth.set_up("wrong-code", OWNER_NAME, PASSWORD, PASSWORD, ATTACKER)) == 401
    assert status_of(lambda: auth.set_up(code, OWNER_NAME, PASSWORD, PASSWORD, ATTACKER)) == 429
    assert not auth.owner.exists()


def test_twelve_setups_at_once_make_exactly_one_owner(auth):
    code = auth.setup_code()
    start = threading.Barrier(12)
    made = []

    def attempt(number):
        start.wait()
        try:
            auth.set_up(code, f"user{number}", PASSWORD, PASSWORD, f"198.51.100.{number}")
        except SignInError:
            return
        made.append(f"user{number}")

    threads = [threading.Thread(target=attempt, args=(number,)) for number in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(made) == 1
    assert auth.owner.read()["user"] == made[0]


def test_a_second_owner_file_is_never_written_over(tmp_path):
    owner = OwnerAccount(tmp_path / "owner.json")
    owner.create(OWNER_NAME, PASSWORD)
    with pytest.raises(SignInError):
        owner.create("intruder", PASSWORD)
    assert owner.read()["user"] == OWNER_NAME


def test_the_user_name_must_match_too(auth):
    set_up(auth)
    assert status_of(lambda: auth.sign_in("someone-else", PASSWORD, OWNER_ADDRESS)) == 401


def test_a_too_long_password_is_refused_before_any_check(auth, monkeypatch):
    set_up(auth)
    monkeypatch.setattr(auth.owner, "check", lambda user, password: pytest.fail("the password was checked"))
    assert status_of(lambda: auth.sign_in(OWNER_NAME, "x" * 257, OWNER_ADDRESS)) == 400


@pytest.mark.parametrize("password", ["a", "123456789"])
def test_a_short_password_is_refused_even_when_typed_twice(password):
    with pytest.raises(SignInError, match="at least 10"):
        check_new_password(password, password)


def test_a_session_signed_with_another_key_is_refused(auth):
    token = set_up(auth)
    impostor = PasswordAuth(auth.owner, OTHER_SECRET, COOKIE, clock=auth.clock)
    assert auth.user_for_token(token) == OWNER_NAME
    assert impostor.user_for_token(token) is None
    assert auth.user_for_token(impostor._start_session(auth.owner.read())) is None


def test_a_session_for_another_user_name_is_refused(auth):
    set_up(auth)
    record = dict(auth.owner.read(), user="someone-else")
    assert auth.user_for_token(auth._start_session(record)) is None


def test_a_session_lasts_thirty_days(auth, clock):
    token = set_up(auth)
    clock.now += THIRTY_DAYS - 1
    assert auth.user_for_token(token) == OWNER_NAME
    clock.now += 2
    assert auth.user_for_token(token) is None


def test_the_password_hash_uses_the_full_scrypt_cost():
    scheme, salt, digest = signin.hash_password(PASSWORD).split("$")
    expected = hashlib.scrypt(PASSWORD.encode(), salt=base64.b64decode(salt), n=2 ** 14, r=8, p=1, dklen=32)
    assert scheme == "scrypt"
    assert base64.b64decode(digest) == expected
