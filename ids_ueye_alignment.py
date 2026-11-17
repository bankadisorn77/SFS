import ctypes
import cv2
import numpy as np
from pyueye import ueye

# พิกัดและสัดส่วนคงที่อ้างอิงจาก Canva 960 x 540 px
CANVA_H = 540.0

# 1. เส้นแนวนอนอ้างอิงจาก Canva ที่ Y = 221.1 px
Y_LINE_RATIO = 221.1 / CANVA_H       # ~0.4094

# 2. เส้นแนวตั้งแบ่งครึ่งซ้าย-ขวาตรงกลาง (50%)
X_CENTER_RATIO = 0.5

WINDOW_NAME = "IDS uEye Alignment"

def get_connected_cameras():
    num_cameras = ueye.INT()
    ueye.is_GetNumberOfCameras(num_cameras)
    count = num_cameras.value
    if count == 0:
        return []

    class UEYE_CAMERA_LIST(ctypes.Structure):
        _fields_ = [
            ('dwCount', ueye.DWORD),
            ('uci', ueye.UEYE_CAMERA_INFO * count)
        ]

    cam_list = UEYE_CAMERA_LIST()
    cam_list.dwCount = count
    cams = []
    
    if ueye.is_GetCameraList(cam_list) == ueye.IS_SUCCESS:
        for i in range(count):
            info = cam_list.uci[i]
            cams.append({
                "id": int(info.dwCameraID),
                "ser_no": info.SerNo.decode('utf-8', errors='ignore').strip(),
                "model": info.Model.decode('utf-8', errors='ignore').strip()
            })
    return cams

def main():
    cameras = get_connected_cameras()
    if not cameras:
        print("[ERROR] ไม่พบกล้อง IDS uEye ที่เชื่อมต่อกับระบบ")
        return

    print("========================================")
    print("      รายการกล้อง IDS uEye ที่ตรวจพบ      ")
    print("========================================")
    for idx, cam in enumerate(cameras):
        print(f"[{idx}] Model: {cam['model']:<10} | CamID: {cam['id']:<3} | S/N: {cam['ser_no']}")
    print("========================================")

    selected_idx = 0
    if len(cameras) > 1:
        while True:
            try:
                val = input(f"เลือกหมายเลขกล้อง (0-{len(cameras)-1}) [กด Enter เลือก 0]: ")
                if not val.strip():
                    selected_idx = 0
                    break
                selected_idx = int(val)
                if 0 <= selected_idx < len(cameras):
                    break
                print("หมายเลขไม่ถูกต้อง กรุณาเลือกใหม่")
            except ValueError:
                print("กรุณากรอกตัวเลข")

    target_cam_id = cameras[selected_idx]["id"]
    hCam = ueye.HIDS(target_cam_id)
    nRet = ueye.is_InitCamera(hCam, None)
    if nRet != ueye.IS_SUCCESS:
        print(f"[ERROR] ไม่สามารถเปิดกล้อง ID: {target_cam_id} ได้ (Error Code: {nRet})")
        return

    ueye.is_SetColorMode(hCam, ueye.IS_CM_BGR8_PACKED)

    rectAOI = ueye.IS_RECT()
    ueye.is_AOI(hCam, ueye.IS_AOI_IMAGE_GET_AOI, rectAOI, ueye.sizeof(rectAOI))
    width = rectAOI.s32Width.value
    height = rectAOI.s32Height.value

    # คำนวณตำแหน่งเส้น
    ly = int(Y_LINE_RATIO * height)       # ระดับเส้นแนวนอน
    lx = int(X_CENTER_RATIO * width)      # กึ่งกลางเส้นแนวตั้ง (แบ่งซ้าย-ขวา)

    # จัดสรร Image Buffer
    pcImageMemory = ueye.c_mem_p()
    MemId = ueye.int()
    nBitsPerPixel = ueye.INT(24)

    ueye.is_AllocImageMem(hCam, width, height, nBitsPerPixel, pcImageMemory, MemId)
    ueye.is_SetImageMem(hCam, pcImageMemory, MemId)
    ueye.is_CaptureVideo(hCam, ueye.IS_DONT_WAIT)

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, 960, 600)

    print(f"\n[SUCCESS] เริ่มการแสดงผลกล้อง ID: {target_cam_id}")
    print(f"เส้นแนวนอน: Y={ly}")
    print(f"เส้นแนวตั้ง (แบ่งครึ่ง): X={lx}")
    print("- กด 'q' หรือ 'ESC' เพื่อปิดโปรแกรม")

    try:
        while True:
            array = ueye.get_data(pcImageMemory, width, height, nBitsPerPixel.value, width * 3, copy=False)
            frame = np.reshape(array, (height, width, 3)).copy()

            # 1. วาดเส้นแนวนอน (ซ้ายสุดไปขวาสุด)
            cv2.line(frame, (0, ly), (width, ly), (0, 255, 0), thickness=1)

            # 2. วาดเส้นแนวตั้งแบ่งครึ่งซ้าย-ขวา (บนสุดลงล่างสุด)
            cv2.line(frame, (lx, 0), (lx, height), (0, 255, 0), thickness=1)

            cv2.imshow(WINDOW_NAME, frame)

            key = cv2.waitKey(1) & 0xFF
            if key in [ord('q'), 27]:
                break
    finally:
        ueye.is_StopLiveVideo(hCam, ueye.IS_FORCE_VIDEO_STOP)
        ueye.is_FreeImageMem(hCam, pcImageMemory, MemId)
        ueye.is_ExitCamera(hCam)
        cv2.destroyAllWindows()
        print("\n[INFO] ปิดกล้องเรียบร้อย")

if __name__ == "__main__":
    main()