"""Detect complete boards in screenshots without needing a real display."""
import chess
import cv2
import numpy as np
import pytest

from app.capture import _detect_board, _looks_like_board, try_auto_detect
from app.squares import BoardRegion, BoardSensor
from app.tracker import board_to_sensor_states
from test_squares import THEMES, render_board


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("cell", [24, 40, 64, 101])
def test_detects_board_bounds_and_calibrates(theme, cell):
    board = chess.Board()
    board.push_san("e4")
    image = render_board(board, theme, cell=cell, white_bottom=False, highlight=28)
    screen = np.full((1100, 1920, 3), 55, np.uint8)
    screen[150:150 + cell * 8, 250:250 + cell * 8] = image
    # Unrelated window borders must not distort the inferred grid.
    cv2.rectangle(screen, (10, 10), (1900, 1080), (200, 200, 200), 2)

    region = _detect_board(screen)

    assert region is not None
    assert abs(region.left - 250) <= 2
    assert abs(region.top - 150) <= 2
    assert abs(region.width - cell * 8) <= 2
    assert abs(region.height - cell * 8) <= 2
    frame = screen[region.top:region.top + region.height,
                   region.left:region.left + region.width]
    sensor = BoardSensor(region)
    sensor.calibrate(frame)
    states, _ = sensor.sense(frame)
    assert states == board_to_sensor_states(board, False)


def test_prefers_main_board_over_thumbnail_on_wide_monitor():
    screen = np.full((1000, 3840, 3), 55, np.uint8)
    screen[100:292, 100:292] = render_board(chess.Board(), "green", cell=24)
    screen[200:712, 2300:2812] = render_board(chess.Board(), "brown")

    region = _detect_board(screen)

    assert region is not None
    assert abs(region.left - 2300) <= 2
    assert abs(region.top - 200) <= 2
    assert abs(region.width - 512) <= 2


@pytest.mark.parametrize("pattern", ["blank", "stripes", "window"])
def test_no_board_returns_none(pattern):
    screen = np.full((900, 1600, 3), 55, np.uint8)
    if pattern == "stripes":
        for row in range(8):
            screen[150 + row * 64:150 + (row + 1) * 64, 250:762] = (
                200 if row % 2 == 0 else 70)
    elif pattern == "window":
        cv2.rectangle(screen, (250, 150), (762, 662), (220, 220, 220), 3)
    assert _detect_board(screen) is None


def test_incomplete_grid_is_rejected():
    image = render_board(chess.Board(), "brown")
    image[:64] = 55
    assert not _looks_like_board(image)


def test_monitor_offset_is_applied(monkeypatch):
    screen = np.full((900, 1600, 3), 55, np.uint8)
    screen[150:662, 250:762] = render_board(chess.Board(), "green")
    monkeypatch.setattr("app.capture.grab_monitor", lambda _: (
        screen, {"left": -1600, "top": 80}))

    region = try_auto_detect()

    assert region is not None
    assert abs(region.left - (-1350)) <= 2
    assert abs(region.top - 230) <= 2


def test_capture_failure_returns_none(monkeypatch):
    def fail(_):
        raise OSError("display unavailable")
    monkeypatch.setattr("app.capture.grab_monitor", fail)
    assert try_auto_detect() is None


def test_passive_detection_excludes_assistants_larger_mini_board():
    screen = np.full((900, 1600, 3), 55, np.uint8)
    screen[150:342, 250:442] = render_board(chess.Board(), "green", cell=24)
    screen[200:712, 900:1412] = render_board(chess.Board(), "brown")

    region = _detect_board(screen, exclude_region=BoardRegion(880, 180, 550, 550))

    assert region is not None
    assert abs(region.left - 250) <= 2
    assert abs(region.top - 150) <= 2
    assert abs(region.width - 192) <= 2


def test_monitor_offset_is_applied_to_passive_detection_exclusion(monkeypatch):
    screen = np.full((900, 1600, 3), 55, np.uint8)
    screen[150:662, 250:762] = render_board(chess.Board(), "green")
    monkeypatch.setattr("app.capture.grab_monitor", lambda _: (
        screen, {"left": -1600, "top": 80}))

    assert try_auto_detect(exclude_region=BoardRegion(-1360, 220, 540, 540)) is None
