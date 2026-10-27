"""Opacity, geometry, saved preferences and native resource safety."""
import ctypes
from ctypes import wintypes as wt
import json
import sys
from unittest.mock import Mock

import chess
from PIL import Image
import pytest

from app.arrows import ArrowStyle, PRESETS, arrow_points, render_arrow, render_preview
from app.overlay import BoardArrowOverlay, OverlayUnavailable, _rgb
from app.squares import BoardRegion
from app.tracker import PositionTracker
from test_activity_ui import tk_root, ui
from test_gui_suggestions import app


@pytest.fixture
def styled(ui):
    ui._save_config = Mock()
    ui._paused = False
    ui._tracking_uncertain = False
    yield ui
    if ui._arrow_save_after:
        ui.root.after_cancel(ui._arrow_save_after)
        ui._arrow_save_after = None


@pytest.mark.parametrize("opacity", [0, 1, 25, 85, 100])
def test_alpha_is_real_transparency_and_never_exceeds_requested_opacity(opacity):
    style = ArrowStyle("#ff00ff", opacity, 100)
    image = render_arrow(272, True, "e2e4", style)
    assert image.mode == "RGBA" and image.size == (272, 272)
    assert image.getpixel((0, 0))[3] == 0
    assert image.getchannel("A").getextrema() == (0, style.alpha)
    if not opacity:
        assert image.getbbox() is None
    else:
        assert image.getbbox() is not None
        assert 0 < image.getpixel((153, 200))[3] <= style.alpha
        over_white = Image.alpha_composite(Image.new("RGBA", image.size, "white"), image)
        over_black = Image.alpha_composite(Image.new("RGBA", image.size, "black"), image)
        if opacity < 100:
            assert over_white.getpixel((153, 200)) != over_black.getpixel((153, 200))


@pytest.mark.parametrize("white_bottom", [True, False])
def test_mini_arrow_stays_on_the_requested_move_after_board_flip(white_bottom):
    image = render_arrow(272, white_bottom, "e2e4", ArrowStyle(opacity=100))
    # White-at-bottom e-file points are x=153; flipped e-file is x=119.
    x, source_y, middle_y, end_y = (153, 221, 187, 153) if white_bottom else (119, 51, 85, 119)
    assert image.getpixel((x, source_y))[3] > 0
    assert image.getpixel((x, middle_y))[3] > 0
    assert image.getpixel((x, end_y))[3] > 0
    assert image.getpixel((272 - x, middle_y))[3] == 0


def test_thickness_changes_geometry_and_pixels_without_moving_the_destination():
    region = BoardRegion(-1200, 150, 800, 800)
    thin = arrow_points(region, True, "e2e4", 50)
    thick = arrow_points(region, True, "e2e4", 200)
    assert thin[3] == thick[3] == (450, 450)
    assert abs(thick[0][0] - thick[6][0]) > abs(thin[0][0] - thin[6][0])
    assert abs(thick[2][0] - thick[4][0]) > abs(thin[2][0] - thin[4][0])
    thin_image = render_arrow(272, True, "b1c3", ArrowStyle(thickness=50))
    thick_image = render_arrow(272, True, "b1c3", ArrowStyle(thickness=200))
    assert sum(thick_image.getchannel("A").tobytes()) > sum(thin_image.getchannel("A").tobytes())


@pytest.mark.parametrize("settings", [None, [], {"color": "bad", "opacity": float("nan"), "thickness": -100},
                                       {"color": 3, "opacity": True, "thickness": "wide"}])
def test_bad_saved_settings_use_defaults(settings):
    assert ArrowStyle.from_config(settings) == ArrowStyle()


def test_partial_saved_settings_and_uppercase_hex_are_supported():
    assert ArrowStyle.from_config({"color": "#AABBCC", "opacity": 50}) == ArrowStyle("#aabbcc", 50, 100)
    assert ArrowStyle.from_config({"color": "bad", "thickness": 175}).thickness == 175


