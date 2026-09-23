"""AT-SPI2 accessibility tree reader.

Can be run as a subprocess CLI for isolated D-Bus session support.
Reads a JSON request from stdin and writes a JSON response to stdout.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

from kwin_mcp.kwin_windows import (  # noqa: E402
    WindowGeometry,
    get_window_geometries,
    match_window,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass
class ElementInfo:
    """Information about a single UI element."""

    role: str
    name: str
    description: str
    states: list[str]
    x: int
    y: int
    width: int
    height: int
    actions: list[str]
    children_count: int
    depth: int
    text: str = ""


#: Longest text reported per element, so a tree dump never inlines a whole document.
TEXT_LIMIT = 200

# Web engines put one of these in a container's text for every child element.
_OBJECT_REPLACEMENT = chr(0xFFFC)


def get_accessibility_tree(
    app_name: str = "",
    max_depth: int = 15,
    role: str = "",
) -> str:
    """Get the accessibility tree as a formatted text string.

    Args:
        app_name: Filter to a specific application (empty = all apps).
        max_depth: Maximum tree depth to traverse.
        role: Filter to elements with this role (empty = all roles).
            Non-matching elements are hidden but their children are still traversed.

    Returns:
        Formatted text representation of the accessibility tree.
    """
    desktop = Atspi.get_desktop(0)
    lines: list[str] = []
    total = 0
    role_filter = role.lower()
    geometries = get_window_geometries()

    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None:
            continue

        name = app.get_name() or ""
        if app_name and app_name.lower() not in name.lower():
            continue

        count = _format_element(
            app,
            lines,
            depth=0,
            max_depth=max_depth,
            role_filter=role_filter,
            app_name=name,
            geometries=geometries,
        )
        total += count

    if not lines:
        return "(no accessible applications found)"

    header = f"# Accessibility Tree ({total} elements)\n\n"
    return header + "\n".join(lines)


def find_elements(
    query: str, app_name: str = "", states: list[str] | None = None
) -> list[ElementInfo]:
    """Find elements matching a query string and/or required states.

    Searches element names, roles, descriptions and text. Optionally filters
    by AT-SPI2 states.

    Args:
        query: Search string (case-insensitive). Empty string matches all elements.
        app_name: Filter to a specific application.
        states: If provided, only return elements that have ALL of these states.

    Returns:
        List of matching ElementInfo objects.
    """
    desktop = Atspi.get_desktop(0)
    results: list[ElementInfo] = []
    query_lower = query.lower()
    geometries = get_window_geometries()

    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None:
            continue

        name = app.get_name() or ""
        if app_name and app_name.lower() not in name.lower():
            continue

        _search_element(
            app,
            query_lower,
            results,
            depth=0,
            max_depth=15,
            required_states=states,
            app_name=name,
            geometries=geometries,
        )

    return results


def list_windows() -> str:
    """List accessible application windows with titles and active/focused state.

    Returns:
        Formatted list of apps with per-window title and state markers.
    """
    desktop = Atspi.get_desktop(0)
    lines: list[str] = []
    app_count = 0
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None:
            continue
        app_name = app.get_name() or "(unnamed)"
        child_count = app.get_child_count()
        app_count += 1
        lines.append(f"- {app_name} ({child_count} windows)")
        for j in range(child_count):
            win = app.get_child_at_index(j)
            if win is None:
                continue
            win_title = win.get_name() or "(untitled)"
            state_set = win.get_state_set()
            markers: list[str] = []
            if state_set.contains(Atspi.StateType.ACTIVE):
                markers.append("active")
            if state_set.contains(Atspi.StateType.FOCUSED):
                markers.append("focused")
            marker_str = f" [{', '.join(markers)}]" if markers else ""
            lines.append(f'    - "{win_title}"{marker_str}')
    if not lines:
        return "(no accessible applications found)"
    return f"Applications ({app_count}):\n" + "\n".join(lines)


def focus_window(app_name: str) -> str:
    """Focus a window by application name.

    Args:
        app_name: Application name substring (case-insensitive).

    Returns:
        Result message.
    """
    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None:
            continue
        name = app.get_name() or ""
        if app_name.lower() in name.lower():
            for j in range(app.get_child_count()):
                win = app.get_child_at_index(j)
                if win is None:
                    continue
                try:
                    component = win.get_component_iface()
                    if component is not None:
                        component.grab_focus()
                        return f"Focused: {name}"
                except Exception:
                    continue
            return f"Found '{name}' but could not focus it"
    return f"No application matching '{app_name}' found"


def wait_for_elements(
    query: str,
    app_name: str = "",
    timeout_ms: int = 5000,
    poll_interval_ms: int = 200,
    states: list[str] | None = None,
    stable_ms: int = 0,
) -> tuple[list[ElementInfo], bool]:
    """Poll for elements matching a query and/or states until found or timeout.

    Args:
        query: Search string (case-insensitive). Empty string matches all elements.
        app_name: Filter to a specific application.
        timeout_ms: Maximum wait time in milliseconds.
        poll_interval_ms: Polling interval in milliseconds.
        states: If provided, only match elements that have ALL of these states.
        stable_ms: Also wait until the matches keep the same rectangles for this long.
            Menus and dialogs that animate in report in-between positions (a menu item
            sliding in from y=-230), and clicking those misses. 0 returns the first match.

    Returns:
        The matching elements, and whether their rectangles had settled. When the timeout
        expires while matches are still moving, the last matches are returned unsettled.

    Raises:
        TimeoutError: If no elements found within timeout.
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    interval = poll_interval_ms / 1000.0
    last: list[ElementInfo] = []
    last_rects: list[tuple[int, int, int, int]] | None = None
    unchanged_since = 0.0

    while True:
        elements = find_elements(query, app_name=app_name, states=states)
        now = time.monotonic()
        if elements:
            if stable_ms <= 0:
                return elements, True
            rects = [(e.x, e.y, e.width, e.height) for e in elements]
            if rects != last_rects:
                last_rects = rects
                unchanged_since = now
            elif (now - unchanged_since) * 1000 >= stable_ms:
                return elements, True
            last = elements
        else:
            last, last_rects = [], None

        if now >= deadline:
            if last:
                return last, False
            criteria = f"query='{query}'"
            if states:
                criteria += f", states={states}"
            msg = f"Timeout after {timeout_ms}ms: no elements matching {criteria}"
            raise TimeoutError(msg)

        time.sleep(min(interval, max(deadline - now, 0.0)))


