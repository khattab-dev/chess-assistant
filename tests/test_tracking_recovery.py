"""Regressions for fast captures, animation frames and recent takebacks."""
from pathlib import Path
from unittest.mock import Mock

import chess
import cv2
import pytest

from app.squares import BoardRegion, BoardSensor
from app.tracker import PositionTracker, board_to_sensor_states

BEFORE_CAPTURE = "2rnn2k/b1q2pp1/3p3p/3Pp3/1PN1P3/2BB3P/R3Q1PK/8 w - - 1 37"
AFTER_CAPTURE = "2rnn2k/b4pp1/3q3p/3Pp3/1P2P3/2BB3P/R3Q1PK/8 w - - 0 38"


def replay(moves):
    tracker = PositionTracker()
    board = chess.Board()
    tracker.start_from_sensed(board_to_sensor_states(board, True), chess.WHITE)
    for san in moves:
        board.push_san(san)
        assert tracker.update(board_to_sensor_states(board, True)).status == "moved"
    return tracker, board


def settle(tracker, states):
    for now in (0, 0.1, 0.2, 0.7):
        result = tracker.update_stable(states, now=now)
    return result


@pytest.mark.parametrize("white_bottom", [True, False])
def test_real_screenshot_recovers_missed_capture_and_recapture(white_bottom):
    # This fixture contains only the user's board, cropped from the report.
    path = Path(__file__).parent / "fixtures" / "chesscom-capture.png"
    image = cv2.imread(str(path))
    assert image is not None
    if not white_bottom:
        image = cv2.rotate(image, cv2.ROTATE_180)
    sensor = BoardSensor(BoardRegion(0, 0, image.shape[1], image.shape[0]))
    sensor.calibrate(image)
    sensed, _ = sensor.sense(image)
    assert sensed == board_to_sensor_states(chess.Board(AFTER_CAPTURE), white_bottom)

    tracker = PositionTracker()
    tracker.set_fen(BEFORE_CAPTURE)
    tracker.white_bottom = white_bottom
    before = board_to_sensor_states(tracker.board, white_bottom)
    # d6 is black before and after; only c4 and c7 become empty.
    assert all(new == "." for old, new in zip(before, sensed) if old != new)

    for now in (0, 0.1, 0.2):
        result = tracker.update_stable(sensed, now=now)

    assert result.status == "moved"
    assert tracker.san_history == ["Nxd6", "Qxd6"]
    assert tracker.fen == AFTER_CAPTURE


def test_transient_legal_animation_is_not_committed():
    tracker, board = replay(["e4", "e5"])
    animation = board.copy()
    animation.push_san("Nh3")  # a transient blob can resemble another legal move
    board.push_san("Nf3")
    for now in (0, 0.1):
        assert tracker.update_stable(board_to_sensor_states(animation, True), now).status == "none"
    assert tracker.san_history == ["e4", "e5"]
    for now in (0.11, 0.21, 0.31):
        result = tracker.update_stable(board_to_sensor_states(board, True), now)
    assert result.status == "moved"
    assert tracker.san_history == ["e4", "e5", "Nf3"]
    assert tracker.fen == board.fen()


def test_stability_requires_elapsed_time_as_well_as_repeated_frames():
    tracker, board = replay([])
    board.push_san("e4")
    sensed = board_to_sensor_states(board, True)
    for now in (0, 0.01, 0.02):
        assert tracker.update_stable(sensed, now).status == "none"
    assert tracker.update_stable(sensed, 0.16).status == "moved"


def test_repeated_mismatch_does_not_repeat_expensive_two_ply_search():
    tracker, board = replay([])
    board.remove_piece_at(chess.E2)
    board.set_piece_at(chess.C4, chess.Piece(chess.PAWN, chess.WHITE))
    sensed = board_to_sensor_states(board, True)
    tracker._match_pair = Mock(wraps=tracker._match_pair)
    for now in (0, 0.1, 0.2, 0.3, 0.4, 0.7, 0.8):
        result = tracker.update_stable(sensed, now)
    assert result.status == "desync"
    tracker._match_pair.assert_called_once()


def test_recent_takeback_restores_position_turn_and_history():
    tracker, _ = replay(["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"])
    previous = chess.Board()
    for san in ["e4", "e5", "Nf3", "Nc6"]:
        previous.push_san(san)

    result = settle(tracker, board_to_sensor_states(previous, True))

    assert result.status == "resynced"
    assert tracker.fen == previous.fen()
    assert tracker.san_history == ["e4", "e5", "Nf3", "Nc6"]
    assert tracker.user_color == chess.WHITE


def test_wrong_recent_branch_is_replaced_only_by_unique_legal_continuation():
    tracker, _ = replay(["e4", "e5", "Nh3"])
    actual = chess.Board()
    for san in ["e4", "e5", "Nf3"]:
        actual.push_san(san)

    result = settle(tracker, board_to_sensor_states(actual, True))

    assert result.status == "resynced"
    assert tracker.fen == actual.fen()
    assert tracker.san_history == ["e4", "e5", "Nf3"]


def test_ambiguous_historical_positions_are_not_guessed():
    tracker, _ = replay(["e4", "e5", "Nf3", "Nc6", "Ng1", "Nb8",
                         "Nf3", "Nc6", "Bb5", "a6"])
    actual = chess.Board()
    actual.push_san("e4")
    actual.push_san("e5")
    before = tracker.fen

    result = settle(tracker, board_to_sensor_states(actual, True))

    assert result.status == "desync"
    assert tracker.fen == before


def test_missing_history_does_not_allow_guessing_turn_from_screenshot():
    tracker = PositionTracker()
    tracker.set_fen("2rnn2k/b1q2pp1/3N3p/3Pp3/1P2P3/2BB3P/R3Q1PK/8 w - - 1 39")
    before = tracker.fen

    result = settle(tracker, board_to_sensor_states(chess.Board(AFTER_CAPTURE), True))

    assert result.status == "desync"
    assert tracker.fen == before
