"""The app always plays the bottom side, without manual color selection."""
from unittest.mock import Mock

import chess
import pytest

from app.tracker import PositionTracker, board_to_sensor_states
from test_gui_suggestions import app

MOVES = "1. e4 d5 2. exd5 Qxd5 3. Nc3 Qa5"


def stable(tracker, states, times=(0, 0.1, 0.2)):
    for now in times:
        result = tracker.update_stable(states, now=now)
    return result


def capture(app, monkeypatch, states, duration=0.7):
    now = [0.0]
    app._next_board_check = float("inf")
    app._board_misses = 0
    app._desync_count = 0
    app.sensor.sense.return_value = (states, [])
    monkeypatch.setattr("app.gui.grab_region", lambda _: object())
    monkeypatch.setattr("app.gui.time.monotonic", lambda: now[0])

    def tick(_):
        now[0] += 0.1
        if now[0] > duration:
            app._running = False

    monkeypatch.setattr("app.gui.time.sleep", tick)
    app._capture_loop()
    app._running = True
    app._poll()


@pytest.mark.parametrize("white_bottom", [True, False])
@pytest.mark.parametrize("moves", [[], ["e4"], ["Nf3", "d5"]])
def test_stable_opening_starts_and_selects_bottom_color(white_bottom, moves):
    tracker = PositionTracker(follow_bottom=True)
    board = chess.Board()
    for san in moves:
        board.push_san(san)
    states = board_to_sensor_states(board, white_bottom)

    result = stable(tracker, states)

    assert result.status == "started"
    assert tracker.orientation_known
    assert tracker.user_color == white_bottom
    assert tracker.fen == board.fen()
    assert tracker.san_history == moves


@pytest.mark.parametrize("white_bottom", [True, False])
def test_explicit_previous_color_cannot_override_bottom_side(white_bottom):
    tracker = PositionTracker(follow_bottom=True)
    assert tracker.start_from_sensed(
        board_to_sensor_states(chess.Board(), white_bottom), not white_bottom) is None
    assert tracker.user_color == white_bottom


@pytest.mark.parametrize("white_bottom", [True, False])
@pytest.mark.parametrize("source", ["fen", "moves"])
def test_import_uses_bottom_color_instead_of_previous_game_color(white_bottom, source):
    tracker = PositionTracker(follow_bottom=True)
    tracker.start_from_sensed(board_to_sensor_states(chess.Board(), not white_bottom))
    board = chess.Board()
    for san in ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5"]:
        board.push_san(san)
    text = board.fen() if source == "fen" else MOVES

    assert tracker.set_position(text, board_to_sensor_states(board, white_bottom)) is None

    assert tracker.user_color == white_bottom
    assert tracker.orientation_known
    assert tracker.fen == board.fen()


@pytest.mark.parametrize("white_bottom", [True, False])
def test_board_flip_changes_player_color_without_resetting_history(white_bottom):
    tracker = PositionTracker(follow_bottom=True)
    board = chess.Board()
    for san in ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5"]:
        board.push_san(san)
    tracker.set_moves(MOVES, board_to_sensor_states(board, white_bottom))
    history = list(tracker.san_history)

    result = stable(tracker, board_to_sensor_states(board, not white_bottom))

    assert result.status == "resynced"
    assert tracker.user_color == (not white_bottom)
    assert tracker.fen == board.fen()
    assert tracker.san_history == history


@pytest.mark.parametrize("white_bottom", [True, False])
def test_new_game_redetects_color_when_sides_change(white_bottom):
    tracker = PositionTracker(follow_bottom=True)
    previous = chess.Board()
    for san in ["f3", "e5", "g4", "Qh4#"]:
        previous.push_san(san)
    tracker.set_moves("1. f3 e5 2. g4 Qh4#", board_to_sensor_states(previous, not white_bottom))
    opening = chess.Board()
    opening.push_san("e4")

    result = stable(tracker, board_to_sensor_states(opening, white_bottom), (0, 0.1, 0.2, 1))

    assert result.status == "newgame"
    assert tracker.user_color == white_bottom
    assert tracker.san_history == ["e4"]
    assert tracker.fen == opening.fen()


def test_ambiguous_orientation_is_not_guessed_from_an_unseen_position():
    tracker = PositionTracker(follow_bottom=True)
    fen = "k7/8/8/4N3/3K4/8/8/7n w - - 0 30"
    tracker.set_fen(fen)
    states = board_to_sensor_states(tracker.board, True)
    assert states == board_to_sensor_states(tracker.board, False)

    assert stable(tracker, states).status == "orientation_unknown"
    assert tracker.user_color is None
    assert not tracker.orientation_known
    assert tracker.fen == fen


def test_ambiguous_import_preserves_previously_known_black_bottom_orientation():
    tracker = PositionTracker(follow_bottom=True)
    tracker.start_from_sensed(board_to_sensor_states(chess.Board(), False))
    board = chess.Board("k7/8/8/4N3/3K4/8/8/7n w - - 0 30")
    tracker.set_fen(board.fen(), board_to_sensor_states(board, False))
    assert tracker.user_color == chess.BLACK
    assert not tracker.white_bottom


@pytest.mark.parametrize("white_bottom,opening", [(True, []), (False, ["e4"])])
def test_capture_bootstraps_and_suggests_without_clicks_or_dialogs(app, monkeypatch, white_bottom, opening):
    app.tracker = PositionTracker(follow_bottom=True)
    app.user_color = None
    board = chess.Board()
    for san in opening:
        board.push_san(san)
    prompt = Mock(side_effect=AssertionError("Automatic startup must not prompt"))
    monkeypatch.setattr("app.gui.simpledialog.askstring", prompt)

    capture(app, monkeypatch, board_to_sensor_states(board, white_bottom))

    assert app.user_color == white_bottom
    assert app.tracker.user_color == white_bottom
    assert app.tracker.fen == board.fen()
    assert app.tracker.san_history == opening
    app.engine_worker.submit.assert_called_once_with(board.fen(), "Balanced")
    prompt.assert_not_called()
    app.root.deiconify.assert_not_called()


def test_late_result_for_old_color_is_ignored_after_flip(app, monkeypatch):
    app.tracker = PositionTracker(follow_bottom=True)
    app.tracker.start_from_sensed(board_to_sensor_states(chess.Board(), True))
    app._suggested_fen = app.tracker.fen
    app.engine_worker.results.put(("eval", {
        "fen": app.tracker.fen, "san": "e4", "uci": "e2e4", "eval": "+0.25", "pct": 54,
    }))

    capture(app, monkeypatch, board_to_sensor_states(chess.Board(), False))

    assert app.user_color == chess.BLACK
    app.suggest_lbl.config.assert_called_with(text="")
    app.engine_worker.submit.assert_not_called()


def test_unrecognized_midgame_logs_import_option_without_asking_for_color(app, monkeypatch):
    app.tracker = PositionTracker(follow_bottom=True)
    app.user_color = None
    board = chess.Board()
    for san in ["e4", "e5", "Nf3"]:
        board.push_san(san)
    prompt = Mock(side_effect=AssertionError("Startup must not open a dialog"))
    monkeypatch.setattr("app.gui.simpledialog.askstring", prompt)

    capture(app, monkeypatch, board_to_sensor_states(board, False))

    assert app.tracker.board is None
    assert app.user_color is None
    assert app._set_status.call_args.args[1] == "orange"
    assert "Import game" in app._set_status.call_args.args[0]
    app.engine_worker.submit.assert_not_called()
    prompt.assert_not_called()
