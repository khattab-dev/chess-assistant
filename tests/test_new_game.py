"""Recognize a stable fresh game without clearing ordinary move history."""
from unittest.mock import Mock

import chess
import pytest

from app.tracker import NEW_GAME_SETTLE_TIME, PositionTracker, board_to_sensor_states
from app.squares import BoardRegion
from test_gui_suggestions import app  # shared headless GUI fixture


def old_game(color=chess.WHITE, white_bottom=True, finished=False):
    tracker = PositionTracker()
    moves = "1. f3 e5 2. g4 Qh4#" if finished else "1. e4 d5 2. exd5 Qxd5 3. Nc3 Qa5"
    assert tracker.set_moves(moves) is None
    tracker.user_color = color
    tracker.white_bottom = white_bottom
    return tracker


def confirm(tracker, states):
    results = [tracker.update_stable(states, now=now)
               for now in (0, 0.1, 0.2, NEW_GAME_SETTLE_TIME + 0.01)]
    return results


@pytest.mark.parametrize("color", [chess.WHITE, chess.BLACK])
@pytest.mark.parametrize("white_bottom", [True, False])
@pytest.mark.parametrize("finished", [False, True])
def test_stable_initial_board_starts_new_game_and_keeps_color(color, white_bottom, finished):
    tracker = old_game(color, not white_bottom, finished)
    initial = chess.Board()

    results = confirm(tracker, board_to_sensor_states(initial, white_bottom))

    assert results[2].status == "newgame_pending"
    assert results[-1].status == "newgame"
    assert tracker.fen == initial.fen()
    assert tracker.white_bottom == white_bottom
    assert tracker.user_color == color
    assert tracker.san_history == []
    assert tracker.board.move_stack == []
    assert tracker.start_fullmove == 1
    assert tracker.start_turn == chess.WHITE


@pytest.mark.parametrize("moves", [["e4"], ["Nf3"], ["d4", "Nf6"]])
@pytest.mark.parametrize("white_bottom", [True, False])
def test_new_game_is_recognized_if_opening_moves_already_happened(moves, white_bottom):
    tracker = old_game(chess.BLACK)
    opening = chess.Board()
    for san in moves:
        opening.push_san(san)

    results = confirm(tracker, board_to_sensor_states(opening, white_bottom))

    assert results[-1].status == "newgame"
    assert tracker.fen == opening.fen()
    assert tracker.san_history == moves
    assert tracker.user_color == chess.BLACK
    assert tracker.white_bottom == white_bottom
    assert tracker.board.castling_rights == opening.castling_rights
    assert tracker.board.ep_square == opening.ep_square


def test_brief_reset_image_does_not_clear_the_active_game():
    tracker = old_game()
    before = tracker.fen
    history = list(tracker.san_history)
    initial_states = board_to_sensor_states(chess.Board(), True)
    for now in (0, 0.1, 0.2):
        tracker.update_stable(initial_states, now=now)
    assert tracker.fen == before
    current_states = board_to_sensor_states(tracker.board, True)
    for now in (0.3, 0.4, 0.5, 1.2):
        result = tracker.update_stable(current_states, now=now)
    assert result.status == "none"
    assert tracker.fen == before
    assert tracker.san_history == history


def test_reset_is_reported_once_while_board_remains_unchanged():
    tracker = old_game()
    states = board_to_sensor_states(chess.Board(), True)
    assert confirm(tracker, states)[-1].status == "newgame"
    for now in (1.0, 1.1, 1.2, 1.5, 2.0):
        assert tracker.update_stable(states, now=now).status == "none"


def test_legal_knight_cycle_back_to_start_is_not_a_new_game():
    tracker = PositionTracker()
    board = chess.Board()
    tracker.start_from_sensed(board_to_sensor_states(board, True), chess.WHITE)
    for i, san in enumerate(["Nf3", "Nf6", "Ng1", "Ng8"]):
        board.push_san(san)
        states = board_to_sensor_states(board, True)
        for offset in (0, 0.1, 0.2):
            result = tracker.update_stable(states, now=i * 2 + offset)
        assert result.status == "moved"
        assert tracker.update_stable(states, now=i * 2 + 1).status == "none"
    assert tracker.fen == board.fen()
    assert tracker.board.fullmove_number == 3
    assert tracker.san_history == ["Nf3", "Nf6", "Ng1", "Ng8"]


