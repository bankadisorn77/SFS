import logging
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import cv2 as cv

logger = logging.getLogger(__name__)
ENABLED = True
DEFAULT_DIR = "results"
COLORS = {"OK": (0, 200, 0), "EMPTY": (0, 220, 255),
          "NG": (0, 0, 255), "ERROR": (0, 0, 255)}


def save_result_image(
    frame, cam, round_, info, *, verdict, reason,
    min_confidence=0.85, out_dir=DEFAULT_DIR, save_raw=False,
):
    # Display only: project.py supplies the decision.
    if not ENABLED:
        return None
    try:
        if frame is None or frame.size == 0:
            raise ValueError("Empty frame")
        if verdict not in COLORS:
            raise ValueError(f"Invalid verdict: {verdict!r}")
        if isinstance(round_, bool) or str(round_) not in ("1", "2"):
            raise ValueError(f"Invalid round: {round_!r}")
        round_num = int(round_)
        if isinstance(info, tuple):
            if len(info) != 2:
                raise ValueError("Expected (detection, pad_info)")
            det = info[0]
        else:
            det = info
        if isinstance(det, list):
            raise ValueError("Multiple detections are not supported")
        det = det if isinstance(det, dict) else {}
        class_name = det.get("class_name", "UNKNOWN")
        confidence = det.get("confidence", "N/A")
        color = COLORS[verdict]
        now = datetime.now()
        lines = [
            f"{cam} | Round {round_num} | RESULT: {verdict}",
            f"REASON: {reason}",
            f"CLASS: {class_name} | CONF: {confidence} | LIMIT: {min_confidence}",
            now.strftime("%Y-%m-%d %H:%M:%S"),
        ]
        vis = frame.copy()
        height, width = vis.shape[:2]
        cv.rectangle(vis, (0, 0), (width - 1, min(139, height - 1)),
                     (20, 20, 20), cv.FILLED)
        for i, text in enumerate(lines):
            text_width = cv.getTextSize(text, cv.FONT_HERSHEY_SIMPLEX,
                                       0.65, 1)[0][0]
            scale = 0.65 * min(1.0, max(1, width - 24) / max(1, text_width))
            cv.putText(vis, text, (12, 27 + i * 32),
                       cv.FONT_HERSHEY_SIMPLEX, scale,
                       color if i == 0 else (255, 255, 255), 1, cv.LINE_AA)
        folder = Path(out_dir) / now.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        safe_cam = "".join(c if c.isalnum() or c in "_-" else "_"
                           for c in str(cam))
        stamp = now.strftime("%Y-%m-%d_%H-%M-%S_%f")
        stem = f"{stamp}_R{round_num}_{safe_cam}_{uuid4().hex}_{verdict}"
        path = folder / f"{stem}.jpg"
        if not cv.imwrite(str(path), vis, [cv.IMWRITE_JPEG_QUALITY, 90]):
            raise OSError(f"Failed to save image: {path}")
        if save_raw:
            raw_path = folder / f"{stem}_raw.jpg"
            if not cv.imwrite(str(raw_path), frame, [cv.IMWRITE_JPEG_QUALITY, 90]):
                raise OSError(f"Failed to save raw image: {raw_path}")
        return path
    except Exception:
        logger.exception("[SFS] Could not save image: cam=%s round=%s", cam, round_)
        return None
