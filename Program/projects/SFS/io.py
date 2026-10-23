from typing import Dict, List, Optional


class IO:
    def __init__(self, io_manager, config):
        self.io = io_manager
        self.config = config

        camera_list = getattr(self.config, "cameras", []) or self.config.get("cameras", []) if isinstance(self.config, dict) else getattr(self.config, "cameras", [])
        self.cam_channel_map = {
            cam["id"]: self._get_output_ch(cam["id"])
            for cam in camera_list
            if "id" in cam and self._get_output_ch(cam["id"]) is not None
        }

        all_outputs = self._get_raw_output_channels()
        fb_names = [item["name"] for item in all_outputs if item.get("name", "").startswith("FB")]
        self.round_channel_map = sorted(fb_names) if fb_names else ["FB1", "FB2"]

        self._fb_fault = False
        self._fb_fail = [False] * len(self.round_channel_map)


    def _get_raw_output_channels(self) -> List[Dict]:
        if isinstance(self.config, dict):
            return self.config.get("outputChannel", [])
        return getattr(self.config, "outputChannel", []) or getattr(self.config, "output_channel", [])

    def _get_input_ch(self, name: str) -> Optional[int]:
        if hasattr(self.config, "get_input_channel"):
            return self.config.get_input_channel(name)
        input_list = self.config.get("inputChannel", []) if isinstance(self.config, dict) else getattr(self.config, "inputChannel", [])
        for item in input_list:
            if item.get("name") == name:
                return item.get("channel")
        return None

    def _get_output_ch(self, name: str) -> Optional[int]:
        if hasattr(self.config, "get_output_channel"):
            return self.config.get_output_channel(name)
        for item in self._get_raw_output_channels():
            if item.get("name") == name:
                return item.get("channel")
        return None

    # ==========================
    # Digital Inputs
    # ==========================
    def read_round_1(self) -> bool:
        ch = self._get_input_ch("round_1")
        return bool(self.io.read_input(ch)) if ch is not None else False

    def read_round_2(self) -> bool:
        ch = self._get_input_ch("round_2")
        return bool(self.io.read_input(ch)) if ch is not None else False

    # ==========================
    # Digital Outputs
    # ==========================
    def set_state_machine(self, value: bool):
        ch = self._get_output_ch("state_machine")
        if ch is not None:
            return self.io.write_output(ch, bool(value))
        return False

    def set_alarm(self, value: bool):
        ch = self._get_output_ch("alarm")
        if ch is not None:
            return self.io.write_output(ch, bool(value))
        return False

    def cam_output(self, camera_statuses: Dict[str, bool], round_idx: int = 0):
        round_ch = self._get_output_ch("round")
        if round_ch is not None:
            self.io.write_output(round_ch, bool(round_idx))

        for cam_id, is_pass in camera_statuses.items():
            ch = self.cam_channel_map.get(cam_id)
            if ch is None:
                ch = self._get_output_ch(cam_id)
            if ch is not None:
                self.io.write_output(ch, bool(is_pass))

    # ==========================
    # FB (feedback) outputs
    # ==========================
    def fb_state(self) -> List[bool]:
        return [self._fb_fault or fail for fail in self._fb_fail]

    def _write_fb(self):
        for ch_name, value in zip(self.round_channel_map, self.fb_state()):
            ch = self._get_output_ch(ch_name)
            if ch is not None:
                self.io.write_output(ch, bool(value))

    def set_fb_fault(self, value: bool):
        self._fb_fault = bool(value)
        self._write_fb()

    def set_fb_fail(self, round_idx: int, value: bool):
        if 0 <= round_idx < len(self._fb_fail):
            self._fb_fail[round_idx] = bool(value)
            self._write_fb()

    def clear_fb_fail(self):
        self._fb_fail = [False] * len(self.round_channel_map)
        self._write_fb()

    # ==========================
    # Reset
    # ==========================
    def reset_state(self):
        round_ch = self._get_output_ch("round")
        if round_ch is not None:
            self.io.write_output(round_ch, False)

        for ch in self.cam_channel_map.values():
            if ch is not None:
                self.io.write_output(ch, False)
        self.clear_fb_fail()

    def reset_outputs(self):
        self.set_alarm(False)
        self.set_state_machine(False)
        self._fb_fault = False
        self.reset_state()