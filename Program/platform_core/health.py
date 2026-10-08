from __future__ import annotations
import threading
import time
import logging
from typing import TYPE_CHECKING, Dict, Any, Callable, Optional

if TYPE_CHECKING:
    from .camera_manager import CameraManager
    from .io_manager import BaseIOManager

logger = logging.getLogger("HealthMonitor")

class HealthMonitor:
    def __init__(
        self,
        camera_manager: Optional["CameraManager"] = None,
        io_manager: Optional["BaseIOManager"] = None,
        check_interval: float = 3.0,
        on_status_change: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.camera_manager = camera_manager
        self.io_manager = io_manager
        
        self.check_interval = check_interval
        self.on_status_change = on_status_change
        
        self._monitors: Dict[str, Callable[[], bool]] = {}
        
        self._current_status: Dict[str, bool] = {}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def register_check(self, name: str, check_func: Callable[[], bool]):
        self._monitors[name] = check_func
        self._current_status[name] = True

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="HealthMonitorThread")
        self._thread.start()
        logger.info("HealthMonitor background service started.")

    def stop(self, timeout: float = 2.0):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("HealthMonitor background service stopped.")

    def check_now(self) -> Dict[str, bool]:
        results = {}
        for name, func in self._monitors.items():
            try:
                results[name] = bool(func())
            except Exception as exc:
                logger.error(f"Error checking health for '{name}': {exc}")
                results[name] = False
        return results

    def is_healthy(self) -> bool:
        def ok(v):
            return all(v.values()) if isinstance(v, dict) else bool(v)
        return all(ok(v) for v in self._current_status.values()) if self._current_status else True

    @property
    def status(self) -> Dict[str, Any]:
        return {
            "healthy": self.is_healthy(),
            "details": dict(self._current_status),
            "timestamp": time.time(),
        }

    def _publish(self, new_status):
        if new_status == self._current_status:
            return
        self._current_status = new_status
        logger.warning(f"System health state changed: {self._current_status}")
        if self.on_status_change:
            try:
                self.on_status_change(self.status)
            except Exception as e:
                logger.error(f"Error executing on_status_change callback: {e}")

    def _run_loop(self):
        while not self._stop_event.is_set():
            gpio_alive = self.io_manager.is_connected() if self.io_manager else False

            cam_statuses = {}
            if self.camera_manager:
                for cam_id, cam in self.camera_manager.cameras.items():
                    cam_statuses[cam_id] = cam.is_connected() if cam else False

            self._publish({"camera": dict(cam_statuses), "gpio": gpio_alive})

            if self.io_manager and not gpio_alive:
                logger.warning("[HealthMonitor] GPIO disconnected! Triggering auto-reconnect...")
                gpio_alive = self.io_manager.connect(max_retries=1)
                self._publish({"camera": dict(cam_statuses), "gpio": gpio_alive})

            if self.camera_manager:
                for cam_id, cam in self.camera_manager.cameras.items():
                    if cam_statuses.get(cam_id):
                        continue
                    logger.warning(f"[HealthMonitor] Camera '{cam_id}' disconnected! Attempting auto-reconnect...")
                    try:
                        cam.disconnect()
                        is_alive = cam.connection(
                            getattr(cam, "req_w", 1920),
                            getattr(cam, "req_h", 1080),
                            getattr(cam, "target_fps", 15),
                        )
                    except Exception as e:
                        logger.error(f"[HealthMonitor] Reconnect camera {cam_id} error: {e}")
                        is_alive = False
                    cam_statuses[cam_id] = is_alive
                    self._publish({"camera": dict(cam_statuses), "gpio": gpio_alive})

            if self._stop_event.wait(self.check_interval):
                break