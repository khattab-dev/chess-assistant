"""Exercise automatic detection, fallback and recovery without Tk windows."""
import json
from unittest.mock import Mock

import chess
import numpy as np
import pytest

from app.gui import ChessAssistantApp
from app.squares import BoardRegion
from app.tracker import PositionTracker, board_to_sensor_states
from test_squares import render_board


@pytest.fixture
def app():
    instance = ChessAssistantApp.__new__(ChessAssistantApp)
    instance.root = Mock()
    instance.sensor = None
    instance.tracker = None
    instance.engine_worker = None
    instance._paused = False
    instance._suggested_fen = ""
    instance._tracking_uncertain = False
    instance._board_recovery_pending = False
    instance._board_hidden = False
    instance._next_recovery_scan = float("inf")
    instance._panel_region = None
    instance._clear_promotion = Mock()
    instance._log_event = Mock()
    instance._set_status = Mock()
    instance._draw_board = Mock()
    instance._draw_eval_bar = Mock()
    instance._place_panel_away_from_board = Mock()
    instance._save_config = Mock()
    instance._post = Mock()
    instance.region_lbl = Mock()
    instance.suggest_lbl = Mock()
    instance.eval_lbl = Mock()
    instance.fen_var = Mock()
    instance.level = Mock()
    return instance


def test_startup_schedules_detection_and_does_not_restore_saved_bounds(monkeypatch):
    monkeypatch.setattr(ChessAssistantApp, "_build_ui", Mock())
    monkeypatch.setattr(ChessAssistantApp, "_load_config", Mock())
    monkeypatch.setattr(ChessAssistantApp, "_set_status", Mock())
    monkeypatch.setattr("app.gui.threading.Thread", Mock())
    root = Mock()

    instance = ChessAssistantApp(root, engine_path=None)

    assert any(call.args == (200, instance._on_auto_detect)
               for call in root.after.call_args_list)


def test_saved_region_is_not_calibrated_at_startup(app, monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"strength": "Max", "region": {
        "left": 20, "top": 30, "width": 500, "height": 500}}))
    monkeypatch.setattr("app.gui.CONFIG_PATH", str(config))
    app._apply_region = Mock()

    app._load_config()

    app.level.set.assert_called_once_with("Max")
    app._apply_region.assert_not_called()


def test_success_hides_assistant_until_board_is_calibrated(app, monkeypatch):
    region = BoardRegion(100, 150, 512, 512)
    hidden = False

    def hide():
        nonlocal hidden
        hidden = True

    def show():
        nonlocal hidden
        hidden = False

    def detect():
        assert hidden and app._paused
        return region

    def calibrate(selected, **kwargs):
        assert selected == region and hidden and app._paused

    app.root.withdraw.side_effect = hide
    app.root.deiconify.side_effect = show
    app._apply_region = Mock(side_effect=calibrate)
    monkeypatch.setattr("app.gui.try_auto_detect", detect)
    prompt = Mock()
    monkeypatch.setattr("tkinter.messagebox.askokcancel", prompt)

    assert app._on_auto_detect()
    assert not hidden and not app._paused
    prompt.assert_not_called()


@pytest.mark.parametrize("failure", ["missing", "capture", "calibration"])
def test_detection_failure_logs_without_opening_manual_selection(app, monkeypatch, failure):
    detector = Mock(return_value=None)
    if failure == "capture":
        detector.side_effect = OSError("capture failed")
    elif failure == "calibration":
        detector.return_value = BoardRegion(100, 100, 512, 512)
        app._apply_region = Mock(side_effect=ValueError("invalid grid"))
    monkeypatch.setattr("app.gui.try_auto_detect", detector)
    prompt = Mock(return_value=True)
    monkeypatch.setattr("tkinter.messagebox.askokcancel", prompt)
    app._on_select_region = Mock(return_value=True)

    assert not app._on_auto_detect()
    prompt.assert_not_called()
    app._on_select_region.assert_not_called()
    assert app._board_hidden
    app._set_status.assert_called_with(
        "Board not visible. Waiting for it to return. You can also use 'Select board region'.",
        "orange")
    assert not app._paused


def test_failed_detection_keeps_previous_region_for_passive_recovery(app, monkeypatch):
    app.sensor = sensor = Mock()
    monkeypatch.setattr("app.gui.try_auto_detect", lambda: None)
    app._on_select_region = Mock()

    assert not app._on_auto_detect()
    assert app.sensor is sensor
    assert not app._paused
    app._on_select_region.assert_not_called()


