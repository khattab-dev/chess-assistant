"""Board grid mapping and per-square occupancy / piece-color sensing.

The sensor never identifies piece types — only which squares are occupied and
by which color. Full positions are reconstructed in tracker.py by matching
sensed changes against the legal moves of the tracked game.

Sensor convention: output is a list of 64 entries in image order (row-major
from the top-left square of the screenshot: index 0 = top-left, 63 =
bottom-right) with values EMPTY / WHITE / BLACK. Mapping to chess.py square
indices (a1=0 .. h8=63) depends on which color sits at the bottom of the
image; see sensor_to_chess_index().
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

EMPTY = "."
WHITE = "W"
BLACK = "B"

RING_FRAC = 0.12     # outer band of each square sampled as its background
INNER_FRAC = 0.72    # central area tested for a piece
# RGB distance from a square's background that counts as "piece". Must stay
# below the white-piece vs light-square gap of common themes (lichess blue is
# the tightest at ~38) but above compression noise.
COLOR_DIST = 30.0
DEFAULT_OCC_THRESHOLD = 0.18


@dataclass
class BoardRegion:
    """Rectangular screen area in absolute virtual-screen pixels."""

    left: int
    top: int
    width: int
    height: int

    def as_dict(self) -> dict:
        return {"left": self.left, "top": self.top,
                "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, d: dict) -> "BoardRegion":
        return cls(int(d["left"]), int(d["top"]),
                   int(d["width"]), int(d["height"]))


def _mode_color(px: np.ndarray) -> np.ndarray:
    """Most common quantized color of a pixel set, as float BGR."""
    if px.size == 0:
        return np.zeros(3, np.float32)
    q = px.astype(np.int32) // 32
    codes = q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2]
    vals, counts = np.unique(codes, return_counts=True)
    sel = codes == vals[int(np.argmax(counts))]
    return np.median(px[sel].astype(np.float32), axis=0)


def _square_box(shape, r: int, c: int):
    h, w = shape[:2]
    cell_h, cell_w = h / 8.0, w / 8.0
    y0 = int(round(r * cell_h))
    y1 = int(round((r + 1) * cell_h))
    x0 = int(round(c * cell_w))
    x1 = int(round((c + 1) * cell_w))
    return y0, x0, y1, x1


def ring_backgrounds(bgr: np.ndarray) -> np.ndarray:
    """Background color of each of the 64 squares, from its outer band.

    The ring (instead of the whole square) is sampled so pieces and
    last-move highlight tints do not dominate the estimate.
    """
    out = np.zeros((64, 3), np.float32)
    for i in range(64):
        r, c = divmod(i, 8)
        y0, x0, y1, x1 = _square_box(bgr.shape, r, c)
        dy = max(1, int((y1 - y0) * RING_FRAC))
        dx = max(1, int((x1 - x0) * RING_FRAC))
        ring = np.concatenate([
            bgr[y0:y0 + dy, x0:x1].reshape(-1, 3),
            bgr[y1 - dy:y1, x0:x1].reshape(-1, 3),
            bgr[y0 + dy:y1 - dy, x0:x0 + dx].reshape(-1, 3),
            bgr[y0 + dy:y1 - dy, x1 - dx:x1].reshape(-1, 3),
        ])
        out[i] = _mode_color(ring)
    return out


def luminance(bgr) -> float:
    b, g, r = float(bgr[0]), float(bgr[1]), float(bgr[2])
    return 0.114 * b + 0.587 * g + 0.299 * r


def _inner_slice(shape, r: int, c: int):
    y0, x0, y1, x1 = _square_box(shape, r, c)
    dy = int((y1 - y0) * (1.0 - INNER_FRAC) / 2.0)
    dx = int((x1 - x0) * (1.0 - INNER_FRAC) / 2.0)
    return y0 + dy, x0 + dx, y1 - dy, x1 - dx


class BoardSensor:
    """Senses occupancy and piece colors for one fixed board region."""

    def __init__(self, region: BoardRegion):
        self.region = region
        self.light_bg = None
        self.dark_bg = None
        self.mid_lum = 128.0
        self.occ_threshold = DEFAULT_OCC_THRESHOLD

    # -- calibration ------------------------------------------------------
    def calibrate(self, bgr: np.ndarray) -> None:
        """Learn the board's two square colors and the occupancy threshold."""
        bgs = ring_backgrounds(bgr)
        lum = np.array([luminance(c) for c in bgs])
        light_idx = [i for i in range(64) if (i // 8 + i % 8) % 2 == 0]
        dark_idx = [i for i in range(64) if (i // 8 + i % 8) % 2 == 1]
        light = np.median(bgs[light_idx], axis=0)
        dark = np.median(bgs[dark_idx], axis=0)
        if luminance(light) < luminance(dark):  # non-standard coloring
            light, dark = dark, light
        self.light_bg, self.dark_bg = light, dark
        self.mid_lum = (luminance(light) + luminance(dark)) / 2.0
        scores = self._occupancy_scores(bgr, bgs)
        self.occ_threshold = self._pick_threshold(scores)
        piece_lums = self._piece_luminances(bgr, bgs, scores)
        values = np.sort(piece_lums[np.isfinite(piece_lums)])
        if values.size >= 2:
            # White pieces can be tinted and darker than the board itself.
            # Learn the two piece-color groups rather than assuming the
            # board's background midpoint separates White from Black.
            split = int(np.argmax(np.diff(values))) + 1
            if values[split] - values[split - 1] >= 20:
                self.mid_lum = float((np.median(values[:split])
                                      + np.median(values[split:])) / 2)

    @staticmethod
    def _pick_threshold(scores: np.ndarray) -> float:
        """Otsu split of the 64 occupancy scores (bimodal when pieces sit
        on ~half the squares), clamped to sane bounds."""
        if scores.max() < 0.08:  # nothing on the board
            return DEFAULT_OCC_THRESHOLD
        img = (np.clip(scores, 0.0, 1.0) * 255).astype(np.uint8)
        t, _ = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return float(min(max(t / 255.0, 0.08), 0.40))

    # -- sensing ----------------------------------------------------------
    def _occupancy_scores(self, bgr: np.ndarray, bgs: np.ndarray) -> np.ndarray:
        scores = np.zeros(64, np.float32)
        for i in range(64):
            r, c = divmod(i, 8)
            iy0, ix0, iy1, ix1 = _inner_slice(bgr.shape, r, c)
            inner = bgr[iy0:iy1, ix0:ix1].reshape(-1, 3).astype(np.int32)
            bg = bgs[i].astype(np.int32)
            dist = np.sqrt(((inner - bg) ** 2).sum(axis=1))
            scores[i] = float(np.mean(dist > COLOR_DIST))
        return scores

    def _piece_luminances(self, bgr: np.ndarray, bgs: np.ndarray,
                          scores: np.ndarray) -> np.ndarray:
        """Measure the foreground of occupied squares, excluding background."""
        values = np.full(64, np.nan, np.float32)
        for i in np.flatnonzero(scores >= self.occ_threshold):
            r, c = divmod(int(i), 8)
            iy0, ix0, iy1, ix1 = _inner_slice(bgr.shape, r, c)
            inner = bgr[iy0:iy1, ix0:ix1].reshape(-1, 3).astype(np.int32)
            dist = np.linalg.norm(inner - bgs[i].astype(np.int32), axis=1)
            piece_px = inner[dist > COLOR_DIST]
            if piece_px.size:
                values[i] = luminance(np.median(piece_px, axis=0))
        return values

    def sense(self, bgr: np.ndarray):
        """Returns (states64 in image order, occupancy scores).

        Per-square background comes from that square's own ring, so squares
        carrying a last-move/selection highlight are still judged against
        their current (tinted) background.
        """
        bgs = ring_backgrounds(bgr)
        scores = self._occupancy_scores(bgr, bgs)
        piece_lums = self._piece_luminances(bgr, bgs, scores)
        states = [EMPTY] * 64
        for i in range(64):
            if not np.isfinite(piece_lums[i]):
                continue
            states[i] = WHITE if piece_lums[i] > self.mid_lum else BLACK
        return states, scores


def sensor_to_chess_index(i: int, white_bottom: bool) -> int:
    """Image-order index -> chess.py square index (a1=0 .. h8=63)."""
    r, c = divmod(i, 8)
    if white_bottom:
        file, rank = c, 7 - r
    else:
        file, rank = 7 - c, r
    return rank * 8 + file


def chess_to_sensor_index(sq: int, white_bottom: bool) -> int:
    """Chess.py square index -> image-order index."""
    file, rank = sq % 8, sq // 8
    if white_bottom:
        r, c = 7 - rank, file
    else:
        r, c = rank, 7 - file
    return r * 8 + c


def states_to_ascii(states) -> str:
    """ASCII dump of image-order states (top row of the screenshot first)."""
    return "\n".join(
        " ".join(states[r * 8 + c] for c in range(8)) for r in range(8)
    )
