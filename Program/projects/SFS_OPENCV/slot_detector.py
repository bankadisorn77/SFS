from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

import cv2 as cv
import numpy as np

try:                                    # inside the project package (projects/SFS/)
    from .frame_alinement import AlignConfig, FrameINIT, find_islands
except ImportError:                     # running this file on its own
    from frame_alinement import AlignConfig, FrameINIT, find_islands

OK, REJECT = "OK", "REJECT"
NO_FRAME, NO_REFERENCE, BAD_ROUND = "NO_FRAME", "NO_REFERENCE", "BAD_ROUND"

ROUND_TO_ROW = {1: "bot", "1": "bot", "back": "bot",
                2: "top", "2": "top", "front": "top"}


@dataclass
class DetectConfig:
    max_open_ratio: float = 0.25     # reject if any slot is open more than this
    # Minimum saturation counted as "open blue", per clamp row. The back clamp (round 1) is
    # lit so that covered slots show pale-navy patches (S 160-175) -> use a stricter value.
    empty_below: float = 0.25        # round 1: lower zone changed < this vs the empty frame -> no workpiece -> skip
    empty_below_front: float = 0.20  # round 2: band between the clamps changed < this -> no front workpiece -> skip
    skip_empty_front: bool = False   # round-2 "empty" skip is OFF: the cue is not reliable yet (see notes)
    smin_bot: int = 175              # round 1 / back clamp / bottom pair
    smin_top: int = 160              # round 2 / front clamp / top pair
    search_x: int = 90               # px the clamp may move around the reference
    search_y: int = 70


class SlotDetector:
    def __init__(self, ref_dir="refs", detect_config: Optional[DetectConfig] = None,
                 align_config: Optional[AlignConfig] = None):
        self.ref_dir = Path(ref_dir)
        self.cfg = detect_config or DetectConfig()
        # Detection uses a RELAXED island filter: a half-covered slot shrinks to a thin
        # sliver (aspect ratio > 4.5) that the strict calibration filter would drop.
        self.align_cfg = align_config or AlignConfig(aspect_min=0.0, aspect_max=1e9, area_min=100)
        self._refs = {}
        self.last_debug = {}          # what the last check() looked at (used by demo_logger)
        self._empty = {}              # cam -> whole empty frame, grey+blur (from ALIGN)

    def _ref(self, cam):
        """Cached, but re-read whenever the file changes (e.g. after an INIT cycle)."""
        p = self.ref_dir / f"reference_{cam}.json"
        if not p.exists():
            self._refs.pop(cam, None)
            return None
        mtime = p.stat().st_mtime
        cached = self._refs.get(cam)
        if cached is None or cached[0] != mtime:
            cached = (mtime, FrameINIT.load_reference(p))
            self._refs[cam] = cached
            png = self.ref_dir / f"reference_{cam}_empty.png"
            self._empty[cam] = cv.imread(str(png), 0) if png.exists() else None
        return cached[1]

    def _workpiece_change(self, image, cam, ref, row):
        """Fraction of a region that differs from the ALIGN empty frame.
        round 1 (bot): lower-centre zone | round 2 (top): band between the clamps.
        None = cannot tell (old reference, other resolution) -> caller assumes a workpiece IS there."""
        base = self._empty.get(cam)
        key = "zone" if row == "bot" else "band"
        if not ref.get("empty_full") or base is None or key not in ref or base.shape[:2] != image.shape[:2]:
            return None                  # reference made before this feature -> run ALIGN again
        h, w = image.shape[:2]
        x0, y0, x1, y1 = (int(ref[key][0] * w), int(ref[key][1] * h), int(ref[key][2] * w), int(ref[key][3] * h))
        cur = cv.GaussianBlur(cv.cvtColor(image, cv.COLOR_BGR2GRAY), (9, 9), 0)
        diff = np.abs(base[y0:y1, x0:x1].astype(np.int16) - cur[y0:y1, x0:x1].astype(np.int16))
        return float((diff > 40).mean())

    def check(self, image: np.ndarray, cam: str, round_) -> dict:
        self.last_debug = {}
        if image is None or getattr(image, "size", 0) == 0:
            return {"status": NO_FRAME}
        row = ROUND_TO_ROW.get(round_)
        if row is None:
            return {"status": BAD_ROUND, "round": round_}
        ref = self._ref(cam)
        if ref is None:
            return {"status": NO_REFERENCE, "cam": cam}

        change = self._workpiece_change(image, cam, ref, row) if (row == "bot" or self.cfg.skip_empty_front) else None
        limit = self.cfg.empty_below if row == "bot" else self.cfg.empty_below_front
        if change is not None and change < limit:
            return {"status": OK, "empty": True, "change": round(change, 3), "ratios": {},
                    "verdict": "SKIP", "worst": 0.0, "cam": cam, "round": round_}

        smin = self.cfg.smin_bot if row == "bot" else self.cfg.smin_top
        lo = self.align_cfg.lower_blue
        islands = find_islands(image, replace(self.align_cfg, lower_blue=(lo[0], smin, lo[2])))
        ratios, dbg = {}, {}
        for name, s in ref["slots"].items():
            if s["row"] != row:
                continue
            cx, cy = s["center"]
            near = [i for i in islands
                    if abs(i["center"][0] - cx) <= self.cfg.search_x
                    and abs(i["center"][1] - cy) <= self.cfg.search_y]
            best = max(near, key=lambda i: i["area"], default=None)
            ratios[name] = round((best["area"] if best else 0.0) / s["area"], 3)
            dbg[name] = {"ref_contour": s["contour"], "ratio": ratios[name], "island": best,
                         "window": (cx - self.cfg.search_x, cy - self.cfg.search_y,
                                    cx + self.cfg.search_x, cy + self.cfg.search_y)}
        self.last_debug = {"cam": cam, "round": round_, "slots": dbg}

        worst = max(ratios.values())
        # logic.py makes the final call using rule["max_open_ratio"]; "verdict" is a convenience
        return {"status": OK, "verdict": REJECT if worst > self.cfg.max_open_ratio else OK,
                "cam": cam, "round": round_, "ratios": ratios, "worst": worst}


if __name__ == "__main__":
    # python slot_detector.py <cam> <round> <image> [ref_dir] [out_dir]
    #   e.g. python slot_detector.py cam_1 1 frame.jpg refs demo_results
    import sys
    import cv2 as cv
    try:
        from . import demo_logger
    except ImportError:                      # run as a plain script
        import demo_logger

    if len(sys.argv) < 4:
        sys.exit("usage: python slot_detector.py <cam> <round> <image> [ref_dir] [out_dir]")
    cam, rnd, path = sys.argv[1], sys.argv[2], sys.argv[3]
    ref_dir = sys.argv[4] if len(sys.argv) > 4 else "refs"
    out_dir = sys.argv[5] if len(sys.argv) > 5 else "demo_results"

    img = cv.imread(path)
    if img is None:
        sys.exit(f"cannot read image: {path}")

    detector = SlotDetector(ref_dir=ref_dir)
    info = detector.check(img, cam, rnd)
    print({k: v for k, v in info.items()})

    saved = demo_logger.save_result_image(
        frame=img, cam=cam, round_=rnd, info=info,
        rule={"max_open_ratio": detector.cfg.max_open_ratio},
        detector=detector, out_dir=out_dir, save_raw=False)
    print("saved:", saved)