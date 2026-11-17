import logging
from dataclasses import dataclass, field, replace
from typing import List, Optional, Sequence, Tuple

import numpy as np
import cv2 as cv

logger = logging.getLogger(__name__)


OK = "OK"                              
NO_FRAME = "NO_FRAME"                 
NOT_ENOUGH_HOLES = "CAN NOT DETEC"  
FAIL_POINTS = "FAIL_POINTS"            
ERROR = "ERROR"                        


@dataclass
class DetectorConfig:

    # --- 1. สีของรู (HSV แบบ OpenCV: H 0-180, S/V 0-255) ---
    # ใส่ได้หลายช่วง ระบบจะ OR รวมกัน
    color_ranges: List[Tuple[Sequence[int], Sequence[int]]] = field(
        default_factory=lambda: [((95, 55, 60), (135, 255, 255))]
    )

    # --- 2. เตรียมภาพ (ขนาด kernel เป็นเลขคี่) ---
    blur_ksize: int = 5     # Gaussian blur ก่อนแปลง HSV
    open_ksize: int = 7     # ลบจุด noise เล็กๆ
    close_ksize: int = 9    # อุดช่องว่างในรู

    # --- 3. เงื่อนไขว่า contour ไหนคือ "รู" ---
    hole_area_min: float = 4000     # พื้นที่ต้อง >  ค่านี้ (px)
    hole_area_max: float = 5500     # พื้นที่ต้อง <= ค่านี้ (px)
    hole_min_vertices: int = 4      # จำนวนมุมหลังประมาณรูปทรง
    hole_max_vertices: int = 5
    hole_max_extent: float = 0.88   # area / bounding-box ต้อง < ค่านี้ (ตัดสี่เหลี่ยมเต็ม)
    approx_epsilon: float = 0.035   # ความหยาบของการประมาณรูปทรง (สัดส่วนของเส้นรอบรูป)
    expected_holes: int = 2         # จำนวนรูที่ต้องเจอ

    # --- 4. การวัดมุม ---
    top_edge_tolerance: int = 2     # จุดที่สูงกว่า min_y ไม่เกินกี่ px ถือเป็นขอบบน
    min_edge_points: int = 6        # ต้องมีจุดขอบบนอย่างน้อยกี่จุดถึง fit เส้นได้

    # --- 5. ดีบัก ---
    # ใส่ path ภาพเพื่อให้ detect_frame ใช้ภาพนี้แทนภาพจากกล้อง (ใช้ทดสอบเท่านั้น)
    # ใน production ต้องเป็น None
    debug_image_path: Optional[str] = None


