"""Start without FEN after a bot's opening, or import the game's move list."""
import chess
import pytest

from app.tracker import PositionTracker, board_to_sensor_states


@pytest.mark.parametrize("move", list(chess.Board().legal_moves))
@pytest.mark.parametrize("white_bottom", [True, False])
def test_any_bot_first_move_is_inferred_in_either_orientation(move, white_bottom):
    board = chess.Board()
    san = board.san(move)
    board.push(move)
    tracker = PositionTracker()

    error = tracker.start_from_sensed(board_to_sensor_states(board, white_bottom), chess.BLACK)

    assert error is None
    assert tracker.white_bottom == white_bottom
    assert tracker.user_color == chess.BLACK
    assert tracker.board.turn == chess.BLACK
    assert tracker.fen == board.fen()
    assert tracker.board.move_stack == [move]
    assert tracker.san_history == [san]
    assert tracker.moves_text() == f"1. {san}"


@pytest.mark.parametrize("moves", [["e4", "e5"], ["d4", "Nf6"], ["Nf3", "d5"]])
@pytest.mark.parametrize("white_bottom", [True, False])
def test_first_two_half_moves_are_inferred_and_tracking_continues(moves, white_bottom):
    board = chess.Board()
    for san in moves:
        board.push_san(san)
    tracker = PositionTracker()

    assert tracker.start_from_sensed(board_to_sensor_states(board, white_bottom), chess.WHITE) is None
    assert tracker.fen == board.fen()
    assert tracker.san_history == moves
    board.push_san("Nc3")
    assert tracker.update(board_to_sensor_states(board, white_bottom)).status == "moved"
    assert tracker.fen == board.fen()


@pytest.mark.parametrize("text", [
    "1. e4 e5 2. Nf3 Nc6 3. Bb5",
    "e4 e5 Nf3 Nc6 Bb5",
    '[Event "Bot training"]\n\n1. e4 {opening} e5 2. Nf3 (2. Bc4) Nc6 3. Bb5 *',
])
@pytest.mark.parametrize("white_bottom", [True, False])
def test_move_list_or_pgn_restores_complete_position_and_history(text, white_bottom):
    board = chess.Board()
    sans = ["e4", "e5", "Nf3", "Nc6", "Bb5"]
    for san in sans:
        board.push_san(san)
    tracker = PositionTracker()
    tracker.user_color = chess.BLACK

    error = tracker.set_position(text, board_to_sensor_states(board, white_bottom))

    assert error is None
    assert tracker.fen == board.fen()
    assert tracker.san_history == sans
    assert tracker.white_bottom == white_bottom
    assert tracker.user_color == chess.BLACK
    board.push_san("a6")
    assert tracker.update(board_to_sensor_states(board, white_bottom)).status == "moved"
    assert tracker.fen == board.fen()


@pytest.mark.parametrize("text", ["", "no moves here", "*", "1. e4 e5 2. Bh6",
    '[Event "First"]\n\n1. e4 *\n\n[Event "Second"]\n\n1. d4 *'])
def test_invalid_or_multiple_move_lists_do_not_replace_existing_position(text):
    tracker = PositionTracker()
    tracker.set_fen(chess.STARTING_FEN)
    before = tracker.fen

    assert tracker.set_position(text) is not None
    assert tracker.fen == before
    assert tracker.san_history == []


def test_fen_still_works_in_shared_position_import():
    board = chess.Board()
    board.push_san("d4")
    tracker = PositionTracker()
    assert tracker.set_position(board.fen()) is None
    assert tracker.fen == board.fen()


def test_pgn_with_custom_start_preserves_move_numbers():
    board = chess.Board()
    board.push_san("e4")
    start = board.fen()
    board.push_san("c5")
    board.push_san("Nf3")
    tracker = PositionTracker()

    assert tracker.set_position(f'[SetUp "1"]\n[FEN "{start}"]\n\n1... c5 2. Nf3 *') is None
    assert tracker.fen == board.fen()
    assert tracker.moves_text() == "1... c5 2. Nf3"
