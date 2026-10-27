"""Screen capture: region grabbing, snip-style selection, auto-detection."""
from __future__ import annotations

import ctypes
import math
import sys

import cv2
import numpy as np

from .squares import BoardRegion, luminance, ring_backgrounds


def make_dpi_aware() -> None:
    """Use physical pixels on Windows so Tk, mss and coordinates agree."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def grab_region(region: BoardRegion) -> np.ndarray:
    """Capture a screen region as a BGR numpy array."""
    import mss

    with mss.mss() as sct:
        shot = sct.grab(region.as_dict())
    return np.asarray(shot)[:, :, :3].copy()


def grab_monitor(index: int = 1):
    """Capture a full monitor. Returns (BGR image, mss monitor dict)."""
    import mss

    with mss.mss() as sct:
        mon = dict(sct.monitors[index])
        shot = sct.grab(mon)
    return np.asarray(shot)[:, :, :3].copy(), mon


def select_region_interactive(parent) -> BoardRegion | None:
    """Fullscreen dimmed screenshot; drag a rectangle around the board."""
    import tkinter as tk
    from PIL import Image, ImageTk

    img, mon = grab_monitor(1)
    result: dict = {"region": None}

    top = tk.Toplevel(parent)
    top.overrideredirect(True)
    top.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
    top.attributes("-topmost", True)
    top.configure(cursor="crosshair")

    photo = ImageTk.PhotoImage(
        Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), master=top)
    canvas = tk.Canvas(top, highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)
    canvas.create_image(0, 0, image=photo, anchor="nw")
    canvas.create_rectangle(0, 0, mon["width"], mon["height"],
                            fill="black", stipple="gray50", outline="")
    canvas.create_text(mon["width"] // 2, 40, fill="white",
                       text="Drag a rectangle tightly around the 8x8 board "
                            "(not the coordinates) — Esc to cancel",
                       font=("Segoe UI", 16, "bold"))
    canvas.image = photo  # keep a reference

    start: dict = {}
    rect_id = None

    def finish(region):
        result["region"] = region
        top.grab_release()
        top.destroy()

    def on_press(e):
        nonlocal rect_id
        start.update(x=e.x, y=e.y)
        rect_id = canvas.create_rectangle(e.x, e.y, e.x, e.y,
                                          outline="#ff3b30", width=3)

    def on_drag(e):
        if rect_id is not None and "x" in start:
            canvas.coords(rect_id, start["x"], start["y"], e.x, e.y)

    def on_release(e):
        if "x" not in start:
            return
        x0, x1 = sorted((start["x"], e.x))
        y0, y1 = sorted((start["y"], e.y))
        if x1 - x0 < 40 or y1 - y0 < 40:
            return  # accidental click — try again
        finish(BoardRegion(mon["left"] + x0, mon["top"] + y0,
                           x1 - x0, y1 - y0))

    def on_esc(_e=None):
        finish(None)

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    top.bind("<Escape>", on_esc)
    top.protocol("WM_DELETE_WINDOW", on_esc)

    top.grab_set()
    top.focus_force()
    parent.wait_window(top)
    return result["region"]


def _checkerboard_backgrounds(bgs: np.ndarray) -> bool:
    """Require two consistent, alternating colors, allowing move highlights."""
    parity = np.indices((8, 8)).sum(axis=0).ravel() % 2
    colors = np.array([np.median(bgs[parity == p], axis=0) for p in (0, 1)])
    contrast = float(np.linalg.norm(colors[0] - colors[1]))
    if abs(luminance(colors[0]) - luminance(colors[1])) <= 12:
        return False
    matches = np.linalg.norm(bgs - colors[parity], axis=1) < max(18, contrast * 0.3)
    grid = matches.reshape(8, 8)
    return (all(np.count_nonzero(matches[parity == p]) >= 28 for p in (0, 1))
            and bool(np.all(grid.sum(axis=0) >= 6))
            and bool(np.all(grid.sum(axis=1) >= 6)))


def _looks_like_board(bgr: np.ndarray) -> bool:
    """Check the entire 8x8 grid rather than just its average brightness."""
    try:
        h, w = bgr.shape[:2]
        if min(h, w) < 40 or abs(w - h) / max(w, h) > 0.08:
            return False
        return _checkerboard_backgrounds(ring_backgrounds(bgr))
    except Exception:
        return False


def _detect_board(img: np.ndarray,
                  exclude_region: BoardRegion | None = None,
                  preferred_region: BoardRegion | None = None) -> BoardRegion | None:
    """Find board-sized square candidates from grid lines in a screenshot."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80,
                            minLineLength=150, maxLineGap=12)
    if lines is None:
        return None
    candidates = set()
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        if abs(y2 - y1) <= 2:
            side = abs(int(x2) - int(x1)) + 1
            for row in range(9):
                candidates.add((min(int(x1), int(x2)),
                                round((int(y1) + int(y2)) / 2 - row * side / 8),
                                side))
        elif abs(x2 - x1) <= 2:
            side = abs(int(y2) - int(y1)) + 1
            for col in range(9):
                candidates.add((round((int(x1) + int(x2)) / 2 - col * side / 8),
                                min(int(y1), int(y2)), side))

    # Prefer the main board over small previews. Sample square corners first
    # so unrelated window edges do not require expensive ring sampling.
    def priority(candidate):
        left, top, side = candidate
        if preferred_region is None:
            return (-side, top, left)
        ref = preferred_region
        distance = math.hypot(left + side / 2 - ref.left - ref.width / 2,
                              top + side / 2 - ref.top - ref.height / 2)
        cost = 2 * abs(math.log(max(1, side) / max(1, ref.width))) + distance / max(side, ref.width, 1)
        return (cost, -side, top)

    for left, top, side in sorted(candidates, key=priority):
        if side < 160 or left < 0 or top < 0 or left + side > w or top + side > h:
            continue
        if (exclude_region and left < exclude_region.left + exclude_region.width
                and left + side > exclude_region.left
                and top < exclude_region.top + exclude_region.height
                and top + side > exclude_region.top):
            continue
        offsets = ((np.arange(8)[:, None] + (0.12, 0.88)) * side / 8).astype(int)
        samples = img[(top + offsets)[:, :, None, None],
                      (left + offsets)[None, None, :, :]].astype(np.float32)
        bgs = np.median(samples, axis=(1, 3)).reshape(64, 3)
        if (_checkerboard_backgrounds(bgs)
                and _looks_like_board(img[top:top + side, left:left + side])):
            return BoardRegion(left, top, side, side)
    return None


def try_auto_detect(exclude_region: BoardRegion | None = None,
                    search_region: BoardRegion | None = None,
                    preferred_region: BoardRegion | None = None) -> BoardRegion | None:
    """Find a board on the primary monitor or within a bound window's client.

    Returns None when nothing convincing is found — fall back to manual
    selection.
    """
    try:
        if search_region is not None:
            img, mon = grab_region(search_region), search_region.as_dict()
        else:
            img, mon = grab_monitor(1)
        exclude = None
        if exclude_region:
            exclude = BoardRegion(exclude_region.left - mon["left"],
                                  exclude_region.top - mon["top"],
                                  exclude_region.width, exclude_region.height)
        preferred = None
        if preferred_region:
            preferred = BoardRegion(preferred_region.left - mon["left"],
                                    preferred_region.top - mon["top"],
                                    preferred_region.width, preferred_region.height)
        region = _detect_board(img, exclude_region=exclude, preferred_region=preferred)
        if region:
            region.left += mon["left"]
            region.top += mon["top"]
        return region
    except Exception:
        return None
