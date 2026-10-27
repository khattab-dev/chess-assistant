"""Recovery runs inline, rejects stale boards and resumes automatic analysis."""
import queue
from types import SimpleNamespace
from unittest.mock import Mock

import chess
import numpy as np
import pytest

from app.recognition import BoardRead, PieceRecognizer, StableBoardReads
from app.recognition_ui import PIECE_NAMES
from app.squares import BoardRegion, BoardSensor, chess_to_sensor_index
from app.tracker import PositionTracker, board_to_sensor_states
from test_activity_ui import tk_root, ui
from test_gui_suggestions import app
from test_recognition import POSITIONS, screenshot, symbols_for


@pytest.fixture
def recovery(ui):
    ui.sensor = BoardSensor(BoardRegion(0, 0, 512, 512))
    ui.sensor.calibrate(screenshot())
    ui.tracker = PositionTracker(follow_bottom=True)
    ui.engine_worker = Mock()
    ui.engine_worker.results = queue.Queue()
    ui._recognizer = PieceRecognizer()
    ui._recognition_gate = StableBoardReads()
    ui._tracking_uncertain = True
    ui._board_hidden = False
    ui._paused = False
    ui._board_recovery_pending = False
    ui._board_misses = 0
    ui._suggested_fen = ""
    ui._recognition_pending = (ui.sensor, ui.tracker)
    read = ui._recognizer.read(screenshot())
    ui._ev_recognition(ui.sensor, ui.tracker, read)
    yield ui
    ui._close_recognition()


def test_review_uses_existing_panel_and_never_restores_or_opens_a_dialog(recovery):
    view = recovery._recognition_review
    assert view is not None
    assert recovery._paused and recovery._tracking_uncertain
    assert recovery.root.state() == "withdrawn"
    assert not [child for child in recovery.root.winfo_children() if child.winfo_class() == "Toplevel"]
    assert view.turn.get() == ""
    assert not any(var.get() for var in view.castling.values())
    assert len(recovery.board_canvas.find_withtag("piece")) == 20
    recovery._maybe_suggest(recovery.tracker, recovery.tracker.fen)
    recovery.engine_worker.submit.assert_not_called()
    recovery.root.update_idletasks()
    assert recovery.root.winfo_reqwidth() < 900
    assert recovery.root.winfo_reqheight() < 850


def test_turn_is_required_and_invalid_metadata_is_logged_inline(recovery):
    view = recovery._recognition_review
    original = recovery.tracker
    view._continue()
    assert recovery.tracker is original
    assert "Choose whose turn" in recovery.activity_txt.get("1.0", "end")
    assert recovery._paused
    view.turn.set("White")
    view.castling["K"].set(True)
    view._continue()
    assert recovery.tracker is original
    assert "castling rights" in recovery.activity_txt.get("1.0", "end")


@pytest.mark.parametrize("white_bottom", [True, False])
def test_confirmation_rebuilds_position_and_automatically_suggests_on_bottom_players_turn(recovery, monkeypatch, white_bottom):
    image = screenshot(white_bottom=white_bottom)
    recovery._close_recognition()
    read = recovery._recognizer.read(image)
    recovery._recognition_pending = (recovery.sensor, recovery.tracker)
    recovery._ev_recognition(recovery.sensor, recovery.tracker, read)
    view = recovery._recognition_review
    view.bottom.set("White" if white_bottom else "Black")
    view.turn.set("White" if white_bottom else "Black")
    view.fullmove.set("38")
    monkeypatch.setattr("app.gui.grab_region", lambda _: image)
    old = recovery.tracker

    view._continue()

    assert recovery.tracker is not old
    assert recovery.tracker.board.board_fen() == chess.Board(POSITIONS["chesscom-capture.png"]).board_fen()
    assert recovery.tracker.user_color == white_bottom
    assert not recovery._paused and not recovery._tracking_uncertain
    assert recovery._recognition_review is None
    assert recovery.fen_var.get() == recovery.tracker.fen
    recovery._maybe_suggest(recovery.tracker, recovery.tracker.fen)
    recovery.engine_worker.submit.assert_called_once_with(recovery.tracker.fen, "Balanced")


@pytest.mark.parametrize("hidden", [True, False])
def test_alt_tab_or_a_changed_position_during_review_cannot_commit_stale_state(recovery, monkeypatch, hidden):
    image = np.zeros((512, 512, 3), np.uint8) if hidden else screenshot("chesscom-textured.png")
    monkeypatch.setattr("app.gui.grab_region", lambda _: image)
    old = recovery.tracker
    view = recovery._recognition_review
    view.turn.set("White")
    view._continue()
    assert recovery.tracker is old
    assert recovery._paused and recovery._tracking_uncertain
    assert "Read again" in recovery.activity_txt.get("1.0", "end")
    recovery.engine_worker.submit.assert_not_called()


def test_user_can_correct_a_low_confidence_piece_before_confirming(recovery, monkeypatch):
    image = screenshot("chesscom-textured.png", 512)
    recovery._close_recognition()
    read = recovery._recognizer.read(image)
    recovery._recognition_pending = (recovery.sensor, recovery.tracker)
    recovery._ev_recognition(recovery.sensor, recovery.tracker, read)
    view = recovery._recognition_review
    index = chess_to_sensor_index(chess.A1, True)
    assert read.symbols[index] == "Q"  # Real ambiguous rook in the busy theme.
    assert index in read.uncertain
    view._select_square(SimpleNamespace(x=17, y=7 * 34 + 17))
    view.piece.set(PIECE_NAMES["R"])
    view._edit_piece()
    assert view.symbols[index] == "R"
    assert index in view.corrected
    view.turn.set("White")
    for value in view.castling.values():
        value.set(True)
    view.fullmove.set("3")
    monkeypatch.setattr("app.gui.grab_region", lambda _: image)

    view._continue()

    assert recovery._recognition_review is None
    assert recovery.tracker.fen == POSITIONS["chesscom-textured.png"]


