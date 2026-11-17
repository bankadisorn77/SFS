import json
import logging
import os
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class Config:
    project_id = "SFS"

    _STR_FIELDS = (
        "device_id",
        "ip",
        "mac",
        "MQTTServer",
        "server_url",
        "save_image_path",
        "api_key",
        "project_id",
        "stop_flag_path",
    )
    _STRUCT_FIELDS = {
        "inputChannel": list,
        "outputChannel": list,
        "cameraAOI": dict,
        "r1_rule": dict,
        "r2_rule": dict,
        "cameras": list,
        "pipelines": dict,
    }

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
        self.save_image_path = str(self.root / "saved_images_output")
        self.stop_flag_path = str(self.root.parent / "stop_worker.flag")

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

    def load(self):
        if not self.path.exists():
            self.save()
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            for name in self._STR_FIELDS:
                if name in data:
                    raw_val = data[name]
                    if raw_val is not None:
                        val = str(raw_val).strip()
                        if val != "":
                            setattr(self, name, val)

            if "api_port" in data and data["api_port"] is not None:
                self.api_port = int(data["api_port"])

            if "stream_port" in data and data["stream_port"] is not None:
                self.stream_port = int(data["stream_port"])

            for name, expected_type in self._STRUCT_FIELDS.items():
                if name in data and isinstance(data[name], expected_type):
                    setattr(self, name, data[name])

            current_stop_flag = self.stop_flag_path or "stop_worker.flag"
            if not os.path.isabs(current_stop_flag):
                self.stop_flag_path = str(self.root.parent / current_stop_flag)

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
            "save_image_path": self.save_image_path,
            "stop_flag_path": self.stop_flag_path,
            "inputChannel": self.inputChannel,
            "outputChannel": self.outputChannel,
            "cameraAOI": self.cameraAOI,
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
                
        if data.get("api_port") is not None:
            self.api_port = int(data["api_port"])
        if data.get("stream_port") is not None:
            self.stream_port = int(data["stream_port"])

        for name, expected_type in self._STRUCT_FIELDS.items():
            if name in data and isinstance(data[name], expected_type):
                setattr(self, name, data[name])

        self.save()
        return True