@dataclass(frozen=True)
class _Transform:
    """Maps an element's raw AT-SPI rectangle to screen coordinates: raw / scale + offset."""

    dx: int = 0
    dy: int = 0
    scale: float = 1.0


_IDENTITY = _Transform()


def _walk(
    element: Atspi.Accessible,
    depth: int,
    max_depth: int,
    app_name: str,
    geometries: list[WindowGeometry] | None,
    transform: _Transform = _IDENTITY,
    parent: ElementInfo | None = None,
) -> Iterator[ElementInfo]:
    """Yield an element and its descendants, pre-order, in true screen coordinates.

    Wayland clients report window-local coordinates, so at depth 1 (a top-level
    window) the offset to the compositor-side client origin is resolved and applied
    to the whole subtree. Chromium additionally reports web content in device
    pixels on a scaled output; see _document_scale.
    """
    if depth > max_depth:
        return
    info = _extract_info(element, depth, transform)
    if depth == 1 and geometries:
        window = _window_transform(element, info, app_name, geometries)
        if window is not None:
            transform = window
            info = _extract_info(element, depth, transform)
    elif parent is not None and info.role == "document web":
        ratio = _document_scale(info, parent)
        if ratio is not None:
            transform = _Transform(transform.dx, transform.dy, transform.scale * ratio)
            info = _extract_info(element, depth, transform)
    yield info
    for i in range(info.children_count):
        child = element.get_child_at_index(i)
        if child is not None:
            yield from _walk(child, depth + 1, max_depth, app_name, geometries, transform, info)


def _window_transform(
    element: Atspi.Accessible,
    info: ElementInfo,
    app_name: str,
    geometries: list[WindowGeometry],
) -> _Transform | None:
    """Offset from a top-level window's own coordinates to the screen.

    A client that draws its own frame (Chrome, GTK) reports a surface that
    includes its drop shadow, larger than the client area KWin reports; its
    content starts at the child that has the client area's size, not at (0, 0).
    """
    geom = match_window(geometries, app_name, info.name)
    if geom is None:
        return None
    origin_x, origin_y = info.x, info.y
    if info.width - geom.client_w > 2 or info.height - geom.client_h > 2:
        content = _find_sized_descendant(element, geom.client_w, geom.client_h)
        if content is not None:
            origin_x, origin_y = content
    return _Transform(geom.client_x - origin_x, geom.client_y - origin_y)


