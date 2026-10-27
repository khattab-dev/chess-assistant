"""Exercise the actual bundled classifier on real screenshots, not mock labels."""
from pathlib import Path

import chess
import cv2
import numpy as np
import pytest

from app.recognition import BoardRead, PieceRecognizer, StableBoardReads, build_position
from app.squares import chess_to_sensor_index
from app.tracker import PositionTracker, board_to_sensor_states

FIXTURES = Path(__file__).parent / "fixtures"
POSITIONS = {
    "chesscom-capture.png": "2rnn2k/b4pp1/3q3p/3Pp3/1P2P3/2BB3P/R3Q1PK/8 w - - 0 38",
    "chesscom-textured.png": "rnbqkbnr/pppppp2/6p1/7p/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 3",
}


def screenshot(name="chesscom-capture.png", side=512, white_bottom=True):
    image = cv2.resize(cv2.imread(str(FIXTURES / name)), (side, side), interpolation=cv2.INTER_AREA)
    if not white_bottom:
        # Sites flip square positions while leaving the artwork upright.
        cell = side // 8
        image = (image.reshape(8, cell, 8, cell, 3).transpose(0, 2, 1, 3, 4)
                 [::-1, ::-1].transpose(0, 2, 1, 3, 4).reshape(side, side, 3).copy())
    return image


@pytest.fixture(scope="module")
def recognizer():
    return PieceRecognizer()


@pytest.mark.parametrize("name", POSITIONS)
@pytest.mark.parametrize("white_bottom", [True, False])
@pytest.mark.parametrize("side", [384, 512, 768])
def test_reads_at_multiple_sizes_flag_any_wrong_piece_as_uncertain(recognizer, name, white_bottom, side):
    read = recognizer.read(screenshot(name, side, white_bottom))
    expected = chess.Board(POSITIONS[name])
    assert read.plausible
    assert len(read.symbols) == len(read.confidence) == 64
    target = symbols_for(expected, white_bottom)
    wrong = {i for i in range(64) if read.symbols[i] != target[i]}
    assert wrong <= set(read.uncertain)
    if name != "chesscom-textured.png" or side != 512:
        assert not wrong
    assert read.bottom_hint() == white_bottom


def test_low_confidence_remains_visible_instead_of_silently_becoming_certain(recognizer):
    read = recognizer.read(screenshot("chesscom-textured.png", 768))
    assert read.uncertain
    assert min(read.confidence) < 0.8
    assert read.placement(True).board_fen() == chess.Board(POSITIONS["chesscom-textured.png"]).board_fen()


@pytest.mark.parametrize("image", [None, np.zeros((8, 8, 3), np.uint8), np.zeros((64, 64), np.uint8)])
def test_incomplete_images_are_rejected(recognizer, image):
    with pytest.raises(ValueError, match="complete"):
        recognizer.read(image)


def test_missing_model_has_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="model is missing"):
        PieceRecognizer(tmp_path / "missing.onnx").read(np.zeros((256, 256, 3), np.uint8))


def test_animation_and_dismissal_do_not_repeat_reviews(recognizer):
    read = recognizer.read(screenshot())
    other = BoardRead(tuple(reversed(read.symbols)), read.confidence)
    gate = StableBoardReads()
    assert not gate.offer(read, 0)
    assert not gate.offer(read, 0.4)
    assert not gate.offer(other, 0.8)
    assert not gate.offer(read, 1.2)
    assert not gate.offer(read, 1.6)
    assert gate.offer(read, 2.0)
    gate.suppressed = read.symbols
    assert not gate.offer(read, 3)
    assert not gate.offer(other, 4)
    assert not gate.offer(other, 4.4)
    assert gate.offer(other, 4.8)


def test_empty_or_kingless_patterns_never_offer_recovery():
    gate = StableBoardReads()
    read = BoardRead(tuple("." * 64), tuple([1.0] * 64))
    assert not any(gate.offer(read, now) for now in (0, 0.5, 1, 2))


def symbols_for(board, white_bottom=True):
    symbols = ["."] * 64
    for square, piece in board.piece_map().items():
        symbols[chess_to_sensor_index(square, white_bottom)] = piece.symbol()
    return symbols


def test_hidden_game_details_are_explicit_and_preserved(recognizer):
    original = chess.Board(POSITIONS["chesscom-textured.png"])
    read = recognizer.read(screenshot("chesscom-textured.png", 768))
    restored = build_position(read.symbols, True, chess.WHITE, castling="KQkq", fullmove=3)
    assert restored.fen() == original.fen()
    # Seeing home squares alone must never grant castling rights.
    assert not build_position(read.symbols, True, chess.WHITE).castling_rights
    with pytest.raises(ValueError, match="whose turn"):
        build_position(read.symbols, True, None)


@pytest.mark.parametrize("white_bottom", [True, False])
def test_reviewed_position_tracks_moves_without_inventing_past_history(white_bottom):
    board = chess.Board(POSITIONS["chesscom-capture.png"])
    tracker = PositionTracker(follow_bottom=True)
    tracker.set_recognized(board, white_bottom)
    assert tracker.user_color == white_bottom
    assert tracker.orientation_known
    assert tracker.fen == board.fen()
    assert tracker.san_history == []
    assert tracker.board.move_stack == []
    board.push_san("Qf1")
    assert tracker.update(board_to_sensor_states(board, white_bottom)).status == "moved"
    assert tracker.fen == board.fen()
    assert tracker.moves_text() == "38. Qf1"


def test_bad_metadata_and_bad_piece_edits_cannot_start_an_engine():
    board = chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 0 1")
    symbols = symbols_for(board)
    for kwargs, message in [({"castling": "K"}, "castling"),
                            ({"en_passant": "e6"}, "en passant"),
                            ({"en_passant": "bad"}, "En passant"),
                            ({"halfmove": -1}, "draw counter"),
                            ({"fullmove": 0}, "move number")]:
        with pytest.raises(ValueError, match=message):
            build_position(symbols, True, chess.WHITE, **kwargs)
    symbols[chess_to_sensor_index(chess.D4, True)] = "K"
    with pytest.raises(ValueError, match="exactly one"):
        build_position(symbols, True, chess.WHITE)


def test_en_passant_survives_reconstruction():
    board = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 12")
    restored = build_position(symbols_for(board), True, chess.WHITE, en_passant="d6", fullmove=12)
    assert restored.fen() == board.fen()
    assert restored.is_en_passant(chess.Move.from_uci("e5d6"))


def test_illegal_recognized_snapshot_does_not_replace_a_tracked_game():
    tracker = PositionTracker(follow_bottom=True)
    tracker.set_recognized(chess.Board(), True)
    before = tracker.fen
    with pytest.raises(ValueError):
        tracker.set_recognized(chess.Board.empty(), False)
    assert tracker.fen == before
