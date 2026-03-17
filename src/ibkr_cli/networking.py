from __future__ import annotations

import socket
import time
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ConnectionResult:
    ok: bool
    host: str
    port: int
    timeout: float
    latency_ms: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def test_tcp_connection(host: str, port: int, timeout: float = 2.0) -> ConnectionResult:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            return ConnectionResult(ok=True, host=host, port=port, timeout=timeout, latency_ms=latency_ms)
    except OSError as exc:
        return ConnectionResult(ok=False, host=host, port=port, timeout=timeout, error=str(exc))
