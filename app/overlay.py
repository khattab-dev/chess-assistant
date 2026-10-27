"""A non-activating Windows arrow layer, excluded from board captures."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import sys

from .arrows import ArrowStyle, arrow_points
from .squares import BoardRegion


class OverlayUnavailable(RuntimeError):
    pass


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
        self.style = ArrowStyle()
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
            self.create_brush = declare(self.gdi, "CreateSolidBrush", wt.HBRUSH, wt.DWORD)
            self.create_pen = declare(self.gdi, "CreatePen", wt.HANDLE,
                                      ctypes.c_int, ctypes.c_int, wt.DWORD)
            self.background = self.create_brush(_rgb(*self.style.color_key))
            self.brush = self.create_brush(_rgb(*self.style.rgb))
            self.pen = self.create_pen(0, 2, _rgb(*self.style.outline))
            self._resources = [self.background, self.brush, self.pen]
            if not all(self._resources):
                raise OverlayUnavailable("Could not create arrow drawing resources")
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
            self.layered = declare(self.user, "SetLayeredWindowAttributes", wt.BOOL,
                                   wt.HWND, wt.DWORD, wt.BYTE, wt.DWORD)
            affinity = declare(self.user, "SetWindowDisplayAffinity", wt.BOOL, wt.HWND, wt.DWORD)
            if not self.layered(self.hwnd, _rgb(*self.style.color_key), self.style.alpha, 3):
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

    def set_style(self, style: ArrowStyle) -> None:
        if style == self.style:
            return
        resources = [self.create_brush(_rgb(*style.color_key)),
                     self.create_brush(_rgb(*style.rgb)),
                     self.create_pen(0, max(1, round(2 * style.scale)), _rgb(*style.outline))]
        if not all(resources):
            for handle in resources:
                if handle:
                    self.delete(handle)
            raise OverlayUnavailable("Could not update arrow drawing resources")
        # Hide before changing the transparency key so no old background can
        # flash opaque. The next eligible show redraws at the current position.
        if style.color_key != self.style.color_key or not style.opacity:
            self.hide()
        if not self.layered(self.hwnd, _rgb(*style.color_key), style.alpha, 3):
            for handle in resources:
                self.delete(handle)
            raise OverlayUnavailable("Could not update arrow opacity")
        old = self._resources
        self.background, self.brush, self.pen = resources
        self._resources, self.style = resources, style
        for handle in old:
            self.delete(handle)
        self.invalidate(self.hwnd, None, False)
        self.update_window(self.hwnd)

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
        self.style = ArrowStyle()

    def set_style(self, style: ArrowStyle) -> None:
        if style != getattr(self, "style", ArrowStyle()):
            self.layer.set_style(style)
            self._drawn = None
        self.style = style

    def bind_region(self, region: BoardRegion, owner=None) -> None:
        self.hide()
        self.region = BoardRegion(**region.as_dict())
        self.owner = owner if owner is not None else self.layer.root_at(
            region.left + region.width // 2, region.top + region.height // 2)

    def show(self, region: BoardRegion, white_bottom: bool, uci: str) -> None:
        style = getattr(self, "style", ArrowStyle())
        if not style.opacity:
            self.hide()
            return
        if region != self.region:
            self.bind_region(region)
        if not self.layer.is_active(self.owner):
            self.hide()
            return
        key = (*region.as_dict().values(), white_bottom, uci, style)
        if not self.layer.visible or key != self._drawn:
            self.layer.show(region, arrow_points(region, white_bottom, uci, style.thickness))
            self._drawn = key

    def hide(self) -> None:
        self.layer.hide()
        self._drawn = None

    def close(self) -> None:
        self.layer.close()
