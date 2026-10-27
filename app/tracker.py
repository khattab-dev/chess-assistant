"""Position tracking: sensed occupancy changes matched against legal moves.

The tracker never trusts a single frame: a change is accepted only when a
legal move (or a pair of moves, if a frame was missed) explains the sensed
diff exactly. Castling, en passant, captures and promotions all fall out of
legality — no piece-type recognition needed. Everything stored derives from
python-chess, so the tracked position is always a legal game.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import io
import time

import chess
import chess.pgn

from .squares import BLACK, EMPTY, WHITE, sensor_to_chess_index

# Reject frames with more than this many changed squares — almost certainly a
# page scroll, window move, or a piece animation in flight.
MAX_CHANGES = 8

_PROMO_ORDER = {chess.QUEEN: 0, chess.ROOK: 1, chess.BISHOP: 2, chess.KNIGHT: 3}


class _StrictGameBuilder(chess.pgn.GameBuilder):
    def handle_error(self, error: Exception) -> None:
        # A partially parsed move list must not become the tracking baseline.
        raise error


@dataclass
class UpdateResult:
    status: str  # "none" | "moved" | "ambiguous" | "desync" | "resynced" | "gameover"
    move: chess.Move | None = None
    san: str | None = None
    mover: chess.Color | None = None
    candidates: list = field(default_factory=list)  # (san, move) pairs
    detail: str = ""


def _state64(board: chess.Board) -> list[str]:
    out = [EMPTY] * 64
    for sq, piece in board.piece_map().items():
        out[sq] = WHITE if piece.color == chess.WHITE else BLACK
    return out


def _expected_changes(board: chess.Board, move: chess.Move) -> set:
    """(square, new state) pairs a move produces, in chess-square space.

    Simulating the move on a copy covers every special case uniformly:
    castling moves two pieces, en passant empties a third square, and a
    promotion fills the last rank with the mover's color.
    """
    after_board = board.copy(stack=False)
    after_board.push(move)
    before, after = _state64(board), _state64(after_board)
    return {(sq, after[sq]) for sq in range(64) if after[sq] != before[sq]}


def board_to_sensor_states(board: chess.Board, white_bottom: bool) -> list[str]:
    """Render a board as sensor-space states (image order)."""
    st = _state64(board)
    return [st[sensor_to_chess_index(i, white_bottom)] for i in range(64)]


def _game_over_text(board: chess.Board) -> str:
    o = board.outcome()
    if o is None:
        return "Game over"
    if o.termination == chess.Termination.CHECKMATE:
        return f"Checkmate — {'White' if o.winner else 'Black'} wins"
    names = {
        chess.Termination.STALEMATE: "Stalemate — draw",
        chess.Termination.INSUFFICIENT_MATERIAL: "Draw — insufficient material",
        chess.Termination.THREEFOLD_REPETITION: "Draw — threefold repetition",
        chess.Termination.FIFTY_MOVES: "Draw — fifty-move rule",
    }
    return names.get(o.termination, "Game over")


class PositionTracker:
    def __init__(self) -> None:
        self.board: chess.Board | None = None
        self.white_bottom = True   # which color the *screenshot* shows at the bottom
        self.occ: list[str] = [EMPTY] * 64  # canonical states, chess-square order
        self.san_history: list[str] = []
        self.start_turn: chess.Color = chess.WHITE
        self.start_fullmove = 1
        self.user_color: chess.Color | None = None
        self.reset_sensing()

    def reset_sensing(self) -> None:
        """Discard pending frames after calibration or a manual resync."""
        self._pending_states = None
        self._pending_since = 0.0
        self._pending_frames = 0
        self._evaluated_key = None
        self._evaluated_result = UpdateResult("none")
        self._history_checked = False

    # -- setup --------------------------------------------------------------
    def start_from_sensed(self, states_img, user_color) -> str | None:
        """Initialize from the starting layout or up to two opening half-moves.

        This handles a White bot moving before the user can pick Black. Only
        accept a unique legal interpretation, checking both board orientations.
        """
        opening = []
        if states_img == [BLACK] * 16 + [EMPTY] * 32 + [WHITE] * 16:
            self.white_bottom = True
        elif states_img == [WHITE] * 16 + [EMPTY] * 32 + [BLACK] * 16:
            self.white_bottom = False
        else:
            found = self._find_opening(states_img)
            if found is None:
                return ("The board could not be matched to the starting layout "
                        "or the first two half-moves. Import the moves played "
                        "so far, paste a FEN, or start a new game.")
            self.white_bottom, opening = found
        self.board = chess.Board()
        self.start_turn = chess.WHITE
        self.start_fullmove = 1
        self.occ = _state64(self.board)
        self.san_history = []
        self.user_color = user_color
        if opening:
            self._commit_all(opening)
        self.reset_sensing()
        return None

    @staticmethod
    def _find_opening(states_img):
        targets = {}
        for white_bottom in (True, False):
            target = [EMPTY] * 64
            for i, state in enumerate(states_img):
                target[sensor_to_chess_index(i, white_bottom)] = state
            targets[white_bottom] = target
        initial = chess.Board()
        first_positions = []
        for move in initial.legal_moves:
            board = initial.copy()
            board.push(move)
            first_positions.append(board)
        for depth in (1, 2):
            matches = []
            for first in first_positions:
                continuations = [None] if depth == 1 else list(first.legal_moves)
                for reply in continuations:
                    board = first.copy()
                    if reply is not None:
                        board.push(reply)
                    observed = _state64(board)
                    for white_bottom, target in targets.items():
                        if observed == target:
                            matches.append((white_bottom, list(board.move_stack)))
                            if len(matches) > 1:
                                return None
            if matches:
                return matches[0]
        return None

    def set_position(self, text: str, states_img=None) -> str | None:
        """Accept either a FEN or the moves of one game (SAN / PGN)."""
        text = text.strip()
        if not text:
            return "Paste a move list or FEN first."
        if not text.startswith("[") and "/" in text.split()[0]:
            return self.set_fen(text, states_img)
        return self.set_moves(text, states_img)

    def set_moves(self, text: str, states_img=None) -> str | None:
        """Reconstruct a complete position and history from the game's moves."""
        try:
            stream = io.StringIO(text)
            game = chess.pgn.read_game(stream, Visitor=_StrictGameBuilder)
            if game is None:
                return "No moves found. Paste moves such as 1. e4 e5 2. Nf3."
            moves = list(game.mainline_moves())
            if not moves:
                return "No moves found. Paste moves such as 1. e4 e5 2. Nf3."
            if chess.pgn.read_game(stream, Visitor=_StrictGameBuilder) is not None:
                return "Paste the moves for one game at a time."
            board = game.board()
            start_turn, start_fullmove = board.turn, board.fullmove_number
            sans = []
            for move in moves:
                sans.append(board.san(move))
                board.push(move)
        except (ValueError, IndexError) as e:
            return f"Could not read the move list: {e}"
        # Parsing is complete before replacing an already tracked game.
        self.board = board
        self._infer_orientation(states_img)
        self.start_turn = start_turn
        self.start_fullmove = start_fullmove
        self.occ = _state64(board)
        self.san_history = sans
        self.reset_sensing()
        return None

    def set_fen(self, fen: str, states_img=None) -> str | None:
        """(Re)initialize from a FEN; infer board orientation from what the
        screenshot currently shows, when available."""
        try:
            self.board = chess.Board(fen)
        except ValueError as e:
            return f"Invalid FEN: {e}"
        self._infer_orientation(states_img)
        self.start_turn = self.board.turn
        self.start_fullmove = self.board.fullmove_number
        self.occ = _state64(self.board)
        self.san_history = []
        self.reset_sensing()
        return None

    def _infer_orientation(self, states_img) -> None:
        self.white_bottom = True
        if states_img is not None:
            mism = {}
            for wb in (True, False):
                expect = board_to_sensor_states(self.board, wb)
                mism[wb] = sum(1 for a, b in zip(expect, states_img) if a != b)
            self.white_bottom = mism[True] <= mism[False]

    # -- per-frame update -----------------------------------------------------
    def update_stable(self, states_img, now: float | None = None) -> UpdateResult:
        """Only match settled frames; animations may resemble legal moves.

        Repeated unmatched frames reuse their result instead of searching all
        two-ply continuations on every capture. After a longer stable mismatch,
        try the known recent history before requiring a manual FEN resync.
        """
        now = time.monotonic() if now is None else now
        states = tuple(states_img)
        if states != self._pending_states:
            self._pending_states = states
            self._pending_since = now
            self._pending_frames = 1
            return UpdateResult("none", detail="waiting for a stable frame")
        self._pending_frames += 1
        age = now - self._pending_since
        if self._pending_frames < 3 or age < 0.15:
            return UpdateResult("none", detail="waiting for a stable frame")
        key = (self.fen, states)
        if key != self._evaluated_key:
            self._evaluated_key = key
            self._evaluated_result = self.update(states)
            self._history_checked = False
        result = self._evaluated_result
        if (age >= 0.6 and result.detail and result.status in ("none", "desync")
                and not self._history_checked):
            self._history_checked = True
            recovered = self._recover_history(states)
            if recovered:
                self._evaluated_result = result = recovered
            else:
                self._evaluated_result = result = UpdateResult(
                    "desync", detail="stable board does not match the tracked position")
        return result

    def _recover_history(self, states_img) -> UpdateResult | None:
        """Rewind a takeback or replace a recent branch only when unambiguous.

        Occupancy cannot identify arbitrary piece types, so recovery uses only
        actual tracked positions and their legal one-move continuations. It
        never repairs a mismatch by guessing a turn or inventing a FEN.
        """
        if self.board is None or not self.board.move_stack:
            return None
        target = [EMPTY] * 64
        for i, state in enumerate(states_img):
            target[sensor_to_chess_index(i, self.white_bottom)] = state

        past = self.board.copy()
        ancestors = []
        for depth in range(1, min(8, len(past.move_stack)) + 1):
            past.pop()
            ancestors.append((depth, past.copy()))

        known = [(depth, board, None) for depth, board in ancestors
                 if _state64(board) == target]
        if known:
            candidates = known
        else:
            candidates = []
            for depth, board in ancestors[:4]:
                for move in board.legal_moves:
                    after = board.copy()
                    after.push(move)
                    if _state64(after) == target:
                        candidates.append((depth, board, move))
        if len(candidates) != 1:
            return None
        depth, board, move = candidates[0]
        self.board = board
        self.san_history = self.san_history[:-depth]
        self.occ = _state64(board)
        if move is not None:
            self._commit_all([move])
        return UpdateResult("resynced", detail="Tracking recovered from recent move history.")

    def update(self, states_img) -> UpdateResult:
        """Match a settled occupancy state; screen capture uses update_stable()."""
        if self.board is None:
            return UpdateResult("none")
        sensed = [EMPTY] * 64
        for i, st in enumerate(states_img):
            sensed[sensor_to_chess_index(i, self.white_bottom)] = st
        changed = [sq for sq in range(64) if sensed[sq] != self.occ[sq]]
        if not changed:
            return self._result("none")
        if len(changed) > MAX_CHANGES:
            return UpdateResult("none",
                                detail="too many squares changed; waiting for a stable frame")

        obs = {(sq, sensed[sq]) for sq in changed}
        # A single move vacates and fills a square, but a capture followed by
        # a recapture can leave its destination the SAME color as before.
        # Its net diff contains only two emptied source squares; still search
        # the two-ply continuations instead of mistaking it for a drag.
        has_fill = any(new != EMPTY for _, new in obs)
        has_empty = any(self.occ[sq] != EMPTY for sq, _ in obs)
        if not has_empty or (not has_fill and len(changed) < 2):
            return UpdateResult("none", detail="transient frame")

        moves = self._match_single(obs)
        if moves:
            if len(moves) == 1:
                return self._commit_all([moves[0]])
            cands = sorted(((self.board.san(m), m) for m in moves),
                           key=lambda p: _PROMO_ORDER.get(p[1].promotion, 9))
            return UpdateResult("ambiguous", candidates=cands)
        pairs = self._match_pair(obs)
        if len(pairs) == 1:
            return self._commit_all(list(pairs[0]))
        if not has_fill:
            return UpdateResult("none", detail="transient frame")
        return UpdateResult("desync",
                            detail="board changed in a way no legal move explains")

    def _match_single(self, obs) -> list | None:
        exact = [mv for mv in self.board.legal_moves
                 if not (obs ^ _expected_changes(self.board, mv))]
        return exact or None

    def _match_pair(self, obs) -> list:
        """Recover two missed plies (e.g. one frame skipped during analysis).

        Returns all exactly-matching move pairs; more than one means the
        diff is ambiguous and the caller should treat it as a desync.
        """
        found = []
        b0 = self.board
        for m1 in b0.legal_moves:
            b1 = b0.copy(stack=False)
            b1.push(m1)
            for m2 in b1.legal_moves:
                b2 = b1.copy(stack=False)
                b2.push(m2)
                after = _state64(b2)
                exp = {(sq, after[sq]) for sq in range(64)
                       if after[sq] != self.occ[sq]}
                if not (obs ^ exp):
                    found.append((m1, m2))
                    if len(found) > 1:
                        return found
        return found

    def _commit_all(self, moves) -> UpdateResult:
        mover = san = None
        for mv in moves:
            mover = self.board.turn
            san = self.board.san(mv)
            self.board.push(mv)
            self.san_history.append(san)
        self.occ = _state64(self.board)
        over = self.board.is_game_over()
        return self._result("gameover" if over else "moved",
                            move=moves[-1], san=san, mover=mover)

    def apply_choice(self, move: chess.Move) -> UpdateResult:
        """Commit the user's promotion-piece choice."""
        if self.board is None:
            return UpdateResult("none")
        return self._commit_all([move])

    def _result(self, status, move=None, san=None, mover=None) -> UpdateResult:
        detail = _game_over_text(self.board) if status == "gameover" else ""
        return UpdateResult(status, move=move, san=san, mover=mover, detail=detail)

    # -- info -----------------------------------------------------------------
    @property
    def fen(self) -> str:
        return self.board.fen() if self.board else ""

    def moves_text(self) -> str:
        if not self.san_history:
            return ""
        parts = []
        num = self.start_fullmove
        i = 0
        if self.start_turn == chess.BLACK:
            parts.append(f"{num}... {self.san_history[0]}")
            i = 1
            num += 1
        while i < len(self.san_history):
            line = f"{num}. {self.san_history[i]}"
            if i + 1 < len(self.san_history):
                line += f" {self.san_history[i + 1]}"
            parts.append(line)
            num += 1
            i += 2
        return " ".join(parts)
