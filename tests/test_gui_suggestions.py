"""Automatic analysis follows the player's turn and reaches the UI queue."""
import queue
from unittest.mock import Mock

import chess
import pytest

from app.gui import ChessAssistantApp
from app.squares import BoardRegion
from app.tracker import PositionTracker, board_to_sensor_states
from test_squares import render_board


@pytest.fixture
def app():
    instance = ChessAssistantApp.__new__(ChessAssistantApp)
    instance.root = Mock()
    instance.sensor = Mock()
    instance.engine_worker = Mock()
    instance.engine_worker.results = queue.Queue()
    instance.level = Mock()
    instance.level.get.return_value = "Balanced"
    instance._events = queue.Queue()
    instance._running = True
    instance._paused = False
    instance._board_recovery_pending = False
    instance._board_hidden = False
    instance._promotion_tracker = None
    instance._next_recovery_scan = float("inf")
    instance._panel_region = None
    instance._suggested_fen = ""
    instance._tracking_uncertain = False
    instance._last_states = None
    instance.user_color = chess.WHITE
    instance.tracker = PositionTracker()
    instance.tracker.start_from_sensed(
        board_to_sensor_states(chess.Board(), True), chess.WHITE)
    for name in ("suggest_lbl", "eval_lbl", "fen_var", "_draw_board",
                 "_draw_eval_bar", "_set_status", "_save_config", "_clear_promotion", "_log_event",
                 "_place_panel_away_from_board", "region_lbl"):
        setattr(instance, name, Mock())
    return instance


def post_position(app):
    tr = app.tracker
    app._post(("position", list(tr.occ), tr.white_bottom, tr.fen,
               list(tr.san_history)))


@pytest.mark.parametrize("color", [chess.WHITE, chess.BLACK])
def test_automatically_submits_once_per_player_turn(app, color):
    app.user_color = app.tracker.user_color = color
    board = chess.Board()
    expected = []
    if board.turn == color:
        expected.append(board.fen())
    app._poll()
    for san in ["e4", "e5", "Nf3", "Nc6"]:
        board.push_san(san)
        assert app.tracker.update(board_to_sensor_states(board, True)).status == "moved"
        post_position(app)
        if board.turn == color:
            expected.append(board.fen())
        app._poll()
        app._poll()  # unchanged frames must not keep restarting analysis

    assert [call.args for call in app.engine_worker.submit.call_args_list] == [
        (fen, "Balanced") for fen in expected]


def test_engine_results_reach_move_label_and_arrow(app):
    app._poll()
    app.engine_worker.results.put(("engine_ready", None))
    app.engine_worker.results.put(("eval", {
        "fen": app.tracker.fen, "san": "e4", "uci": "e2e4",
        "eval": "+0.25", "pct": 54.0,
    }))

    app._poll()

    app.suggest_lbl.config.assert_called_with(text="Play  e4")
    app._draw_board.assert_called_with(app.tracker.board, True, "e2e4")
    app.engine_worker.submit.assert_called_once()


def test_played_move_clears_suggestion_and_ignores_late_analysis(app):
    old_fen = app.tracker.fen
    app._poll()
    board = chess.Board()
    board.push_san("e4")
    app.tracker.update(board_to_sensor_states(board, True))
    post_position(app)
    app.engine_worker.results.put(("eval", {
        "fen": old_fen, "san": "e4", "uci": "e2e4", "eval": "+0.25", "pct": 54,
    }))

    app._poll()

    app.suggest_lbl.config.assert_called_with(text="")
    app._draw_board.assert_called_with(app.tracker.board, True, None)
    app.engine_worker.submit.assert_called_once()


def test_stale_position_event_cannot_erase_current_suggestion(app):
    old_fen = app.tracker.fen
    board = chess.Board()
    board.push_san("e4")
    app.tracker.update(board_to_sensor_states(board, True))
    app._post(("position", [], True, old_fen, []))

    app._poll()

    app.suggest_lbl.config.assert_not_called()
    app._draw_board.assert_not_called()


