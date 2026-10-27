"""Use real Tk widgets to check inline notices, colors and promotion choices."""
import queue
import tkinter as tk

import chess
import pytest

from app.gui import ChessAssistantApp, LOG_COLORS, LOG_LIMIT
from app.tracker import PositionTracker


@pytest.fixture(scope="module")
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk display unavailable: {e}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def ui(tk_root):
    root = tk_root
    app = ChessAssistantApp.__new__(ChessAssistantApp)
    app.root = root
    app.sensor = app.tracker = None
    app._last_log_entry = None
    app._gameover_shown = False
    app._promotion_tracker = None
    app._events = queue.Queue()
    app._build_ui()
    root.update_idletasks()
    yield app
    for child in root.winfo_children():
        child.destroy()


@pytest.mark.parametrize("color,level", [
    ("orange", "warning"), ("red", "error"), ("green", "success")])
def test_activity_notices_have_requested_colors(ui, color, level):
    ui._set_status("Example notice", color)

    assert ui.activity_txt.get("1.0", "end-1c") == f"{level.upper()}: Example notice\n"
    assert level in ui.activity_txt.tag_names("1.0")
    assert ui.activity_txt.tag_cget(level, "foreground") == LOG_COLORS[level]
    assert ui.activity_txt.cget("state") == "disabled"
    assert ui.root.state() == "withdrawn"


def test_activity_deduplicates_and_limits_history_without_recording_each_move(ui):
    ui._set_status("Board hidden", "orange")
    ui._set_status("Board hidden", "orange")
    ui._set_status("Opponent played e5.")
    assert len(ui.activity_txt.get("1.0", "end-1c").splitlines()) == 1
    for i in range(LOG_LIMIT + 3):
        ui._set_status(f"Error {i}", "red")
    lines = ui.activity_txt.get("1.0", "end-1c").splitlines()
    assert len(lines) == LOG_LIMIT
    assert lines[0] == "ERROR: Error 3"
    assert lines[-1] == f"ERROR: Error {LOG_LIMIT + 2}"


@pytest.mark.parametrize("result", ["Checkmate — White wins", "Stalemate — draw", "Draw — insufficient material"])
def test_game_results_are_green_once_without_opening_windows(ui, result, monkeypatch):
    def unexpected(*a, **k):
        pytest.fail("Game result must not show a dialog")
    monkeypatch.setattr("tkinter.messagebox.showinfo", unexpected)

    ui._ev_gameover(result)
    ui._ev_gameover(result)

    assert ui.activity_txt.get("1.0", "end-1c") == f"SUCCESS: {result}\n"
    assert "success" in ui.activity_txt.tag_names("1.0")
    assert ui.suggest_lbl.cget("text") == ""
    assert ui.root.state() == "withdrawn"


def test_activity_replaces_moves_and_manual_suggest_button_is_removed(ui):
    def walk(widget):
        yield widget
        for child in widget.winfo_children():
            yield from walk(child)
    widgets = list(walk(ui.root))
    buttons = [w.cget("text") for w in widgets if w.winfo_class() == "TButton"]
    frames = [w.cget("text") for w in widgets if w.winfo_class() == "TLabelframe"]
    assert "Suggest now" not in buttons
    assert "Copy FEN" in buttons
    assert "Import game…" in buttons
    assert "I'm White" not in buttons
    assert "I'm Black" not in buttons
    assert "Resync…" not in buttons
    assert not any(w.cget("text") == "FEN:" for w in widgets
                   if w.winfo_class() == "TLabel")
    assert "Activity" in frames
    assert "Moves" not in frames
    assert not hasattr(ui, "moves_txt")
    assert not hasattr(ui, "_suggest_now")
    assert not hasattr(ui, "status_lbl")
    assert not hasattr(ui, "region_lbl")
    assert not hasattr(ui, "eval_lbl")
    assert not hasattr(ui, "eval_bar")


def test_promotion_is_chosen_inline_without_a_popup(ui):
    tracker = PositionTracker()
    tracker.set_fen("k7/7P/8/8/8/8/8/7K w - - 0 1")
    ui.tracker = tracker
    candidates = [(tracker.board.san(m), m) for m in tracker.board.legal_moves if m.promotion]

    ui._ask_promotion(candidates)

    assert ui._paused
    buttons = [w for w in ui.promotion_row.winfo_children() if w.winfo_class() == "TButton"]
    assert len(buttons) == 4
    assert not any(w.winfo_class() == "Toplevel" for w in ui.root.winfo_children())
    next(button for button in buttons if "=R" in button.cget("text")).invoke()
    assert tracker.board.piece_at(chess.H8).piece_type == chess.ROOK
    assert not ui._paused
    assert not ui.promotion_row.winfo_children()
    assert ui._events.get_nowait()[0] == "position"
    assert ui.root.state() == "withdrawn"


def test_automatic_new_game_notice_is_green_and_clears_old_result_without_popup(ui):
    tracker = PositionTracker(follow_bottom=True)
    tracker.start_from_sensed(["W"] * 16 + ["."] * 32 + ["B"] * 16)
    ui.tracker = tracker
    ui._gameover_shown = True
    ui._ev_new_game(tracker)
    assert not ui._gameover_shown
    assert ui.user_color == chess.BLACK
    assert "SUCCESS: New game detected. Your color is Black (bottom)." in ui.activity_txt.get("1.0", "end-1c")
    assert ui.activity_txt.tag_ranges("success")
    assert ui.suggest_lbl.cget("text") == ""
    assert ui.root.state() == "withdrawn"
