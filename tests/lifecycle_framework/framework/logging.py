"""Structured transition logging."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol


@dataclass
class TransitionEvent:
    step: str
    status: str  # "ok" | "fail" | "skip"
    elapsed_ms: float
    detail: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ScenarioLogger(Protocol):
    def log(self, event: TransitionEvent) -> None: ...
    def summary(self, events: list[TransitionEvent]) -> None: ...


_RESET = "\033[0m"
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"


class ColoredConsoleLogger:
    def log(self, event: TransitionEvent) -> None:
        color = _GREEN if event.status == "ok" else (_RED if event.status == "fail" else _YELLOW)
        symbol = "✅" if event.status == "ok" else ("❌" if event.status == "fail" else "⏭ ")
        detail = f"  {event.detail}" if event.detail else ""
        print(
            f"{color}{symbol} [{event.step}] {event.elapsed_ms:.0f}ms{_RESET}{detail}"
        )

    def summary(self, events: list[TransitionEvent]) -> None:
        ok = sum(1 for e in events if e.status == "ok")
        fail = sum(1 for e in events if e.status == "fail")
        total_ms = sum(e.elapsed_ms for e in events)
        status_color = _GREEN if fail == 0 else _RED
        print(
            f"\n{_BOLD}{status_color}"
            f"{'PASS' if fail == 0 else 'FAIL'} — {ok}/{len(events)} steps OK, "
            f"{total_ms:.0f}ms total{_RESET}"
        )


class SilentLogger:
    """Collects events without printing — useful for programmatic consumption."""

    def __init__(self) -> None:
        self._events: list[TransitionEvent] = []

    def log(self, event: TransitionEvent) -> None:
        self._events.append(event)

    def summary(self, events: list[TransitionEvent]) -> None:
        pass

    @property
    def events(self) -> list[TransitionEvent]:
        return list(self._events)


class TimedStep:
    """Context manager that records elapsed time and logs a TransitionEvent.

    On success: caller calls step.ok(detail) explicitly.
    On exception: __exit__ automatically logs a fail event with the exception
                  message, then returns False so the exception propagates.
                  The exception is NEVER swallowed.

    Usage:
        with TimedStep("my_step", logger) as step:
            do_work()
            step.ok("work done")
    """

    def __init__(self, name: str, logger: ScenarioLogger) -> None:
        self._name = name
        self._logger = logger
        self._start = 0.0

    def __enter__(self) -> "TimedStep":
        self._start = time.perf_counter()
        return self

    def ok(self, detail: str = "") -> None:
        elapsed = (time.perf_counter() - self._start) * 1000
        self._logger.log(TransitionEvent(self._name, "ok", elapsed, detail))

    def fail(self, detail: str = "") -> None:
        elapsed = (time.perf_counter() - self._start) * 1000
        self._logger.log(TransitionEvent(self._name, "fail", elapsed, detail))

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # Auto-log a fail event so the summary accurately reflects the failure.
            # The exception is NOT suppressed — it propagates after logging.
            elapsed = (time.perf_counter() - self._start) * 1000
            self._logger.log(
                TransitionEvent(self._name, "fail", elapsed, str(exc_val))
            )
        return False  # never swallow exceptions
