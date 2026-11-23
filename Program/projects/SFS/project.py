import json
import os
import time
from typing import Dict, Any, Callable, List
from pathlib import Path

from platform_core.project import ProjectAdapter
from platform_core.context import InspectionContext
from platform_core.module.stream import StreamServer
from platform_core.module.result_uploader import ResultUploader
import logging
from .detector import Detector
from .io import IO
from .config import Config
from .logic import Logic
from .overlay import draw_alignment_lines

logger = logging.getLogger("SfsPokaYokeProject")
try:
    from .img_logger import save_result_image  
    logger.info('save DEMO')
except ImportError:
    save_result_image = None


class SfsPokaYokeProject(ProjectAdapter):

    project_id = Config.project_id
    

    def __init__(self):
        self.config = Config()
        self.logic = Logic(min_confidence=self.config.confident)
        self.device_id = self.config.device_id
        self.cameras = self.config.cameras
        self.detector = Detector(modelPath=str(Path(__file__).resolve().parent / "model" / "best.pt"))
        self.round_index = 0
        self.io = None
        self.R1_passed = False
        self._hw_prev = None
        self._hw_synced = False
        self._hw_fault = False
        self._cycle_alarm = False
        self._input_conflict = False
        self._alarm_until = None
        self.streaming = False
        self._stream_cam = None
        self._stream_mtime = None
        self._stream_started = None
        self.stream = None
        self.uploader = ResultUploader(self.config)
        self._last_status = {
            "round_1_input": False,
            "round_2_input": False,
            "machine_status": False,
            "alarm_status": False,
            "round_1_output": False,
            "round_2_output": False,
            "stream_status": False,
            "stream_camera": "",
            "stream_port": self.config.stream_port,
        }

    def camera_definition(self) -> List[Dict[str, Any]]:
        if hasattr(self.config, "camera_definitions"):
            # enabled cameras + camera_image_format merged into settings
            return self.config.camera_definitions()
        if isinstance(self.config, dict):
            cameras = self.config.get("cameras", [])
        else:
            cameras = getattr(self.config, "cameras", [])

        return [c for c in cameras if c.get("enabled", True)]
    
    def storage_definition(self):
        return {"base_dir": self.config.save_image_path}

    def pipeline_definition(self, pipeline_id: str) -> Dict[str, Any]:
        return self.config.pipelines.get(pipeline_id)

    def get_step_handlers(self) -> Dict[str, Callable]:
        return {
            "capture": self.handle_capture,
            "measure": self.handle_measure,
            "validate": self.handle_validate,
            "decision": self.handle_decision,
        }

    def _inspection_camera_ids(self):
        return [c["id"] for c in self.config.inspection_cameras]

    def status_payload(self):
        return dict(self._last_status)

    def handle_capture(self, context: InspectionContext, step: Dict[str, Any]) -> bool:
        target_camera = context.camera_id or (list(context.frames.keys())[0] if context.frames else None)
        return bool(target_camera and context.frames.get(target_camera) is not None)

    def handle_measure(self, context: InspectionContext, step: Dict[str, Any]):
        for camera_id in self._inspection_camera_ids():
            frame = context.frames.get(camera_id)
            if frame is None:
                logger.warning("[%s] No frame available for measurement.", camera_id)
                continue
            try:
                context.detections[camera_id] = self.detector.detection(frame, roi=self.config.ROI)
            except Exception:
                logger.exception("[%s] Inference failed -> NG", camera_id)
                context.detections[camera_id] = None


    def handle_validate(self, context: InspectionContext, step: Dict[str, Any]):
        for camera_id in self._inspection_camera_ids():
            info = context.detections.get(camera_id)
            valid, reason = self.logic.evaluate(info, self.round_index)
            passed = bool(valid)
            context.results[camera_id] = passed
            if passed:
                verdict = "EMPTY" if reason == "PASS (EMPTY)" else "OK"
            else:
                verdict = "NG" if reason in ("FAIL", "LOW CONFIDENCE") else "ERROR"
            context.metadata.setdefault("camera_decisions", {})[camera_id] = {
                "passed": passed, "reason": reason, "verdict": verdict,
                "threshold": self.logic.min_confidence,
            }
            logger.info(
                "[%s] Round %s: %s | %s",
                camera_id, self.round_index + 1, verdict, reason,
            )
            frame = context.frames.get(camera_id)
            path = None
            if save_result_image is not None and frame is not None:
                path = save_result_image(
                    frame, camera_id, self.round_index + 1, info,
                    verdict=verdict, reason=reason,
                    min_confidence=self.logic.min_confidence,
                )
                context.metadata.setdefault("result_images", {})[camera_id] = (
                    str(path) if path is not None else None
                )
                if path is None:
                    logger.warning("[%s] Result image was not saved.", camera_id)
            # Non-blocking: only queues a small dict; encode/HTTP run in the uploader thread.
            self.uploader.submit(self._result_record(context, camera_id, info, verdict, reason), path)

    def _result_record(self, context, camera_id, info, verdict, reason) -> Dict[str, Any]:
        det = info[0] if isinstance(info, (tuple, list)) and info else info
        det = det if isinstance(det, dict) else {}
        return {
            "camera_id": camera_id,
            "cycle_id": context.cycle_id,
            "pipeline_id": context.pipeline_id,
            "result": verdict,
            "detected_objects": {
                "class_name": det.get("class_name"),
                "confidence": det.get("confidence"),
                "all_probs": det.get("all_probs"),
            },
            "meta": {
                "round": self.round_index + 1,
                "reason": reason,
                "threshold": self.logic.min_confidence,
                "started_at": context.started_at,
            },
        }

    def handle_decision(self, context: InspectionContext, step: Dict[str, Any]) -> bool:
        inspection_ids = self._inspection_camera_ids()
        camera_statuses = {
            cam_id: bool(context.results.get(cam_id, False))
            for cam_id in inspection_ids
        }
        frames_ok = all(context.frames.get(cam_id) is not None for cam_id in inspection_ids)
        passed = bool(inspection_ids) and frames_ok and all(camera_statuses.values())
        context.metadata["final"] = passed
        return passed

    def run(self, runtime):
        self.io = IO(runtime.io_manager, self.config)
        self.io.reset_outputs()
        # Do not tell the PLC "ready" yet; _sync_hardware() turns state_machine on
        # only after cameras and GPIO are confirmed healthy.
        self.io.set_state_machine(False)

        self._last_status["machine_status"] = False
        self._remove_stream_control()
        # Live view shows the camera alignment lines (config "stream_overlay", default on;
        # ?overlay=0 in the stream URL turns them off). Inspection images are not touched.
        try:
            self.stream = StreamServer(runtime.camera_manager, port=self.config.stream_port,
                                       overlay=draw_alignment_lines,
                                       overlay_default=getattr(self.config, "stream_overlay", True))
        except TypeError:
            # Old platform_core/module/stream.py without the overlay hook: keep the
            # machine running (live view without lines) instead of a worker crash loop.
            logger.warning("StreamServer has no overlay support (old platform_core/module/stream.py); "
                           "live view runs WITHOUT alignment lines. Update stream.py.")
            self.stream = StreamServer(runtime.camera_manager, port=self.config.stream_port)
        self.stream.start()
        self.uploader.start()

        for _ in range(100):
            if isinstance(runtime.health_monitor.status["details"].get("camera"), dict):
                break
            time.sleep(0.1)

        self._last_r1 = self.io.read_round_1()
        self._last_r2 = self.io.read_round_2()
        self._loop_errors = 0

        while True:
            try:
                if self._loop_once(runtime):
                    break
                self._loop_errors = 0
            except Exception:
                # Never let one unexpected error kill the worker: a dead worker leaves
                # the outputs as they are. Fail safe (NG + alarm) and keep running.
                self._on_loop_error(runtime)
            time.sleep(0.05)

    def _on_loop_error(self, runtime):
        self._loop_errors += 1
        if self._loop_errors == 1 or self._loop_errors % 50 == 0:
            logger.exception("Unexpected error in inspection loop (#%d) -> NG + ALARM, continuing.",
                             self._loop_errors)
        try:
            self.io.reset_state()  # PASS outputs (cam_x / round) OFF
            self._cycle_alarm = True
            self._start_alarm_hold()
            self.io.set_alarm(True)
            for round_idx in range(len(self.io.fb_state())):
                self.io.set_fb_fail(round_idx, True)
            self._last_status["alarm_status"] = True
            self._sync_fb_status()
            runtime.update_status(**self._last_status)
        except Exception:
            logger.debug("Fail-safe output write failed", exc_info=True)
        time.sleep(0.5)

    def _loop_once(self, runtime) -> bool:
        """One pass of the inspection loop. Returns True when STOP was requested."""
        last_r1, last_r2 = self._last_r1, self._last_r2
        if self._stop_requested():
            self.io.reset_outputs()
            return True

        r1 = self.io.read_round_1()
        r2 = self.io.read_round_2()
        # Remember the inputs now: if this pass fails, the same edge must not start
        # another inspection on the next pass (the error already gave NG + alarm).
        self._last_r1, self._last_r2 = r1, r2

        self._poll_stream(runtime)
        ready = runtime.hardware_ready()
        gpio_ok = bool(runtime.health_monitor.status["details"].get("gpio"))
        self._sync_hardware(ready, gpio_ok)
        self._expire_alarm_hold()

        self._last_status.update({
            "round_1_input": r1,
            "round_2_input": r2
        })
        runtime.update_status(**self._last_status)

        if r1 and r2:
            if not self._input_conflict:
                self._on_input_conflict(runtime)

        elif not last_r1 and r1 and not r2:
            if self.streaming:
                logger.warning("Round 1 input detected but stream mode is on. Rejecting.")
                self._reject_not_ready(runtime, round_index=0, alarm=False)
            elif ready:
                logger.info("Round 1 input detected. Starting inspection cycle.")
                self._run_inspection_cycle(runtime, round_index=0)
            else:
                logger.warning("Round 1 input detected but hardware is not ready. Rejecting.")
                self._reject_not_ready(runtime, round_index=0)

        elif not last_r2 and r2 and not r1:
            if self.streaming:
                logger.warning("Round 2 input detected but stream mode is on. Rejecting.")
                self._reject_not_ready(runtime, round_index=1, alarm=False)
            elif not ready:
                logger.warning("Round 2 input detected but hardware is not ready. Rejecting.")
                self._reject_not_ready(runtime, round_index=1)
            elif self.R1_passed:
                logger.info("Round 2 input detected. Starting inspection cycle.")
                self._run_inspection_cycle(runtime, round_index=1)
            else:
                logger.warning("Round 2 input detected but Round 1 has not passed.")
                self._run_inspection_cycle(runtime, round_index=1)

        if not r1 and not r2:
            if self._input_conflict:
                self._clear_input_conflict()
            self._on_no_input()
        return False

    def _on_input_conflict(self, runtime):
        # round_1 and round_2 must never be high together: alarm, no inspection.
        logger.error("Input conflict: round_1 and round_2 are both HIGH. Alarm, no inspection.")
        self._input_conflict = True
        self._cycle_alarm = True
        self._alarm_until = None  # held for as long as the conflict lasts
        self.io.set_alarm(True)
        # FB1+FB2 FAIL (fb_fail, not fb_fault) so the 5 s hold releases them like an NG.
        for round_idx in range(len(self.io.fb_state())):
            self.io.set_fb_fail(round_idx, True)
        self._last_status["alarm_status"] = True
        self._sync_fb_status()
        runtime.update_status(**self._last_status)

    def _clear_input_conflict(self):
        # Alarm + FB1/FB2 stay ON for alarm_hold_s after the conflict ends.
        logger.info("Input conflict cleared (both inputs LOW). Holding alarm %.1fs.", self._alarm_hold_s())
        self._input_conflict = False
        self._start_alarm_hold()

    def _alarm_hold_s(self) -> float:
        try:
            return max(0.0, float(getattr(self.config, "alarm_hold_s", 5.0)))
        except (TypeError, ValueError):
            return 5.0

    def _start_alarm_hold(self):
        self._alarm_until = time.monotonic() + self._alarm_hold_s()

    def _expire_alarm_hold(self):
        """Reject alarm (out6) + FB1/FB2 FAIL are held alarm_hold_s seconds, then released.

        out6 = reject only (NG, input conflict, loop error, trigger while not ready).
        Hardware faults never drive out6: they show on state_machine (out7 OFF) and on
        FB1/FB2 (fault), which stay until the hardware is ready again.
        """
        if not self._cycle_alarm or self._input_conflict or self._alarm_until is None:
            return
        if time.monotonic() < self._alarm_until:
            return
        logger.info("Alarm hold (%.1fs) elapsed. Releasing alarm and FB.", self._alarm_hold_s())
        self._cycle_alarm = False
        self._alarm_until = None
        self.io.clear_fb_fail()
        self.io.set_fb_fault(self._hw_fault)
        self.io.set_alarm(False)
        self._last_status["alarm_status"] = False
        self._sync_fb_status()

    def _sync_fb_status(self):
        for i, value in enumerate(self.io.fb_state()):
            self._last_status[f"round_{i + 1}_output"] = value

    def _on_no_input(self):
        if self._cycle_alarm:
            return  # NG / conflict alarm + FB are held by _expire_alarm_hold()
        self.io.clear_fb_fail()
        if not self._hw_fault:
            self.io.reset_state()
        # out6 is reject-only: no reject is held, so it is OFF (also during hw faults).
        self.io.set_alarm(False)
        self._last_status["alarm_status"] = False
        self._sync_fb_status()

    def _sync_hardware(self, ready: bool, gpio_ok: bool):
        # out7 (state_machine) ON = cameras + GPIO ready and not in live mode.
        # Not ready -> out7 OFF + FB1/FB2 fault; out6 (reject alarm) is NOT touched.
        light = ready and not self.streaming
        self._last_status["machine_status"] = light
        state = (ready, self.streaming)
        
        if not gpio_ok:
            self._hw_synced = False
            return
        if self._hw_synced and state == self._hw_prev:
            return
            
        ok = self.io.set_state_machine(light)
        
        if not ready:
            self.io.set_fb_fault(True)
            self._hw_fault = True
            self._sync_fb_status()
        elif self._hw_fault:
            self.io.set_fb_fault(False)
            self._hw_fault = False
            self._sync_fb_status()
            
        self._hw_prev = state
        self._hw_synced = bool(ok)

    def _reject_not_ready(self, runtime, round_index: int, alarm: bool = True):
        # A trigger that cannot be inspected is a reject: out6 ON, held alarm_hold_s.
        # (alarm=False in live mode: the operator turned live on on purpose.)
        if alarm:
            self._cycle_alarm = True
            self._start_alarm_hold()
            self.io.set_alarm(True)
            self._last_status["alarm_status"] = True
        
        self._hw_fault = True
        self.io.set_fb_fault(True)
        self._last_status["machine_status"] = False
        self._sync_fb_status()
        runtime.update_status(**self._last_status)

    def _update_stream_status(self):
        self._last_status.update({
            "stream_status": self.streaming,
            "stream_camera": self._stream_cam or "",
            "stream_port": self.config.stream_port,
        })

    def _remove_stream_control(self):
        try:
            os.remove(self.config.stream_control_path)
        except OSError:
            pass
        self._stream_mtime = None

    def _poll_stream(self, runtime):
        manager = runtime.camera_manager
        if self.streaming:
            cam = manager.get(self._stream_cam)
            if cam is None or not getattr(cam, "is_streaming", False):
                logger.warning(f"Stream on {self._stream_cam} stopped unexpectedly.")
                self.streaming = False
                self._stream_cam = None
                self._stream_started = None
                self._update_stream_status()
            elif self._live_expired():
                # Safety: never stay in live mode (= no inspection) if the web forgets to stop it.
                logger.warning("Live on %s exceeded %ss. Turning live OFF.",
                               self._stream_cam, self.config.live_timeout_s)
                manager.stop_alignment_live(self._stream_cam)
                self.streaming = False
                self._stream_cam = None
                self._stream_started = None
                self._remove_stream_control()
                self._update_stream_status()
                return
        path = self.config.stream_control_path
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            mtime = None
        if mtime == self._stream_mtime:
            return
        self._stream_mtime = mtime
        enabled, camera_id = False, None
        if mtime is not None:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                enabled = bool(data.get("enabled", False))
                camera_id = data.get("camera_id")
            except (OSError, ValueError, AttributeError) as exc:
                logger.warning(f"Invalid stream control file: {exc}")
                return
        self._apply_stream(runtime, enabled, camera_id)

    def _apply_stream(self, runtime, enabled: bool, camera_id):
        manager = runtime.camera_manager
        if camera_id is None:
            ids = [c["id"] for c in self.config.inspection_cameras]
            camera_id = ids[0] if ids else None
        if self.streaming and (not enabled or camera_id != self._stream_cam):
            manager.stop_alignment_live(self._stream_cam)
            self.streaming = False
            self._stream_cam = None
            self._stream_started = None
            logger.info("Stream mode OFF.")
        if enabled and not self.streaming:
            if camera_id is None or manager.get(camera_id) is None:
                logger.warning(f"Stream requested for unknown camera: {camera_id}")
            elif manager.start_alignment_live(camera_id):
                self.streaming = True
                self._stream_cam = camera_id
                self._stream_started = time.monotonic()
                logger.info(f"Stream mode ON ({camera_id}).")
            else:
                logger.warning(f"Failed to start live stream on {camera_id}.")
        self._update_stream_status()

    def _live_expired(self) -> bool:
        limit = int(getattr(self.config, "live_timeout_s", 600) or 0)
        return bool(limit > 0 and self._stream_started is not None
                    and time.monotonic() - self._stream_started >= limit)

    def _run_inspection_cycle(self, runtime, round_index: int = 0):
        self._cycle_alarm = False
        self._alarm_until = None
        self.io.reset_state()
        self.io.set_alarm(False)
        self._sync_fb_status()

        self.round_index = round_index
        # self.rule = self.config.r1_rule if round_index == 0 else self.config.r2_rule
        context = runtime.run_cycle(
            pipeline_id="SFS",
            cam_active=[c["id"] for c in self.config.inspection_cameras],
        )

        is_pass = bool(context.metadata.get("final", False))
        camera_statuses = context.results
        self.io.cam_output(camera_statuses, round_idx=round_index)

        if is_pass:
            self.R1_passed = True if round_index == 0 else False
            self.io.set_alarm(False)
            self._last_status["alarm_status"] = False
            self.io.set_fb_fail(round_index, False)
        else:
            self._cycle_alarm = True
            self._start_alarm_hold()  # alarm + FB released after alarm_hold_s
            self.io.set_alarm(True)
            self._last_status["alarm_status"] = True
            self.io.set_fb_fail(round_index, True)

        self._sync_fb_status()
        runtime.update_status(**self._last_status)
        
    def shutdown(self, runtime):
        if self.stream:
            self.stream.stop()
        self.uploader.stop()
        self._remove_stream_control()
        self.streaming = False
        self._stream_cam = None
        self._update_stream_status()
        if self.io:
            self.io.reset_outputs()
        self._last_status.update({
            "machine_status": False,
            "alarm_status": False,
            "round_1_output": False,
            "round_2_output": False,
        })

    def _stop_requested(self):
        flag = self.config.stop_flag_path
        if not os.path.exists(flag):
            return False
        self._last_status["machine_status"] = False
        try:
            os.remove(flag)
        except OSError:
            pass
        return True
