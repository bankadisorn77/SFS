import cv2 as cv
import numpy as np
import logging
logger = logging.getLogger(__name__)
class FrameINIT:

    def __init__(self, config=None):
        self.config = config or {}
        self.lower_blue = np.array([95, 55, 60])
        self.upper_blue = np.array([135, 255, 255])

    def detector(self, image: np.ndarray, cam_id):
        if image is None or image.size == 0:
            logger.error("Image is None or empty")
            return None, None

        hsv = cv.cvtColor(image, cv.COLOR_BGR2HSV)
        color_mask = cv.inRange(hsv, self.lower_blue, self.upper_blue)
        
        kernel_clean = cv.getStructuringElement(cv.MORPH_ELLIPSE, (7, 7))
        opening = cv.morphologyEx(color_mask, cv.MORPH_OPEN, kernel_clean)

        kernel_fill = cv.getStructuringElement(cv.MORPH_RECT, (9, 9))
        closed = cv.morphologyEx(opening, cv.MORPH_CLOSE, kernel_fill)
        
        valid_contours, center = self.find_area(closed)
        
        if len(center) != 2:
            logger.warning(f"Expected 2 hole centers, but detected {len(center)}")
            return None, center

        angle_deg = self.calculator(center)
        
        result ={
            "angle_deg": angle_deg,
            "center":center
        }
        # self.saveJSON(data=result,cam_id=cam_id)
        return result

    def find_area(self, img: np.ndarray):
        contours, _ = cv.findContours(img, cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE)
        h, w = img.shape[:2]
        selected_mask = np.zeros((h, w), dtype=np.uint8)
        valid_contours = []
        center = []

        # print("\n--- Contour Search ROI ---")
        for cnt in contours:
            area = cv.contourArea(cnt)
            bx, by, bw, bh = cv.boundingRect(cnt)
            aspect_ratio = float(bw) / bh if bh > 0 else 0

            # print(f"Area: {area:7.1f} | Width: {bw:3d} | Height: {bh:3d} | Ratio(W/H): {aspect_ratio:.2f}")

            if 1000 < area <= 3500:
                valid_contours.append(cnt)
                cv.drawContours(selected_mask, [cnt], -1, 255, thickness=cv.FILLED)

                cx = bx + bw // 2
                cy = by + bh // 2
                center.append((cx, cy))
        center = sorted(center, key=lambda pt: pt[0])
        return valid_contours, center

    def calculator(self, point: list):
        if len(point) < 2:
            return None

        x1, y1 = point[0]
        x2, y2 = point[1]
        
        if x1 > x2:
            x1, y1, x2, y2 = x2, y2, x1, y1

        dy = y2 - y1
        dx = x2 - x1

        # np.arctan2 จัดการกรณี dx == 0 ให้ได้อัตโนมัติ (ไม่พึ่งพา m = dy / dx)
        angle_rad = np.arctan2(dy, dx)
        angle_deg = float(np.degrees(angle_rad))

        slope_str = f"{dy / dx:.4f}" if dx != 0 else "Vertical"
        # print(f"Slope (m): {slope_str}")
        # print(f"Angle: {angle_deg:.2f} Deg")
        return angle_deg

    def saveJSON(self, data: dict, cam_id: str = "cam_1") -> bool:
        if not data or not self.config:
            return False

        angle = data.get("angle_deg") if "angle_deg" in data else data.get("Angle")
        centers = data.get("center", [])

        x1, y1, x2, y2 = None, None, None, None

        if isinstance(centers, (list, tuple)) and len(centers) >= 2:
            pt1, pt2 = centers[0], centers[1]
            if isinstance(pt1, (list, tuple)) and len(pt1) >= 2:
                x1, y1 = int(pt1[0]), int(pt1[1])
            if isinstance(pt2, (list, tuple)) and len(pt2) >= 2:
                x2, y2 = int(pt2[0]), int(pt2[1])
        else:
            x1 = data.get("x1")
            y1 = data.get("y1")
            x2 = data.get("x2")
            y2 = data.get("y2")

        if any(v is None for v in (x1, y1, x2, y2, angle)):
            print(f"[Warning] [{cam_id}] Invalid data for referenceLine, skipping save.")
            return False

        ref_line_payload = {
            "Angle": round(float(angle), 2),
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
        }

        try:
            return self.config.update({"referenceLine": {cam_id: ref_line_payload}})
        except Exception as e:
            print(f"[Error] Failed to update config: {e}")
            return False

if __name__ == "__main__":
    from config import Config
    c = Config()
    d = FrameINIT(c)
    img = cv.imread('/home/utac/Project/SFS/image.png')
    result = d.detector(img,'cam_2')
    print("Result (Angle, Centers):", result)
    # d.saveJSON(result)