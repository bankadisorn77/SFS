import json
import logging
import os
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class Config:
    project_id = "SFS"

    def __init__(self, path: Optional[str] = None):
        logger.info("Setting up config.")
        self.root = Path(__file__).resolve().parents[2]
        self.path = Path(
            path
            or os.environ.get(
                "SFS_CONFIG_PATH", str(self.root / "config" / "config.json")
            )
        )
        self.device_id = "Dev-01"
        self.ip = "127.0.0.1"
        self.mac = "XX:XX:XX:XX:XX:XX"
        self.api_port = 8080
        self.stream_port = 8081
        self.api_key = "api_key"

        self.MQTTServer = "localhost"
        self.server_url = "localhost"
        # MQTT topic prefix: must match the web server project profile.
        self.mqtt_prefix = "SFS_YOKA_POKE"
        self.save_image_path = str(self.root / "saved_images_output")
        self.stop_flag_path = str(self.root.parent / "stop_worker.flag")

        # Live stream safety: edge turns live off by itself after this many seconds.
        self.live_timeout_s = 600
        # Send every inspection result (+ resized image) to the web server.
        self.upload_results = True
        self.upload_image_width = 1280
        self.upload_jpeg_quality = 80

        # พารามิเตอร์สำหรับ AI Model และ ROI
        self.confident = 0.85
        self.ROI = [0.25, 0.80, 0.20, 0.55]

        self.inputChannel = [
            {"name": "round_1", "channel": 0},
            {"name": "round_2", "channel": 1},
        ]
        self.outputChannel = [
            {"name": "cam_1", "channel": 0},
            {"name": "cam_2", "channel": 1},
            {"name": "cam_3", "channel": 2},
            {"name": "round", "channel": 3},
            {"name": "alarm", "channel": 4},
            {"name": "state_machine", "channel": 5},
            {"name": "FB1", "channel": 6},
            {"name": "FB2", "channel": 7},
        ]
        self.cameraAOI = {"width": 1920, "height": 1080}

        self.referenceLine = {
            "cam_1": {"Angle": None, "x1": None, "y1": None, "x2": None, "y2": None},
            "cam_2": {"Angle": None, "x1": None, "y1": None, "x2": None, "y2": None},
            "cam_3": {"Angle": None, "x1": None, "y1": None, "x2": None, "y2": None},
        }

        self.r1_rule = {"width": 1920, "height": 1080, "angle": 10}
        self.r2_rule = {"width": 1920, "height": 1080, "angle": 10}

        self.cameras = [
            {
                "id": "cam_1",
                "driver": "ueye",
                "hardware_index": 1,
                "enabled": True,
                "pipeline": "SFS",
            }
        ]
        self.pipelines = {
            "SFS": {
                "mode": "shared",
                "steps": [
                    {"id": "capture", "type": "capture"},
                    {"id": "measure", "type": "measure"},
                    {"id": "validate", "type": "validate"},
                    {"id": "decision", "type": "decision"},
                ],
            },
            "stream_only": {
                "mode": "shared",
                "steps": [{"id": "stream", "type": "stream_only"}],
            },
        }
        self.load()

    @property
    def width(self) -> int:
        return int(self.cameraAOI.get("width", 1920))

    @property
    def height(self) -> int:
        return int(self.cameraAOI.get("height", 1080))

    @property
    def stream_control_path(self) -> str:
        return str(Path(self.stop_flag_path).with_name("stream_control.json"))

    @property
    def inspection_cameras(self) -> List[dict]:
        return [c for c in self.cameras if c.get("enabled", True)]

    def get_input_channel(self, name: str, default: int = 0) -> int:
        for item in self.inputChannel:
            if isinstance(item, dict) and item.get("name") == name:
                return int(item.get("channel", default))
        return default

    def get_output_channel(self, name: str, default: int = 0) -> int:
        for item in self.outputChannel:
            if isinstance(item, dict) and item.get("name") == name:
                return int(item.get("channel", default))
        return default

    _INT_FIELDS = ("api_port", "stream_port", "live_timeout_s", "upload_image_width", "upload_jpeg_quality")

    def load(self):
        if not self.path.exists():
            self.save()
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            for name in (
                "device_id",
                "ip",
                "mac",
                "MQTTServer",
                "server_url",
                "save_image_path",
                "api_key",
                "project_id",
                "stop_flag_path",
                "mqtt_prefix",
            ):
                if name in data:
                    setattr(self, name, str(data[name]))

            for name in self._INT_FIELDS:
                if name in data:
                    setattr(self, name, int(data[name]))

            if "upload_results" in data:
                self.upload_results = bool(data["upload_results"])

            if "confident" in data:
                self.confident = float(data["confident"])

            if "ROI" in data and isinstance(data["ROI"], list):
                self.ROI = [float(x) for x in data["ROI"]]

            for name in (
                "inputChannel",
                "outputChannel",
                "cameraAOI",
                "referenceLine",
                "r1_rule",
                "r2_rule",
                "cameras",
                "pipelines",
            ):
                if name in data:
                    setattr(self, name, data[name])

            if not os.path.isabs(self.stop_flag_path):
                self.stop_flag_path = str(self.root.parent / self.stop_flag_path)

        except (OSError, ValueError, TypeError, KeyError) as exc:
            logger.warning("Failed to load config %s: %s", self.path, exc)

    def as_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "device_id": self.device_id,
            "ip": self.ip,
            "mac": self.mac,
            "api_port": self.api_port,
            "stream_port": self.stream_port,
            "api_key": self.api_key,
            "MQTTServer": self.MQTTServer,
            "server_url": self.server_url,
            "mqtt_prefix": self.mqtt_prefix,
            "save_image_path": self.save_image_path,
            "stop_flag_path": self.stop_flag_path,
            "live_timeout_s": self.live_timeout_s,
            "upload_results": self.upload_results,
            "upload_image_width": self.upload_image_width,
            "upload_jpeg_quality": self.upload_jpeg_quality,
            "confident": self.confident,
            "ROI": self.ROI,
            "inputChannel": self.inputChannel,
            "outputChannel": self.outputChannel,
            "cameraAOI": self.cameraAOI,
            "referenceLine": self.referenceLine,
            "r1_rule": self.r1_rule,
            "r2_rule": self.r2_rule,
            "cameras": self.cameras,
            "pipelines": self.pipelines,
        }

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self.as_dict(), f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    _STR_FIELDS = (
        "device_id",
        "ip",
        "mac",
        "MQTTServer",
        "server_url",
        "save_image_path",
        "api_key",
        "project_id",
        "mqtt_prefix",
    )
    _STRUCT_FIELDS = {
        "ROI": list,
        "inputChannel": list,
        "outputChannel": list,
        "cameraAOI": dict,
        "referenceLine": dict,
        "r1_rule": dict,
        "r2_rule": dict,
        "cameras": list,
        "pipelines": dict,
    }

    @staticmethod
    def _set_channel(items, name, channel):
        for item in items:
            if isinstance(item, dict) and item.get("name") == name:
                item["channel"] = channel
                return
        items.append({"name": name, "channel": channel})

    def update(self, params: dict) -> bool:
        if not isinstance(params, dict):
            return False
        data = dict(params)
        if data.get("name") is not None:
            data.setdefault("device_id", data["name"])
        if data.get("mqtt_broker") is not None:
            data.setdefault("MQTTServer", data["mqtt_broker"])
            data.setdefault("server_url", data["mqtt_broker"])
        for legacy_key, channel_name in (
            ("output_alarm_channel", "alarm"),
            ("output_light_channel", "state_machine"),
        ):
            if data.get(legacy_key) is not None:
                self._set_channel(self.outputChannel, channel_name, int(data[legacy_key]))
        for name in self._STR_FIELDS:
            if data.get(name) is not None:
                setattr(self, name, str(data[name]))
        for name in self._INT_FIELDS:
            if data.get(name) is not None:
                setattr(self, name, int(data[name]))
        if data.get("upload_results") is not None:
            self.upload_results = bool(data["upload_results"])
        if data.get("confident") is not None:
            self.confident = float(data["confident"])

        if "referenceLine" in data and isinstance(data["referenceLine"], dict):
            if not isinstance(self.referenceLine, dict):
                self.referenceLine = {}
            for cam_key, line_val in data["referenceLine"].items():
                self.referenceLine[cam_key] = line_val

        for name, kind in self._STRUCT_FIELDS.items():
            if name == "referenceLine":
                continue
            if isinstance(data.get(name), kind):
                setattr(self, name, data[name])

        if isinstance(data.get("ROI"), list):
            self.ROI = [float(x) for x in data["ROI"]]

        self.save()
        return True
