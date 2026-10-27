"""Board-arrow geometry, visibility policy and native capture exclusion."""
import ctypes
from ctypes import wintypes as wt
import sys
import time
from unittest.mock import Mock

import chess
import numpy as np
import pytest

from app.capture import grab_region
from app.overlay import BoardArrowOverlay, OverlayUnavailable, _WindowsLayer, _rgb, arrow_points
from app.squares import BoardRegion
from app.tracker import board_to_sensor_states
from test_activity_ui import tk_root
from test_gui_suggestions import app


@pytest.mark.parametrize("white_bottom,start,tip", [
    (True, (450, 650), (450, 450)), (False, (350, 150), (350, 350))])
def test_arrow_coordinates_match_orientation_in_local_physical_pixels(white_bottom, start, tip):
    points = arrow_points(BoardRegion(-1200, 150, 800, 800), white_bottom, "e2e4")
    assert points[3] == tip
    assert ((points[0][0] + points[6][0]) / 2,
            (points[0][1] + points[6][1]) / 2) == start
    assert all(0 <= x < 800 and 0 <= y < 800 for x, y in points)


@pytest.mark.parametrize("uci", ["0000", "a1a1", "invalid"])
def test_invalid_arrows_are_rejected(uci):
    with pytest.raises(ValueError):
        arrow_points(BoardRegion(0, 0, 512, 512), True, uci)


def test_arrow_polygon_supports_promotion_and_rectangular_capture_regions():
    region = BoardRegion(10, 20, 800, 808)
    points = arrow_points(region, True, "h7h8q")
    assert points[3] == (750, 50)
    assert len(points) == 7


def test_overlay_follows_bound_game_focus_and_reuses_unchanged_arrow():
    overlay = BoardArrowOverlay.__new__(BoardArrowOverlay)
    overlay.layer = Mock()
    overlay.layer.visible = False
    overlay.layer.root_at.return_value = 123
    overlay.layer.is_active.return_value = False
    overlay.region = overlay.owner = overlay._drawn = None
    overlay.layer.show.side_effect = lambda *a: setattr(overlay.layer, "visible", True)
    overlay.layer.hide.side_effect = lambda: setattr(overlay.layer, "visible", False)
    region = BoardRegion(100, 200, 512, 512)

    overlay.show(region, True, "e2e4")
    overlay.layer.show.assert_not_called()
    overlay.layer.root_at.assert_called_once_with(356, 456)
    overlay.layer.is_active.return_value = True
    overlay.show(region, True, "e2e4")
    overlay.show(region, True, "e2e4")
    overlay.layer.show.assert_called_once()
    overlay.layer.is_active.return_value = False  # alt-tab
    overlay.show(region, True, "e2e4")
    assert not overlay.layer.visible
    overlay.layer.is_active.return_value = True
    overlay.show(region, True, "e2e4")
    assert overlay.layer.show.call_count == 2
    moved = BoardRegion(400, 100, 512, 512)
    overlay.show(moved, False, "e2e4")
    overlay.layer.root_at.assert_called_with(656, 356)
    overlay.layer.show.assert_called_with(moved, arrow_points(moved, False, "e2e4"))


def ready_overlay(app):
    app.overlay = Mock()
    app.board_arrow.get.return_value = True
    app._board_misses = 0
    app._last_states = board_to_sensor_states(app.tracker.board, app.tracker.white_bottom)
    app.sensor.region = BoardRegion(100, 200, 512, 512)
    app._overlay_move = (app.tracker.fen, "e2e4")


def test_current_engine_result_is_drawn_on_the_detected_game_board(app):
    ready_overlay(app)
    app._overlay_move = None
    app._ev_eval({"fen": app.tracker.fen, "san": "e4", "uci": "e2e4"})

    app._refresh_overlay()

    app.overlay.show.assert_called_once_with(app.sensor.region, True, "e2e4")


