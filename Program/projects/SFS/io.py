from typing import Dict, Optional


class IO:
    def __init__(self, io_manager, config):
        self.io = io_manager
        self.config = config
        self.cam_channel_map = {"cam_1": 0, "cam_2": 1, "cam_3": 2}
        self.round_channel_map = ["FB1", "FB2"]


    # ==========================
    # Digital Inputs
    # ==========================
    def read_round_1(self) -> bool:
        ch = self.config.get_input_channel("round_1", default=0)
        return bool(self.io.read_input(ch))

    def read_round_2(self) -> bool:
        ch = self.config.get_input_channel("round_2", default=1)
        return bool(self.io.read_input(ch))

    # ==========================
    # Digital Outputs
    # ==========================

    def set_state_machine(self, value: bool):
        ch = self.config.get_output_channel("state_machine", default=1)
        return self.io.write_output(ch, bool(value))

    def set_alarm(self, value: bool):
        ch = self.config.get_output_channel("alarm", default=4)
        return self.io.write_output(ch, bool(value))

    def cam_output(self, camera_statuses: Dict[str, bool], round_idx: int = 0):
        for cam_id, is_pass in camera_statuses.items():
            self.io.write_output(self.config.get_output_channel("round", default=3), bool(round_idx))
            if is_pass:
                ch = self.cam_channel_map.get(cam_id)
                if ch is not None:
                    self.io.write_output(ch, True)
            else:
                ch = self.cam_channel_map.get(cam_id)
                if ch is not None:
                    self.io.write_output(ch, False)

    def set_FB_trick(self, value: bool, round_idx: int = 0):
        ch = self.config.get_output_channel(self.round_channel_map[round_idx], default=0)
        return self.io.write_output(ch, bool(value))
   
            

    # ==========================
    # Reset
    # ==========================
    # def reset_round_outputs(self):
    #     for ch in self.cam_channel_map.values():
    #         self.io.write_output(ch, False)

    # def reset_FB_trick(self):
    #     for ch_name in self.round_channel_map:
    #         ch = self.config.get_output_channel(ch_name, default=0)
    #         self.io.write_output(ch, False)

    def reset_state(self):
        self.io.write_output(self.config.get_output_channel("round", default=3), False)
        for ch in self.cam_channel_map.values():
            self.io.write_output(ch, False)
        for ch_name in self.round_channel_map:
                    ch = self.config.get_output_channel(ch_name, default=0)
                    self.io.write_output(ch, False)

    def reset_outputs(self):
        self.set_alarm(False)
        self.set_state_machine(False)
        self.reset_state()