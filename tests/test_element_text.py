"""Element text: paragraphs and labels report their content, containers stay quiet."""

from __future__ import annotations

from kwin_mcp import accessibility
from kwin_mcp.core import _format_found_element


class _FakeText:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeElement:
    def __init__(
        self, text: str | None, children: list[_FakeElement] | None = None, name: str = ""
    ) -> None:
        self._text = text
        self._children = children or []
        self._name = name

    def get_text_iface(self) -> _FakeText | None:
        return None if self._text is None else _FakeText(self._text)

    def get_hypertext_iface(self) -> _FakeElement | None:
        return self if self._children else None

    def get_name(self) -> str:
        return self._name

    def link_at(self, offset: int) -> int:
        """Index of the embedded object at a character offset, like AT-SPI Hypertext."""
        if self._text is None or self._text[offset] != accessibility._OBJECT_REPLACEMENT:
            return -1
        return self._text[:offset].count(accessibility._OBJECT_REPLACEMENT)


class _FakeAtspiHypertext:
    @staticmethod
    def get_link_index(element: _FakeElement, offset: int) -> int:
        return element.link_at(offset)

    @staticmethod
    def get_link(element: _FakeElement, index: int) -> _FakeElement:
        return element._children[index]


class _FakeAtspiHyperlink:
    @staticmethod
    def get_object(link: _FakeElement, _index: int) -> _FakeElement:
        return link


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


def test_inline_links_keep_their_text(monkeypatch) -> None:
    monkeypatch.setattr(accessibility.Atspi, "Text", _FakeAtspiText)
    monkeypatch.setattr(accessibility.Atspi, "Hypertext", _FakeAtspiHypertext)
    monkeypatch.setattr(accessibility.Atspi, "Hyperlink", _FakeAtspiHyperlink)
    obj = accessibility._OBJECT_REPLACEMENT
    paragraph = _FakeElement(
        f"Wayland is a {obj} that specifies the {obj}.",
        children=[
            _FakeElement("communication protocol"),
            # A child without text falls back to its name.
            _FakeElement(None, name="display server"),
        ],
    )
    assert (
        accessibility._extract_text(paragraph, "")
        == "Wayland is a communication protocol that specifies the display server."
    )


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
