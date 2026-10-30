import ctypes
import cv2
import numpy as np
from pyueye import ueye

# พิกัดและสัดส่วนคงที่อ้างอิงจาก Canva 960 x 540 px
CANVA_W = 960.0
CANVA_H = 540.0

# อันที่ 1: กรอบสี่เหลี่ยม
X1_RATIO = 299.0 / CANVA_W          # ~0.3115
Y1_RATIO = 128.4 / CANVA_H          # ~0.2378
W1_RATIO = 362.1 / CANVA_W          # ~0.3772
H1_RATIO = 63.6 / CANVA_H           # ~0.1178

# อันที่ 2: เส้นตรงแนวนอนพาดผ่านจอที่ Y = 221.1 px
Y2_RATIO = 221.1 / CANVA_H          # ~0.4094

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

    # คำนวณพิกัดกล่องที่ 1
    bx1 = int(X1_RATIO * width)
    by1 = int(Y1_RATIO * height)
    bw1 = int(W1_RATIO * width)
    bh1 = int(H1_RATIO * height)

    # คำนวณพิกัดแกน Y ของเส้นตรงแนวนอนที่ 2
    ly2 = int(Y2_RATIO * height)

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
    print(f"กล่อง 1: X={bx1}, Y={by1}, W={bw1}, H={bh1}")
    print(f"เส้นแนวนอน 2: Y={ly2} (จาก X: 0 ถึง {width})")
    print("- กด 'q' หรือ 'ESC' เพื่อปิดโปรแกรม")

    try:
        while True:
            array = ueye.get_data(pcImageMemory, width, height, nBitsPerPixel.value, width * 3, copy=False)
            frame = np.reshape(array, (height, width, 3)).copy()

            # วาดกรอบสี่เหลี่ยมอันที่ 1 (สีเขียว เส้นบางสุด)
            cv2.rectangle(frame, (bx1, by1), (bx1 + bw1, by1 + bh1), (0, 255, 0), thickness=1)

            # วาดเส้นตรงแนวนอนอันที่ 2 (สีเขียว เส้นบางสุด ลากซ้ายสุดไปขวาสุด)
            cv2.line(frame, (0, ly2), (width, ly2), (0, 255, 0), thickness=1)

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