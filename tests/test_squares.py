"""Tests for BoardSensor against synthetic board screenshots.

Renders positions the way chess sites do (colored squares + piece sprites,
approximated by circles) across common board themes, then checks that the
sensor reads back exactly the intended occupancy and piece colors.
"""
import cv2
import chess
import numpy as np
import pytest

from app.squares import BLACK, EMPTY, WHITE, BoardRegion, BoardSensor
from app.tracker import board_to_sensor_states

# BGR colors, matching real site themes.
THEMES = {
    "brown": ((181, 217, 240), (99, 136, 181)),   # chess.com brown
    "green": ((208, 236, 235), (82, 149, 115)),   # chess.com green
    "blue": ((230, 227, 222), (173, 162, 140)),   # lichess blue
}
WHITE_PIECE = (250, 250, 250)
BLACK_PIECE = (35, 35, 35)


def render_board(board, theme, cell=64, white_bottom=True, highlight=None):
    """Render a position as a synthetic screenshot (circles as pieces)."""
    light, dark = THEMES[theme]
    states = board_to_sensor_states(board, white_bottom)
    canvas = np.zeros((cell * 8, cell * 8, 3), np.uint8)
    for i in range(64):
        r, c = divmod(i, 8)
        color = light if (r + c) % 2 == 0 else dark
        if highlight == i:  # yellowish last-move tint, like chess sites
            color = tuple(int(a * 0.5 + b * 0.5)
                          for a, b in zip(color, (30, 200, 255)))
        y0, x0 = r * cell, c * cell
        canvas[y0:y0 + cell, x0:x0 + cell] = color
        st = states[i]
        if st != EMPTY:
            col = WHITE_PIECE if st == WHITE else BLACK_PIECE
            cv2.circle(canvas, (x0 + cell // 2, y0 + cell // 2),
                       int(cell * 0.38), col, -1)
    return canvas


def sense(img):
    s = BoardSensor(BoardRegion(0, 0, img.shape[1], img.shape[0]))
    s.calibrate(img)
    states, scores = s.sense(img)
    return s, states, scores


@pytest.mark.parametrize("theme", list(THEMES))
def test_standard_start(theme):
    board = chess.Board()
    s, states, _ = sense(render_board(board, theme))
    assert states == board_to_sensor_states(board, True)


@pytest.mark.parametrize("theme", list(THEMES))
def test_midgame_position(theme):
    board = chess.Board()
    for san in ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Bxc6", "dxc6", "O-O"]:
        board.push_san(san)
    s, states, _ = sense(render_board(board, theme))
    assert states == board_to_sensor_states(board, True)


def test_flipped_board():
    board = chess.Board()
    for san in ["e4", "e5"]:
        board.push_san(san)
    s, states, _ = sense(render_board(board, "brown", white_bottom=False))
    assert states == board_to_sensor_states(board, False)


def test_last_move_highlight_does_not_break_sensing():
    board = chess.Board()
    board.push_san("e4")
    img = render_board(board, "green", highlight=chess.E4)
    s, states, _ = sense(img)
    assert states == board_to_sensor_states(board, True)


def test_small_board_still_works():
    board = chess.Board()
    for san in ["d4", "d5", "c4"]:
        board.push_san(san)
    s, states, _ = sense(render_board(board, "brown", cell=40))
    assert states == board_to_sensor_states(board, True)


def test_empty_board():
    board = chess.Board()
    board.clear()
    s, states, _ = sense(render_board(board, "brown"))
    assert states == [EMPTY] * 64
