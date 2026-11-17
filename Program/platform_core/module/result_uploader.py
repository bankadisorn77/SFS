"""Background uploader: inspection result (+ resized image) -> web /image_log.

Never blocks the inspection loop:
  * submit() only puts a small dict into a bounded queue.
  * Image read/resize/JPEG encode and HTTP happen in this worker thread.
  * When the queue is full (web down / slow), the OLDEST item is dropped.
Uses only the standard library + OpenCV, no extra dependency.
"""
import base64
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

import cv2 as cv

logger = logging.getLogger("ResultUploader")


class ResultUploader:

    def __init__(self, config, max_queue: int = 30, timeout_s: float = 5.0):
        self.config = config
        self.timeout_s = timeout_s
        self._queue: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=max_queue)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.dropped = 0
        self.sent = 0
        self.failed = 0
        self._last_error_log = 0.0

    # ---------------- public ----------------
    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "upload_results", True))

    def start(self):
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ResultUploader")
        self._thread.start()
        logger.info("Result uploader started (queue=%s).", self._queue.maxsize)

    def stop(self, timeout: float = 2.0):
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=timeout)
        self._thread = None

    def submit(self, record: dict, image_path: Optional[str] = None) -> bool:
        """Queue one camera result. Returns False if an old item had to be dropped."""
        if not self.enabled or self._stop.is_set():
            return False
        item = {"record": dict(record), "image_path": str(image_path) if image_path else None}
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 50 == 0:
                logger.warning("Upload queue full: dropped %d result(s) so far.", self.dropped)
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                pass
            return False

    # ---------------- worker ----------------
    def _url(self) -> str:
        return f"http://{self.config.server_url}:{getattr(self.config, 'api_port', 8080)}/image_log"

    def encode_image(self, path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        img = cv.imread(path)
        if img is None:
            return None
        width = int(getattr(self.config, "upload_image_width", 1280))
        h, w = img.shape[:2]
        if width and w > width:
            img = cv.resize(img, (width, int(round(h * width / w))), interpolation=cv.INTER_AREA)
        quality = int(getattr(self.config, "upload_jpeg_quality", 80))
        ok, buf = cv.imencode(".jpg", img, [cv.IMWRITE_JPEG_QUALITY, quality])
        return base64.b64encode(buf.tobytes()).decode("ascii") if ok else None

    def _post(self, payload: dict) -> int:
        api_key = getattr(self.config, "api_key", "") or ""
        req = urllib.request.Request(
            self._url(),
            data=json.dumps(payload, default=str).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-API-Key": api_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code

    def _send(self, item: dict) -> bool:
        payload = dict(item["record"])
        image = self.encode_image(item.get("image_path"))
        if image:
            payload["image"] = image
        status = self._post(payload)
        if status in (401, 403):
            # agent.py may have just (re)registered and saved a new api_key.
            try:
                self.config.load()
            except Exception:
                pass
            status = self._post(payload)
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        return True

    def _run(self):
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            try:
                self._send(item)
                self.sent += 1
            except Exception as e:
                self.failed += 1
                now = time.monotonic()
                if now - self._last_error_log > 30:
                    self._last_error_log = now
                    logger.warning("Result upload failed (%d failed, %d dropped): %s",
                                   self.failed, self.dropped, e)
