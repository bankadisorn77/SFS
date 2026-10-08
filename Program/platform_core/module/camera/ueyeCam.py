# ProcessClass/ueyeCam.py
import cv2 as cv
import numpy as np
from pyueye import ueye
import sys
import threading
import time
import weakref

class UeyeCamera:
    _capture_lock = threading.Lock()
    _instances = weakref.WeakSet()
    SAFETY_PROBE_S = 60.0
    ACTIVE_PROBE_S = 2.0

    def __init__(self, camera_id: int = 0):
        UeyeCamera._instances.add(self)
        self.camera_id = camera_id
        self.hCam = ueye.HIDS(0)
        self.sInfo = ueye.SENSORINFO()
        self.cInfo = ueye.CAMINFO()
        self.pcImageMemory = ueye.c_mem_p()
        self.MemID = ueye.int(0)
        self.rectAOI = ueye.IS_RECT()

        self.pitch = ueye.INT(0)
        self.nBitsPerPixel_ctypes = ueye.INT(0)
        self.width = 0
        self.height = 0
        self.target_fps = 15
        self.req_w = 1920
        self.req_h = 1080
        self.nBitsPerPixel_py = 0
        self.bytes_per_pixel = 0

        self.m_nColorMode = ueye.INT(0)
        self.hCamConnected = False
        self.x = 0
        self.y = 0

        self.latest_frame = None
        self.frame_lock = threading.Lock()
        self.is_streaming = False
        self.stream_thread = None

        self.io_lock = threading.RLock()
        self._last_seq = None
        self._last_seq_time = time.time()
        self._seq_changed = False
        self._last_probe_time = 0.0
        self._last_probe_ok = True
        self._probe_fails = 0

    def connection(self, target_width: int, target_height: int, target_fps: int = 15) -> bool:
        with self.io_lock:
            ok = self._connection_impl(target_width, target_height, target_fps)
            self._last_probe_time = time.time()
            self._last_probe_ok = True
            self._probe_fails = 0
            return ok

    def _connection_impl(self, target_width: int, target_height: int, target_fps: int = 15) -> bool:
        try:
            self.target_fps = target_fps
            self.req_w = target_width
            self.req_h = target_height
            self.disconnect()

            # device_id_flag = self.camera_id | 0x8000
            device_id_flag = self.camera_id 
            self.hCam = ueye.HIDS(device_id_flag)
            print(f"[CAM {self.camera_id}] Initializing camera ID {self.hCam.value}...")

            if ueye.is_InitCamera(self.hCam, None) != ueye.IS_SUCCESS:
                print(f"[CAM {self.camera_id}] Init failed.")
                self.hCamConnected = False
                return False

            self.hCamConnected = True
            ueye.is_GetCameraInfo(self.hCam, self.cInfo)
            ueye.is_GetSensorInfo(self.hCam, self.sInfo)
            ueye.is_ResetToDefault(self.hCam)
            ueye.is_SetDisplayMode(self.hCam, ueye.IS_SET_DM_DIB)

            self._determine_color_mode()
            ueye.is_SetColorMode(self.hCam, self.m_nColorMode)

            w, h = self._set_aoi(self.x, self.y, target_width, target_height)
            self.width, self.height = w, h

            if ueye.is_AllocImageMem(self.hCam, self.width, self.height, self.nBitsPerPixel_ctypes, self.pcImageMemory, self.MemID) != ueye.IS_SUCCESS:
                return False

            ueye.is_SetImageMem(self.hCam, self.pcImageMemory, self.MemID)

            inquired_w, inquired_h, inquired_bits = ueye.INT(0), ueye.INT(0), ueye.INT(0)
            if ueye.is_InquireImageMem(self.hCam, self.pcImageMemory, self.MemID, inquired_w, inquired_h, inquired_bits, self.pitch) == ueye.IS_SUCCESS:
                self.width = inquired_w.value
                self.height = inquired_h.value
                self.nBitsPerPixel_py = inquired_bits.value
                self.bytes_per_pixel = int(self.nBitsPerPixel_py / 8)

            ueye.is_SetExternalTrigger(self.hCam, ueye.IS_SET_TRIGGER_OFF)
            print(f"[CAM {self.camera_id}] Standby ready (Trigger/Snap Mode).")
            return True
        except Exception as e:
            print(f"[CAM {self.camera_id}] Connection error: {e}")
            self.disconnect()
            return False

    def snap_frame(self, timeout_ms: int = 1000):
        if not self.hCamConnected or self.is_streaming:
            return None
        with self.io_lock:
            for attempt in range(3):
                with UeyeCamera._capture_lock:
                    ret = ueye.is_FreezeVideo(self.hCam, timeout_ms)
                if ret == ueye.IS_SUCCESS:
                    return self._extract_frame()
                print(f"[CAM {self.camera_id}] FreezeVideo failed with code {ret} (attempt {attempt + 1}/3)")
                time.sleep(0.1)
            self.hCamConnected = False
            return None

    def start_live(self) -> bool:
        if not self.hCamConnected or self.is_streaming:
            return False
        
        actual_fps = ueye.double(0.0)
        ueye.is_SetFrameRate(self.hCam, ueye.double(self.target_fps), actual_fps)

        self._last_seq = None
        self._last_seq_time = time.time()
        self._seq_changed = False
        
        if ueye.is_CaptureVideo(self.hCam, ueye.IS_DONT_WAIT) != ueye.IS_SUCCESS:
            return False

        self.is_streaming = True
        self.stream_thread = threading.Thread(target=self._live_worker, daemon=True)
        self.stream_thread.start()
        print(f"[CAM {self.camera_id}] Live Alignment Video Started.")
        return True

    def stop_live(self):
        self.is_streaming = False
        if self.stream_thread and self.stream_thread.is_alive():
            self.stream_thread.join(timeout=1.0)
        if self.hCamConnected and self.hCam.value != 0:
            ueye.is_StopLiveVideo(self.hCam, ueye.IS_WAIT)
        print(f"[CAM {self.camera_id}] Live Video Stopped.")

    def _live_worker(self):
        delay = 1.0 / max(1, self.target_fps)
        while self.is_streaming and self.hCamConnected:
            frame = self._extract_frame()
            if frame is not None:
                with self.frame_lock:
                    self.latest_frame = frame
            time.sleep(delay)

    def _extract_frame(self):
        try:
            array = ueye.get_data(self.pcImageMemory, self.width, self.height, self.nBitsPerPixel_py, self.pitch.value, copy=False)
            if array is None or array.size == 0:
                return None
            if self.bytes_per_pixel == 3:
                return np.reshape(array, (self.height, self.width, 3)).copy()
            elif self.bytes_per_pixel == 1:
                gray = np.reshape(array, (self.height, self.width))
                return cv.cvtColor(gray, cv.COLOR_GRAY2BGR)
            return None
        except Exception:
            return None

    def get_latest_frame(self):
        with self.frame_lock:
            return self.latest_frame.copy() if self.latest_frame is not None else None

    def is_connected(self) -> bool:
        if not self.hCamConnected or self.hCam.value == 0:
            return False

        num_cams = ueye.INT()
        count_ret = ueye.is_GetNumberOfCameras(num_cams)
        if count_ret == ueye.IS_SUCCESS and num_cams.value == 0:
            self.hCamConnected = False
            return False
        expected = sum(1 for cam in list(UeyeCamera._instances) if cam.hCamConnected)
        count_ok = count_ret == ueye.IS_SUCCESS and num_cams.value >= expected

        if self.is_streaming:
            seq = ueye.is_CameraStatus(self.hCam, ueye.IS_SEQUENCE_CNT, ueye.IS_GET_STATUS)
            now = time.time()
            if seq != self._last_seq:
                if self._last_seq is not None:
                    self._seq_changed = True
                self._last_seq = seq
                self._last_seq_time = now
            elif self._seq_changed and now - self._last_seq_time > 2.0:
                print(f"[CAM {self.camera_id}] No new frames -> disconnected")
                self.hCamConnected = False
                return False
            return True

        if not self.io_lock.acquire(timeout=0.2):
            return True
        try:
            now = time.time()
            interval = UeyeCamera.SAFETY_PROBE_S if count_ok else UeyeCamera.ACTIVE_PROBE_S
            if now - self._last_probe_time < interval:
                return self._last_probe_ok
            if not UeyeCamera._capture_lock.acquire(timeout=0.2):
                return True
            try:
                ret = ueye.is_FreezeVideo(self.hCam, 500)
            finally:
                UeyeCamera._capture_lock.release()
            self._last_probe_time = time.time()
            if ret != ueye.IS_SUCCESS:
                self._probe_fails += 1
                limit = 2 if ret == -1 else 5
                print(f"[CAM {self.camera_id}] probe failed ret={ret} ({self._probe_fails}/{limit})")
                if self._probe_fails >= limit:
                    self.hCamConnected = False
                    self._last_probe_ok = False
                    return False
                return True
            self._probe_fails = 0
            self._last_probe_ok = True
            return True
        finally:
            self.io_lock.release()

    def _determine_color_mode(self):
        if int.from_bytes(self.sInfo.nColorMode.value, byteorder=sys.byteorder) == ueye.IS_COLORMODE_CBYCRY:
            self.m_nColorMode.value = ueye.IS_CM_BGR8_PACKED
            self.nBitsPerPixel_ctypes.value = 24
            self.bytes_per_pixel = 3
        else:
            self.m_nColorMode.value = ueye.IS_CM_MONO8
            self.nBitsPerPixel_ctypes.value = 8
            self.bytes_per_pixel = 1

    def _set_aoi(self, x, y, w, h):
        self.rectAOI.s32X = ueye.int(x)
        self.rectAOI.s32Y = ueye.int(y)
        self.rectAOI.s32Width = ueye.int(w)
        self.rectAOI.s32Height = ueye.int(h)
        ueye.is_AOI(self.hCam, ueye.IS_AOI_IMAGE_SET_AOI, self.rectAOI, ueye.sizeof(self.rectAOI))
        ueye.is_AOI(self.hCam, ueye.IS_AOI_IMAGE_GET_AOI, self.rectAOI, ueye.sizeof(self.rectAOI))
        return self.rectAOI.s32Width.value, self.rectAOI.s32Height.value

    def disconnect(self):
        with self.io_lock:
            self._disconnect_impl()

    def _disconnect_impl(self):
        self.stop_live()
        if self.hCamConnected or (hasattr(self, "hCam") and self.hCam.value != 0):
            try:
                if self.pcImageMemory and self.MemID.value != 0:
                    ueye.is_FreeImageMem(self.hCam, self.pcImageMemory, self.MemID)
            except Exception:
                pass
            self.pcImageMemory = ueye.c_mem_p()
            self.MemID = ueye.int(0)
            try:
                ueye.is_ExitCamera(self.hCam)
            except Exception:
                pass
            self.hCamConnected = False
            self.hCam = ueye.HIDS(0)