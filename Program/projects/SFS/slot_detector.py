"""Part 2 - DETECT: is the workpiece still held by the clamp?

check(image, cam, round) -> {"status", "verdict", "ratios", ...}
  round 1 (or "back")  -> watch the BOTTOM pair (back clamp)
  round 2 (or "front") -> watch the TOP pair    (front clamp)

For each watched slot we look for a bright-blue island near where the empty
reference had that slot (search window tolerates the clamp moving a little) and
compare its area with the slot's fully-open area:
    open_ratio = island_area / reference_area      (0 = fully covered, 1 = fully open)
REJECT if ANY watched slot has open_ratio > max_open_ratio.
"""
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

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
        return cached[1]

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
    import sys, cv2 as cv
    # python slot_detector.py cam_1 1 frame.jpg
    cam, rnd, path = sys.argv[1], sys.argv[2], sys.argv[3]
    print(SlotDetector().check(cv.imread(path), cam, rnd))