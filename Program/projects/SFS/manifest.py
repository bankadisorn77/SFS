"""SFS device manifest sent to the web server at registration.

Pure function of the config (no camera / model / IO access) so agent.py can
build it without starting the inspection worker. The web renders status
badges, cameras, live control and the settings form from this document;
see web docs/EDGE_CONTRACT.md.
"""
from typing import Any, Dict, List

MANIFEST_VERSION = 1

ONLINE = {"on": "ONLINE", "off": "ERROR", "on_color": "green", "off_color": "red"}
ON_OFF = {"on": "ON", "off": "OFF", "on_color": "green", "off_color": "grey"}
ALARM = {"on": "ON", "off": "OFF", "on_color": "red", "off_color": "grey"}
FAIL = {"on": "FAIL", "off": "OK", "on_color": "red", "off_color": "green"}


def _cameras(config) -> List[Dict[str, Any]]:
    cams = []
    for cam in getattr(config, "inspection_cameras", []) or []:
        cam_id = str(cam.get("id"))
        cams.append({"id": cam_id, "label": cam_id.replace("_", " ").upper()})
    return cams


def build_manifest(config) -> Dict[str, Any]:
    cameras = _cameras(config)
    return {
        "version": MANIFEST_VERSION,
        "project_id": getattr(config, "project_id", "SFS"),
        "project_name": "SFS Poka-Yoke Ring Loading",
        "groups": [
            {"id": "system", "title": "System"},
            {"id": "io", "title": "PLC I/O"},
            {"id": "stream", "title": "Live"},
        ],
        "summary": ["program_status", "camera", "gpio", "machine_status", "alarm_status"],
        "status": [
            {"key": "program_status", "label": "AI", "type": "enum", "group": "system",
             "colors": {"RUNNING": "green", "OFF": "grey", "ERROR": "red"}},
            {"key": "camera", "label": "CAMERA", "type": "bool_map", "group": "system",
             "labels": {c["id"]: c["label"] for c in cameras}, **ONLINE},
            {"key": "gpio", "label": "GPIO", "type": "bool", "group": "system", **ONLINE},
            {"key": "machine_status", "label": "READY", "type": "bool", "group": "io",
             "on": "READY", "off": "NOT READY", "on_color": "green", "off_color": "orange"},
            {"key": "alarm_status", "label": "ALARM", "type": "bool", "group": "io", **ALARM},
            {"key": "round_1_input", "label": "R1 IN", "type": "bool", "group": "io", **ON_OFF},
            {"key": "round_2_input", "label": "R2 IN", "type": "bool", "group": "io", **ON_OFF},
            {"key": "round_1_output", "label": "FB1", "type": "bool", "group": "io", **FAIL},
            {"key": "round_2_output", "label": "FB2", "type": "bool", "group": "io", **FAIL},
            {"key": "stream_status", "label": "LIVE", "type": "bool", "group": "stream",
             "on": "ON", "off": "OFF", "on_color": "orange", "off_color": "grey"},
            {"key": "stream_camera", "label": "LIVE CAM", "type": "text", "group": "stream"},
        ],
        "cameras": cameras,
        "stream": {
            "port": int(getattr(config, "stream_port", 8081)),
            "path": "/stream/{camera_id}",
            "live_control": True,
            "blocks_inspection": True,
            "max_live_seconds": int(getattr(config, "live_timeout_s", 600)),
        },
        "settings": [
            {"key": "MQTTServer", "label": "MQTT Broker IP", "type": "text", "group": "Network",
             "value": getattr(config, "MQTTServer", "")},
            {"key": "confident", "label": "Confidence threshold", "type": "number", "group": "AI",
             "min": 0.5, "max": 1.0, "step": 0.01, "value": float(getattr(config, "confident", 0.85))},
            {"key": "ROI", "label": "ROI (ratio)", "type": "number_list", "group": "AI",
             "labels": ["x min", "x max", "y min", "y max"], "min": 0.0, "max": 1.0, "step": 0.01,
             "value": list(getattr(config, "ROI", []))},
            {"key": "inputChannel", "label": "Input channels", "type": "channel_list", "group": "I/O",
             "min": 0, "max": 15, "value": list(getattr(config, "inputChannel", []))},
            {"key": "outputChannel", "label": "Output channels", "type": "channel_list", "group": "I/O",
             "min": 0, "max": 15, "value": list(getattr(config, "outputChannel", []))},
            {"key": "cameras", "label": "Cameras enabled", "type": "camera_enable", "group": "Cameras",
             "value": list(getattr(config, "cameras", []))},
        ],
    }