class Detector:
    def __init__(self, config: Optional[DetectorConfig] = None,
                 color_ranges=None, spec=None):
        # color_ranges / spec คงไว้เพื่อให้โค้ดเก่าที่เรียก Detector(...) ไม่พัง
        # (spec เดิมไม่เคยถูกใช้ ตรวจ spec อยู่ที่ logic.py)
        self.cfg = replace(config) if config else DetectorConfig()
        if color_ranges is not None:
            self.cfg.color_ranges = color_ranges

    # ------------------------------------------------------------------
    # จุดเข้าใช้งานหลัก
    # ------------------------------------------------------------------
    def detect_frame(self, img: np.ndarray):
        try:
            if self.cfg.debug_image_path:
                img = cv.imread(self.cfg.debug_image_path)
            if img is None or getattr(img, "size", 0) == 0:
                return img, {"status": NO_FRAME}

            holes = self.find_holes(img)
            return img, self._measure(holes)
        except Exception as exc:  
            logger.exception("Detector crashed")
            return img, {"status": ERROR, "error": f"{type(exc).__name__}: {exc}"}

    def find_holes(self, img: np.ndarray) -> list:
        """คืน contour ของรูที่เจอ เรียงจากซ้ายไปขวา (ใช้ดีบักแยกจากการวัดได้)"""
        mask = self._color_mask(img)
        mask = self._clean_mask(mask)
        hull_mask = self._fill_hulls(mask)
        return self._select_holes(hull_mask)

    # ------------------------------------------------------------------
    # ขั้นที่ 1-3: ทำ mask
    # ------------------------------------------------------------------
    def _color_mask(self, img: np.ndarray) -> np.ndarray:
        k = self.cfg.blur_ksize
        hsv = cv.cvtColor(cv.GaussianBlur(img, (k, k), 0), cv.COLOR_BGR2HSV)

        mask = np.zeros(img.shape[:2], dtype=np.uint8)
        for lower, upper in self.cfg.color_ranges:
            mask = cv.bitwise_or(mask, cv.inRange(hsv, np.array(lower), np.array(upper)))
        return mask

    def _clean_mask(self, mask: np.ndarray) -> np.ndarray:
        o, c = self.cfg.open_ksize, self.cfg.close_ksize
        opened = cv.morphologyEx(
            mask, cv.MORPH_OPEN, cv.getStructuringElement(cv.MORPH_ELLIPSE, (o, o)))
        return cv.morphologyEx(
            opened, cv.MORPH_CLOSE, cv.getStructuringElement(cv.MORPH_RECT, (c, c)))

    @staticmethod
    def _fill_hulls(mask: np.ndarray) -> np.ndarray:
        contours, _ = cv.findContours(mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
        filled = np.zeros_like(mask, dtype=np.uint8)
        for cnt in contours:
            cv.drawContours(filled, [cv.convexHull(cnt)], -1, 255, thickness=cv.FILLED)
        return filled

    # ------------------------------------------------------------------
    # ขั้นที่ 4: คัดเลือกรู
    # ------------------------------------------------------------------
    def _is_hole(self, cnt: np.ndarray) -> bool:

        cfg = self.cfg
        area = cv.contourArea(cnt)
        _, _, bw, bh = cv.boundingRect(cnt)
        if bw == 0 or bh == 0:
            return False

        extent = area / (bw * bh)
        approx = cv.approxPolyDP(cnt, cfg.approx_epsilon * cv.arcLength(cnt, True), True)

        return (
            cfg.hole_area_min < area <= cfg.hole_area_max
            and cfg.hole_min_vertices <= len(approx) <= cfg.hole_max_vertices
            and extent < cfg.hole_max_extent
        )

    def _select_holes(self, hull_mask: np.ndarray) -> list:
        contours, _ = cv.findContours(hull_mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
        holes = [c for c in contours if self._is_hole(c)]
        return sorted(holes, key=lambda c: int(np.min(c[:, :, 0])))  # ซ้าย -> ขวา

    # ------------------------------------------------------------------
    # ขั้นที่ 5: วัดค่า
    # ------------------------------------------------------------------
    @staticmethod
    def _center(cnt: np.ndarray) -> Tuple[int, int]:
        return int(np.mean(cnt[:, :, 0])), int(np.mean(cnt[:, :, 1]))

    def _top_edge_points(self, holes: list) -> np.ndarray:
        tol = self.cfg.top_edge_tolerance
        points = []
        for cnt in holes:
            min_y = np.min(cnt[:, :, 1])
            points.extend([int(x), int(y)] for x, y in cnt[:, 0, :] if abs(y - min_y) <= tol)
        return np.array(points, dtype=np.int32)

    def _measure(self, holes: list) -> dict:
        need = self.cfg.expected_holes
        if len(holes) < need:
            logger.warning("Found %d hole(s), expected %d.", len(holes), need)
            return {"status": NOT_ENOUGH_HOLES, "holes_found": len(holes),
                    "holes_expected": need}
        if len(holes) > need:
            logger.warning("Found %d holes, expected %d; using the leftmost %d.",
                           len(holes), need, need)
        holes = holes[:need]  # เรียงซ้าย->ขวาแล้ว: ใช้ n รูทางซ้ายสุด

        points = self._top_edge_points(holes)
        if len(points) < self.cfg.min_edge_points:
            return {"status": FAIL_POINTS, "holes_found": len(holes),
                    "edge_points": int(len(points))}

        vx, vy, x0, y0 = (float(v[0]) for v in cv.fitLine(points, cv.DIST_L2, 0, 0.01, 0.01))
        angle_deg = float(np.degrees(np.arctan2(vy, vx)))

        left_hole = self._center(holes[0])
        right_hole = self._center(holes[-1])

        mid_x = int((left_hole[0] + right_hole[0]) / 2)
        if abs(vx) > 1e-6:
            mid_y = int(((mid_x - x0) * vy / vx) + y0)
        else:
            mid_y = int((left_hole[1] + right_hole[1]) / 2)

        return {
            "status": OK,
            "angle_deg": angle_deg,
            "mid_x": mid_x,
            "mid_y": mid_y,
            "left_hole": left_hole,
            "right_hole": right_hole,
            "holes_found": len(holes),
        }


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    path = sys.argv[1] if len(sys.argv) > 1 else "e9.bmp"
    image = cv.imread(path)
    if image is None:
        logger.error("Cannot read image: %s", path)
        sys.exit(1)
    image, result = Detector().detect_frame(image)
    logger.info("%s", result)
    cv.imshow("Detected Image", image)
    cv.waitKey(0)
    cv.destroyAllWindows()