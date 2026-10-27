"""Shared arrow appearance, geometry and alpha-aware preview rendering."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re

import chess
from PIL import Image, ImageDraw

from .squares import BoardRegion, chess_to_sensor_index

PRESETS = {"Amber": "#ffca28", "Green": "#43c96f", "Blue": "#4285f4",
           "Cyan": "#00c8d7", "Red": "#ef5350", "Purple": "#ab66e8",
           "Pink": "#ff00ff", "White": "#ffffff"}


@dataclass(frozen=True)
class ArrowStyle:
    color: str = "#ffca28"
    opacity: int = 85
    thickness: int = 100

    def __post_init__(self):
        if not isinstance(self.color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", self.color):
            raise ValueError("Use a six-digit hex color, such as #43c96f.")
        for name, low, high in (("opacity", 0, 100), ("thickness", 50, 200)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Arrow {name} must be a number from {low} to {high}.")
            if not low <= value <= high:
                raise ValueError(f"Arrow {name} must be from {low} to {high}.")
            object.__setattr__(self, name, round(value))
        object.__setattr__(self, "color", self.color.lower())

    @classmethod
    def from_config(cls, settings):
        if not isinstance(settings, dict):
            return cls()
        values = asdict(cls())
        for name in values:
            try:
                candidate = dict(values, **{name: settings.get(name, values[name])})
                cls(**candidate)
                values = candidate
            except (ValueError, TypeError):
                pass
        return cls(**values)

    def as_dict(self):
        return asdict(self)

    @property
    def rgb(self):
        return tuple(int(self.color[index:index + 2], 16) for index in (1, 3, 5))

    @property
    def alpha(self):
        return round(255 * self.opacity / 100)

    @property
    def scale(self):
        return self.thickness / 100

    @property
    def outline(self):
        red, green, blue = self.rgb
        if 0.299 * red + 0.587 * green + 0.114 * blue < 120:
            return tuple(round(value * 0.45 + 255 * 0.55) for value in self.rgb)
        return tuple(round(value * 0.35) for value in self.rgb)

    @property
    def color_key(self):
        return next(color for color in ((255, 0, 255), (0, 255, 0), (1, 2, 3))
                    if color not in (self.rgb, self.outline))


def arrow_polygon(start, tip, cell: float, thickness: int = 100):
    (x1, y1), (x2, y2) = start, tip
    length = math.hypot(x2 - x1, y2 - y1)
    if not length:
        raise ValueError("An arrow needs two different squares")
    scale = thickness / 100
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    nx, ny = -uy, ux
    shaft = max(3, cell * 0.1) * scale / 2
    head = max(10, cell * 0.42) * scale / 2
    head_length = min(max(12, cell * 0.48) * scale, length * 0.45)
    bx, by = x2 - ux * head_length, y2 - uy * head_length
    return [(round(x), round(y)) for x, y in (
        (x1 + nx * shaft, y1 + ny * shaft), (bx + nx * shaft, by + ny * shaft),
        (bx + nx * head, by + ny * head), (x2, y2), (bx - nx * head, by - ny * head),
        (bx - nx * shaft, by - ny * shaft), (x1 - nx * shaft, y1 - ny * shaft))]


def arrow_points(region: BoardRegion, white_bottom: bool, uci: str, thickness: int = 100):
    move = chess.Move.from_uci(uci)
    if not move or move.from_square == move.to_square:
        raise ValueError("An overlay arrow needs two different squares")
    centers = []
    for square in (move.from_square, move.to_square):
        row, col = divmod(chess_to_sensor_index(square, white_bottom), 8)
        centers.append(((col + 0.5) * region.width / 8, (row + 0.5) * region.height / 8))
    return arrow_polygon(*centers, min(region.width, region.height) / 8, thickness)


def render_polygon(size, points, style: ArrowStyle, cell: float) -> Image.Image:
    factor = 4
    image = Image.new("RGBA", (size[0] * factor, size[1] * factor))
    if not style.opacity:
        return image.resize(size)
    draw = ImageDraw.Draw(image)
    vertices = [(x * factor, y * factor) for x, y in points]
    draw.polygon(vertices, fill=(*style.rgb, style.alpha))
    draw.line(vertices + vertices[:1], fill=(*style.outline, style.alpha),
              width=max(1, round(cell * 0.02 * style.scale)) * factor, joint="curve")
    image = image.resize(size, Image.Resampling.LANCZOS)
    # Lanczos ringing can overshoot the requested alpha at sharp edges.
    image.putalpha(image.getchannel("A").point(lambda value: min(value, style.alpha)))
    return image


def render_arrow(size: int, white_bottom: bool, uci: str, style: ArrowStyle) -> Image.Image:
    points = arrow_points(BoardRegion(0, 0, size, size), white_bottom, uci, style.thickness)
    return render_polygon((size, size), points, style, size / 8)


def render_preview(style: ArrowStyle) -> Image.Image:
    return render_polygon((120, 40), arrow_polygon((12, 20), (106, 20), 40, style.thickness), style, 40)
