import logging
from dataclasses import dataclass, field, replace
from typing import List, Optional, Sequence, Tuple
from ultralytics import YOLO
import numpy as np
import cv2 as cv

logger = logging.getLogger(__name__)

class Detector:
    
    def __init__(self,modelPath=None,config=None):
        self.model = YOLO(modelPath)
        sz = self.model.overrides.get("imgsz", 224)
        if isinstance(sz, (list, tuple)):
            sz = sz[0]
        self.imgsz = int(sz)
        logger.info(f"Detector initialized with model: {modelPath}, image size: {self.imgsz}")


    @staticmethod
    def _letterbox_square(img, target_size=224, color=(114, 114, 114)):
        h, w = img.shape[:2]
        scale = target_size / max(h, w)
        new_w, new_h = int(round(w * scale)), int(round(h * scale))

        resized = cv.resize(img, (new_w, new_h), interpolation=cv.INTER_AREA)

        canvas = np.full((target_size, target_size, 3), color, dtype=np.uint8)
        top = (target_size - new_h) // 2
        left = (target_size - new_w) // 2
        canvas[top : top + new_h, left : left + new_w] = resized

        pad_info = (top, left, new_h, new_w)
        return canvas, pad_info

    def _roi_crop(self, img=None, roi=None):
        if roi is None:
            return img
        h, w = img.shape[:2]
        x_min_ratio, x_max_ratio, y_min_ratio, y_max_ratio = roi
        x_start = int(w * x_min_ratio)
        x_end = int(w * x_max_ratio)
        y_start = int(h * y_min_ratio)
        y_end = int(h * y_max_ratio)
        cropped = img[y_start:y_end, x_start:x_end]
        return cropped

    def _classify(self, img):
        results = self.model.predict(img, verbose=False)
        if not results:
            return None

        res = results[0] 
        probs = res.probs  

        if probs is None:
            return None


        top1_idx = probs.top1  # เช่น 1
        pred_class = res.names[top1_idx] 
        confidence = float(probs.top1conf.item())  


        all_probs = {
            res.names[i]: float(probs.data[i].item()) for i in range(len(res.names))
        }
        return {
            "class_id": top1_idx,
            "class_name": pred_class,
            "confidence": confidence,
            "all_probs": all_probs,
        }

    def detection(self, img, roi=None):
        
        if roi is not None:
            img = self._roi_crop(img, roi)
        img, pad_info = self._letterbox_square(img, self.imgsz)
        box = self._classify(img)
        return box, pad_info


if __name__ == "__main__":
    a = Detector(modelPath="Program/projects/SFS/model/best.pt")
    a.detection(cv.imread("image.png"), roi=(0.25, 0.80, 0.20, 0.55))