def test_flipping_current_board_preserves_game_history_and_color():
    tracker = old_game(chess.BLACK, white_bottom=True)
    before = tracker.fen
    history = list(tracker.san_history)
    states = board_to_sensor_states(tracker.board, False)
    for now in (0, 0.1, 0.2):
        result = tracker.update_stable(states, now=now)
    assert result.status == "resynced"
    assert tracker.fen == before
    assert tracker.san_history == history
    assert tracker.user_color == chess.BLACK
    assert not tracker.white_bottom


def test_symmetric_occupancy_does_not_repeatedly_flip_orientation():
    tracker = PositionTracker()
    tracker.set_fen("k7/8/8/4N3/3K4/8/8/7n w - - 0 30")
    states = board_to_sensor_states(tracker.board, True)
    assert states == board_to_sensor_states(tracker.board, False)
    for now in (0, 0.1, 0.2, 0.7, 1.0):
        assert tracker.update_stable(states, now=now).status == "none"
    assert tracker.white_bottom


def test_unrecognized_position_with_32_pieces_is_not_used_as_new_baseline():
    tracker = old_game()
    before = tracker.fen
    arbitrary = chess.Board()
    for san in ["e4", "e5", "Nf3"]:
        arbitrary.push_san(san)
    assert len(arbitrary.piece_map()) == 32
    states = board_to_sensor_states(arbitrary, True)
    for now in (0, 0.1, 0.2, 0.7, 1.0):
        result = tracker.update_stable(states, now=now)
    assert result.status == "desync"
    assert tracker.fen == before


@pytest.mark.parametrize("color,opening,promotion", [
    (chess.WHITE, [], False), (chess.BLACK, ["e4"], False), (chess.WHITE, [], True)])
def test_capture_detects_new_game_and_restarts_auto_suggestions_without_dialogs(
        app, monkeypatch, color, opening, promotion):
    app.tracker = tracker = old_game(color, finished=True)
    if promotion:
        tracker.set_fen("k7/7P/8/8/8/8/8/7K w - - 0 1")
        app._paused = True
        app._promotion_tracker = tracker
    app._gameover_shown = True
    app._desync_count = 5
    old_fen = tracker.fen
    app._suggested_fen = old_fen
    app.user_color = color
    app._next_board_check = float("inf")
    app._board_misses = 0
    board = chess.Board()
    for san in opening:
        board.push_san(san)
    states = board_to_sensor_states(board, False)
    app.sensor.sense.return_value = (states, [])
    app._clear_promotion.side_effect = lambda: setattr(app, "_promotion_tracker", None)
    now = [0.0]
    monkeypatch.setattr("app.gui.grab_region", lambda _: object())
    monkeypatch.setattr("app.gui.time.monotonic", lambda: now[0])

    def tick(_):
        now[0] += 0.1
        if now[0] > 1.6:
            app._running = False

    monkeypatch.setattr("app.gui.time.sleep", tick)
    app.engine_worker.results.put(("eval", {
        "fen": old_fen, "san": "old", "uci": "h7h8q", "eval": "old", "pct": 0,
    }))
    app._on_auto_detect = Mock()

    app._capture_loop()
    app._running = True
    app._poll()
    app._poll()

    assert app.tracker is tracker
    assert tracker.fen == board.fen()
    assert tracker.san_history == opening
    assert app.user_color == color
    assert not app._gameover_shown
    assert not app._paused
    assert not app._tracking_uncertain
    assert not app._desync_count
    app.engine_worker.submit.assert_called_once_with(board.fen(), "Balanced")
    assert app._set_status.call_args.args[0].startswith("New game detected.")
    assert app._set_status.call_args.args[1] == "green"
    app._on_auto_detect.assert_not_called()
    app.root.withdraw.assert_not_called()
    app.root.deiconify.assert_not_called()
    app.root.focus_force.assert_not_called()


def test_stale_new_game_event_does_not_reset_a_different_tracker(app):
    app._gameover_shown = True
    app._ev_new_game(old_game())
    assert app._gameover_shown
    app._set_status.assert_not_called()


@pytest.mark.parametrize("promotion_waiting", [False, True])
def test_passive_scan_can_find_a_reset_board_during_pending_promotion(app, monkeypatch, promotion_waiting):
    app._paused = True
    app._promotion_tracker = app.tracker if promotion_waiting else None
    app._next_recovery_scan = 0
    region = BoardRegion(100, 150, 512, 512)
    monkeypatch.setattr("app.gui.try_auto_detect", lambda **kw: region)
    app._scan_for_board(app.sensor, 1.0)
    assert app._board_recovery_pending == promotion_waiting
    if promotion_waiting:
        assert app._events.get_nowait() == ("board_found", app.sensor, region)
    else:
        assert app._events.empty()
