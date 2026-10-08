import json
import os
import time
from typing import Dict, Any, Callable, List
from platform_core.project import ProjectAdapter
from platform_core.context import InspectionContext
from platform_core.module.stream import StreamServer
import logging
import asyncio
from .detector import Detector
from .io import IO
from .config import Config
from .logic import Logic
logger = logging.getLogger("SfsPokaYokeProject")
class SfsPokaYokeProject(ProjectAdapter):

    project_id = Config.project_id
    logic = Logic()

    def __init__(self):
        self.config = Config()
        self.device_id = self.config.device_id
        self.cameras = self.config.cameras
        self.detector = Detector()
        self.rule = None
        self.io = None
        self.R1_passed = False
        self._hw_prev = None
        self._hw_synced = False
        self._hw_fault = False
        self._cycle_alarm = False
        self.streaming = False
        self._stream_cam = None
        self._stream_mtime = None
        self.stream = None
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
        if isinstance(self.config, dict):
            cameras = self.config.get("cameras", [])
        else:
            cameras = getattr(self.config, "cameras", [])

        return [
            c for c in cameras
            if c.get("enabled", True)
        ]
    
    def storage_definition(self):
            return {"base_dir": self.config.save_image_path}

    def pipeline_definition(self, pipeline_id: str) -> Dict[str, Any]:
        pipeline_cfg = self.config.pipelines.get(pipeline_id)
        return pipeline_cfg

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
                logger.warning(f"[{camera_id}] No frame available for measurement.")
                continue
            _, info = self.detector.detect_frame(frame,)
            context.detections[camera_id] = info

        return 

    def handle_validate(self, context: InspectionContext, step: Dict[str, Any]):
            for camera_id in self._inspection_camera_ids():
                detections = context.detections.get(camera_id, [])
                valid, info = self.logic.evaluate(detections,self.rule)
                # if camera_id == 'cam_1':
                #      valid = True
                # else:
                #      valid = False
                context.results[camera_id] = bool(valid)

            print(f'validation results: {info}')
            return


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
        self._last_status["machine_status"] = True
        self._remove_stream_control()
        self.stream = StreamServer(runtime.camera_manager, port=self.config.stream_port)
        self.stream.start()

        for _ in range(100):
            if isinstance(runtime.health_monitor.status["details"].get("camera"), dict):
                break
            time.sleep(0.1)

        last_r1 = self.io.read_round_1()
        last_r2 = self.io.read_round_2()

        while True:
            if self._stop_requested():
                self.io.reset_outputs()
                break

            r1 = self.io.read_round_1()
            r2 = self.io.read_round_2()

            self._poll_stream(runtime)
            ready = runtime.hardware_ready()
            gpio_ok = bool(runtime.health_monitor.status["details"].get("gpio"))
            self._sync_hardware(ready, gpio_ok)

            self._last_status.update({
                "round_1_input": r1 ,
                "round_2_input": r2 
            })
            runtime.update_status(**self._last_status)
            if not last_r1 and r1 and not r2:
                if self.streaming:
                    logger.warning("Round 1 input detected but stream mode is on. Rejecting.")
                    self._reject_not_ready(runtime, round_index=0, alarm=False)
                elif ready:
                    logger.info("Round 1 input detected. Starting inspection cycle.")
                    self._run_inspection_cycle(runtime, round_index=0)
                else:
                    logger.warning("Round 1 input detected but hardware is not ready. Rejecting.")
                    self._reject_not_ready(runtime, round_index=0)


            if not last_r2 and r2 and not r1:
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
                    logger.warning("Round 2 input detected but Round 1 has not passed. Ignoring Round 2 input.")

            if not r1 and not r2:
                 self.io.reset_state()
                 if not self._hw_fault:
                     self.io.set_alarm(False)


            last_r1 = r1
            last_r2 = r2
            time.sleep(0.05)

    def _sync_hardware(self, ready: bool, gpio_ok: bool):
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
            ok = self.io.set_alarm(True) and ok
            self._hw_fault = True
            self._last_status["alarm_status"] = True
        elif self._hw_fault:
            if not self._cycle_alarm:
                ok = self.io.set_alarm(False) and ok
                self._last_status["alarm_status"] = False
            self._hw_fault = False
        self._hw_prev = state
        self._hw_synced = bool(ok)

    def _reject_not_ready(self, runtime, round_index: int, alarm: bool = True):
        if alarm:
            self.io.set_alarm(True)
            self._last_status["alarm_status"] = True
        self.io.set_FB_trick(True, round_idx=round_index)
        self._last_status["machine_status"] = False
        self._last_status["round_" + str(round_index + 1) + "_output"] = True
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
                self._update_stream_status()
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
            logger.info("Stream mode OFF.")
        if enabled and not self.streaming:
            if camera_id is None or manager.get(camera_id) is None:
                logger.warning(f"Stream requested for unknown camera: {camera_id}")
            elif manager.start_alignment_live(camera_id):
                self.streaming = True
                self._stream_cam = camera_id
                logger.info(f"Stream mode ON ({camera_id}).")
            else:
                logger.warning(f"Failed to start live stream on {camera_id}.")
        self._update_stream_status()

    def _run_inspection_cycle(self, runtime, round_index: int = 0):
        self.io.reset_state()
        self.io.set_alarm(False)
        self._last_status["round_" + str(round_index + 1) + "_output"] = False
        self._cycle_alarm = False
        self.rule = self.config.r1_rule if round_index == 0 else self.config.r2_rule
        context = runtime.run_cycle(
                    pipeline_id="SFS",
                    cam_active=[c["id"] for c in self.config.inspection_cameras],
        )

        is_pass = bool(context.metadata.get("final", False))
        camera_statuses = context.results
        if is_pass:
            self.R1_passed = True if round_index == 0 else False
            self.io.set_alarm(False)
            self._last_status["alarm_status"] = False
        else:
            self._cycle_alarm = True
            self.io.set_alarm(True)
            self._last_status["alarm_status"] = True
            self.io.set_FB_trick(True, round_idx=round_index)
            self._last_status["round_" + str(round_index + 1) + "_output"] = True
        self.io.cam_output(camera_statuses, round_idx=round_index,)
        runtime.update_status(**self._last_status)


    def shutdown(self, runtime):
        if self.stream:
            self.stream.stop()
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
