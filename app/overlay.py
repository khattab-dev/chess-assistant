"""A non-activating Windows arrow layer, excluded from board captures."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import math
import sys

import chess

from .squares import BoardRegion, chess_to_sensor_index


class OverlayUnavailable(RuntimeError):
    pass


def arrow_points(region: BoardRegion, white_bottom: bool, uci: str) -> list[tuple[int, int]]:
    """Arrow polygon in board-local physical pixels, including flipped boards."""
    move = chess.Move.from_uci(uci)
    if not move or move.from_square == move.to_square:
        raise ValueError("An overlay arrow needs two different squares")
    centers = []
    for square in (move.from_square, move.to_square):
        row, col = divmod(chess_to_sensor_index(square, white_bottom), 8)
        centers.append(((col + 0.5) * region.width / 8,
                        (row + 0.5) * region.height / 8))
    (x1, y1), (x2, y2) = centers
    length = math.hypot(x2 - x1, y2 - y1)
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    nx, ny = -uy, ux
    cell = min(region.width, region.height) / 8
    shaft = max(3, cell * 0.1) / 2
    head = max(10, cell * 0.42) / 2
    head_length = min(max(12, cell * 0.48), length * 0.45)
    bx, by = x2 - ux * head_length, y2 - uy * head_length
    points = [(x1 + nx * shaft, y1 + ny * shaft),
              (bx + nx * shaft, by + ny * shaft),
              (bx + nx * head, by + ny * head), (x2, y2),
              (bx - nx * head, by - ny * head),
              (bx - nx * shaft, by - ny * shaft),
              (x1 - nx * shaft, y1 - ny * shaft)]
    return [(round(x), round(y)) for x, y in points]


def _rgb(red, green, blue):
    return red | green << 8 | blue << 16


class _WindowsLayer:
    """Own the native window so showing it never calls Tk's activation path."""

    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.hwnd = None
        self.visible = False
        self.points = []
        self.error = None
        self._resources = []
        self.class_name = f"ChessAssistantBoardArrow_{id(self):x}"

        def declare(dll, name, restype, *argtypes):
            fn = getattr(dll, name)
            fn.restype, fn.argtypes = restype, list(argtypes)
            return fn

        result_type = ctypes.c_ssize_t
        self.callback_type = ctypes.WINFUNCTYPE(result_type, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class WindowClass(ctypes.Structure):
            _fields_ = [("style", wt.UINT), ("callback", self.callback_type),
                        ("class_extra", ctypes.c_int), ("window_extra", ctypes.c_int),
                        ("instance", wt.HINSTANCE), ("icon", wt.HICON),
                        ("cursor", wt.HANDLE), ("background", wt.HBRUSH),
                        ("menu", wt.LPCWSTR), ("name", wt.LPCWSTR)]

        class Paint(ctypes.Structure):
            _fields_ = [("dc", wt.HDC), ("erase", wt.BOOL), ("rect", wt.RECT),
                        ("restore", wt.BOOL), ("update", wt.BOOL), ("reserved", wt.BYTE * 32)]

        self.Paint = Paint
        self.def_proc = declare(self.user, "DefWindowProcW", result_type,
                                wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        self.begin_paint = declare(self.user, "BeginPaint", wt.HDC, wt.HWND, ctypes.POINTER(Paint))
        self.end_paint = declare(self.user, "EndPaint", wt.BOOL, wt.HWND, ctypes.POINTER(Paint))
        self.fill_rect = declare(self.user, "FillRect", ctypes.c_int,
                                 wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH)
        self.client_rect = declare(self.user, "GetClientRect", wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
        self.select = declare(self.gdi, "SelectObject", wt.HANDLE, wt.HDC, wt.HANDLE)
        self.polygon = declare(self.gdi, "Polygon", wt.BOOL, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.c_int)
        self.delete = declare(self.gdi, "DeleteObject", wt.BOOL, wt.HANDLE)
        self.show_window = declare(self.user, "ShowWindow", wt.BOOL, wt.HWND, ctypes.c_int)
        self.position = declare(self.user, "SetWindowPos", wt.BOOL, wt.HWND, wt.HWND,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT)
        self.invalidate = declare(self.user, "InvalidateRect", wt.BOOL,
                                  wt.HWND, ctypes.POINTER(wt.RECT), wt.BOOL)
        self.update_window = declare(self.user, "UpdateWindow", wt.BOOL, wt.HWND)
        self.foreground = declare(self.user, "GetForegroundWindow", wt.HWND)
        self.window_at = declare(self.user, "WindowFromPoint", wt.HWND, wt.POINT)
        self.ancestor = declare(self.user, "GetAncestor", wt.HWND, wt.HWND, wt.UINT)
        self.is_window = declare(self.user, "IsWindow", wt.BOOL, wt.HWND)
        self.destroy_window = declare(self.user, "DestroyWindow", wt.BOOL, wt.HWND)
        self.unregister = declare(self.user, "UnregisterClassW", wt.BOOL, wt.LPCWSTR, wt.HINSTANCE)
        kernel.GetModuleHandleW.restype = wt.HINSTANCE
        kernel.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        self.instance = kernel.GetModuleHandleW(None)
        self.callback = self.callback_type(self._window_proc)
        cls = WindowClass(name=self.class_name, callback=self.callback, instance=self.instance)
        register = declare(self.user, "RegisterClassW", wt.WORD, ctypes.POINTER(WindowClass))
        if not register(ctypes.byref(cls)):
            raise OverlayUnavailable("Could not register the board-arrow window")
        try:
            create_brush = declare(self.gdi, "CreateSolidBrush", wt.HBRUSH, wt.DWORD)
            create_pen = declare(self.gdi, "CreatePen", wt.HANDLE,
                                 ctypes.c_int, ctypes.c_int, wt.DWORD)
            self.background = create_brush(_rgb(255, 0, 255))
            self.brush = create_brush(_rgb(255, 202, 40))
            self.pen = create_pen(0, 2, _rgb(92, 57, 0))
            self._resources = [self.background, self.brush, self.pen]
            create = declare(self.user, "CreateWindowExW", wt.HWND,
                             wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                             ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID)
            # LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE; no owner window,
            # so the layer works while the control panel is minimized too.
            styles = 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000
            self.hwnd = create(styles, self.class_name, "Chess Assistant board arrow",
                               0x80000000, 0, 0, 1, 1, None, None, self.instance, None)
            if not self.hwnd:
                raise OverlayUnavailable("Could not create the board-arrow window")
            layered = declare(self.user, "SetLayeredWindowAttributes", wt.BOOL,
                              wt.HWND, wt.DWORD, wt.BYTE, wt.DWORD)
            affinity = declare(self.user, "SetWindowDisplayAffinity", wt.BOOL, wt.HWND, wt.DWORD)
            if not layered(self.hwnd, _rgb(255, 0, 255), 215, 3):
                raise OverlayUnavailable("Transparent board arrows are unavailable")
            if not affinity(self.hwnd, 0x11):
                raise OverlayUnavailable("Could not exclude the arrow from screen capture")
        except Exception:
            self.close()
            raise

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message == 0x0084:  # WM_NCHITTEST: mouse input passes to the game.
            return -1
        if message == 0x0021:  # WM_MOUSEACTIVATE: never activate this window.
            return 3
        if message == 0x0014:  # WM_ERASEBKGND: painting below owns the background.
            return 1
        if message == 0x000F:  # WM_PAINT
            paint = self.Paint()
            dc = self.begin_paint(hwnd, ctypes.byref(paint))
            try:
                rect = wt.RECT()
                self.client_rect(hwnd, ctypes.byref(rect))
                self.fill_rect(dc, ctypes.byref(rect), self.background)
                if self.points:
                    old_brush = self.select(dc, self.brush)
                    old_pen = self.select(dc, self.pen)
                    try:
                        points = (wt.POINT * len(self.points))(*(wt.POINT(x, y) for x, y in self.points))
                        self.polygon(dc, points, len(points))
                    finally:
                        self.select(dc, old_pen)
                        self.select(dc, old_brush)
            except Exception as e:
                self.error = str(e)
            finally:
                self.end_paint(hwnd, ctypes.byref(paint))
            return 0
        return self.def_proc(hwnd, message, wparam, lparam)

    def root_at(self, x: int, y: int):
        hwnd = self.window_at(wt.POINT(x, y))
        return self.ancestor(hwnd, 2) if hwnd else None

    def is_active(self, owner) -> bool:
        return bool(owner and self.is_window(owner) and self.foreground() == owner)

    def show(self, region: BoardRegion, points) -> None:
        self.points = points
        # TOPMOST, SWP_NOACTIVATE | SWP_SHOWWINDOW. This never restores or
        # focuses either the game or the assistant window.
        if not self.position(self.hwnd, wt.HWND(-1), region.left, region.top,
                             region.width, region.height, 0x0010 | 0x0040):
            raise OverlayUnavailable("Could not position the board arrow")
        self.invalidate(self.hwnd, None, False)
        self.update_window(self.hwnd)
        self.visible = True
        if self.error:
            raise OverlayUnavailable(self.error)

    def hide(self) -> None:
        if self.hwnd and self.visible:
            self.show_window(self.hwnd, 0)
            self.visible = False

    def close(self) -> None:
        if self.hwnd:
            self.destroy_window(self.hwnd)
            self.hwnd = None
        for resource in self._resources:
            if resource:
                self.delete(resource)
        self._resources = []
        self.unregister(self.class_name, self.instance)


class BoardArrowOverlay:
    """Bind an arrow to the detected game window, never to unrelated apps."""

    def __init__(self):
        if sys.platform != "win32" or sys.getwindowsversion().build < 19041:
            raise OverlayUnavailable("Board arrows require Windows 10 version 2004 or newer")
        self.layer = _WindowsLayer()
        self.region = None
        self.owner = None
        self._drawn = None

    def bind_region(self, region: BoardRegion, owner=None) -> None:
        self.hide()
        self.region = BoardRegion(**region.as_dict())
        self.owner = owner if owner is not None else self.layer.root_at(
            region.left + region.width // 2, region.top + region.height // 2)

    def show(self, region: BoardRegion, white_bottom: bool, uci: str) -> None:
        if region != self.region:
            self.bind_region(region)
        if not self.layer.is_active(self.owner):
            self.hide()
            return
        key = (*region.as_dict().values(), white_bottom, uci)
        if not self.layer.visible or key != self._drawn:
            self.layer.show(region, arrow_points(region, white_bottom, uci))
            self._drawn = key

    def hide(self) -> None:
        self.layer.hide()
        self._drawn = None

    def close(self) -> None:
        self.layer.close()
