"""Tinted pieces must be classified by piece colors, not board brightness."""
from pathlib import Path

import chess
import cv2
import numpy as np
import pytest

from app.capture import _looks_like_board
from app.squares import BoardRegion, BoardSensor, luminance
from app.tracker import PositionTracker, board_to_sensor_states
from test_squares import THEMES, render_board

TEXTURED_FEN = "rnbqkbnr/pppppp2/6p1/7p/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 3"


@pytest.mark.parametrize("white_bottom", [True, False])
@pytest.mark.parametrize("side", [384, 768])
def test_reported_fen_stays_in_sync_with_textured_screenshot(white_bottom, side):
    path = Path(__file__).parent / "fixtures" / "chesscom-textured.png"
    image = cv2.imread(str(path))
    assert image is not None
    image = cv2.resize(image, (side, side), interpolation=cv2.INTER_AREA)
    if not white_bottom:
        image = cv2.rotate(image, cv2.ROTATE_180)
    board = chess.Board(TEXTURED_FEN)
    assert board.is_valid()
    assert _looks_like_board(image)
    sensor = BoardSensor(BoardRegion(0, 0, side, side))

    sensor.calibrate(image)
    states, _ = sensor.sense(image)

    assert states == board_to_sensor_states(board, white_bottom)
    assert states.count("W") == 16
    assert states.count("B") == 16
    tracker = PositionTracker()
    assert tracker.set_fen(TEXTURED_FEN, states) is None
    assert tracker.white_bottom == white_bottom
    for now in (0, 0.1, 0.2, 0.7, 1.0):
        result = tracker.update_stable(states, now=now)
    assert result.status == "none"
    assert not result.detail
    assert tracker.fen == TEXTURED_FEN


@pytest.mark.parametrize("theme", THEMES)
def test_white_pieces_darker_than_board_background_are_still_white(theme):
    board = chess.Board()
    image = render_board(board, theme)
    image[np.all(image == (250, 250, 250), axis=2)] = (120, 167, 161)
    sensor = BoardSensor(BoardRegion(0, 0, 512, 512))
    old_midpoint = sum(luminance(color) for color in THEMES[theme]) / 2
    assert luminance((120, 167, 161)) < old_midpoint

    sensor.calibrate(image)
    states, _ = sensor.sense(image)

    assert states == board_to_sensor_states(board, True)


def test_piece_color_learning_handles_one_white_king_among_many_black_pieces():
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/8/4K3 w kq - 0 1")
    image = render_board(board, "green")
    image[np.all(image == (250, 250, 250), axis=2)] = (120, 167, 161)
    sensor = BoardSensor(BoardRegion(0, 0, 512, 512))

    sensor.calibrate(image)
    states, _ = sensor.sense(image)

    assert states == board_to_sensor_states(board, True)
