"""Circuit breaker for short-circuiting repeated failures.

After `threshold` consecutive failures, the breaker opens and rejects all
subsequent calls for `cooldown_seconds`. After the cooldown, one call is
allowed through (half-open) to probe recovery. Success closes the breaker.
"""

from __future__ import annotations

import logging
import time
from enum import StrEnum

logger = logging.getLogger(__name__)


class State(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Track consecutive failures and open the circuit when threshold is exceeded."""

    def __init__(self, threshold: int, cooldown_seconds: float) -> None:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self._state: State = State.CLOSED
        self._consecutive_failures: int = 0
        self._opened_at: float = 0.0

    @property
    def state(self) -> State:
        return self._state

    def allow(self) -> bool:
        """Return True if a call is permitted right now."""
        if self._state is State.CLOSED:
            return True

        if self._state is State.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_seconds:
                self._state = State.HALF_OPEN
                logger.info("Circuit breaker transitioning to HALF_OPEN")
                return True
            return False

        # HALF_OPEN: allow exactly one probe
        return True

    def record_success(self) -> None:
        """Record a successful call; close the breaker if it was open."""
        if self._state is not State.CLOSED:
            logger.info("Circuit breaker closing after success")
        self._state = State.CLOSED
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        """Record a failed call; trip the breaker at threshold."""
        self._consecutive_failures += 1
        logger.warning(
            "Circuit breaker failure %d/%d (state=%s)",
            self._consecutive_failures,
            self.threshold,
            self._state,
        )
        if self._consecutive_failures >= self.threshold and self._state is State.CLOSED:
            self._state = State.OPEN
            self._opened_at = time.monotonic()
            logger.error("Circuit breaker OPENED; pausing for %.1fs", self.cooldown_seconds)

    def reset(self) -> None:
        """Manually reset the breaker to closed state."""
        self._state = State.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
