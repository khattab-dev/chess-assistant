# Chess Assistant

A Python desktop tool that watches a chess game on your screen (chess.com,
lichess, or any other site/app), lets you pick your color, tracks the live
position, and suggests the best move with an arrow — powered by
Stockfish.

## How it works

- **No FEN needed for a normal start.** Pick your color even if a White bot
  has already moved. The tool matches the starting layout or the first two
  half-moves against legal openings in either board orientation, retaining
  the detected moves and the correct side to move.
- **Automatic new-game detection.** When a tracked game returns to a fresh
  starting layout, the tool confirms it for about a second, clears the old
  history and result, and resumes automatic tracking. It also recognizes a
  new game if the first one or two half-moves already happened. Your chosen
  color is kept; choose your color again if you switched sides. New-game
  notices appear in green in **Activity**, without opening dialogs or
  restoring the control panel. Ordinary legal moves and board flips keep
  the existing game history.
- **No piece recognition.** The tool senses only which squares are occupied
  and by which color (background-color deviation per square, robust across
  themes and piece sets). Every sensed change is matched against the *legal
  moves* of the tracked game with `python-chess`, so captures, castling, en
  passant and promotions are all disambiguated by the rules of chess.
- Screen changes must settle for several frames before a move is accepted,
  so a piece animation is not immediately committed as a move.
- A missed frame is recovered by searching two consecutive moves, including
  a fast capture and recapture that leaves only the source squares empty.
- When the board shows something no legal move explains (window moved,
  page scrolled, animation glitch), the tool flags it and keeps retrying.
  If the board disappears from its capture region for several checks, the
  tool keeps the tracked game and looks for it quietly in the background.
  Alt-tabbing does not open a dialog or restore/focus the control panel.
  Tracking resumes when a visible board matches again. You can always
  resync by pasting the complete move list or current FEN.
- **Activity panel instead of notification dialogs.** Warnings appear in
  orange, errors in red, and checkmate, draws and recovery notices in green.
  The panel replaces the move log, suppresses consecutive duplicate entries,
  and retains the latest 200 lines. Promotion choices appear inline too.
- During a position mismatch, suggestions and arrows are cleared. Recent
  takebacks or mistaken branches can be repaired when the observed board
  has a unique match in recent tracked history or its legal next moves.
  Ambiguous positions require a move list or FEN resync rather than guessing
  whose turn it is.

## Setup

Requires Python 3.9+ on Windows (macOS/Linux also work).

```
cd chess-assistant
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python setup.py                   # downloads Stockfish into engines/
python main.py
```

If `setup.py` can't download Stockfish, grab it manually from
<https://stockfishchess.org/download/> and place the binary in `engines/`
(or set the `CHESS_ENGINE_PATH` environment variable). Stockfish is GPL-licensed;
this tool just launches it as a separate process.

## Usage

1. **Automatic board detection** — keep the board visible on your primary
   monitor when you launch the tool. It finds and calibrates the board
   automatically, checking fresh coordinates on every launch and **New game**.
   If detection fails, an orange warning appears in **Activity** and the
   tool retries quietly. You can click **Select board region** and drag a
   rectangle tightly around the 8x8 board (not the coordinates or side panels),
   or use **Detect board again** when it is visible.
   The control panel moves out of the board's way when screen space allows;
   keep the full board unobstructed for tracking.
2. **I'm White / I'm Black** — choose your color. The starting layout and
   the first two half-moves are recognized automatically, so a bot playing
   White can make its first move before you click **I'm Black**. No FEN is
   needed. If the game is further along, paste the **complete move list from
   the start**, such as `1. e4 e5 2. Nf3 Nc6`, or a PGN. FEN also remains
   supported. An arbitrary midgame board cannot be reconstructed from square
   colors alone; if neither moves nor FEN are available, start a fresh game.
3. Play. Suggestions run **automatically on your turn**, including your
   first move and after FEN resync or board recovery. The panel shows
   the suggested move and an arrow on the mini-board when analysis is ready.
   The suggestion clears after your move or when tracking is paused. Warnings
   and game results appear only in **Activity**. Move tracking runs at several
   frames per second.
4. **Copy FEN** copies the tracked position to the clipboard. If tracking
   cannot recover, choose your color again and import the full move list,
   PGN, or current FEN when prompted. Suggestions run automatically; there
   is no manual suggestion button or visible FEN field.

Engine strength: **Casual / Balanced / Max** (Stockfish Skill Level and
think time).

## Tests

```
pip install -r requirements-dev.txt
python -m pytest tests/
```

The tracker tests replay games (castling, en passant, promotion, missed
frames, desyncs) through synthetic occupancy diffs; the sensor tests render
synthetic board screenshots in several site themes and check the sensing
round-trip. Detection tests check board boundaries, sizes, themes, unrelated
window borders, and manual fallback/recovery. For a real screenshot, run:

```
python tools/sense_test.py screenshot.png
```

## Troubleshooting

- **Suggestions wrong on hi-DPI screens** — DPI awareness is set at startup;
  make sure you launch via `python main.py` (not through some scaling shim).
- **Tracking desyncs every move** — the board region may be slightly off
  (grid misaligned). Re-select the region; drag exactly along the board edges.
- **Multiple monitors** — region selection covers the primary monitor only.
- **Tinted piece sets** — calibration learns the light and dark piece colors
  from their foreground, so White's pieces can be darker than the board.
  Pieces that blend almost completely into their square may still confuse
  occupancy sensing; use a clearer theme in that case.

## Fair play

Running an engine against human opponents violates the fair-play policies of
chess.com, lichess and every other serious chess platform, and can get
accounts banned. Use this tool against bots, in training, or to review your
games — not to cheat real players.

## Project layout

```
main.py              entry point
setup.py             dependency check + Stockfish download
app/capture.py       mss screen capture, snip overlay, automatic board detection
app/squares.py       8x8 grid mapping, occupancy + piece-color sensing
app/tracker.py       diff -> legal move matching, FEN state, desync detection
app/engine.py        Stockfish worker thread: best move + eval
app/gui.py           Tkinter control panel
tools/sense_test.py  debug the sensor on an image file
tests/               tracker and sensor test suites
```

## Roadmap ideas

- Always-on-top transparent overlay drawing the arrow on the game window
- Camera-based physical board support
- Coaching mode: threat warnings, blunder alerts, alternate lines
