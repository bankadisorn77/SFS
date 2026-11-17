import logging
from datetime import datetime
from pathlib import Path

import cv2 as cv

logger = logging.getLogger(__name__)

ENABLED = True
DEFAULT_DIR = "results"
MIN_CONFIDENCE = 0.85

GREEN = (0, 200, 0)
RED = (0, 0, 255)
YELLOW = (0, 220, 255)
GREY = (170, 170, 170)
WHITE = (255, 255, 255)


def save_result_image(
    frame,
    cam,
    round_,
    info,
    detector=None,
    out_dir=DEFAULT_DIR,
    save_raw=False,
    min_confidence: float = 0.85
):
    MIN_CONFIDENCE = min_confidence
    if not ENABLED:
        return None
    try:
        return _save(frame, cam, round_, info, detector, out_dir, save_raw)
    except Exception as exc:
        logger.warning("[SFS] could not save result image: %s", exc)
        return None


def _parse_single_detection(item, round_idx_0based):
    if not item:
        return False, "ERROR", "NO DATA", RED

    det = item[0] if isinstance(item, (tuple, list)) else item
    if not isinstance(det, dict):
        return False, "ERROR", "INVALID DATA", RED

    class_id = det.get("class_id")
    class_name = det.get("class_name", "UNKNOWN")
    conf = float(det.get("confidence", 0.0))

    if class_id is None:
        return False, "ERROR", "DETECTION ERROR", RED

    if conf < MIN_CONFIDENCE:
        return False, "NG", f"{class_name} ({conf:.1%} < {MIN_CONFIDENCE:.0%})", RED

    if round_idx_0based == 0:
        if class_id == 0:
            return True, "EMPTY", f"{class_name} ({conf:.1%})", YELLOW
        elif class_id == 1:
            return True, "OK", f"{class_name} ({conf:.1%})", GREEN
        elif class_id == 2:
            return True, "NG", f"{class_name} ({conf:.1%})", RED
        else:
            return False, "EROR", f"{class_name} ({conf:.1%})", RED

    elif round_idx_0based == 1:
        if class_id == 0:
            return True, "EMPTY", f"{class_name} ({conf:.1%})", YELLOW
        elif class_id in (1, 3):
            return True, "OK", f"{class_name} ({conf:.1%})", GREEN
        elif class_id == 4:
            return False, "NG", f"{class_name} ({conf:.1%})", RED
        else:
            return False, "EROR", f"{class_name} ({conf:.1%})", RED

    return False, "ERROR", f"INVALID ROUND ({round_idx_0based})", RED


def _save(frame, cam, round_, info, detector, out_dir, save_raw):
    if frame is None:
        return None

    round_num = int(round_) if str(round_).isdigit() else 1
    round_idx_0based = max(0, round_num - 1)

    is_multi_slot = (
        isinstance(info, list)
        and len(info) > 0
        and isinstance(info[0], (dict, tuple, list))
    )

    if is_multi_slot:
        slot_evals = [_parse_single_detection(s, round_idx_0based) for s in info]
        overall_ok = all(ev[0] for ev in slot_evals)
        verdict = "OK" if overall_ok else "NG"
        detail = " | ".join(f"S{i+1}:{ev[2]}" for i, ev in enumerate(slot_evals))
        banner_color = GREEN if overall_ok else RED
    else:
        is_ok, v_tag, detail, banner_color = _parse_single_detection(info, round_idx_0based)
        verdict = "OK" if is_ok else v_tag

    vis = frame.copy()
    h, w = vis.shape[:2]

    dbg = getattr(detector, "last_debug", None) or {}
    if dbg.get("cam") == cam and "slots" in dbg:
        for idx, (name, d) in enumerate(dbg["slots"].items()):
            x0, y0, x1, y1 = d["window"]
            if is_multi_slot and idx < len(slot_evals):
                _, _, s_detail, s_col = slot_evals[idx]
            else:
                s_col = banner_color
                s_detail = detail

            cv.rectangle(vis, (x0, y0), (x1, y1), s_col, 2)
            cv.putText(
                vis,
                f"{name}: {s_detail}",
                (x0, max(y0 - 8, 22)),
                cv.FONT_HERSHEY_SIMPLEX,
                0.65,
                s_col,
                2,
                cv.LINE_AA,
            )

    now = datetime.now()
    lines = [
        f"{cam} | Round {round_num}",
        f"RESULT: {verdict}  [{detail}]",
        now.strftime("%Y-%m-%d %H:%M:%S"),
    ]

    box_w = min(max(620, len(lines[1]) * 13 + 40), w - 16)
    cv.rectangle(vis, (8, 8), (8 + box_w, 112), (20, 20, 20), cv.FILLED)
    cv.rectangle(vis, (8, 8), (8 + box_w, 112), banner_color, 3)

    for i, (text_line, text_col) in enumerate(zip(lines, (WHITE, banner_color, GREY))):
        cv.putText(
            vis,
            text_line,
            (20, 38 + i * 30),
            cv.FONT_HERSHEY_SIMPLEX,
            0.72,
            text_col,
            2,
            cv.LINE_AA,
        )

    folder = Path(out_dir) / now.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{now.strftime('%Y-%m-%d_%H-%M-%S')}_R{round_num}_{cam}_{verdict}"
    path = folder / f"{stem}.jpg"

    cv.imwrite(str(path), vis, [cv.IMWRITE_JPEG_QUALITY, 90])
    if save_raw:
        cv.imwrite(str(folder / f"{stem}_raw.jpg"), frame, [cv.IMWRITE_JPEG_QUALITY, 90])

    return path