from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit


class ModelAdmissionTimeout(RuntimeError):
    code = "MODEL_BUSY"


class _EndpointSlot:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.active = 0
        self.condition = threading.Condition()

    def acquire(self, deadline: float) -> None:
        with self.condition:
            while self.active >= self.capacity:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ModelAdmissionTimeout(
                        "MODEL_BUSY: timed out waiting for model slot"
                    )
                self.condition.wait(timeout=remaining)
            self.active += 1

    def release(self) -> None:
        with self.condition:
            if self.active <= 0:
                raise RuntimeError("model admission slot released without acquire")
            self.active -= 1
            self.condition.notify()


_REGISTRY_LOCK = threading.Lock()
_SLOTS: dict[str, _EndpointSlot] = {}


def normalize_endpoint(endpoint: str) -> str:
    parsed = urlsplit(str(endpoint or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("invalid model endpoint")
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), path.rstrip("/"), "", "")
    )


def _get_slot(endpoint: str, capacity: int) -> _EndpointSlot:
    key = normalize_endpoint(endpoint)
    with _REGISTRY_LOCK:
        slot = _SLOTS.get(key)
        if slot is None:
            slot = _EndpointSlot(capacity)
            _SLOTS[key] = slot
        elif slot.capacity != capacity:
            raise RuntimeError(
                "model admission capacity cannot change while process is running"
            )
        return slot


@contextmanager
def admission_slot(
    endpoint: str,
    *,
    timeout_seconds: float,
    capacity: int = 1,
) -> Iterator[None]:
    if isinstance(capacity, bool) or not 1 <= int(capacity) <= 8:
        raise ValueError("model admission capacity must be between 1 and 8")
    timeout = float(timeout_seconds)
    if timeout <= 0:
        raise ValueError("model admission timeout must be positive")
    slot = _get_slot(endpoint, int(capacity))
    slot.acquire(time.monotonic() + timeout)
    try:
        yield
    finally:
        slot.release()