def test_cancel_suppresses_the_same_position_and_retry_is_explicit(recovery):
    read = recovery._recognition_review.read
    recovery._cancel_recognition()
    assert not recovery._paused
    assert recovery._tracking_uncertain
    assert recovery._recognition_gate.suppressed == read.symbols
    for now in (0, 0.5, 1, 2):
        assert not recovery._recognition_gate.offer(read, now)
    recovery._retry_recognition()
    assert recovery._recognition_gate.suppressed is None


def test_stale_queued_reads_are_ignored(app):
    app._recognition_pending = (app.sensor, app.tracker)
    app._tracking_uncertain = True
    app._ev_recognition(Mock(), app.tracker, BoardRead(tuple("." * 64), (1.0,) * 64))
    assert not app._paused
    assert not getattr(app, "_recognition_review", None)


def test_recovery_is_offered_only_after_multiple_consistent_reads(app):
    app._tracking_uncertain = True
    app._recognizer = PieceRecognizer()
    image = screenshot()
    for now in (0, 0.4):
        app._consider_recognition(app.sensor, app.tracker, image, now)
    assert app._events.empty()
    app._consider_recognition(app.sensor, app.tracker, image, 0.8)
    event = app._events.get_nowait()
    assert event[:3] == ("recognition", app.sensor, app.tracker)
    app._consider_recognition(app.sensor, app.tracker, image, 1.2)
    assert app._events.empty()


def test_broken_model_logs_once_and_keeps_import_available(app):
    app._recognizer = Mock()
    app._recognizer.read.side_effect = OSError("model missing")
    for now in (0, 0.5, 1):
        app._consider_recognition(app.sensor, app.tracker, screenshot(), now)
    event = app._events.get_nowait()
    assert event[0] == "status" and event[2] == "red"
    assert "Import game" in event[1]
    assert app._events.empty()


def test_capture_to_review_to_confirm_resumes_without_a_fen(recovery, monkeypatch):
    recovery._close_recognition()
    recovery.tracker = PositionTracker(follow_bottom=True)
    recovery._tracking_uncertain = False
    recovery._running = True
    recovery._next_board_check = 0.0
    recovery._recognition_source = None
    recovery._next_recognition = 0.0
    recovery._next_recovery_scan = float("inf")
    recovery._panel_region = None
    recovery._desync_count = 0
    image = screenshot()
    monkeypatch.setattr("app.gui.grab_region", lambda _: image)
    now = [0.0]
    events = []
    monkeypatch.setattr("app.gui.time.monotonic", lambda: now[0])

    def tick(_):
        now[0] += 0.1
        if now[0] > 4:
            recovery._running = False

    def post(event):
        events.append(event)
        if event[0] == "recognition":
            recovery._running = False

    monkeypatch.setattr("app.gui.time.sleep", tick)
    monkeypatch.setattr(recovery, "_post", post)
    recovery._capture_loop()
    reads = [event for event in events if event[0] == "recognition"]
    assert len(reads) == 1
    assert recovery.tracker.board is None  # No guessed turn or rights.
    recovery._ev_recognition(*reads[0][1:])
    assert recovery._paused
    recovery._recognition_review.turn.set("White")
    recovery._recognition_review._continue()
    assert recovery.tracker.board.board_fen() == chess.Board(POSITIONS["chesscom-capture.png"]).board_fen()
    assert recovery._recognition_review is None
    assert not recovery._tracking_uncertain
    recovery._maybe_suggest(recovery.tracker, recovery.tracker.fen)
    recovery.engine_worker.submit.assert_called_once()


def test_a_confirmed_checkmate_is_green_inline_and_never_an_engine_request(recovery, monkeypatch):
    board = chess.Board("7k/6Q1/6K1/8/8/8/8/8 b - - 0 45")
    assert board.is_checkmate()
    read = BoardRead(tuple(symbols_for(board)), (1.0,) * 64)
    recovery._recognition_review.read = read
    recovery._recognizer = Mock()
    recovery._recognizer.read.return_value = read
    recovery.sensor.calibrate = Mock()
    recovery.sensor.sense = Mock(return_value=(board_to_sensor_states(board, True), []))
    monkeypatch.setattr("app.gui.grab_region", lambda _: screenshot())
    recovery._confirm_recognition(board, True)
    assert recovery.tracker.fen == board.fen()
    assert recovery._gameover_shown
    assert "SUCCESS: Checkmate — White wins" in recovery.activity_txt.get("1.0", "end")
    assert recovery.root.state() == "withdrawn"
    recovery._maybe_suggest(recovery.tracker, recovery.tracker.fen)
    recovery.engine_worker.submit.assert_not_called()


def test_confirming_opponents_turn_does_not_suggest(recovery, monkeypatch):
    view = recovery._recognition_review
    view.turn.set("Black")
    monkeypatch.setattr("app.gui.grab_region", lambda _: screenshot())
    view._continue()
    assert recovery.tracker.board is not None
    assert recovery.tracker.user_color == chess.WHITE
    assert recovery.tracker.board.turn == chess.BLACK
    recovery._maybe_suggest(recovery.tracker, recovery.tracker.fen)
    recovery.engine_worker.submit.assert_not_called()
