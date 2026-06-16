"""Unit tests for the circuit breaker."""

from __future__ import annotations

import asyncio

import pytest

from daily_bot.circuit_breaker import CircuitBreaker, State


def test_starts_closed():
    """A fresh breaker should be closed and allow all calls."""
    cb = CircuitBreaker(threshold=3, cooldown_seconds=1.0)
    assert cb.state is State.CLOSED
    assert cb.allow() is True


def test_threshold_must_be_positive():
    """Threshold below 1 should be rejected at construction time."""
    with pytest.raises(ValueError, match="threshold"):
        CircuitBreaker(threshold=0, cooldown_seconds=1.0)


def test_opens_at_threshold():
    """After `threshold` consecutive failures the breaker should open."""
    cb = CircuitBreaker(threshold=3, cooldown_seconds=1.0)

    cb.record_failure()
    assert cb.state is State.CLOSED, "1 failure should not trip the breaker"

    cb.record_failure()
    assert cb.state is State.CLOSED, "2 failures should not trip the breaker (threshold=3)"

    cb.record_failure()
    assert cb.state is State.OPEN, "3 failures should trip the breaker"


def test_open_rejects_calls():
    """When open, allow() should return False until cooldown elapses."""
    cb = CircuitBreaker(threshold=1, cooldown_seconds=10.0)
    cb.record_failure()
    assert cb.state is State.OPEN
    assert cb.allow() is False


def test_success_resets_failure_count():
    """A success after partial failures should reset the counter."""
    cb = CircuitBreaker(threshold=3, cooldown_seconds=1.0)

    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    cb.record_failure()
    assert cb.state is State.CLOSED, "Success should reset the failure counter"

    cb.record_failure()
    assert cb.state is State.OPEN


async def test_transitions_to_half_open_after_cooldown():
    """Once cooldown elapses, the breaker should allow one probe call."""
    cb = CircuitBreaker(threshold=1, cooldown_seconds=0.05)
    cb.record_failure()
    assert cb.state is State.OPEN
    assert cb.allow() is False

    await asyncio.sleep(0.1)
    assert cb.allow() is True
    assert cb.state is State.HALF_OPEN


async def test_success_in_half_open_closes_circuit():
    """A successful probe in half-open state should close the circuit."""
    cb = CircuitBreaker(threshold=1, cooldown_seconds=0.05)
    cb.record_failure()
    await asyncio.sleep(0.1)
    cb.allow()  # transitions to HALF_OPEN
    assert cb.state is State.HALF_OPEN
    cb.record_success()
    assert cb.state is State.CLOSED


def test_manual_reset():
    """reset() should put the breaker back to closed state."""
    cb = CircuitBreaker(threshold=1, cooldown_seconds=10.0)
    cb.record_failure()
    assert cb.state is State.OPEN
    cb.reset()
    assert cb.state is State.CLOSED
    assert cb.allow() is True
