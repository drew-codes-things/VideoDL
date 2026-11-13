import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")
MAX_URL_LENGTH = 2000


def resolve_addresses(host):
    return [info[4][0] for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]


def is_public_address(address):
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def address_refusal(url):
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port
    except ValueError:
        return "is not a valid link"
    if parts.scheme not in ALLOWED_SCHEMES or not host:
        return "is not an http:// or https:// link"
    try:
        addresses = resolve_addresses(host)
    except (socket.gaierror, UnicodeError):
        return f"names a host that could not be found ({host})"
    if not addresses or not all(is_public_address(address) for address in addresses):
        return f"points at a private or local address ({host})"
    return None


def link_refusal(url):
    if len(url) > MAX_URL_LENGTH:
        return f"is longer than {MAX_URL_LENGTH} characters"
    return address_refusal(url)
