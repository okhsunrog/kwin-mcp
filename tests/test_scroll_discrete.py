"""Discrete scroll must be sent to libei in 1/120 detent units."""

from __future__ import annotations

from kwin_mcp import input as input_module
from kwin_mcp.input import EISClient


class _RecordingLibei:
    def __init__(self) -> None:
        self.discrete: list[tuple[int, int]] = []

    def ei_device_scroll_discrete(self, _device: int, dx: int, dy: int) -> None:
        self.discrete.append((dx, dy))

    def ei_device_frame(self, _device: int, _time: int) -> None:
        pass


def test_one_tick_is_one_detent(monkeypatch) -> None:
    fake = _RecordingLibei()
    monkeypatch.setattr(input_module, "_get_libei", lambda: fake)
    client = EISClient.__new__(EISClient)
    monkeypatch.setattr(client, "_pointer", 1, raising=False)
    monkeypatch.setattr(client, "_ensure_devices_ready", lambda: None)
    monkeypatch.setattr(client, "_flush", lambda: None)
    monkeypatch.setattr(client, "_now_us", lambda: 0)

    client.pointer_scroll_discrete(0, 3)
    client.pointer_scroll_discrete(-2, 0)

    assert fake.discrete == [(0, 360), (-240, 0)]
