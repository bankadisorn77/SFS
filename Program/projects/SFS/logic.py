import logging

logger = logging.getLogger(__name__)

DEFAULT_MAX_OPEN_RATIO = 0.25

# ข้อความเมื่อ detector ตรวจไม่สำเร็จ (key = info["status"] จาก slot_detector.py)
FAIL_MESSAGES = {
    "NO_FRAME": "Detection Failed (No frame)",
    "NO_REFERENCE": "Detection Failed (No reference - run INIT mode)",
    "BAD_ROUND": "Detection Failed (Unknown round)",
}


class Logic:
    def __init__(self):
        self.state = 1

    def evaluate(self, info: dict, rule: dict) -> tuple[bool, str]:
        """info = SlotDetector.check() result, rule = r1_rule / r2_rule from config.
        PASS only if EVERY watched slot is open <= rule["max_open_ratio"]."""
        logger.debug("evaluate info=%s", info)
        info = info or {}
        rule = rule or {}
        status = info.get("status")
        if status != "OK":
            return False, FAIL_MESSAGES.get(status, f"Detection Failed ({status or 'No result'})")

        ratios = info.get("ratios") or {}
        if not ratios:
            return False, "Detection Failed (No slots measured)"

        max_open = rule.get("max_open_ratio", DEFAULT_MAX_OPEN_RATIO)
        open_slots = {k: v for k, v in ratios.items() if v > max_open}
        if open_slots:
            detail = ", ".join(f"{k} {v:.0%}" for k, v in sorted(open_slots.items()))
            return False, f"Slot open too much ({detail} > {max_open:.0%})"

        return True, "PASS"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logic = Logic()
    rule = {"max_open_ratio": 0.25}
    for info in (
        {"status": "OK", "ratios": {"bot_L": 0.0, "bot_R": 0.0}},
        {"status": "OK", "ratios": {"bot_L": 0.61, "bot_R": 0.0}},
        {"status": "NO_REFERENCE"},
    ):
        logger.info("%s", logic.evaluate(info, rule))