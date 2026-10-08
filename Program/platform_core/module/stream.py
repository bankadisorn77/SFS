import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

logger = logging.getLogger("StreamServer")

BOUNDARY = "frame"


class StreamServer:
    def __init__(self, camera_manager, port: int = 8081, width: int = 960,
                 quality: int = 70, fps: int = 10):
        self.camera_manager = camera_manager
        self.port = port
        self.width = width
        self.quality = quality
        self.interval = 1.0 / max(1, fps)
        self._server = None
        self._thread = None

    def is_live(self, camera_id: str) -> bool:
        camera = self.camera_manager.get(camera_id)
        return camera is not None and bool(getattr(camera, "is_streaming", False))

    def encode(self, camera_id: str):
        frame = self.camera_manager.get_frame(camera_id, copy=False)
        if frame is None:
            return None
        height, width = frame.shape[:2]
        if self.width and width > self.width:
            frame = cv2.resize(frame, (self.width, int(height * self.width / width)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        return buf.tobytes() if ok else None

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parts = self.path.split("?")[0].strip("/").split("/")
                if len(parts) == 2 and parts[0] == "stream":
                    self._stream(parts[1])
                elif len(parts) == 2 and parts[0] == "snapshot":
                    self._snapshot(parts[1])
                else:
                    self.send_error(404)

            def _snapshot(self, camera_id):
                jpg = outer.encode(camera_id) if outer.is_live(camera_id) else None
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

            def _stream(self, camera_id):
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
                        jpg = outer.encode(camera_id)
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
