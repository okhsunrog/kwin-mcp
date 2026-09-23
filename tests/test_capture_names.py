"""Every screenshot and frame burst gets its own file names."""

from __future__ import annotations

from kwin_mcp import screenshot as screenshot_module
from kwin_mcp.core import _FRAME_NAME_RE


def test_stems_are_unique_within_one_second() -> None:
    stems = {screenshot_module._unique_stem() for _ in range(50)}
    assert len(stems) == 50


def test_frame_names_from_one_call_do_not_collide_with_the_next(monkeypatch, tmp_path) -> None:
    written: list[str] = []

    def fake_spectacle(_dbus: str, _socket: str, *, output_path, include_cursor: bool) -> None:
        del include_cursor
        output_path.write_bytes(b"png")
        written.append(output_path.name)

    monkeypatch.setattr(screenshot_module, "_capture_via_spectacle", fake_spectacle)
    for _ in range(2):
        screenshot_module._capture_frame_burst_spectacle(
            "", "", tmp_path, [400], screenshot_module._unique_stem()
        )
    assert len(set(written)) == 2


def test_delay_is_read_from_new_and_old_frame_names() -> None:
    for name in ("frame_20260923_031732_0007_002_400ms.png", "frame_002_400ms.png"):
        match = _FRAME_NAME_RE.search(name)
        assert match is not None
        assert match.group(1) == "400"