def _find_sized_descendant(
    element: Atspi.Accessible, width: int, height: int, max_depth: int = 3
) -> tuple[int, int] | None:
    """Origin of the shallowest descendant with this size (within a pixel)."""
    level = [element]
    for _ in range(max_depth):
        next_level = []
        for node in level:
            for i in range(node.get_child_count()):
                child = node.get_child_at_index(i)
                if child is None:
                    continue
                rect = _raw_extents(child)
                if rect and abs(rect[2] - width) <= 1 and abs(rect[3] - height) <= 1:
                    return rect[0], rect[1]
                next_level.append(child)
        level = next_level
    return None


def _document_scale(document: ElementInfo, parent: ElementInfo) -> float | None:
    """How many device pixels a web document reports per logical pixel, if not one.

    Chromium on a fractionally scaled Wayland output reports its browser UI in
    logical pixels but web content in device pixels, origin included, so the
    document is exactly `scale` times the view that hosts it (2109x1302 inside
    1406x868 at 150%). WebKitGTK and Gecko report logical pixels throughout.
    """
    if parent.width <= 0 or parent.height <= 0:
        return None
    ratio_w = document.width / parent.width
    ratio_h = document.height / parent.height
    if ratio_w < 1.1 or abs(ratio_w - ratio_h) > 0.05 * ratio_w:
        return None
    return ratio_w


def _raw_extents(element: Atspi.Accessible) -> tuple[int, int, int, int] | None:
    try:
        component = element.get_component_iface()
        if component is None:
            return None
        rect = component.get_extents(Atspi.CoordType.SCREEN)
    except Exception:
        return None
    return rect.x, rect.y, rect.width, rect.height


def _format_element(
    element: Atspi.Accessible,
    lines: list[str],
    depth: int,
    max_depth: int,
    role_filter: str = "",
    app_name: str = "",
    geometries: list[WindowGeometry] | None = None,
) -> int:
    """Format an element and its descendants into `lines`. Returns the element count.

    When role_filter is set, only elements with a matching role are displayed,
    but children of non-matching elements are still traversed.
    """
    count = 0
    for info in _walk(element, depth, max_depth, app_name, geometries):
        if role_filter and role_filter != info.role.lower():
            continue
        indent = "  " * info.depth
        states_str = f" ({', '.join(info.states)})" if info.states else ""
        pos_str = f" @ ({info.x}, {info.y}, {info.width}x{info.height})"
        actions_str = f" [actions: {', '.join(info.actions)}]" if info.actions else ""
        text_str = f" text={info.text!r}" if info.text else ""
        lines.append(
            f'{indent}- [{info.role}] "{info.name}"{text_str}{states_str}{pos_str}{actions_str}'
        )
        count += 1
    return count


def _search_element(
    element: Atspi.Accessible,
    query: str,
    results: list[ElementInfo],
    depth: int,
    max_depth: int,
    required_states: list[str] | None = None,
    app_name: str = "",
    geometries: list[WindowGeometry] | None = None,
) -> None:
    """Collect elements matching the query (name, role, description or text) and states."""
    for info in _walk(element, depth, max_depth, app_name, geometries):
        query_match = (
            query in info.name.lower()
            or query in info.role.lower()
            or query in info.description.lower()
            or query in info.text.lower()
        )
        states_match = required_states is None or all(s in info.states for s in required_states)
        if query_match and states_match:
            results.append(info)


def _extract_info(
    element: Atspi.Accessible, depth: int, transform: _Transform = _IDENTITY
) -> ElementInfo:
    """Extract information from an AT-SPI accessible element.

    Args:
        element: The accessible element.
        depth: Depth in the traversed tree.
        transform: Maps the raw rectangle to screen coordinates (see _walk).
    """
    role = element.get_role_name() or "unknown"
    name = element.get_name() or ""
    description = element.get_description() or ""

    # Get states
    state_set = element.get_state_set()
    states: list[str] = []
    for state in Atspi.StateType:
        if state_set.contains(state):
            state_name = state.value_nick
            if state_name:
                states.append(state_name)

    # Get position and size
    x, y, width, height = 0, 0, 0, 0
    rect = _raw_extents(element)
    if rect is not None:
        scale = transform.scale
        x = round(rect[0] / scale) + transform.dx
        y = round(rect[1] / scale) + transform.dy
        width, height = round(rect[2] / scale), round(rect[3] / scale)

    # Get available actions
    actions: list[str] = []
    try:
        action_iface = element.get_action_iface()
        if action_iface is not None:
            for i in range(action_iface.get_n_actions()):
                action_name = action_iface.get_action_name(i)
                if action_name:
                    actions.append(action_name)
    except Exception:
        pass

    return ElementInfo(
        role=role,
        name=name,
        description=description,
        states=states,
        x=x,
        y=y,
        width=width,
        height=height,
        actions=actions,
        children_count=element.get_child_count(),
        depth=depth,
        # Never read a password field's contents into a tree dump or a log.
        text="" if role == "password text" else _extract_text(element, name),
    )


