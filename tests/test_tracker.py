"""Tests for PositionTracker: synthetic occupancy diffs -> legal moves."""
import chess
import pytest

from app.squares import EMPTY, chess_to_sensor_index
from app.tracker import PositionTracker, board_to_sensor_states


def img(board, white_bottom=True):
    return board_to_sensor_states(board, white_bottom)


def new_tracker(user=chess.WHITE):
    t = PositionTracker()
    assert t.start_from_sensed(img(chess.Board()), user) is None
    return t


def apply(t, board_after):
    return t.update(img(board_after, t.white_bottom))


def test_start_and_e4():
    t = new_tracker()
    b = chess.Board()
    b.push_san("e4")
    res = apply(t, b)
    assert res.status == "moved" and res.san == "e4"
    assert t.board.fen() == b.fen()


def test_opponent_reply_and_capture():
    t = new_tracker()
    b = chess.Board()
    for san in ["e4", "d5", "exd5", "Qxd5"]:
        b.push_san(san)
        res = apply(t, b)
        assert res.status == "moved", res
        assert res.san == san
    assert t.board.fen() == b.fen()


def test_castling():
    t = new_tracker()
    b = chess.Board()
    for san in ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O"]:
        b.push_san(san)
        res = apply(t, b)
        assert res.status == "moved" and res.san == san, res


def test_en_passant():
    t = new_tracker()
    b = chess.Board()
    for san in ["e4", "a6", "e5", "d5", "exd6"]:
        b.push_san(san)
        res = apply(t, b)
        assert res.status == "moved" and res.san == san, res


def test_promotion_ambiguous_then_choice():
    t = new_tracker()
    b = chess.Board()
    for san in ["h4", "a5", "h5", "a4", "h6", "a3", "hxg7", "Nf6"]:
        b.push_san(san)
        assert apply(t, b).status == "moved"
    b.push_san("g8=Q")
    res = apply(t, b)  # the sensor can't tell which piece appeared on g8
    assert res.status == "ambiguous"
    assert len(res.candidates) == 4
    assert res.candidates[0][1].promotion == chess.QUEEN  # queen listed first
    res2 = t.apply_choice(chess.Move.from_uci("g7g8q"))
    assert res2.status == "moved"
    assert t.board.piece_at(chess.G8).piece_type == chess.QUEEN
    assert t.board.fen() == b.fen()


def test_two_missed_moves_recovered():
    t = new_tracker()
    b = chess.Board()
    b.push_san("e4")
    b.push_san("e5")
    res = apply(t, b)  # a whole round happened between two samples
    assert res.status == "moved"
    assert t.san_history == ["e4", "e5"]
    assert t.board.fen() == b.fen()


def test_desync_on_impossible_change():
    t = new_tracker()
    b = chess.Board()
    b.push_san("e4")
    states = img(b)
    states[chess_to_sensor_index(chess.A1, True)] = EMPTY  # rook vanished
    res = t.update(states)
    assert res.status == "desync"
    assert t.board.fen() == chess.Board().fen()  # nothing was committed


def test_transient_frames_ignored():
    t = new_tracker()
    states = img(chess.Board())
    states[chess_to_sensor_index(chess.E2, True)] = EMPTY  # piece lifted mid-drag
    res = t.update(states)
    assert res.status == "none"
    assert t.san_history == []


def test_scrolled_frame_ignored():
    t = new_tracker()
    states = img(chess.Board())
    for i in range(0, 64, 3):  # more than MAX_CHANGES squares "changed"
        states[i] = EMPTY if states[i] != EMPTY else "W"
    assert t.update(states).status == "none"


def test_black_bottom_orientation():
    t = PositionTracker()
    assert t.start_from_sensed(img(chess.Board(), white_bottom=False),
                               chess.BLACK) is None
    assert t.white_bottom is False
    b = chess.Board()
    b.push_san("e4")
    assert apply(t, b).san == "e4"
    b.push_san("e5")
    res = apply(t, b)
    assert res.status == "moved" and res.san == "e5"
    assert t.board.fen() == b.fen()


def test_later_positions_require_moves_or_fen_import():
    t = PositionTracker()
    b = chess.Board()
    for san in ["e4", "e5", "Nf3"]:
        b.push_san(san)
    assert t.start_from_sensed(img(b), chess.WHITE) is not None


def test_fen_resync_and_continue():
    t = PositionTracker()
    b = chess.Board()
    b.push_san("e4")
    b.push_san("c5")
    assert t.set_fen(b.fen(), img(b)) is None
    assert t.board.fen() == b.fen()
    b.push_san("Nf3")
    res = t.update(img(b))
    assert res.status == "moved" and res.san == "Nf3"


def test_checkmate_detected():
    t = new_tracker()
    b = chess.Board()
    res = None
    for san in ["f3", "e5", "g4", "Qh4#"]:
        b.push_san(san)
        res = apply(t, b)
    assert res.status == "gameover"
    assert "Checkmate" in res.detail


def test_moves_text_pairing():
    t = new_tracker()
    b = chess.Board()
    for san in ["e4", "e5", "Nf3"]:
        b.push_san(san)
        apply(t, b)
    assert t.moves_text() == "1. e4 e5 2. Nf3"


def test_moves_text_black_first():
    t = PositionTracker()
    b = chess.Board()
    b.push_san("d4")  # white already moved before tracking started
    assert t.set_fen(b.fen()) is None
    b.push_san("Nf6")
    assert t.update(img(b)).status == "moved"
    assert t.moves_text() == "1... Nf6"
