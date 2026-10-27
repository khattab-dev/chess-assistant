"""Window identity, physical geometry and capture/overlay follow integration."""
from dataclasses import replace
import sys
from unittest.mock import Mock

import chess
import numpy as np
import pytest

from app.capture import try_auto_detect
from app.overlay import BoardArrowOverlay
from app.squares import BoardRegion, BoardSensor
from app.tracker import board_to_sensor_states
from app.window_follow import WindowsWindowAPI, WindowBinding, WindowSnapshot, intersection
from test_gui_suggestions import app
from test_squares import render_board
from test_activity_ui import tk_root


class FakeWindows:
    def __init__(self):
        self.current = WindowSnapshot(123, 456, BoardRegion(100, 100, 1000, 800))
        self.hit = 123
        self.bounds = BoardRegion(-1920, -200, 5760, 1600)
        self.queries = []

    def snapshot(self, hwnd):
        self.queries.append(hwnd)
        return self.current

    def root_at(self, x, y):
        return self.hit

    def desktop(self):
        return self.bounds


@pytest.fixture
def followed(app):
    windows = FakeWindows()
    region = BoardRegion(180, 200, 512, 512)
    app.sensor = BoardSensor(region)
    app.sensor.calibrate(render_board(chess.Board(), "green"))
    app._window_binding = WindowBinding(windows, windows.current, region)
    app._next_board_check = 0.0
    app._next_recovery_scan = 0.0
    app._board_misses = 0
    app._desync_count = 0
    app._follow_moving = app._follow_waiting = False
    app._follow_geometry = app._follow_pending = None
    app._window_wait_reason = None
    return app, windows


@pytest.mark.parametrize("left,top", [(300, 210), (-1100, -100), (1300, 100)])
def test_translation_uses_client_offsets_on_all_monitors(left, top):
    api = FakeWindows()
    binding = WindowBinding(api, api.current, BoardRegion(180, 200, 512, 512))
    moved = replace(api.current, client=BoardRegion(left, top, 1000, 800))
    assert binding.predicted(moved) == BoardRegion(left + 80, top + 100, 512, 512)


@pytest.mark.parametrize("client,dpi", [(BoardRegion(100, 100, 1200, 900), 96),
                                       (BoardRegion(100, 100, 1000, 800), 144)])
def test_resize_or_dpi_change_requires_detection_instead_of_assumed_scaling(client, dpi):
    api = FakeWindows()
    binding = WindowBinding(api, api.current, BoardRegion(180, 200, 512, 512))
    assert binding.predicted(replace(api.current, client=client, dpi=dpi)) is None


def test_closed_window_or_handle_reused_by_another_process_is_rejected():
    api = FakeWindows()
    binding = WindowBinding(api, api.current, BoardRegion(180, 200, 512, 512))
    api.current = replace(api.current, pid=999)
    assert binding.snapshot() is None
    api.current = None
    assert binding.snapshot() is None


@pytest.mark.parametrize("reason", ["minimized", "hidden", "covered", "partly_offscreen"])
def test_unavailable_or_partial_boards_are_not_usable(reason):
    api = FakeWindows()
    region = BoardRegion(180, 200, 512, 512)
    binding = WindowBinding(api, api.current, region)
    if reason == "minimized":
        api.current = replace(api.current, minimized=True)
    elif reason == "hidden":
        api.current = replace(api.current, visible=False)
    elif reason == "covered":
        api.hit = 789
    else:
        api.bounds = BoardRegion(0, 0, 400, 1080)
    assert not binding.unobstructed(region, api.current)


def test_search_is_clipped_to_visible_virtual_desktop():
    api = FakeWindows()
    binding = WindowBinding(api, api.current, BoardRegion(180, 200, 512, 512))
    api.current = replace(api.current, client=BoardRegion(-2200, -400, 1000, 800))
    assert binding.search_area(api.current) == BoardRegion(-1920, -200, 720, 600)
    assert intersection(BoardRegion(0, 0, 10, 10), BoardRegion(20, 20, 5, 5)) is None


def test_own_assistant_window_cannot_be_bound(monkeypatch):
    api = FakeWindows()
    monkeypatch.setattr("app.window_follow.os.getpid", lambda: 456)
    assert WindowsWindowAPI.bind(api, BoardRegion(180, 200, 512, 512)) is None
    monkeypatch.setattr("app.window_follow.os.getpid", lambda: 999)
    assert WindowsWindowAPI.bind(api, BoardRegion(180, 200, 512, 512)).hwnd == 123


def test_initial_board_selection_binds_the_window_even_when_board_arrow_is_disabled(app, monkeypatch):
    app._window_api = Mock()
    binding = app._window_api.bind.return_value
    app.overlay = None
    region = BoardRegion(180, 200, 512, 512)
    monkeypatch.setattr("app.gui.grab_region", lambda _: render_board(chess.Board(), "green"))
    app._apply_region(region, preserve_game=True)
    app._window_api.bind.assert_called_once_with(region)
    assert app._window_binding is binding