@pytest.mark.parametrize("blocked", ["disabled", "opponent", "uncertain", "hidden", "changed_screen", "paused"])
def test_overlay_hides_immediately_when_suggestion_is_not_safe_to_show(app, blocked):
    ready_overlay(app)
    if blocked == "disabled":
        app.board_arrow.get.return_value = False
    elif blocked == "opponent":
        app.tracker.user_color = chess.BLACK
    elif blocked == "uncertain":
        app._tracking_uncertain = True
    elif blocked == "hidden":
        app._board_hidden = True
    elif blocked == "changed_screen":
        app._last_states = ["."] * 64
    else:
        app._paused = True

    app._refresh_overlay()

    app.overlay.show.assert_not_called()
    app.overlay.hide.assert_called_once()


def test_clearing_advice_removes_cached_overlay_and_close_destroys_native_window(app):
    ready_overlay(app)
    overlay = app.overlay
    app._clear_advice()
    assert app._overlay_move is None
    overlay.hide.assert_called_once()
    app._on_close()
    overlay.close.assert_called_once()
    assert app.overlay is None


def test_board_arrow_toggle_hides_layer_and_saves_preference(app):
    ready_overlay(app)
    app.board_arrow.get.return_value = False
    app._on_overlay_toggle()
    app.overlay.hide.assert_called_once()
    app._save_config.assert_called_once()
    assert app._overlay_move is not None  # can re-enable the still-current arrow


def test_unsupported_overlay_logs_warning_and_keeps_mini_board_available(app, monkeypatch):
    monkeypatch.setattr("app.gui.BoardArrowOverlay", Mock(side_effect=OverlayUnavailable("unsupported")))
    app.overlay_check = Mock()
    app._init_overlay()
    assert app.overlay is None
    app.board_arrow.set.assert_called_once_with(False)
    app.overlay_check.state.assert_called_once_with(["disabled"])
    assert app._set_status.call_args.args[1] == "orange"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows compositor integration")
def test_native_arrow_does_not_capture_input_focus_or_appear_in_board_screenshots(tk_root):
    if sys.getwindowsversion().build < 19041:
        pytest.skip("Capture exclusion requires Windows 10 version 2004 or newer")
    # Read only a small owned test rectangle. Never activate a window or
    # generate mouse/keyboard input.
    region = BoardRegion(25, 35, 320, 320)
    background = _WindowsLayer()
    overlay = BoardArrowOverlay()

    def pump():
        for _ in range(6):
            tk_root.update()
            time.sleep(0.03)
        ctypes.windll.dwmapi.DwmFlush()

    try:
        user, gdi = background.user, background.gdi
        user.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
        user.SetWindowDisplayAffinity.restype = wt.BOOL
        assert user.SetWindowDisplayAffinity(background.hwnd, 0)
        user.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.DWORD, wt.BYTE, wt.DWORD]
        assert user.SetLayeredWindowAttributes(background.hwnd, _rgb(255, 0, 255), 255, 3)
        gdi.CreateSolidBrush.argtypes = [wt.DWORD]
        gdi.CreateSolidBrush.restype = wt.HBRUSH
        blue = gdi.CreateSolidBrush(_rgb(40, 120, 210))
        old = background.brush
        background.delete(old)
        background._resources.remove(old)
        background.brush = blue
        background._resources.append(blue)
        foreground = user.GetForegroundWindow()
        background.show(region, [(0, 0), (320, 0), (320, 320), (0, 320)])
        deadline = time.monotonic() + 2
        while True:
            pump()
            before = grab_region(region)
            if np.allclose(before[160, 160], (210, 120, 40), atol=2):
                break
            if time.monotonic() >= deadline:
                break
            background.invalidate(background.hwnd, None, False)
            background.update_window(background.hwnd)
        assert np.allclose(before[160, 160], (210, 120, 40), atol=2)
        hit = background.root_at(region.left + 180, region.top + 220)
        overlay.layer.show(region, arrow_points(region, True, "e2e4"))
        pump()
        assert np.array_equal(before, grab_region(region))
        assert overlay.layer.root_at(region.left + 180, region.top + 220) == hit
        assert user.GetForegroundWindow() == foreground
        # Positive control: the same visible arrow IS detectable without the
        # capture exclusion flag, so a hidden/broken renderer cannot pass.
        assert user.SetWindowDisplayAffinity(overlay.layer.hwnd, 0)
        pump()
        assert np.count_nonzero(before != grab_region(region)) > 100
    finally:
        overlay.close()
        background.close()
