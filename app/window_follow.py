"""Read-only Windows window binding in physical virtual-desktop pixels."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from dataclasses import dataclass
import os
import sys

from .squares import BoardRegion


def contains(outer: BoardRegion, inner: BoardRegion) -> bool:
    return (inner.width > 0 and inner.height > 0 and outer.left <= inner.left
            and outer.top <= inner.top
            and inner.left + inner.width <= outer.left + outer.width
            and inner.top + inner.height <= outer.top + outer.height)


def intersection(a: BoardRegion, b: BoardRegion) -> BoardRegion | None:
    left, top = max(a.left, b.left), max(a.top, b.top)
    right = min(a.left + a.width, b.left + b.width)
    bottom = min(a.top + a.height, b.top + b.height)
    return BoardRegion(left, top, right - left, bottom - top) if right > left and bottom > top else None


def sample_points(region: BoardRegion):
    for x, y in ((0.5, 0.5), (0.1, 0.1), (0.9, 0.1), (0.1, 0.9), (0.9, 0.9)):
        yield round(region.left + x * region.width), round(region.top + y * region.height)


@dataclass(frozen=True)
class WindowSnapshot:
    hwnd: int
    pid: int
    client: BoardRegion
    visible: bool = True
    minimized: bool = False
    dpi: int = 96

    @property
    def geometry(self):
        return (*self.client.as_dict().values(), self.dpi)


class WindowsWindowAPI:
    """No activation, restoration, moving, resizing or keyboard/mouse input."""
    def __init__(self):
        if sys.platform != "win32":
            raise OSError("Window following is available on Windows")
        self.user = ctypes.WinDLL("user32", use_last_error=True)

        def declare(name, result, *args):
            fn = getattr(self.user, name)
            fn.restype, fn.argtypes = result, list(args)
            return fn

        self._at = declare("WindowFromPoint", wt.HWND, wt.POINT)
        self._ancestor = declare("GetAncestor", wt.HWND, wt.HWND, wt.UINT)
        self._valid = declare("IsWindow", wt.BOOL, wt.HWND)
        self._visible = declare("IsWindowVisible", wt.BOOL, wt.HWND)
        self._iconic = declare("IsIconic", wt.BOOL, wt.HWND)
        self._client = declare("GetClientRect", wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
        self._screen = declare("ClientToScreen", wt.BOOL, wt.HWND, ctypes.POINTER(wt.POINT))
        self._pid = declare("GetWindowThreadProcessId", wt.DWORD, wt.HWND, ctypes.POINTER(wt.DWORD))
        self._metric = declare("GetSystemMetrics", ctypes.c_int, ctypes.c_int)
        self._dpi = (declare("GetDpiForWindow", wt.UINT, wt.HWND)
                     if hasattr(self.user, "GetDpiForWindow") else lambda hwnd: 96)

    def root_at(self, x: int, y: int):
        hwnd = self._at(wt.POINT(x, y))
        return self._ancestor(hwnd, 2) if hwnd else None

    def desktop(self) -> BoardRegion:
        return BoardRegion(*(self._metric(index) for index in (76, 77, 78, 79)))

    def snapshot(self, hwnd) -> WindowSnapshot | None:
        if not hwnd or not self._valid(hwnd):
            return None
        pid = wt.DWORD()
        rect, origin = wt.RECT(), wt.POINT()
        if (not self._pid(hwnd, ctypes.byref(pid))
                or not self._client(hwnd, ctypes.byref(rect))
                or not self._screen(hwnd, ctypes.byref(origin))):
            return None
        return WindowSnapshot(int(hwnd), int(pid.value),
                              BoardRegion(origin.x, origin.y, rect.right, rect.bottom),
                              bool(self._visible(hwnd)), bool(self._iconic(hwnd)), int(self._dpi(hwnd) or 96))

    def bind(self, region: BoardRegion):
        owner = self.root_at(region.left + region.width // 2, region.top + region.height // 2)
        snapshot = self.snapshot(owner)
        if not snapshot or snapshot.pid == os.getpid():
            return None
        binding = WindowBinding(self, snapshot, region)
        return binding if binding.unobstructed(region, snapshot) else None


class WindowBinding:
    """Keep the owner and reject handles reassigned to another process."""
    def __init__(self, api, snapshot: WindowSnapshot, region: BoardRegion):
        self.api = api
        self.hwnd, self.pid = snapshot.hwnd, snapshot.pid
        self.anchor = snapshot
        self.region = BoardRegion(**region.as_dict())

    def snapshot(self) -> WindowSnapshot | None:
        current = self.api.snapshot(self.hwnd)
        return current if current and current.pid == self.pid else None

    def search_area(self, current: WindowSnapshot) -> BoardRegion | None:
        if not current.visible or current.minimized:
            return None
        return intersection(current.client, self.api.desktop())

    def predicted(self, current: WindowSnapshot) -> BoardRegion | None:
        previous = self.anchor
        if (current.client.width, current.client.height, current.dpi) != (
                previous.client.width, previous.client.height, previous.dpi):
            return None  # Responsive layouts need actual board detection.
        return BoardRegion(self.region.left + current.client.left - previous.client.left,
                           self.region.top + current.client.top - previous.client.top,
                           self.region.width, self.region.height)

    def search_hint(self, current: WindowSnapshot) -> BoardRegion:
        scale = current.dpi / self.anchor.dpi
        return BoardRegion(current.client.left + round((self.region.left - self.anchor.client.left) * scale),
                           current.client.top + round((self.region.top - self.anchor.client.top) * scale),
                           round(self.region.width * scale), round(self.region.height * scale))

    def unobstructed(self, region: BoardRegion, current: WindowSnapshot) -> bool:
        if (not current.visible or current.minimized or not contains(current.client, region)
                or not contains(self.api.desktop(), region)):
            return False
        return all(self.api.root_at(x, y) == self.hwnd for x, y in sample_points(region))

    def aligned(self, region: BoardRegion) -> bool:
        current = self.snapshot()
        return bool(current and current.geometry == self.anchor.geometry
                    and region == self.region and self.unobstructed(region, current))

    def reanchor(self, region: BoardRegion, current: WindowSnapshot) -> None:
        if (current.hwnd, current.pid) != (self.hwnd, self.pid):
            raise ValueError("The original game window is no longer available")
        self.region, self.anchor = BoardRegion(**region.as_dict()), current