def test_move_waits_for_settled_geometry_then_preserves_game_and_arrow_owner(followed, monkeypatch):
    app, api = followed
    original_sensor, tracker = app.sensor, app.tracker
    original_fen = tracker.fen
    api.current = replace(api.current, client=BoardRegion(300, 250, 1000, 800))
    monkeypatch.setattr("app.gui.grab_region", lambda _: render_board(tracker.board, "green"))
    assert app._follow_window(app.sensor, 1.0)
    assert app._follow_window(app.sensor, 1.1)
    assert app._events.get_nowait()[0] == "window_moving"
    assert app._events.empty()
    assert app._follow_window(app.sensor, 1.25)
    event = app._events.get_nowait()
    assert event[0] == "window_region"
    assert event[3] == BoardRegion(380, 350, 512, 512)
    assert app._board_recovery_pending
    app.overlay = Mock()
    app._ev_window_region(*event[1:])
    assert app.sensor is not original_sensor
    assert app.sensor.region == event[3]
    assert app.tracker is tracker and tracker.fen == original_fen
    assert app._window_binding.hwnd == 123
    assert not app._follow_moving and not app._board_recovery_pending
    assert app._tracking_uncertain  # Fresh settled frames still have to match.
    app.overlay.bind_region.assert_called_once_with(event[3], owner=123)
    app.root.deiconify.assert_not_called()
    app.root.focus_force.assert_not_called()


def test_continuous_drag_never_commits_intermediate_geometry(followed):
    app, api = followed
    for i in range(8):
        api.current = replace(api.current, client=BoardRegion(100 + i * 40, 100, 1000, 800))
        app._follow_window(app.sensor, i * 0.1)
    assert not any(event[0] == "window_region" for event in list(app._events.queue))


def test_resize_and_zoom_search_only_the_bound_window_and_exclude_the_panel(followed, monkeypatch):
    app, api = followed
    api.current = replace(api.current, client=BoardRegion(-1400, 30, 1200, 1000))
    target = BoardRegion(-1300, 200, 640, 640)
    app._panel_region = BoardRegion(1100, 20, 600, 500)
    detect = Mock(return_value=target)
    monkeypatch.setattr("app.gui.try_auto_detect", detect)
    app._follow_window(app.sensor, 1.0)
    app._follow_window(app.sensor, 1.3)
    detect.assert_called_once_with(exclude_region=app._panel_region, search_region=api.current.client,
                                   preferred_region=app._window_binding.search_hint(api.current))
    queued = list(app._events.queue)[-1]
    assert queued[0] == "window_region" and queued[3] == target


def test_an_occluding_windows_chessboard_is_never_adopted(followed, monkeypatch):
    app, api = followed
    api.hit = 789
    monkeypatch.setattr("app.gui.try_auto_detect", Mock(return_value=BoardRegion(180, 200, 512, 512)))
    app._scan_for_board(app.sensor, 1)
    assert app._events.empty()
    assert not app._board_recovery_pending


def test_minimize_warns_once_without_scanning_other_games_and_restore_keeps_history(followed, monkeypatch):
    app, api = followed
    tracker, before = app.tracker, app.tracker.fen
    api.current = replace(api.current, minimized=True)
    scan = Mock()
    monkeypatch.setattr("app.gui.try_auto_detect", scan)
    assert app._follow_window(app.sensor, 1)
    assert app._follow_window(app.sensor, 2)
    assert app._events.qsize() == 1
    event = app._events.get_nowait()
    assert event[0] == "window_waiting"
    app._ev_window_waiting(*event[1:])
    assert app._board_hidden and app._tracking_uncertain
    scan.assert_not_called()
    api.current = replace(api.current, minimized=False)
    assert not app._follow_window(app.sensor, 3)
    assert not app._follow_waiting
    assert app._next_board_check == 0
    assert app.tracker is tracker and tracker.fen == before
    app.root.deiconify.assert_not_called()


def test_stale_move_event_cannot_install_old_coordinates(followed, monkeypatch):
    app, api = followed
    old = app.sensor
    binding = app._window_binding
    detected = replace(api.current, client=BoardRegion(300, 100, 1000, 800))
    target = BoardRegion(380, 200, 512, 512)
    app._follow_pending = (old, binding)
    app._board_recovery_pending = True
    app._ev_window_region(old, binding, target, detected)  # Current window is elsewhere.
    assert app.sensor is old
    assert binding.region == old.region
    assert not app._board_recovery_pending


def test_overlay_hides_on_native_move_before_the_next_capture_tick(followed):
    app, api = followed
    app.overlay = Mock()
    app._overlay_move = (app.tracker.fen, "e2e4")
    app._last_states = board_to_sensor_states(app.tracker.board, True)
    app.board_arrow.get.return_value = True
    api.current = replace(api.current, client=BoardRegion(400, 100, 1000, 800))
    app._refresh_overlay()
    app.overlay.hide.assert_called_once()
    app.overlay.show.assert_not_called()


