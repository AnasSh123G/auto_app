"""Public-target guard for employer discovery.

Every address is checked before a request (scheme, credentials, restricted platforms, and DNS
resolving only to public addresses), and the actual TCP connection is checked again with the
addresses it connects to, so DNS rebinding cannot reach a private service between the check and
the connection. Redirects are returned to the caller and validated one by one.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

import httpcore
import httpx

from autoapp.assist.filler import is_restricted

Resolver = Callable[[str], list[str]]

_BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet")


def system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def is_public_address(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_target(url: str, restricted_domains: list[str], resolver: Resolver = system_resolver) -> str | None:
    """Why the address must not be requested, or ``None`` if it may be."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "invalid address"
    if parts.scheme not in ("http", "https"):
        return f"unsafe scheme {parts.scheme or '(none)'!r}"
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return "address contains credentials"
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return "address has no host"
    if port is not None and port not in (80, 443):
        return f"non-standard port {port}"
    if is_restricted(url, restricted_domains):
        return f"restricted platform {host}"
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES) or "." not in host and not _is_ip(host):
        return f"non-public host {host}"
    try:
        addresses = [host] if _is_ip(host) else resolver(host)
    except OSError:
        return f"{host} does not resolve"
    if not addresses:
        return f"{host} does not resolve"
    bad = [a for a in addresses if not is_public_address(a)]
    if bad:
        return f"{host} resolves to a non-public address"
    return None


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


class _GuardedBackend(httpcore.SyncBackend):
    """Connects only to addresses that are public at connection time."""

    def __init__(self, resolver: Resolver) -> None:
        super().__init__()
        self._resolver = resolver

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):  # noqa: ANN001
        try:
            addresses = [host] if _is_ip(host) else self._resolver(host)
        except OSError as err:
            raise httpcore.ConnectError(f"{host} does not resolve") from err
        public = [a for a in addresses if is_public_address(a)]
        if not addresses or len(public) != len(addresses):
            raise httpcore.ConnectError(f"refusing to connect: {host} resolves to a non-public address")
        last: Exception | None = None
        for address in public:
            try:
                return super().connect_tcp(address, port, timeout=timeout, local_address=local_address,
                                           socket_options=socket_options)
            except httpcore.ConnectError as err:
                last = err
        raise last or httpcore.ConnectError(f"cannot connect to {host}")


class GuardedTransport(httpx.HTTPTransport):
    """An ``HTTPTransport`` whose connections go through ``_GuardedBackend`` (no proxies, no env trust)."""

    def __init__(self, resolver: Resolver = system_resolver) -> None:
        super().__init__(trust_env=False)
        # httpx exposes no hook for the network backend, so the pool is rebuilt with the guarded one.
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False), max_connections=20, max_keepalive_connections=5,
            keepalive_expiry=5.0, http1=True, http2=False, network_backend=_GuardedBackend(resolver),
        )


def guarded_client(resolver: Resolver = system_resolver, *, timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(transport=GuardedTransport(resolver), timeout=timeout, follow_redirects=False, trust_env=False)