@pytest.mark.parametrize("values", [{"color": "red"}, {"opacity": 101}, {"thickness": 49}, {"opacity": float("inf")}])
def test_invalid_manual_settings_have_clear_errors(values):
    with pytest.raises(ValueError):
        ArrowStyle(**values)


def test_controls_change_color_opacity_and_thickness_without_restarting_analysis(styled):
    tr = styled.tracker = PositionTracker(follow_bottom=True)
    tr.set_recognized(chess.Board(), True)
    styled._overlay_move = (tr.fen, "e2e4")
    styled._refresh_overlay = Mock()
    controls = styled.arrow_controls
    controls.preset.set("Cyan")
    controls._preset_changed()
    controls.opacity_scale.set(40)
    controls.thickness_scale.set(175)
    assert styled.arrow_style == ArrowStyle(PRESETS["Cyan"], 40, 175)
    assert controls.opacity_text.get() == "40%" and controls.thickness_text.get() == "175%"
    assert styled.board_canvas.find_withtag("arrow:e2e4")
    assert styled._overlay_move == (tr.fen, "e2e4")
    assert styled._arrow_save_after
    styled._save_arrow_preferences()
    styled._save_config.assert_called_once()
    assert styled._arrow_save_after is None
    assert controls.preview.find_withtag("arrow")
    assert styled.root.state() == "withdrawn"


def test_custom_hex_and_bad_input_are_handled_inline(styled):
    controls = styled.arrow_controls
    controls.hex_color.set("#ABCDEF")
    controls._hex_changed()
    assert styled.arrow_style.color == "#abcdef"
    assert controls.preset.get() == "Custom"
    controls.hex_color.set("oops")
    controls._hex_changed()
    assert styled.arrow_style.color == "#abcdef"
    assert controls.hex_color.get() == "#abcdef"
    assert "six-digit hex" in styled.activity_txt.get("1.0", "end")
    assert "warning" in styled.activity_txt.tag_names("1.0")


def test_reset_restores_defaults_and_settings_expand_inline(styled):
    styled._toggle_arrow_settings()
    assert styled.arrow_controls.winfo_manager() == "pack"
    styled.arrow_controls.hex_color.set("#222222")
    styled.arrow_controls._hex_changed()
    styled.arrow_controls._reset()
    assert styled.arrow_style == ArrowStyle()
    styled._toggle_arrow_settings()
    assert styled.arrow_controls.winfo_manager() == ""
    assert not [w for w in styled.root.winfo_children() if w.winfo_class() == "Toplevel"]


