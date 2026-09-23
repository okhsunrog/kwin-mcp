"""Screen coordinates for clients that draw their own frame and scale web content (Chromium)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kwin_mcp import accessibility
from kwin_mcp.accessibility import ElementInfo
from kwin_mcp.kwin_windows import WindowGeometry


def _info(role: str, x: int, y: int, width: int, height: int, name: str = "") -> ElementInfo:
    return ElementInfo(
        role=role,
        name=name,
        description="",
        states=[],
        x=x,
        y=y,
        width=width,
        height=height,
        actions=[],
        children_count=0,
        depth=0,
    )


def test_chromium_document_at_150_percent_is_scaled_back() -> None:
    view = _info("panel", 16, 97, 1406, 868)
    document = _info("document web", 24, 145, 2109, 1302)
    assert accessibility._document_scale(document, view) == 2109 / 1406


def test_logical_documents_are_left_alone() -> None:
    # WebKitGTK: the document overflows its scroll pane a little, in height only.
    pane = _info("scroll pane", 1672, 28, 2560, 1368)
    document = _info("document web", 1672, 28, 2556, 1416)
    assert accessibility._document_scale(document, pane) is None
    assert (
        accessibility._document_scale(_info("document web", 0, 0, 10, 10), _info("x", 0, 0, 0, 0))
        is None
    )


@dataclass
class _Rect:
    x: int
    y: int
    width: int
    height: int


@dataclass
class _Node:
    rect: _Rect
    children: list[_Node] = field(default_factory=list)

    def get_child_count(self) -> int:
        return len(self.children)

    def get_child_at_index(self, index: int) -> _Node:
        return self.children[index]

    def get_component_iface(self) -> _Node:
        return self

    def get_extents(self, _coords: Any) -> _Rect:
        return self.rect


def test_client_side_shadow_is_skipped_to_the_client_area() -> None:
    client = _Node(_Rect(16, 10, 1406, 955))
    surface = _Node(_Rect(0, 0, 1438, 997), [client])
    geometry = WindowGeometry(
        caption="Claude Proxy - Google Chrome",
        resource_class="google-chrome",
        client_x=2249,
        client_y=220,
        client_w=1406,
        client_h=955,
        frame_x=2249,
        frame_y=220,
        frame_w=1406,
        frame_h=955,
    )
    frame = _info("frame", 0, 0, 1438, 997, name="Claude Proxy - Google Chrome")
    transform = accessibility._window_transform(surface, frame, "Google Chrome", [geometry])
    assert transform == accessibility._Transform(2249 - 16, 220 - 10)


def test_frames_the_size_of_the_client_keep_their_own_origin() -> None:
    surface = _Node(_Rect(0, 0, 640, 480))
    geometry = WindowGeometry("KCalc", "kcalc", 602, 188, 640, 480, 602, 160, 640, 508)
    frame = _info("frame", 0, 0, 640, 480, name="KCalc")
    transform = accessibility._window_transform(surface, frame, "kcalc", [geometry])
    assert transform == accessibility._Transform(602, 188)