def test_manual_selection_calibrates_and_keeps_game(app, monkeypatch):
    region = BoardRegion(100, 150, 512, 512)
    tracker = PositionTracker()
    tracker.start_from_sensed(board_to_sensor_states(chess.Board(), True), chess.WHITE)
    app.tracker = tracker
    monkeypatch.setattr("app.gui.select_region_interactive", lambda _: region)
    monkeypatch.setattr("app.gui.grab_region", lambda _: render_board(chess.Board(), "green"))

    assert app._on_select_region()
    assert app.tracker is tracker
    assert app.sensor.region == region
    assert not app._paused
    app.root.deiconify.assert_called_once()


def test_blank_manual_selection_is_not_accepted(app, monkeypatch):
    monkeypatch.setattr("app.gui.select_region_interactive",
                        lambda _: BoardRegion(0, 0, 512, 512))
    monkeypatch.setattr("app.gui.grab_region",
                        lambda _: np.zeros((512, 512, 3), np.uint8))
    error = Mock()
    monkeypatch.setattr("tkinter.messagebox.showerror", error)

    assert not app._on_select_region()
    assert app.sensor is None
    assert not app._paused
    error.assert_not_called()
    assert app._set_status.call_args.args[1] == "red"
    assert "Calibration failed" in app._set_status.call_args.args[0]


def test_color_selection_detects_board_before_starting(app):
    app._on_auto_detect = Mock(return_value=False)
    app._on_pick_color(chess.WHITE)
    app._on_auto_detect.assert_called_once()
    assert app.tracker is None


def test_new_game_detects_board_again(app):
    app.tracker = Mock()
    app._on_auto_detect = Mock()
    app._on_new_game()
    app._on_auto_detect.assert_called_once()
    assert app.tracker is None


def test_lost_board_is_reported_without_resetting_game_or_focusing_panel(app):
    sensor = Mock()
    app.sensor = sensor
    app._board_recovery_pending = True
    app._on_auto_detect = Mock()

    app._ev_board_lost(sensor)

    app._on_auto_detect.assert_not_called()
    app.root.withdraw.assert_not_called()
    app.root.deiconify.assert_not_called()
    assert app.sensor is sensor
    assert app._board_hidden
    assert app._tracking_uncertain
    assert not app._board_recovery_pending


def test_stale_board_lost_event_does_not_redetect_new_selection(app):
    app.sensor = Mock()
    app._board_recovery_pending = True
    app._on_auto_detect = Mock()

    app._ev_board_lost(Mock())

    app._on_auto_detect.assert_not_called()
    assert not app._board_recovery_pending


@pytest.mark.parametrize("unavailable", ["blank", "capture_error"])
def test_capture_loop_recovers_after_persistent_board_loss(app, monkeypatch, unavailable):
    app.sensor = Mock()
    sensor = app.sensor
    app._running = True
    app._next_board_check = 0
    app._board_misses = 0
    posted = []
    ticks = iter(range(20))
    monkeypatch.setattr("app.gui.time.sleep", lambda _: None)
    monkeypatch.setattr("app.gui.time.monotonic", lambda: next(ticks))
    if unavailable == "capture_error":
        def fail(_):
            raise OSError("capture failed")
        monkeypatch.setattr("app.gui.grab_region", fail)
    else:
        monkeypatch.setattr("app.gui.grab_region",
                            lambda _: np.zeros((512, 512, 3), np.uint8))

    def post(event):
        posted.append(event)
        app._running = False

    app._post = post
    app._capture_loop()

    assert app._board_misses == 3
    assert app._board_hidden
    assert posted == [("board_lost", sensor)]
    assert app._board_recovery_pending


def test_brief_board_occlusion_does_not_prompt(app, monkeypatch):
    app.sensor = Mock()
    app._running = True
    app._next_board_check = 0
    app._board_misses = 0
    ticks = iter(range(20))
    frames = iter([np.zeros((512, 512, 3), np.uint8),
                   render_board(chess.Board(), "green")])
    monkeypatch.setattr("app.gui.time.sleep", lambda _: None)
    monkeypatch.setattr("app.gui.time.monotonic", lambda: next(ticks))

    def grab(_):
        frame = next(frames)
        if frame.max():
            app._running = False
        return frame

    monkeypatch.setattr("app.gui.grab_region", grab)
    app._capture_loop()

    assert app._board_misses == 0
    app._post.assert_not_called()


