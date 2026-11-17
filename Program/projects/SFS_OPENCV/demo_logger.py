import logging
from datetime import datetime
from pathlib import Path

import cv2 as cv
import numpy as np

logger = logging.getLogger(__name__)

ENABLED = True
DEFAULT_DIR = "demo_results"

GREEN, RED, CYAN, GREY, WHITE = (0, 200, 0), (0, 0, 255), (255, 255, 0), (170, 170, 170), (255, 255, 255)


def save_result_image(frame, cam, round_, info, rule=None, detector=None,
                      out_dir=DEFAULT_DIR, save_raw=False):
    if not ENABLED:
        return None
    try:
        return _save(frame, cam, round_, info or {}, rule or {}, detector, out_dir, save_raw)
    except Exception as exc:
        logger.warning("[DEMO] could not save result image: %s", exc)
        return None


def _save(frame, cam, round_, info, rule, detector, out_dir, save_raw):
    if frame is None:
        return None
    limit = rule.get("max_open_ratio", 0.25)
    ratios = info.get("ratios") or {}
    worst = max(ratios.values(), default=0.0)
    if info.get("status") != "OK":
        verdict, detail = "ERROR", str(info.get("status"))
    elif info.get("empty"):
        verdict, detail = "EMPTY", f"no workpiece, change {float(info.get('change', 0)):.2f}"
    elif worst > limit:
        verdict, detail = "NG", f"open {worst:.2f} > limit {limit:.2f}"
    else:
        verdict, detail = "OK", f"open {worst:.2f} <= limit {limit:.2f}"
    color = GREEN if verdict == "OK" else (GREY if verdict == "EMPTY" else RED)

    vis = frame.copy()
    dbg = getattr(detector, "last_debug", None) or {}
    if dbg.get("cam") == cam:                      # only draw what belongs to THIS image
        overlay = vis.copy()
        for name, d in dbg.get("slots", {}).items():
            x0, y0, x1, y1 = d["window"]
            cv.rectangle(vis, (x0, y0), (x1, y1), GREY, 1)
            cv.polylines(vis, [np.array(d["ref_contour"], np.int32)], True, CYAN, 2)
            if d["island"] is not None:
                cv.drawContours(overlay, [d["island"]["contour"]], -1, RED, cv.FILLED)
                cv.drawContours(vis, [d["island"]["contour"]], -1, RED, 2)
            lab_col = RED if d["ratio"] > limit else GREEN
            cv.putText(vis, f"{name} {d['ratio']:.0%}", (x0, max(y0 - 8, 18)),
                       cv.FONT_HERSHEY_SIMPLEX, 1.1, lab_col, 3, cv.LINE_AA)
        vis = cv.addWeighted(overlay, 0.45, vis, 0.55, 0)

    now = datetime.now()
    rname = "back" if str(round_) in ("1", "back") else "front" if str(round_) in ("2", "front") else "refs"
    lines = [f"{cam} | {rname}", f"RESULT: {verdict}  ({detail})",
             now.strftime("%Y-%m-%d %H:%M:%S")]
    cv.rectangle(vis, (8, 8), (640, 112), (20, 20, 20), cv.FILLED)
    cv.rectangle(vis, (8, 8), (640, 112), color, 3)
    for i, (t, c) in enumerate(zip(lines, (WHITE, color, GREY))):
        cv.putText(vis, t, (20, 38 + i * 30), cv.FONT_HERSHEY_SIMPLEX, 0.8, c, 2, cv.LINE_AA)

    folder = Path(out_dir) / now.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{cam}_{now.strftime('%Y-%m-%d_%H-%M-%S')}_R{round_}_{verdict}"
    path = folder / f"{stem}.jpg"
    cv.imwrite(str(path), vis, [cv.IMWRITE_JPEG_QUALITY, 90])
    if save_raw:
        cv.imwrite(str(folder / f"{stem}_raw.jpg"), frame, [cv.IMWRITE_JPEG_QUALITY, 90])
    return path