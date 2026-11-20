"""Camera alignment lines on the SFS live view (same as the IDS uEye Alignment tool).

- Horizontal reference line at Y = 221.1 px of the 960 x 540 Canva layout (~40.9 %).
- Vertical line at the centre (50 %) splitting left / right.
Positions are ratios, so they fit any stream size. Drawn only on live-stream frames,
never on inspection / saved images.
"""
import cv2

# Canva layout 960 x 540 px
CANVA_H = 540.0
Y_LINE_RATIO = 221.1 / CANVA_H   # ~0.4094
X_CENTER_RATIO = 0.5

COLOR_BGR = (0, 255, 0)
THICKNESS = 1


def draw_alignment_lines(frame, camera_id=None):
    """Draw the alignment cross on frame in place; returns the frame."""
    h, w = frame.shape[:2]
    ly = int(Y_LINE_RATIO * h)
    lx = int(X_CENTER_RATIO * w)
    cv2.line(frame, (0, ly), (w, ly), COLOR_BGR, THICKNESS)
    cv2.line(frame, (lx, 0), (lx, h), COLOR_BGR, THICKNESS)
    return frame
