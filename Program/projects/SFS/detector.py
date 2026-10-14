import logging

import numpy as np
import cv2 as cv

logger = logging.getLogger(__name__)


class Detector:
    def __init__(self, color_ranges=None, spec=None):
        self.color_ranges = color_ranges or [
            (np.array([95, 55, 60]), np.array([135, 255, 255]))
        ]
        self.spec = spec or {
            "max_angle": 7.5,
            "max_mid_y": 248,
            "min_mid_x": 540,
            "max_mid_x": 690,
        }

    def detect_frame(self, img: np.ndarray):
        img = cv.imread('/home/utac/Project/SFS-main/e9.bmp')
        if img is None:
            return None, {"status": "NO_FRAME"}
        blur = cv.GaussianBlur(img, (5, 5), 0)
        hsv = cv.cvtColor(blur, cv.COLOR_BGR2HSV)

        combined_mask = np.zeros(img.shape[:2], dtype=np.uint8)
        for lower, upper in self.color_ranges:
            sub_mask = cv.inRange(hsv, np.array(lower), np.array(upper))
            combined_mask = cv.bitwise_or(combined_mask, sub_mask)

        kernal_clean = cv.getStructuringElement(cv.MORPH_ELLIPSE, (7, 7))
        opening = cv.morphologyEx(combined_mask, cv.MORPH_OPEN, kernal_clean)

        kernal_fill = cv.getStructuringElement(cv.MORPH_RECT, (9, 9))
        closing = cv.morphologyEx(opening, cv.MORPH_CLOSE, kernal_fill)

        contours, _ = cv.findContours(closing, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
        filled_contours = []
        filled_mask = np.zeros_like(closing, dtype=np.uint8)

        for cnt in contours:
            hull = cv.convexHull(cnt)
            filled_contours.append(hull)
            cv.drawContours(filled_mask, [hull], -1, 255, thickness=cv.FILLED)

        get_trapezoid_holes,_ = self.get_trapezoid_holes(filled_mask)


        contours, _ = cv.findContours(get_trapezoid_holes, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)

        return self._process_contours(img, contours)

    def _process_contours(self, img, valid_contours):
        valid_contours = sorted(valid_contours, key=lambda c: np.min(c[:, :, 0]))
        top_edge_points = []
        for cnt in valid_contours:
            min_y = np.min(cnt[:, :, 1])
            for pt in cnt:
                x, y = pt[0]
                if abs(y - min_y) <= 2:
                    top_edge_points.append([x, y])

        top_edge_points = np.array(top_edge_points, dtype=np.int32)
        if len(top_edge_points) <= 5:
            return img, {"status": "FAIL_POINTS"}

        line_params = cv.fitLine(top_edge_points, cv.DIST_L2, 0, 0.01, 0.01)
        vx, vy, x0, y0 = float(line_params[0][0]), float(line_params[1][0]), float(line_params[2][0]), float(line_params[3][0])
        angle_deg = float(np.degrees(np.arctan2(vy, vx)))

        left_hole_x = int(np.mean(valid_contours[0][:, :, 0]))
        left_hole_y = int(np.mean(valid_contours[0][:, :, 1]))
        right_hole_x = int(np.mean(valid_contours[1][:, :, 0]))
        right_hole_y = int(np.mean(valid_contours[1][:, :, 1]))

        mid_x = int((left_hole_x + right_hole_x) / 2)
        if abs(vx) > 1e-6:
            mid_y = int(((mid_x - x0) * vy / vx) + y0)
        else:
            mid_y = int((left_hole_y + right_hole_y) / 2)

        info = {
            "status": "OK",
            "angle_deg": angle_deg,
            "mid_x": mid_x,
            "mid_y": mid_y,
            "left_hole": (left_hole_x, left_hole_y),
            "right_hole": (right_hole_x, right_hole_y),
        }
        return img, info

    def get_trapezoid_holes(self, filled_hull_mask):

        contours, _ = cv.findContours(filled_hull_mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
        
        selected_mask = np.zeros_like(filled_hull_mask)
        valid_contours = []

        for cnt in contours:
            area = cv.contourArea(cnt)
            bx, by, bw, bh = cv.boundingRect(cnt)
            if bw == 0 or bh == 0:
                continue

            extent = area / (bw * bh)  
            approx = cv.approxPolyDP(cnt, 0.035 * cv.arcLength(cnt, True), True)

            if 4000 < area <= 5500 and 4 <= len(approx) <= 5 and extent < 0.88:
                valid_contours.append(cnt)
                cv.drawContours(selected_mask, [cnt], -1, 255, -1)

        valid_contours = sorted(valid_contours, key=lambda c: np.min(c[:, :, 0]))
        return selected_mask, valid_contours


if __name__ == "__main__":
    detector = Detector()
    img = cv.imread("/home/utac/Desktop/SFS/BG/e9.bmp")
    if img is None:
        logger.error("Cannot read image.")
        exit(1)
    img, info = detector.detect_frame(img)
    logger.info("%s", info)
    cv.imshow("Detected Image", img)
    cv.waitKey(0)
    cv.destroyAllWindows()
