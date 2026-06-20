from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable, Iterable, Optional, TypeVar
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
DEFAULT_RETRY_DELAYS = (1.0, 2.0, 4.0)
LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class SourceHealth:
    status: str
    last_success_at: Optional[str] = None
    last_error_at: Optional[str] = None
    last_error: str = ""
    success_count: int = 0
    failure_count: int = 0
    partial_count: int = 0
    retry_count: int = 0
    failed_symbols: tuple[str, ...] = ()
    message: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["failed_symbols"] = list(self.failed_symbols)
        return data


class HealthStatusRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sources: dict[str, SourceHealth] = {}

    def record(
        self,
        source: str,
        status: str,
        *,
        success_count: int = 0,
        failure_count: int = 0,
        partial_count: int = 0,
        retry_count: int = 0,
        failed_symbols: Optional[Iterable[str]] = None,
        error: str = "",
        message: str = "",
    ) -> None:
        now = datetime.now(TAIPEI).isoformat(timespec="seconds")
        normalized = status.upper()
        with self._lock:
            previous = self._sources.get(source)
            self._sources[source] = SourceHealth(
                status=normalized,
                last_success_at=now if normalized in {"OK", "PARTIAL"} else (previous.last_success_at if previous else None),
                last_error_at=now if normalized in {"ERROR", "PARTIAL"} and error else (previous.last_error_at if previous else None),
                last_error=error or "",
                success_count=success_count,
                failure_count=failure_count,
                partial_count=partial_count,
                retry_count=retry_count,
                failed_symbols=tuple(sorted(str(item) for item in (failed_symbols or []))),
                message=message,
            )

    def snapshot(self) -> dict:
        with self._lock:
            return {source: health.to_dict() for source, health in sorted(self._sources.items())}

    def compact_snapshot(self) -> dict:
        with self._lock:
            return {source: health.status for source, health in sorted(self._sources.items())}

    def reset(self) -> None:
        with self._lock:
            self._sources.clear()


GLOBAL_HEALTH = HealthStatusRegistry()


def record_source_health(source: str, status: str, **kwargs) -> None:
    GLOBAL_HEALTH.record(source, status, **kwargs)


def health_status_snapshot() -> dict:
    return GLOBAL_HEALTH.snapshot()


def health_status_compact() -> dict:
    return GLOBAL_HEALTH.compact_snapshot()


def retry_sync(
    operation: Callable[[], T],
    *,
    source: str,
    operation_name: str,
    retry_delays: Iterable[float] = DEFAULT_RETRY_DELAYS,
    should_retry: Optional[Callable[[Exception], bool]] = None,
) -> T:
    delays = tuple(retry_delays)
    attempts = len(delays) + 1
    last_error: Optional[Exception] = None
    attempts_used = 0
    stopped_by_policy = False
    for index in range(attempts):
        attempts_used = index + 1
        try:
            result = operation()
            if index:
                record_source_health(source, "OK", retry_count=index, message=f"{operation_name} recovered after retry")
            return result
        except Exception as exc:
            last_error = exc
            retry_allowed = should_retry(exc) if should_retry else True
            if index >= len(delays) or not retry_allowed:
                stopped_by_policy = not retry_allowed
                break
            LOGGER.warning(
                "%s failed for %s on attempt %s/%s: %s; retrying in %.1fs",
                operation_name,
                source,
                index + 1,
                attempts,
                exc,
                delays[index],
            )
            time.sleep(delays[index])
    message = f"{operation_name} failed after {attempts_used} attempt{'s' if attempts_used != 1 else ''}: {last_error}"
    if stopped_by_policy:
        LOGGER.info(message)
        record_source_health(
            source,
            "PARTIAL",
            partial_count=1,
            retry_count=max(attempts_used - 1, 0),
            error=str(last_error or "unknown error"),
            message=f"{operation_name} skipped retry for non-retryable error.",
        )
    else:
        LOGGER.error(message)
        record_source_health(source, "ERROR", failure_count=1, retry_count=len(delays), error=str(last_error or "unknown error"))
    raise last_error or RuntimeError(message)
