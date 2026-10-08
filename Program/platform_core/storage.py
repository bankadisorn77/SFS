import os
import cv2 as cv
from datetime import datetime
class ImageStorage:
    def __init__(self, base_save_dir: str):
        self.base_save_dir = base_save_dir

    def save_cycle_image(self, frame, cycle_id: str, camera_id: str) -> str:
        save_dir = os.path.join(self.base_save_dir, f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{cycle_id}")
        os.makedirs(save_dir, exist_ok=True)
        file_path = os.path.join(save_dir, f"{camera_id}.jpg")
        cv.imwrite(file_path, frame)
        return file_path