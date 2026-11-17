from pathlib import Path
import cv2
import numpy as np

DATASET_DIR = Path("data_split")
TARGET_SIZE = 224  # หรือ 320 ตามที่ตั้งใน train.py
PAD_COLOR = (114, 114, 114)  # สีเทามาตรฐานของ YOLO

valid_exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def letterbox_square(img, target_size=224, color=(114, 114, 114)):
    h, w = img.shape[:2]
    scale = target_size / max(h, w)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))

    # ย่อภาพตามสัดส่วนจริง
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    # สร้างพื้นหลังสี่เหลี่ยมจัตุรัสและวางภาพไว้ตรงกลาง
    canvas = np.full((target_size, target_size, 3), color, dtype=np.uint8)
    top = (target_size - new_h) // 2
    left = (target_size - new_w) // 2
    canvas[top : top + new_h, left : left + new_w] = resized
    return canvas


for img_path in DATASET_DIR.rglob("*"):
    if img_path.suffix.lower() in valid_exts:
        img = cv2.imread(str(img_path))
        if img is not None:
            padded = letterbox_square(img, TARGET_SIZE, PAD_COLOR)
            cv2.imwrite(str(img_path), padded)

print("✅ แปลงภาพทั้งหมดเป็นสี่เหลี่ยมจัตุรัสแบบไม่ตัดขอบเรียบร้อย!")