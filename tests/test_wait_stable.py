"""wait_for_elements settles on elements that stopped moving (animated menus, dialogs)."""

from __future__ import annotations

import pytest

from kwin_mcp import accessibility
from kwin_mcp.accessibility import ElementInfo


def _element(y: int) -> ElementInfo:
    return ElementInfo(
        role="menu item",
        name="Settings",
        description="",
        states=[],
        x=100,
        y=y,
        width=200,
        height=30,
        actions=[],
        children_count=0,
        depth=5,
    )


class _Clock:
    """Fake monotonic clock that advances only when the code sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _run(monkeypatch, frames: list[list[ElementInfo]], *, stable_ms: int, timeout_ms: int = 5000):
    clock = _Clock()
    monkeypatch.setattr(accessibility.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(accessibility.time, "sleep", clock.sleep)
    polls = iter(frames)
    last = frames[-1]
    monkeypatch.setattr(accessibility, "find_elements", lambda *_a, **_k: next(polls, last))
    return accessibility.wait_for_elements(
        "Settings", poll_interval_ms=100, stable_ms=stable_ms, timeout_ms=timeout_ms
    )


def test_returns_first_match_without_stability(monkeypatch) -> None:
    elements, stable = _run(monkeypatch, [[_element(-230)], [_element(600)]], stable_ms=0)
    assert stable
    assert elements[0].y == -230


def test_waits_until_rect_stops_moving(monkeypatch) -> None:
    frames = [[_element(-230)], [_element(300)], [_element(600)], [_element(600)]]
    elements, stable = _run(monkeypatch, frames, stable_ms=100)
    assert stable
    assert elements[0].y == 600


def test_still_moving_at_timeout_is_returned_unsettled(monkeypatch) -> None:
    frames = [[_element(y)] for y in range(0, 1000, 10)]
    elements, stable = _run(monkeypatch, frames, stable_ms=300, timeout_ms=500)
    assert not stable
    assert elements


def test_nothing_found_still_times_out(monkeypatch) -> None:
    with pytest.raises(TimeoutError):
        _run(monkeypatch, [[]], stable_ms=300, timeout_ms=300)
