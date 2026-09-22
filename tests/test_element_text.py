"""Element text: paragraphs and labels report their content, containers stay quiet."""

from __future__ import annotations

from kwin_mcp import accessibility
from kwin_mcp.core import _format_found_element


class _FakeText:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeElement:
    def __init__(self, text: str | None) -> None:
        self._text = text

    def get_text_iface(self) -> _FakeText | None:
        return None if self._text is None else _FakeText(self._text)


class _FakeAtspiText:
    @staticmethod
    def get_character_count(iface: _FakeText) -> int:
        return len(iface.text)

    @staticmethod
    def get_text(iface: _FakeText, start: int, end: int) -> str:
        return iface.text[start:end]


def _text(monkeypatch, raw: str | None, name: str = "") -> str:
    monkeypatch.setattr(accessibility.Atspi, "Text", _FakeAtspiText)
    return accessibility._extract_text(_FakeElement(raw), name)


def test_paragraph_text_is_reported(monkeypatch) -> None:
    assert _text(monkeypatch, "Make sure to  disable\nany VPN.") == "Make sure to disable any VPN."


def test_no_text_interface(monkeypatch) -> None:
    assert _text(monkeypatch, None) == ""


def test_text_equal_to_name_is_dropped(monkeypatch) -> None:
    assert _text(monkeypatch, "Sign in", name="Sign in") == ""


def test_container_of_child_objects_is_empty(monkeypatch) -> None:
    obj = accessibility._OBJECT_REPLACEMENT
    assert _text(monkeypatch, f"{obj}\n{obj} {obj}") == ""


def test_long_text_is_capped(monkeypatch) -> None:
    text = _text(monkeypatch, "x" * 1000)
    assert len(text) == accessibility.TEXT_LIMIT
    assert text.endswith("…")


def test_found_element_line_includes_text() -> None:
    el = {
        "role": "paragraph",
        "name": "",
        "text": "Paste the code here",
        "x": 1,
        "y": 2,
        "width": 3,
        "height": 4,
        "actions": [],
    }
    assert (
        _format_found_element(el) == "- [paragraph] \"\" text='Paste the code here' @ (1, 2, 3x4)"
    )


def test_found_element_line_without_text() -> None:
    el = {
        "role": "button",
        "name": "OK",
        "x": 0,
        "y": 0,
        "width": 1,
        "height": 1,
        "actions": ["press"],
    }
    assert _format_found_element(el) == '- [button] "OK" @ (0, 0, 1x1) [actions: press]'
