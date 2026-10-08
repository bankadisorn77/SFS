class Logic:
    def __init__(self):
        self.state = 1

    def evaluate(self, info: dict, rule: dict) -> tuple[bool, str]:
        # print(f"Evaluating detection info: {info} with rule: {rule}")
        if not info or info.get("status") != "OK":
            return False, "Detection Failed (< 2 Holes)"

        angle = info.get("angle_deg", 0.0)
        mid_y = info.get("mid_y", 0)
        mid_x = info.get("mid_x", 0)

        max_angle = rule.get("angle", 7)
        if abs(angle) > max_angle:
            return False, f"Angle out of spec ({angle:.2f} deg > ±{max_angle} deg)"

        max_mid_y = rule.get("max_mid_y", rule.get("height", 1080))
        min_mid_x = rule.get("min_mid_x", 0)
        max_mid_x = rule.get("max_mid_x", rule.get("width", 1920))

        if mid_y > max_mid_y:
            return False, f"Position Y out of spec ({mid_y} > {max_mid_y})"

        if not (min_mid_x <= mid_x <= max_mid_x):
            return False, f"Position X out of spec ({mid_x} not in [{min_mid_x}, {max_mid_x}])"

        return True, "PASS"


if __name__ == "__main__":
    logic = Logic()
    
    info = {
        'status': 'OK',
        'angle_deg': 0.6464204248135019,
        'mid_x': 590,
        'mid_y': 124,
        'left_hole': (296, 145),
        'right_hole': (885, 151)
    }
    
    rule = {
        'width': 1920,
        'height': 1080,
        'angle': 7,

    }

    is_pass, message = logic.evaluate(info, rule)
    print(f"Result: {is_pass} | Reason: {message}")