@pytest.mark.parametrize("blocked", ["paused", "recovering", "no_board", "game_over"])
def test_automatic_analysis_waits_for_valid_active_game(app, blocked):
    if blocked == "paused":
        app._paused = True
    elif blocked == "recovering":
        app._board_recovery_pending = True
    elif blocked == "no_board":
        app.sensor = None
    else:
        app.tracker.set_fen("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
        app.tracker.user_color = chess.BLACK

    app._poll()

    app.engine_worker.submit.assert_not_called()


@pytest.mark.parametrize("existing_tracker", [False, True])
def test_import_detects_black_at_bottom_and_automatically_analyzes(app, monkeypatch, existing_tracker):
    board = chess.Board()
    board.push_san("e4")
    if existing_tracker:
        app.tracker.user_color = chess.BLACK
        app.user_color = None
    else:
        app.tracker = None
        app.user_color = chess.BLACK
    app._last_states = board_to_sensor_states(board, False)
    monkeypatch.setattr("app.gui.simpledialog.askstring", lambda *a, **k: board.fen())

    app._resync_fen()
    app._poll()

    assert app.tracker.user_color == chess.BLACK
    assert app.user_color == chess.BLACK
    app.engine_worker.submit.assert_called_once_with(board.fen(), "Balanced")


def test_joining_midgame_detects_bottom_color_for_automatic_analysis(app, monkeypatch):
    board = chess.Board()
    for san in ["e4", "e5", "Nf3"]:
        board.push_san(san)
    app._last_states = board_to_sensor_states(board, False)
    monkeypatch.setattr("app.gui.simpledialog.askstring", lambda *a, **k: board.fen())

    app._resync_fen()
    app._poll()

    assert app.tracker.user_color == chess.BLACK
    app.engine_worker.submit.assert_called_once_with(board.fen(), "Balanced")


def test_board_recovery_restarts_automatic_analysis(app, monkeypatch):
    app._poll()
    tracker = app.tracker
    monkeypatch.setattr("app.gui.grab_region",
                        lambda _: render_board(chess.Board(), "green"))

    app._apply_region(BoardRegion(100, 100, 512, 512), preserve_game=True)
    app._poll()

    assert app.tracker is tracker
    assert app.engine_worker.submit.call_count == 2
    app.engine_worker.submit.assert_called_with(tracker.fen, "Balanced")


def test_engine_errors_are_read_even_when_no_capture_events_exist(app):
    app.engine_worker.results.put(("engine_error", "analysis failed"))

    app._poll()

    app._set_status.assert_called_with("Engine: analysis failed", "red")


def test_uncertain_tracking_clears_advice_and_rejects_late_engine_results(app):
    app._tracking_uncertain = True
    app._post(("tracking_lost", app.tracker))
    app.engine_worker.results.put(("eval", {
        "fen": app.tracker.fen, "san": "e4", "uci": "e2e4", "eval": "+0.25", "pct": 54,
    }))

    app._poll()
    assert not hasattr(app, "_suggest_now")

    app.suggest_lbl.config.assert_called_with(text="")
    app._draw_board.assert_called_with(app.tracker.board, True, None)
    app.engine_worker.submit.assert_not_called()


def test_return_to_known_position_resumes_automatic_suggestions(app, monkeypatch):
    app._tracking_uncertain = True
    app._suggested_fen = app.tracker.fen
    app._next_board_check = float("inf")
    app._board_misses = 0
    app._desync_count = 3
    now = [0.0]
    states = board_to_sensor_states(app.tracker.board, True)
    app.sensor.sense.return_value = (states, [])
    monkeypatch.setattr("app.gui.grab_region", lambda _: object())
    monkeypatch.setattr("app.gui.time.monotonic", lambda: now[0])

    def tick(_):
        now[0] += 0.1
        if now[0] > 0.7:
            app._running = False

    monkeypatch.setattr("app.gui.time.sleep", tick)

    app._capture_loop()
    assert not app._tracking_uncertain
    assert app._desync_count == 0
    app._running = True
    app._poll()

    app.engine_worker.submit.assert_called_once_with(app.tracker.fen, "Balanced")
    app._set_status.assert_called_with("Board matches again. Tracking resumed.", "green")


@pytest.mark.parametrize("white_bottom", [True, False])
def test_bot_opening_is_detected_and_suggested_without_fen(app, monkeypatch, white_bottom):
    board = chess.Board()
    board.push_san("e4")
    states = board_to_sensor_states(board, white_bottom)
    app.sensor.sense.return_value = (states, [])
    monkeypatch.setattr("app.gui.grab_region", lambda _: object())
    prompt = Mock(side_effect=AssertionError("Opening must not request a FEN"))
    monkeypatch.setattr("app.gui.simpledialog.askstring", prompt)

    app.tracker = PositionTracker(follow_bottom=True)
    for now in (0, 0.1, 0.2):
        result = app.tracker.update_stable(states, now=now)
    assert result.status == "started"
    app._post(("tracking_started", app.tracker))
    post_position(app)
    app._poll()

    prompt.assert_not_called()
    assert app.tracker.white_bottom == white_bottom
    assert app.tracker.san_history == ["e4"]
    assert app.tracker.board.turn == chess.BLACK
    assert app.user_color == white_bottom
    if white_bottom == chess.BLACK:
        app.engine_worker.submit.assert_called_once_with(board.fen(), "Balanced")
    else:
        app.engine_worker.submit.assert_not_called()


def test_later_game_can_start_from_move_list_instead_of_fen(app, monkeypatch):
    board = chess.Board()
    for san in ["e4", "e5", "Nf3", "Nc6", "Bb5"]:
        board.push_san(san)
    app._last_states = board_to_sensor_states(board, False)
    monkeypatch.setattr("app.gui.simpledialog.askstring",
                        lambda *a, **k: "1. e4 e5 2. Nf3 Nc6 3. Bb5")

    app._resync_fen()
    app._poll()

    assert app.tracker.fen == board.fen()
    assert not app.tracker.white_bottom
    assert app.tracker.san_history == ["e4", "e5", "Nf3", "Nc6", "Bb5"]
    app.engine_worker.submit.assert_called_once_with(board.fen(), "Balanced")


def test_resync_accepts_full_move_list_without_fen(app, monkeypatch):
    board = chess.Board()
    for san in ["d4", "d5", "c4"]:
        board.push_san(san)
    app.user_color = app.tracker.user_color = chess.BLACK
    app._last_states = board_to_sensor_states(board, False)
    monkeypatch.setattr("app.gui.simpledialog.askstring",
                        lambda *a, **k: "1. d4 d5 2. c4")

    app._resync_fen()
    app._poll()

    assert app.tracker.fen == board.fen()
    assert app.tracker.user_color == chess.BLACK
    app.engine_worker.submit.assert_called_once_with(board.fen(), "Balanced")