def _extract_text(element: Atspi.Accessible, name: str) -> str:
    """Return the element's own visible text, or "" when it adds nothing to the name.

    Paragraphs, labels and editors carry their content in the Text interface, not in the
    name, so without this a hint or an error message is invisible in the tree.
    """
    raw = _read_text(element)
    if not raw:
        return ""
    text = " ".join(_inline_children(element, raw, depth=0).split())
    if not text or text == name.strip():
        return ""
    if len(text) > TEXT_LIMIT:
        text = text[: TEXT_LIMIT - 1] + "…"
    return text


def _read_text(element: Atspi.Accessible) -> str:
    try:
        text_iface = element.get_text_iface()
        if text_iface is None:
            return ""
        # Call through the interface class: the bound method on the accessible returns an
        # empty string on some PyGObject versions.
        count = Atspi.Text.get_character_count(text_iface)
        if count <= 0:
            return ""
        return Atspi.Text.get_text(text_iface, 0, min(count, TEXT_LIMIT * 4))
    except Exception:
        return ""


def _inline_children(element: Atspi.Accessible, raw: str, depth: int) -> str:
    """Replace each embedded-object character with the text of the child it stands for.

    Browsers put one U+FFFC in a paragraph's text for every inline child, links included,
    so dropping them turns "is a communication protocol that" into "is a that". The
    Hypertext interface maps each such offset to its object; child order does not, since
    plain text runs are children too. A container made of nothing but children is left
    empty: its content is already reported by the children themselves.
    """
    if _OBJECT_REPLACEMENT not in raw:
        return raw
    hypertext = None
    with contextlib.suppress(Exception):
        hypertext = element.get_hypertext_iface()
    if hypertext is None or depth >= 2 or not raw.replace(_OBJECT_REPLACEMENT, "").strip():
        return raw.replace(_OBJECT_REPLACEMENT, " ")
    parts: list[str] = []
    for offset, char in enumerate(raw):
        if char != _OBJECT_REPLACEMENT:
            parts.append(char)
            continue
        child = None
        with contextlib.suppress(Exception):
            index = Atspi.Hypertext.get_link_index(hypertext, offset)
            if index >= 0:
                link = Atspi.Hypertext.get_link(hypertext, index)
                child = Atspi.Hyperlink.get_object(link, 0) if link is not None else None
        if child is None:
            parts.append(" ")
            continue
        child_raw = _read_text(child)
        inline = (
            _inline_children(child, child_raw, depth + 1) if child_raw else child.get_name() or ""
        )
        parts.append(inline or " ")
    return "".join(parts)


# ── CLI entrypoint for subprocess execution ──────────────────────────────


def _handle_request(request: dict) -> dict:
    """Dispatch a JSON request to the appropriate function."""
    op = request.get("op", "")

    if op == "tree":
        result = get_accessibility_tree(
            app_name=request.get("app_name", ""),
            max_depth=request.get("max_depth", 15),
            role=request.get("role", ""),
        )
        return {"ok": True, "result": result}

    if op == "find":
        elements = find_elements(
            query=request.get("query", ""),
            app_name=request.get("app_name", ""),
            states=request.get("states"),
        )
        return {"ok": True, "result": [asdict(e) for e in elements]}

    if op == "wait":
        try:
            elements, stable = wait_for_elements(
                query=request.get("query", ""),
                app_name=request.get("app_name", ""),
                timeout_ms=request.get("timeout_ms", 5000),
                poll_interval_ms=request.get("poll_interval_ms", 200),
                states=request.get("states"),
                stable_ms=request.get("stable_ms", 0),
            )
        except TimeoutError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "result": [asdict(e) for e in elements], "stable": stable}

    if op == "list_windows":
        return {"ok": True, "result": list_windows()}

    if op == "focus_window":
        result = focus_window(app_name=request.get("app_name", ""))
        return {"ok": True, "result": result}

    return {"ok": False, "error": f"Unknown operation: {op}"}


if __name__ == "__main__":
    raw = sys.stdin.read()
    try:
        req = json.loads(raw)
    except json.JSONDecodeError as exc:
        json.dump({"ok": False, "error": f"Invalid JSON: {exc}"}, sys.stdout)
        sys.exit(1)

    resp = _handle_request(req)
    json.dump(resp, sys.stdout)
