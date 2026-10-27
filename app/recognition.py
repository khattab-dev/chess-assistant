"""Offline piece classification and explicit reconstruction of game state.

The bundled fenshot tile classifier is MIT licensed; see licenses/. Pixels
provide piece placement, not turn, castling rights or previous game history.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading

import chess
import cv2
import numpy as np

from .paths import bundle_root
from .squares import EMPTY, sensor_to_chess_index

LABELS = ".KQRBNPkqrbnp"
CONFIDENCE_FLOOR = 0.80
MODEL_PATH = Path("app/assets/chess-tiles-v2.onnx")


@dataclass(frozen=True)
class BoardRead:
    """Piece symbols and confidence in screenshot order, top-left first."""
    symbols: tuple[str, ...]
    confidence: tuple[float, ...]

    @property
    def plausible(self) -> bool:
        return self.symbols.count("K") == self.symbols.count("k") == 1

    @property
    def uncertain(self) -> tuple[int, ...]:
        return tuple(i for i, value in enumerate(self.confidence) if value < CONFIDENCE_FLOOR)

    def placement(self, white_bottom: bool) -> chess.Board:
        board = chess.Board.empty()
        for i, symbol in enumerate(self.symbols):
            if symbol != EMPTY:
                board.set_piece_at(sensor_to_chess_index(i, white_bottom),
                                   chess.Piece.from_symbol(symbol))
        return board

    def bottom_hint(self, previous: bool | None = None) -> bool:
        # A hint for the review form, never a decision to start tracking.
        if previous is not None:
            return previous
        white = [i // 8 for i, symbol in enumerate(self.symbols) if symbol == "P"]
        black = [i // 8 for i, symbol in enumerate(self.symbols) if symbol == "p"]
        if white and black:
            return sum(white) / len(white) >= sum(black) / len(black)
        return True


class PieceRecognizer:
    """Lazy CPU-only ONNX inference using the existing OpenCV dependency."""
    def __init__(self, path: Path | None = None):
        self.path = path or bundle_root() / MODEL_PATH
        self._net = None
        self._lock = threading.Lock()

    def read(self, bgr: np.ndarray) -> BoardRead:
        if (bgr is None or bgr.ndim != 3 or bgr.shape[2] != 3
                or min(bgr.shape[:2]) < 64):
            raise ValueError("Recognition needs a complete, cropped board image.")
        gray = cv2.cvtColor(bgr.astype(np.float32), cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (256, 256), interpolation=cv2.INTER_LINEAR) / 255.0
        tiles = np.ascontiguousarray(
            small.reshape(8, 32, 8, 32).transpose(0, 2, 1, 3).reshape(64, 1024))
        with self._lock:
            if self._net is None:
                if not self.path.is_file():
                    raise FileNotFoundError("The bundled piece-recognition model is missing.")
                self._net = cv2.dnn.readNetFromONNX(str(self.path))
                self._net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                self._net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            self._net.setInput(tiles)
            probs = self._net.forward()
        if probs.shape != (64, 13) or not np.isfinite(probs).all():
            raise ValueError("Piece recognition returned invalid predictions.")
        predicted = probs.argmax(axis=1)
        return BoardRead(tuple(LABELS[int(i)] for i in predicted),
                         tuple(float(value) for value in probs.max(axis=1)))


class StableBoardReads:
    """Require repeated whole-board reads; suppress a dismissed placement."""
    def __init__(self):
        self.key = None
        self.since = 0.0
        self.frames = 0
        self.suppressed = None

    def offer(self, read: BoardRead, now: float) -> bool:
        if read.symbols != self.key:
            self.key, self.since, self.frames = read.symbols, now, 1
            if self.key != self.suppressed:
                self.suppressed = None
            return False
        self.frames += 1
        return (read.plausible and self.frames >= 3 and now - self.since >= 0.6
                and self.key != self.suppressed)


def build_position(symbols, white_bottom: bool, turn: chess.Color, *,
                   castling: str = "-", en_passant: str = "-",
                   halfmove: int = 0, fullmove: int = 1) -> chess.Board:
    """Validate a confirmed read without inventing hidden history."""
    if len(symbols) != 64 or any(symbol not in LABELS for symbol in symbols):
        raise ValueError("Choose a valid piece or Empty for every square.")
    if turn is None:
        raise ValueError("Choose whose turn it is before continuing.")
    if halfmove < 0 or fullmove < 1:
        raise ValueError("The draw counter must be 0 or greater; move number must be 1 or greater.")
    board = BoardRead(tuple(symbols), ()).placement(white_bottom)
    board.turn = turn
    board.set_castling_fen(castling or "-")
    try:
        board.ep_square = None if en_passant.strip() == "-" else chess.parse_square(en_passant.strip())
    except ValueError:
        raise ValueError("En passant must be '-' or a square such as e3 or d6.") from None
    board.halfmove_clock, board.fullmove_number = halfmove, fullmove
    status = board.status()
    if status & (chess.STATUS_NO_WHITE_KING | chess.STATUS_NO_BLACK_KING | chess.STATUS_TOO_MANY_KINGS):
        raise ValueError("The board needs exactly one White king and one Black king.")
    if status & chess.STATUS_PAWNS_ON_BACKRANK:
        raise ValueError("A pawn is on the first or eighth rank. Check the piece and bottom color.")
    if status & chess.STATUS_BAD_CASTLING_RIGHTS:
        raise ValueError("Those castling rights do not match the king and rook squares.")
    if status & chess.STATUS_INVALID_EP_SQUARE:
        raise ValueError("That en passant square does not match this position and turn.")
    if status != chess.STATUS_VALID:
        raise ValueError("This position is not legal. Check the pieces, bottom color and whose turn it is.")
    return board