@pytest.mark.parametrize("panel_x, expected", [(0, "+1058+0"), (1100, None)])
def test_panel_is_moved_only_when_it_covers_the_board(app, panel_x, expected):
    for name, value in {"x": panel_x, "y": 0, "width": 648, "height": 484,
                        "reqwidth": 648, "reqheight": 484,
                        "screenwidth": 1920, "screenheight": 1080}.items():
        getattr(app.root, "winfo_" + name).return_value = value

    ChessAssistantApp._place_panel_away_from_board(
        app, BoardRegion(236, 151, 810, 811))

    if expected:
        app.root.geometry.assert_called_once_with(expected)
    else:
        app.root.geometry.assert_not_called()


def test_passive_scan_excludes_control_panel_and_retries_at_bounded_rate(app, monkeypatch):
    app.sensor = old_sensor = Mock()
    app._panel_region = BoardRegion(800, 100, 600, 550)
    app._next_recovery_scan = 0
    region = BoardRegion(100, 150, 512, 512)
    detect = Mock(return_value=region)
    monkeypatch.setattr("app.gui.try_auto_detect", detect)

    app._scan_for_board(old_sensor, 1.0)
    app._scan_for_board(old_sensor, 1.5)

    detect.assert_called_once_with(exclude_region=app._panel_region)
    app._post.assert_called_once_with(("board_found", old_sensor, region))
    app.root.withdraw.assert_not_called()
    app.root.deiconify.assert_not_called()
    app.root.focus_force.assert_not_called()


def test_passive_board_found_preserves_game_without_showing_window(app, monkeypatch):
    tracker = PositionTracker()
    board = chess.Board()
    tracker.start_from_sensed(board_to_sensor_states(board, True), chess.WHITE)
    app.tracker = tracker
    app.sensor = old_sensor = Mock()
    app._board_hidden = True
    app._tracking_uncertain = True
    app._board_recovery_pending = True
    region = BoardRegion(100, 150, 512, 512)
    monkeypatch.setattr("app.gui.grab_region", lambda _: render_board(board, "green"))

    app._ev_board_found(old_sensor, region)

    assert app.tracker is tracker
    assert app.sensor.region == region
    assert not app._board_hidden
    assert not app._board_recovery_pending
    assert app._tracking_uncertain  # still wait for settled matching frames
    app.root.withdraw.assert_not_called()
    app.root.deiconify.assert_not_called()


@pytest.mark.parametrize("returns", [False, True])
def test_alt_tab_emits_one_warning_and_resumes_without_dialogs(app, monkeypatch, returns):
    from app.squares import BoardSensor
    board = chess.Board()
    frame = render_board(board, "green")
    sensor = BoardSensor(BoardRegion(100, 150, 512, 512))
    sensor.calibrate(frame)
    tracker = PositionTracker()
    tracker.start_from_sensed(board_to_sensor_states(board, True), chess.WHITE)
    app.sensor, app.tracker = sensor, tracker
    app._running = True
    app._next_board_check = 0
    app._board_misses = 0
    app._desync_count = 0
    now = [0.0]
    events = []
    monkeypatch.setattr("app.gui.time.monotonic", lambda: now[0])
    monkeypatch.setattr("app.gui.grab_region", lambda _: (
        frame if returns and now[0] >= 3.5 else np.zeros_like(frame)))

    def tick(_):
        now[0] += 0.1
        if now[0] > 6:
            app._running = False

    def post(event):
        events.append(event)
        if event[0] in ("board_lost", "board_visible"):
            getattr(app, "_ev_" + event[0])(*event[1:])

    monkeypatch.setattr("app.gui.time.sleep", tick)
    app._post = post

    app._capture_loop()

    assert sum(event[0] == "board_lost" for event in events) == 1
    assert app.tracker is tracker
    assert tracker.fen == board.fen()
    assert app._board_hidden == (not returns)
    assert app._tracking_uncertain == (not returns)
    if returns:
        assert ("status", "Board matches again. Tracking resumed.", "green") in events
    app.root.withdraw.assert_not_called()
    app.root.deiconify.assert_not_called()
    app.root.focus_force.assert_not_called()
