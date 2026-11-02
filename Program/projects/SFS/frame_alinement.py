"""Part 1 - ALIGNMENT: build the reference from an EMPTY frame (no workpiece).

For every camera, take one empty frame where both clamps are clearly visible.
We find the 4 slots (2 rows x 2) and store each slot's centre, area, bbox and
contour. Part 2 (slot_detector.py) uses this file to decide OK / REJECT.

How a slot is recognised: it is a BRIGHT, SATURATED blue island (the blue plate
seen through the clamp) completely surrounded by the dark clamp body.
 * open slot      -> bright saturated blue (S ~ 175-230)
 * covered slot   -> dark navy (S ~ 105-140), so it is NOT picked up
That is why saturation (S >= 160), not brightness, is the key threshold.

Rows:  top  = FRONT clamp (nearer the camera)  -> round 2
       bot  = BACK  clamp (farther)            -> round 1
"""
import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2 as cv
import numpy as np

logger = logging.getLogger(__name__)

OK, NO_FRAME, REF_NOT_CLEAR = "OK", "NO_FRAME", "REF_NOT_CLEAR"


@dataclass
class AlignConfig:
    lower_blue: tuple = (95, 160, 60)     # HSV (H, S, V). S>=160 separates open from covered
    upper_blue: tuple = (135, 255, 255)
    blur_ksize: int = 5
    open_ksize: int = 5
    area_min: float = 250                 # px, at 1920x1080
    area_max: float = 14000
    aspect_min: float = 1.3               # width / height of the slot
    aspect_max: float = 4.5
    ring: int = 10                        # px ring around the island
    dark_v: int = 100                     # ring pixels with V < this count as "dark clamp"
    ring_dark_min: float = 0.6            # island must be >= 60% enclosed by dark
    # where the clamps live (fractions of the image) - ignores slots-like things elsewhere
    zone_x: tuple = (0.26, 0.73)
    zone_y: tuple = (0.23, 0.58)
    row_split_gap: int = 100              # px between the two clamp rows


def find_islands(img: np.ndarray, cfg: AlignConfig) -> list:
    """Bright blue islands enclosed by dark metal. -> [{center, area, bbox, contour}]"""
    k = cfg.blur_ksize
    hsv = cv.cvtColor(cv.GaussianBlur(img, (k, k), 0), cv.COLOR_BGR2HSV)
    mask = cv.inRange(hsv, np.array(cfg.lower_blue), np.array(cfg.upper_blue))
    o = cfg.open_ksize
    mask = cv.morphologyEx(mask, cv.MORPH_OPEN,
                           cv.getStructuringElement(cv.MORPH_ELLIPSE, (o, o)))
    cnts, _ = cv.findContours(mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
    dark = hsv[:, :, 2] < cfg.dark_v
    r, out = cfg.ring, []
    for c in cnts:
        area = float(cv.contourArea(c))
        x, y, w, h = cv.boundingRect(c)
        if not (cfg.area_min < area <= cfg.area_max) or h == 0:
            continue
        if not (cfg.aspect_min <= w / h <= cfg.aspect_max):
            continue
        m = np.zeros(mask.shape, np.uint8)
        cv.drawContours(m, [c], -1, 255, cv.FILLED)
        ring = cv.dilate(m, np.ones((2 * r + 1, 2 * r + 1), np.uint8)) & ~m
        if dark[ring > 0].mean() < cfg.ring_dark_min:
            continue                       # not surrounded by the clamp -> plate/background
        out.append({"center": (x + w // 2, y + h // 2), "area": area,
                    "bbox": (x, y, w, h), "contour": c})
    return out


class FrameINIT:
    def __init__(self, config=None, align_config: Optional[AlignConfig] = None,
                 save_dir: Optional[str] = None):
        """config: your project Config (optional). Reference files go to
        save_dir, else config.reference_dir, else ./refs"""
        self.cfg = replace(align_config) if align_config else AlignConfig()
        self.save_dir = Path(save_dir or getattr(config, "reference_dir", None) or "refs")

    def calibrate(self, image: np.ndarray, cam_id: str) -> dict:
        """-> {"status": OK, "reference": {...}} or {"status": REF_NOT_CLEAR, "candidates": ...}"""
        if image is None or getattr(image, "size", 0) == 0:
            return {"status": NO_FRAME}
        h, w = image.shape[:2]
        zx, zy = self.cfg.zone_x, self.cfg.zone_y
        isl = [i for i in find_islands(image, self.cfg)
               if zx[0] * w <= i["center"][0] <= zx[1] * w
               and zy[0] * h <= i["center"][1] <= zy[1] * h]

        rows = []                                           # cluster by y: top -> bottom
        for i in sorted(isl, key=lambda i: i["center"][1]):
            if rows and i["center"][1] - rows[-1][-1]["center"][1] <= self.cfg.row_split_gap:
                rows[-1].append(i)
            else:
                rows.append([i])
        if len(rows) != 2 or any(len(r) != 2 for r in rows):
            logger.warning("%s: need 2 rows x 2 slots, got %s", cam_id, [len(r) for r in rows])
            return {"status": REF_NOT_CLEAR,
                    "candidates": [(i["center"], int(i["area"])) for i in isl]}

        slots = {}
        for name, row in zip(("top", "bot"), rows):
            for side, i in zip(("L", "R"), sorted(row, key=lambda i: i["center"][0])):
                slots[f"{name}_{side}"] = {
                    "row": name, "center": list(i["center"]), "area": round(i["area"], 1),
                    "bbox": [int(v) for v in i["bbox"]],
                    "contour": i["contour"][:, 0, :].astype(int).tolist()}
        ref = {"cam_id": cam_id, "image_size": [int(w), int(h)],
               "created_at": datetime.now().isoformat(timespec="seconds"), "slots": slots}
        return {"status": OK, "reference": ref}

    def save_reference(self, ref: dict) -> Path:
        self.save_dir.mkdir(parents=True, exist_ok=True)
        p = self.save_dir / f"reference_{ref['cam_id']}.json"
        p.write_text(json.dumps(ref, indent=2), encoding="utf-8")
        return p

    @staticmethod
    def load_reference(path) -> dict:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @staticmethod
    def draw(image: np.ndarray, ref: dict) -> np.ndarray:
        vis = image.copy()
        for name, s in ref["slots"].items():
            cv.polylines(vis, [np.array(s["contour"], np.int32)], True, (0, 255, 0), 2)
            cv.putText(vis, name, (s["bbox"][0], s["bbox"][1] - 8),
                       cv.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        return vis


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    # python frame_alinement.py empty_cam_1.jpg cam_1
    img, cam = cv.imread(sys.argv[1]), sys.argv[2]
    f = FrameINIT()
    res = f.calibrate(img, cam)
    if res["status"] != OK:
        sys.exit(f"FAILED: {res}")
    print("saved:", f.save_reference(res["reference"]))
    cv.imwrite(f"reference_{cam}_debug.png", f.draw(img, res["reference"]))
    for n, s in res["reference"]["slots"].items():
        print(n, s["center"], "area", s["area"])