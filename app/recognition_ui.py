"""Inline review of recognized pieces and details absent from a screenshot."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import chess

from .recognition import BoardRead, build_position
from .squares import EMPTY, sensor_to_chess_index

PIECE_CHOICES = {"Empty": EMPTY}
for color in (chess.WHITE, chess.BLACK):
    for kind in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN, chess.KING):
        piece = chess.Piece(kind, color)
        PIECE_CHOICES[("White " if color else "Black ") + chess.piece_name(kind)] = piece.symbol()
PIECE_NAMES = {symbol: name for name, symbol in PIECE_CHOICES.items()}


class RecognitionReview(ttk.Frame):
    def __init__(self, parent, canvas, read: BoardRead, white_bottom: bool,
                 draw_board, apply, retry, cancel, warn):
        super().__init__(parent)
        self.canvas, self.read = canvas, read
        self.symbols = list(read.symbols)
        self.corrected = set()
        self.draw_board, self.apply = draw_board, apply
        self.warn = warn
        self.bottom = tk.StringVar(self, "White" if white_bottom else "Black")
        self.turn = tk.StringVar(self, "")
        self.selected = min(range(64), key=lambda i: read.confidence[i])
        self.square_text = tk.StringVar(self)
        self.piece = tk.StringVar(self)
        self.ep = tk.StringVar(self, "-")
        self.halfmove, self.fullmove = tk.StringVar(self, "0"), tk.StringVar(self, "1")
        ttk.Label(self, text="Review recognized position", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(self, text="Click a square to correct its piece. Orange = uncertain.",
                  wraplength=315).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self._choice("Your color (bottom)", self.bottom, ("White", "Black"), 2, self._redraw)
        self._choice("Whose turn?", self.turn, ("White", "Black"), 3)
        ttk.Label(self, textvariable=self.square_text).grid(row=4, column=0, columnspan=2, sticky="w", pady=(5, 0))
        editor = ttk.Combobox(self, textvariable=self.piece, values=list(PIECE_CHOICES),
                              state="readonly", width=17)
        editor.grid(row=5, column=0, columnspan=2, sticky="w")
        editor.bind("<<ComboboxSelected>>", lambda _: self._edit_piece())
        ttk.Label(self, text="Castling still allowed (check all):").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(5, 0))
        rights = ttk.Frame(self)
        rights.grid(row=7, column=0, columnspan=2, sticky="w")
        self.castling = {}
        for i, (symbol, name) in enumerate((("K", "White kingside"), ("Q", "White queenside"),
                                             ("k", "Black kingside"), ("q", "Black queenside"))):
            value = self.castling[symbol] = tk.BooleanVar(self, False)
            ttk.Checkbutton(rights, text=name, variable=value).grid(row=i // 2, column=i % 2, sticky="w")
        self._choice("En passant target", self.ep,
                     ("-", *(file + rank for rank in "36" for file in "abcdefgh")), 8)
        counts = ttk.Frame(self)
        counts.grid(row=9, column=0, columnspan=2, sticky="w", pady=(3, 0))
        ttk.Label(counts, text="Move number").pack(side="left")
        ttk.Entry(counts, textvariable=self.fullmove, width=4).pack(side="left", padx=(3, 10))
        ttk.Label(counts, text="Draw counter").pack(side="left")
        ttk.Entry(counts, textvariable=self.halfmove, width=4).pack(side="left", padx=3)
        ttk.Label(self, text="Draw counter = half-moves since a pawn move/capture.\n"
                  "Use 0 / move 1 if unknown; earlier repetitions are unavailable.",
                  wraplength=315).grid(row=10, column=0, columnspan=2, sticky="w", pady=(2, 4))
        actions = ttk.Frame(self)
        actions.grid(row=11, column=0, columnspan=2, sticky="w")
        ttk.Button(actions, text="Continue", command=self._continue).pack(side="left")
        ttk.Button(actions, text="Read again", command=retry).pack(side="left", padx=5)
        ttk.Button(actions, text="Cancel", command=cancel).pack(side="left")
        self._binding = self.canvas.bind("<Button-1>", self._select_square, add="+")
        self._redraw()

    @property
    def white_bottom(self):
        return self.bottom.get() == "White"

    def _choice(self, label, variable, values, row, callback=None):
        ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=(0, 6))
        field = ttk.Combobox(self, textvariable=variable, values=values, state="readonly", width=9)
        field.grid(row=row, column=1, sticky="w")
        if callback:
            field.bind("<<ComboboxSelected>>", lambda _: callback())

    def _select_square(self, event):
        width, height = int(self.canvas.cget("width")), int(self.canvas.cget("height"))
        if 0 <= event.x < width and 0 <= event.y < height:
            self.selected = min(7, event.y * 8 // height) * 8 + min(7, event.x * 8 // width)
            self._redraw()

    def _edit_piece(self):
        self.symbols[self.selected] = PIECE_CHOICES[self.piece.get()]
        self.corrected.add(self.selected)
        self._redraw()

    def _redraw(self):
        board = BoardRead(tuple(self.symbols), self.read.confidence).placement(self.white_bottom)
        self.draw_board(board, self.white_bottom, None)
        width, height = int(self.canvas.cget("width")) / 8, int(self.canvas.cget("height")) / 8
        for i in (*self.read.uncertain, self.selected):
            if i in self.corrected and i != self.selected:
                continue
            row, col = divmod(i, 8)
            self.canvas.create_rectangle(col * width + 2, row * height + 2,
                                         (col + 1) * width - 2, (row + 1) * height - 2,
                                         outline="#2675cc" if i == self.selected else "#d48200",
                                         width=2, tags=("recognition-review",))
        square = chess.square_name(sensor_to_chess_index(self.selected, self.white_bottom))
        confidence = "edited" if self.selected in self.corrected else f"{self.read.confidence[self.selected]:.0%} confidence"
        self.square_text.set(f"{square}: {confidence}")
        self.piece.set(PIECE_NAMES[self.symbols[self.selected]])

    def _continue(self):
        try:
            if self.turn.get() not in ("White", "Black"):
                raise ValueError("Choose whose turn it is before continuing.")
            rights = "".join(symbol for symbol, value in self.castling.items() if value.get()) or "-"
            try:
                halfmove, fullmove = int(self.halfmove.get()), int(self.fullmove.get())
            except ValueError:
                raise ValueError("Move number and draw counter must be whole numbers.") from None
            board = build_position(self.symbols, self.white_bottom, self.turn.get() == "White",
                                   castling=rights, en_passant=self.ep.get(),
                                   halfmove=halfmove, fullmove=fullmove)
            self.apply(board, self.white_bottom)
        except ValueError as error:
            self.warn(str(error), "red")

    def destroy(self):
        if self._binding and self.canvas.winfo_exists():
            self.canvas.unbind("<Button-1>", self._binding)
        if self.canvas.winfo_exists():
            self.canvas.delete("recognition-review")
        if self._binding:
            self._binding = None
        super().destroy()
