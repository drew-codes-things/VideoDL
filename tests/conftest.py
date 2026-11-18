import ipaddress
import os
import shutil
import socket
import tempfile

import pytest

TEST_STATE_DIR = tempfile.mkdtemp(prefix="videodl-tests-")
os.environ.setdefault("VIDEODL_DB_PATH", os.path.join(TEST_STATE_DIR, "videodl.db"))

PUBLIC_ADDRESS = "93.184.215.14"
FAKE_DNS = {
    "nas.lan": ["192.168.1.5"],
    "localhost": ["127.0.0.1"],
    "rebind.example": [PUBLIC_ADDRESS, "10.0.0.7"],
    "metadata.example": ["169.254.169.254"],
}


def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host == "no-such-host.invalid":
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    try:
        addresses = [str(ipaddress.ip_address(int(host) if host.isdigit() else host))]
    except ValueError:
        addresses = FAKE_DNS.get(host, [PUBLIC_ADDRESS])
    results = []
    for address in addresses:
        if ":" in address:
            results.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, port or 0, 0, 0)))
        else:
            results.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0)))
    return results


@pytest.fixture(autouse=True)
def no_real_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def pytest_unconfigure(config):
    shutil.rmtree(TEST_STATE_DIR, ignore_errors=True)