def test_arrow_keeps_explicit_owner_even_if_window_under_board_changes():
    overlay = BoardArrowOverlay.__new__(BoardArrowOverlay)
    overlay.layer = Mock()
    overlay.layer.root_at.return_value = 789
    overlay.bind_region(BoardRegion(100, 100, 512, 512), owner=123)
    assert overlay.owner == 123
    overlay.layer.root_at.assert_not_called()


def test_bound_capture_detection_supports_negative_monitor_coordinates(monkeypatch):
    client = BoardRegion(-1600, -100, 1200, 900)
    screenshot = np.full((900, 1200, 3), 55, np.uint8)
    screenshot[160:672, 150:662] = render_board(chess.Board(), "green")
    grab = Mock(return_value=screenshot)
    monkeypatch.setattr("app.capture.grab_region", grab)
    result = try_auto_detect(search_region=client)
    grab.assert_called_once_with(client)
    assert abs(result.left - (-1450)) <= 2
    assert abs(result.top - 60) <= 2
    assert abs(result.width - 512) <= 2
    assert try_auto_detect(search_region=client, exclude_region=BoardRegion(-1460, 50, 540, 540)) is None


def test_rescan_prefers_previously_selected_board_over_a_larger_preview(monkeypatch):
    client = BoardRegion(0, 0, 1600, 900)
    screenshot = np.full((900, 1600, 3), 55, np.uint8)
    screenshot[200:520, 150:470] = render_board(chess.Board(), "green", cell=40)
    screenshot[100:612, 900:1412] = render_board(chess.Board(), "brown")
    monkeypatch.setattr("app.capture.grab_region", lambda _: screenshot)
    result = try_auto_detect(search_region=client, preferred_region=BoardRegion(145, 195, 320, 320))
    assert abs(result.left - 150) <= 2
    assert abs(result.width - 320) <= 2


@pytest.mark.parametrize("resize", [False, True])
def test_capture_loop_realigns_then_resumes_automatic_suggestions(followed, monkeypatch, resize):
    app, api = followed
    original = app.tracker
    assert original.set_moves("1. e4 e5") is None
    before = original.fen
    image = render_board(original.board, "green", cell=80 if resize else 64)
    api.current = replace(api.current, client=BoardRegion(300, 150, 1200 if resize else 1000,
                                                        1000 if resize else 800))
    target = BoardRegion(400, 260, 640, 640) if resize else BoardRegion(380, 250, 512, 512)
    monkeypatch.setattr("app.gui.grab_region", lambda _: image)
    monkeypatch.setattr("app.gui.try_auto_detect", Mock(return_value=target))
    clock = [0.0]
    monkeypatch.setattr("app.gui.time.monotonic", lambda: clock[0])

    def tick(_):
        clock[0] += 0.1
        if clock[0] > 1.6:
            app._running = False

    monkeypatch.setattr("app.gui.time.sleep", tick)
    app._running = True
    app._capture_loop()  # Queued location is applied on the UI thread.
    app._running = True
    app._poll()
    assert app.sensor.region == target
    clock[0] = 0.0
    app._capture_loop()
    app._running = True
    app._poll()
    assert app.tracker is original and app.tracker.fen == before
    assert original.san_history == ["e4", "e5"]
    assert len(original.board.move_stack) == 2
    assert not app._tracking_uncertain
    app.engine_worker.submit.assert_called_once_with(before, "Balanced")


def test_scroll_with_unchanged_window_geometry_triggers_local_board_search(followed, monkeypatch):
    app, api = followed
    original = app.tracker
    target = BoardRegion(180, 120, 512, 512)
    clock = [0.0]
    monkeypatch.setattr("app.gui.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("app.gui.grab_region", lambda _: np.zeros((512, 512, 3), np.uint8))
    detect = Mock(return_value=target)
    monkeypatch.setattr("app.gui.try_auto_detect", detect)

    def tick(_):
        clock[0] += 0.1
        if clock[0] > 0.5:
            app._running = False

    monkeypatch.setattr("app.gui.time.sleep", tick)
    app._capture_loop()
    assert app._follow_moving
    assert app._board_recovery_pending
    assert app.tracker is original
    assert detect.call_args.kwargs["search_region"] == api.current.client
    assert list(app._events.queue)[0][0] == "window_region"


@pytest.mark.skipif(sys.platform != "win32", reason="Native window geometry")
def test_native_api_reads_physical_geometry_without_showing_or_activating_the_window(tk_root):
    api = WindowsWindowAPI()
    hwnd = api._ancestor(tk_root.winfo_id(), 2)
    snapshot = api.snapshot(hwnd)
    assert snapshot and snapshot.pid > 0
    assert not snapshot.visible
    assert snapshot.client.width >= 1 and snapshot.client.height >= 1
    assert snapshot.dpi >= 96
    assert api.desktop().width > 0
    assert tk_root.state() == "withdrawn"
