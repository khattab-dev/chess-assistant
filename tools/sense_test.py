"""Debug utility: run the occupancy sensor on an image file.

Usage:
    python tools/sense_test.py board.png
    python tools/sense_test.py screenshot.png <left> <top> <width> <height>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402

from app.squares import BoardRegion, BoardSensor, states_to_ascii  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    img = cv2.imread(sys.argv[1])
    if img is None:
        print("could not read image:", sys.argv[1])
        return 1
    if len(sys.argv) >= 6:
        l, t, w, h = map(int, sys.argv[2:6])
        img = img[t:t + h, l:l + w]
    sensor = BoardSensor(BoardRegion(0, 0, img.shape[1], img.shape[0]))
    sensor.calibrate(img)
    states, scores = sensor.sense(img)
    print(states_to_ascii(states))
    print("occupancy threshold:", round(sensor.occ_threshold, 3))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
