import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

import cv2

logger = logging.getLogger("StreamServer")

BOUNDARY = "frame"


def _overlay_flag(query: str, default: bool) -> bool:
    """?overlay=0/1 in the stream URL overrides the project default."""
    values = parse_qs(query or "").get("overlay")
    if not values:
        return default
    return values[-1].strip().lower() not in ("0", "false", "off", "no")


class StreamServer:
    def __init__(self, camera_manager, port: int = 8081, width: int = 960,
                 quality: int = 70, fps: int = 10,
                 overlay: Optional[Callable] = None, overlay_default: bool = True):
        self.camera_manager = camera_manager
        self.port = port
        self.width = width
        self.quality = quality
        self.interval = 1.0 / max(1, fps)
        # overlay(frame, camera_id) draws project guide lines (e.g. BG setup frame)
        # on the live image only; inspection images are never touched.
        self.overlay = overlay
        self.overlay_default = bool(overlay_default)
        self._server = None
        self._thread = None

    def is_live(self, camera_id: str) -> bool:
        camera = self.camera_manager.get(camera_id)
        return camera is not None and bool(getattr(camera, "is_streaming", False))

    def encode(self, camera_id: str, overlay: Optional[bool] = None):
        frame = self.camera_manager.get_frame(camera_id, copy=False)
        if frame is None:
            return None
        height, width = frame.shape[:2]
        if self.width and width > self.width:
            frame = cv2.resize(frame, (self.width, int(height * self.width / width)))
        elif self.overlay is not None:
            frame = frame.copy()  # never draw on the camera's own buffer
        use_overlay = self.overlay_default if overlay is None else overlay
        if self.overlay is not None and use_overlay:
            try:
                self.overlay(frame, camera_id)
            except Exception as exc:
                logger.debug("Stream overlay failed: %s", exc)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        return buf.tobytes() if ok else None

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                url = urlsplit(self.path)
                parts = url.path.strip("/").split("/")
                overlay = _overlay_flag(url.query, outer.overlay_default)
                if len(parts) == 2 and parts[0] == "stream":
                    self._stream(parts[1], overlay)
                elif len(parts) == 2 and parts[0] == "snapshot":
                    self._snapshot(parts[1], overlay)
                else:
                    self.send_error(404)

            def _snapshot(self, camera_id, overlay=None):
                jpg = outer.encode(camera_id, overlay) if outer.is_live(camera_id) else None
                if jpg is None:
                    self.send_error(503, "camera is not streaming")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpg)))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(jpg)

            def _stream(self, camera_id, overlay=None):
                if not outer.is_live(camera_id):
                    self.send_error(503, "camera is not streaming")
                    return
                self.send_response(200)
                self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                try:
                    while outer.is_live(camera_id):
                        jpg = outer.encode(camera_id, overlay)
                        if jpg is not None:
                            self.wfile.write(
                                f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                                f"Content-Length: {len(jpg)}\r\n\r\n".encode()
                            )
                            self.wfile.write(jpg)
                            self.wfile.write(b"\r\n")
                        time.sleep(outer.interval)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        return Handler

    def start(self):
        if self._server is not None:
            return
        try:
            self._server = ThreadingHTTPServer(("0.0.0.0", self.port), self._make_handler())
        except OSError as exc:
            logger.error("Stream server failed to bind port %s: %s", self.port, exc)
            self._server = None
            return
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="StreamServer"
        )
        self._thread.start()
        logger.info("Stream server listening on port %s", self.port)

    def stop(self):
        if self._server is None:
            return
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
        self._server = None
        self._thread = None
