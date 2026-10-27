"""Small, cached chess-piece icons for the tracked-position preview."""
from __future__ import annotations

import sys
import tkinter as tk

import chess
from PIL import Image, ImageDraw, ImageFont, ImageTk


GLYPHS = {
    chess.KING: "\u265a", chess.QUEEN: "\u265b", chess.ROOK: "\u265c",
    chess.BISHOP: "\u265d", chess.KNIGHT: "\u265e", chess.PAWN: "\u265f",
}
PIECE_COLORS = {chess.WHITE: ("#faf8ef", "#30363b"),
                chess.BLACK: ("#252b30", "#f2efdf")}


def render_piece_icons(size: int) -> dict[str, Image.Image]:
    """Render outlined silhouettes at 4x resolution, then antialias them."""
    scale = 4
    font = None
    for filename in ("seguisym.ttf", "DejaVuSans.ttf", "NotoSansSymbols2-Regular.ttf",
                     "/System/Library/Fonts/Apple Symbols.ttf", "FreeSerif.ttf"):
        try:
            font = ImageFont.truetype(filename, (size - 6) * scale)
            break
        except OSError:
            continue
    if font is None:
        return {}  # Tk can use the system's font fallback instead.

    icons = {}
    for color, (fill, outline) in PIECE_COLORS.items():
        for piece_type, glyph in GLYPHS.items():
            icon = Image.new("RGBA", (size * scale, size * scale))
            draw = ImageDraw.Draw(icon)
            left, top, right, bottom = draw.textbbox(
                (0, 0), glyph, font=font, stroke_width=scale)
            x = (size * scale - (right - left)) / 2 - left
            y = (size * scale - (bottom - top)) / 2 - top
            draw.text((round(x), round(y)), glyph, font=font, fill=fill,
                      stroke_width=scale, stroke_fill=outline)
            symbol = chess.Piece(piece_type, color).symbol()
            icons[symbol] = icon.resize((size, size), Image.Resampling.LANCZOS)
    return icons


class PieceRenderer:
    """Keep Tk image references alive and reuse all twelve icons on redraws."""

    def __init__(self, master, size: int):
        self.size = size
        self.images = {symbol: ImageTk.PhotoImage(icon, master=master)
                       for symbol, icon in render_piece_icons(size).items()}
        self.font_family = ("Segoe UI Symbol" if sys.platform == "win32" else
                            "Apple Symbols" if sys.platform == "darwin" else "DejaVu Sans")

    def draw(self, canvas: tk.Canvas, piece: chess.Piece, square: chess.Square,
             x: float, y: float) -> None:
        square_tag = "square:" + chess.square_name(square)
        tags = ("piece", "piece:" + piece.symbol(), square_tag)
        icon = self.images.get(piece.symbol())
        if icon is not None:
            canvas.create_image(x, y, image=icon, tags=tags)
            return
        # If Pillow cannot locate a font file, let Tk resolve the same Unicode
        # silhouettes through native font fallback. Keep contrasting outlines.
        fill, outline = PIECE_COLORS[piece.color]
        options = dict(text=GLYPHS[piece.piece_type],
                       font=(self.font_family, -(self.size - 6)))
        for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0),
                       (1, 0), (-1, 1), (0, 1), (1, 1)):
            canvas.create_text(x + dx, y + dy, fill=outline,
                               tags=("piece-outline", square_tag), **options)
        canvas.create_text(x, y, fill=fill, tags=tags, **options)