def test_preferences_round_trip_and_missing_arrow_settings_keep_old_configs_valid(styled, monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    monkeypatch.setattr("app.gui.CONFIG_PATH", str(config))
    from app.gui import ChessAssistantApp
    styled.arrow_style = ArrowStyle("#123456", 37, 160)
    ChessAssistantApp._save_config(styled)
    assert json.loads(config.read_text())["arrow_style"] == styled.arrow_style.as_dict()
    styled.arrow_style = ArrowStyle()
    styled._load_config()
    assert styled.arrow_style == ArrowStyle("#123456", 37, 160)
    assert styled.arrow_controls.style == styled.arrow_style
    config.write_text(json.dumps({"strength": "Max", "board_arrow": False}))
    styled._load_config()
    assert styled.arrow_style == ArrowStyle()
    assert styled.level.get() == "Max"
    assert not styled.board_arrow.get()


def test_style_change_invalidates_the_cached_arrow_and_zero_opacity_hides_it():
    overlay = BoardArrowOverlay.__new__(BoardArrowOverlay)
    overlay.layer = Mock()
    overlay.layer.visible = True
    overlay.layer.is_active.return_value = True
    overlay.style = ArrowStyle()
    overlay.owner = 123
    overlay.region = BoardRegion(100, 100, 512, 512)
    overlay._drawn = None
    overlay.show(overlay.region, True, "e2e4")
    old_points = overlay.layer.show.call_args.args[1]
    overlay.set_style(ArrowStyle("#00ff00", 50, 200))
    overlay.show(overlay.region, True, "e2e4")
    assert overlay.layer.show.call_count == 2
    assert overlay.layer.show.call_args.args[1] != old_points
    overlay.set_style(ArrowStyle(opacity=0))
    overlay.show(overlay.region, True, "e2e4")
    assert overlay.layer.show.call_count == 2
    overlay.layer.hide.assert_called_once()
    assert overlay.owner == 123


@pytest.mark.skipif(sys.platform != "win32", reason="Native layered-window settings")
def test_native_styles_keep_opacity_capture_exclusion_focus_and_gdi_resources_correct():
    try:
        overlay = BoardArrowOverlay()
    except OverlayUnavailable as error:
        pytest.skip(str(error))
    layer = overlay.layer
    try:
        get_alpha = layer.user.GetLayeredWindowAttributes
        get_alpha.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.BYTE), ctypes.POINTER(wt.DWORD)]
        get_alpha.restype = wt.BOOL
        affinity = layer.user.GetWindowDisplayAffinity
        affinity.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        affinity.restype = wt.BOOL
        get_resources = layer.user.GetGuiResources
        get_resources.argtypes, get_resources.restype = [wt.HANDLE, wt.DWORD], wt.DWORD
        process = wt.HANDLE(-1)
        baseline = None
        foreground = layer.foreground()
        for i in range(80):
            color = ("#ff00ff", "#00ff00", "#ffffff", "#000000")[i % 4]
            style = ArrowStyle(color, i % 101, 50 + i)
            overlay.set_style(style)
            key, alpha, flags = wt.DWORD(), wt.BYTE(), wt.DWORD()
            assert get_alpha(layer.hwnd, ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags))
            assert alpha.value == style.alpha
            assert key.value == _rgb(*style.color_key)
            assert key.value != _rgb(*style.rgb)
            excluded = wt.DWORD()
            assert affinity(layer.hwnd, ctypes.byref(excluded)) and excluded.value == 0x11
            assert len(layer._resources) == 3
            if i == 5:
                baseline = get_resources(process, 0)  # Windows lazily creates its paint buffers.
        assert get_resources(process, 0) <= baseline + 1
        assert layer.foreground() == foreground
        assert not layer.visible
    finally:
        overlay.close()


def test_preview_and_mini_board_use_the_same_color_and_alpha():
    style = ArrowStyle("#12ab34", 40, 150)
    assert render_preview(style).getchannel("A").getextrema()[1] == style.alpha
    assert render_arrow(272, True, "a1h8", style).getchannel("A").getextrema()[1] == style.alpha


def test_closing_before_the_save_timer_fires_flushes_the_last_style(app):
    app._arrow_save_after = "pending-save"
    app.arrow_style = ArrowStyle("#123456", 40, 180)
    app._on_close()
    app.root.after_cancel.assert_called_once_with("pending-save")
    app._save_config.assert_called_once()
    app.root.destroy.assert_called_once()
    assert app._arrow_save_after is None


def test_native_style_initialization_failure_releases_the_layer(app, monkeypatch):
    native = Mock()
    native.set_style.side_effect = OverlayUnavailable("opacity unavailable")
    monkeypatch.setattr("app.gui.BoardArrowOverlay", Mock(return_value=native))
    app.overlay_check = Mock()
    app._init_overlay()
    native.close.assert_called_once()
    assert app.overlay is None
    app.board_arrow.set.assert_called_once_with(False)
    assert app._set_status.call_args.args[1] == "orange"


def test_style_change_keeps_an_inline_recognition_review_open(app):
    review = app._recognition_review = Mock()
    app._arrow_save_after = None
    app._on_arrow_style_changed(ArrowStyle("#224466", 50, 150))
    review._redraw.assert_called_once()
    review.destroy.assert_not_called()
    assert app.arrow_style == ArrowStyle("#224466", 50, 150)
