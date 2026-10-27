"""The preview shows tracked piece types with correct orientation and moves."""
import chess
import pytest

from app.gui import CELL
from app.pieces import GLYPHS, PieceRenderer, render_piece_icons
from app.squares import chess_to_sensor_index
from app.tracker import PositionTracker
from test_activity_ui import tk_root, ui


def piece_at(ui, square):
    return [item for item in ui.board_canvas.find_withtag("square:" + square)
            if "piece" in ui.board_canvas.gettags(item)]


def test_icons_are_distinct_transparent_and_fit_each_square():
    icons = render_piece_icons(CELL)
    if not icons:
        pytest.skip("Pillow symbol font unavailable; native Tk fallback tested separately")
    assert set(icons) == set("KQRBNPkqrbnp")
    assert len({icon.tobytes() for icon in icons.values()}) == 12
    for icon in icons.values():
        assert icon.size == (CELL, CELL)
        assert icon.mode == "RGBA"
        assert icon.getbbox() is not None
        assert icon.getpixel((0, 0))[3] == 0


@pytest.mark.parametrize("white_bottom", [True, False])
def test_initial_board_draws_all_twelve_piece_types_in_correct_squares(ui, white_bottom):
    board = chess.Board()

    ui._draw_board(board, white_bottom, None)

    assert len(ui.board_canvas.find_withtag("piece")) == 32
    assert not any(ui.board_canvas.type(item) == "oval" for item in ui.board_canvas.find_all())
    for square, piece in board.piece_map().items():
        items = piece_at(ui, chess.square_name(square))
        assert len(items) == 1
        item = items[0]
        assert "piece:" + piece.symbol() in ui.board_canvas.gettags(item)
        r, c = divmod(chess_to_sensor_index(square, white_bottom), 8)
        assert ui.board_canvas.coords(item) == [c * CELL + CELL / 2, r * CELL + CELL / 2]
        if piece.symbol() in ui.piece_renderer.images:
            assert ui.board_canvas.itemcget(item, "image") == str(ui.piece_renderer.images[piece.symbol()])


def test_unknown_position_is_blank_instead_of_inventing_piece_types(ui):
    ui._draw_board(None, True, None)
    assert len(ui.board_canvas.find_all()) == 64
    assert not ui.board_canvas.find_withtag("piece")


@pytest.mark.parametrize("moves,expected,empty", [
    (["e4", "d5", "exd5"], {"d5": "P"}, ["e4"]),
    (["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O"],
     {"g1": "K", "f1": "R"}, ["e1", "h1"]),
    (["e4", "a6", "e5", "d5", "exd6"], {"d6": "P"}, ["d5", "e5"]),
])
def test_capture_castling_and_en_passant_update_displayed_piece_types(ui, moves, expected, empty):
    board = chess.Board()
    for san in moves:
        board.push_san(san)

    ui._draw_board(board, True, None)

    assert len(ui.board_canvas.find_withtag("piece")) == len(board.piece_map())
    for square, symbol in expected.items():
        items = piece_at(ui, square)
        assert len(items) == 1
        assert "piece:" + symbol in ui.board_canvas.gettags(items[0])
    for square in empty:
        assert not piece_at(ui, square)


@pytest.mark.parametrize("promotion", [chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT])
def test_promoted_piece_is_drawn_with_its_selected_type(ui, promotion):
    board = chess.Board("k7/7P/8/8/8/8/8/7K w - - 0 1")
    board.push(chess.Move(chess.H7, chess.H8, promotion=promotion))

    ui._draw_board(board, True, None)

    items = piece_at(ui, "h8")
    assert len(items) == 1
    assert "piece:" + chess.piece_symbol(promotion).upper() in ui.board_canvas.gettags(items[0])
    assert not piece_at(ui, "h7")
    assert not ui.board_canvas.find_withtag("piece:P")


@pytest.mark.parametrize("white_bottom", [True, False])
def test_suggestion_arrow_keeps_correct_coordinates_over_piece_icons(ui, white_bottom):
    ui._draw_board(chess.Board(), white_bottom, "e2e4")
    arrows = ui.board_canvas.find_withtag("suggestion")
    assert len(arrows) == 1
    expected = []
    for square in (chess.E2, chess.E4):
        r, c = divmod(chess_to_sensor_index(square, white_bottom), 8)
        expected.extend([c * CELL + CELL / 2, r * CELL + CELL / 2])
    assert ui.board_canvas.coords(arrows[0]) == expected
    assert ui.board_canvas.itemcget(arrows[0], "arrow") == "last"
    assert len(ui.board_canvas.find_withtag("piece")) == 32


def test_native_font_fallback_keeps_recognizable_piece_types(ui, monkeypatch):
    monkeypatch.setattr("app.pieces.render_piece_icons", lambda _: {})
    ui.piece_renderer = PieceRenderer(ui.root, CELL)

    ui._draw_board(chess.Board(), True, None)

    assert len(ui.board_canvas.find_withtag("piece")) == 32
    knight = piece_at(ui, "b1")[0]
    assert ui.board_canvas.type(knight) == "text"
    assert ui.board_canvas.itemcget(knight, "text") == GLYPHS[chess.KNIGHT]


def test_position_events_draw_full_piece_snapshots(ui):
    tracker = PositionTracker()
    tracker.set_fen(chess.STARTING_FEN)
    ui.tracker = tracker
    ui.user_color = tracker.user_color
    ui._ev_position(tracker.occ, True, tracker.fen, [])
    assert piece_at(ui, "e2")
    tracker.board.push_san("e4")
    assert piece_at(ui, "e2")  # displayed snapshot stays until the next event
    ui._ev_position(tracker.occ, True, tracker.fen, [])
    assert not piece_at(ui, "e2")
    assert "piece:P" in ui.board_canvas.gettags(piece_at(ui, "e4")[0])
